"""The super admin's speed controls (package S, 0053) against PostgreSQL: migration up, down
(0052's schema exactly) and up again; the shared scheduler uses the saved RPM at its next
admission, never above MODEL_RPM; the documents-at-once cap holds across worker copies, counts
slots still held above a lowered cap, never goes above WORKER_REPLICAS, and a dead worker's
slot is let go; the operator route (a fresh code, the server's caps, an audit row with the old
and new values, NULL to reset, never the app). Pages at once: test_reader_concurrency and
test_model_choice_pipeline.

The values belong to no customer: every test that sets one puts it back.
"""

import contextlib
import functools
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.api import totp
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import Job
from kaagaz.jobs import fair_share
from kaagaz.jobs.fair_share import ReadingCap
from kaagaz.jobs.revive import revive_stuck
from kaagaz.jobs.worker import Deferred
from kaagaz.worker_main import _documents_at_once
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api, TestClient
from tests.test_operator_auth_db import _Operator, _owner

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
SPEED = "/api/operator/speed"
_SET = "UPDATE platform_ai_settings SET model_rpm = :r, documents_at_once = :d, pages_at_once = :p"
_NEXT = "SELECT extract(epoch FROM next_at - clock_timestamp()) FROM model_rate_scheduler WHERE id"


@contextlib.contextmanager
def saved(
    db: Database, rpm: int | None = None, documents: int | None = None, pages: int | None = None
) -> Iterator[None]:
    """The three values saved for the block, then NULL again and the scheduler's turn free."""
    _owner(db, _SET, {"r": rpm, "d": documents, "p": pages})
    try:
        yield
    finally:
        _owner(db, _SET, {"r": None, "d": None, "p": None})
        _owner(db, "UPDATE model_rate_scheduler SET next_at = '-infinity' WHERE id", {})


def test_0053_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0052")
    at_0052 = m0002._schema(database)
    migrate(url, "0053")
    at_0053 = m0002._schema(database)
    with database.engine.begin() as conn:
        row = conn.execute(
            text("SELECT model_rpm, documents_at_once, pages_at_once FROM platform_ai_settings")
        )
        assert row.one() == (None, None, None)  # nothing saved: the server's settings
    m0002._downgrade(url, "0052")
    assert m0002._schema(database) == at_0052
    migrate(url, "0053")
    assert m0002._schema(database) == at_0053


def test_the_values_are_checked_in_the_database_and_the_app_cannot_write_them(db: Database) -> None:
    for rpm, documents, pages in ((0, None, None), (1201, None, None), (None, 0, None), (None, None, 0)):
        with pytest.raises(DBAPIError):
            _owner(db, _SET, {"r": rpm, "d": documents, "p": pages})
    customer, _ = new_customer(db)
    with pytest.raises(DBAPIError), db.customer(customer) as conn:
        conn.execute(text("UPDATE platform_ai_settings SET model_rpm = 1"))
    with pytest.raises(DBAPIError), db.customer(customer) as conn:  # the operator's function is not the app's
        conn.execute(text("SELECT kaagaz_op_set_speed(:o, 1, 1, 1, 1)"), {"o": str(uuid.uuid4())})


def _admit(db: Database, platform_rpm: int) -> None:
    request_id = str(uuid.uuid4())
    while (wait := db.model_rate_slot(request_id, None, None, 10.0, platform_rpm)) > 0:
        time.sleep(wait)
    assert wait == 0


def _next_turn(db: Database) -> float:
    return float(_owner(db, _NEXT, {})[0][0])


def test_the_scheduler_uses_the_saved_rpm_at_its_next_admission(db: Database) -> None:
    with saved(db):
        _admit(db, 1200)
        assert _next_turn(db) <= 0.05  # MODEL_RPM 1200: 50 ms apart
        _owner(db, _SET, {"r": 60, "d": None, "p": None})  # no restart, no new gate
        _admit(db, 1200)
        assert 0.9 <= _next_turn(db) <= 1.0  # 60 RPM: a second apart


def test_the_saved_rpm_never_goes_above_model_rpm(db: Database) -> None:
    with saved(db, rpm=1000):  # saved when the server allowed more, then MODEL_RPM was lowered
        _admit(db, 60)
        assert 0.9 <= _next_turn(db) <= 1.0  # MODEL_RPM's 60, not 1000


def _cap(db: Database, most: int = 3) -> ReadingCap:
    """One worker copy's cap: its slots are its own connections."""
    return ReadingCap(
        db.engine, most, functools.partial(_documents_at_once, db), PgDocuments(db).still_waiting
    )


def test_a_document_waiting_under_the_cap_is_never_taken_for_stuck(db: Database) -> None:
    """Cap 1: A's batch holds the only place while B's queued documents pass the sweep's
    6000 s. Their hand-backs write on them, so the sweep neither queues them again (a try
    spent, a duplicate job) nor fails them ``stuck_no_job`` while their jobs are queued."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    old = datetime.now(UTC) - timedelta(seconds=7000)
    with db.customer(b) as conn:
        fresh_try = insert_document(conn, b, status="received", updated_at=old)
        last_try = insert_document(conn, b, status="clean", attempts=5, max_attempts=5, updated_at=old)
    reader, waiter = _cap(db), _cap(db)
    sent: list[tuple[Job, int]] = []
    with saved(db, documents=1), reader.slot(Job(a, str(uuid.uuid4()))):
        for document in (fresh_try, last_try):
            with pytest.raises(Deferred) as waited, waiter.slot(Job(b, document)):
                pass
            assert waited.value.code == "platform_busy"
        done = revive_stuck(
            db,
            lambda job, tries: sent.append((job, tries)),
            lambda c: c != b,
            stale_seconds=6000,
            max_attempts=5,
        )
    assert (done, sent) == ([], [])
    with db.customer(b) as conn:
        rows = conn.execute(
            text("SELECT status, attempts FROM documents WHERE customer_id = :c ORDER BY attempts"), {"c": b}
        ).all()
    assert [tuple(row) for row in rows] == [("received", 0), ("clean", 5)]


def test_the_documents_at_once_cap_holds_across_two_workers(db: Database) -> None:
    customer, _ = new_customer(db)
    first, second = _cap(db), _cap(db)
    with saved(db, documents=1):
        with first.slot(Job(customer, str(uuid.uuid4()))):
            with pytest.raises(Deferred) as waited, second.slot(Job(customer, str(uuid.uuid4()))):
                pass
            assert (waited.value.seconds, waited.value.code) == (15, "platform_busy")
        with second.slot(Job(customer, str(uuid.uuid4()))):  # let go when the job ended
            pass


def test_a_lowered_cap_counts_the_slots_still_held(db: Database) -> None:
    customer, _ = new_customer(db)
    one, two, three = _cap(db), _cap(db), _cap(db)
    with saved(db, documents=2), contextlib.ExitStack() as jobs:
        first = jobs.enter_context(contextlib.ExitStack())
        first.enter_context(one.slot(Job(customer, str(uuid.uuid4()))))  # slot 0
        jobs.enter_context(two.slot(Job(customer, str(uuid.uuid4()))))  # slot 1
        _owner(db, _SET, {"r": None, "d": 1, "p": None})
        first.close()  # slot 0 is free again, slot 1 is still held
        with pytest.raises(Deferred), three.slot(Job(customer, str(uuid.uuid4()))):
            pass


def test_the_cap_never_goes_above_worker_replicas(db: Database) -> None:
    customer, _ = new_customer(db)
    copies = [_cap(db, most=2) for _ in range(3)]
    with saved(db, documents=5), contextlib.ExitStack() as jobs:
        jobs.enter_context(copies[0].slot(Job(customer, str(uuid.uuid4()))))
        jobs.enter_context(copies[1].slot(Job(customer, str(uuid.uuid4()))))
        with pytest.raises(Deferred), copies[2].slot(Job(customer, str(uuid.uuid4()))):
            pass


def test_a_dead_workers_slot_is_let_go(db: Database) -> None:
    customer, _ = new_customer(db)
    first, second = _cap(db), _cap(db)
    with saved(db, documents=1), first.slot(Job(customer, str(uuid.uuid4()))):
        with db.engine.connect() as admin:  # the worker copy holding it crashes
            killed = admin.execute(
                text(
                    "SELECT pg_terminate_backend(l.pid) FROM pg_locks l WHERE l.locktype = 'advisory'"
                    " AND ((l.classid::bigint << 32) | l.objid::bigint) = hashtextextended(:key, 0)"
                ),
                {"key": "kaagaz.reading:0"},
            ).all()
        assert len(killed) == 1
        with second.slot(Job(customer, str(uuid.uuid4()))):  # free again without waiting for the job
            pass


def _held(db: Database, *keys: str) -> int:
    """How many of these advisory locks any session holds now."""
    found = _owner(
        db,
        "SELECT count(*) FROM pg_locks l WHERE l.locktype = 'advisory' AND l.granted"
        " AND ((l.classid::bigint << 32) | l.objid::bigint)"
        " IN (SELECT hashtextextended(k, 0) FROM unnest(CAST(:k AS text[])) k)",
        {"k": list(keys)},
    )
    return int(found[0][0])


def test_a_gate_held_elsewhere_hands_the_job_back_at_once(db: Database) -> None:
    customer, _ = new_customer(db)
    with saved(db), db.engine.connect() as stalled, ThreadPoolExecutor(max_workers=1) as pool:
        stalled.execute(text("SELECT pg_advisory_lock(hashtextextended('kaagaz.reading:gate', 0))"))
        try:

            def admit() -> str:
                with _cap(db).slot(Job(customer, str(uuid.uuid4()))):
                    return "read"

            with pytest.raises(Deferred) as waited:
                pool.submit(admit).result(timeout=5)  # never blocks on a stalled gate holder
            assert waited.value.code == "platform_busy"
        finally:
            stalled.execute(text("SELECT pg_advisory_unlock_all()"))
            stalled.commit()


def _race(cap: ReadingCap, job: Job, start: threading.Barrier, done: threading.Barrier) -> bool:
    """One worker's admission, started with the other's; True when it got the place."""
    start.wait()
    try:
        with cap.slot(job):
            done.wait()  # both have tried before the winner lets go
            return True
    except Deferred:
        done.wait()
        return False


def test_two_workers_racing_for_the_last_place_exactly_one_wins(db: Database) -> None:
    customer, _ = new_customer(db)
    with saved(db, documents=1), ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(30):
            start, done = threading.Barrier(2), threading.Barrier(2)
            racers = [
                pool.submit(_race, _cap(db), Job(customer, str(uuid.uuid4())), start, done) for _ in range(2)
            ]
            assert sorted(racer.result(timeout=10) for racer in racers) == [False, True]


def test_an_error_while_counting_drops_the_connection_and_every_lock_on_it(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer, _ = new_customer(db)
    failing = text(
        "SELECT CASE WHEN :key = 'kaagaz.reading:1' THEN 1 / 0 = 1"
        " ELSE pg_try_advisory_lock(hashtextextended(:key, 0)) END"
    )
    monkeypatch.setattr(fair_share, "_TRY_LOCK", failing)
    with saved(db), pytest.raises(DBAPIError), _cap(db).slot(Job(customer, str(uuid.uuid4()))):
        pass
    # The gate and slot 0 were taken before the error: neither is left on a pooled connection.
    assert _held(db, "kaagaz.reading:gate", "kaagaz.reading:0") == 0


def _operator(api: Api) -> tuple[_Operator, TestClient]:
    operator, client = _Operator(api), api.client()
    assert operator.login(client).status_code == 200
    return operator, client


def _put(api: Api, operator: _Operator, client: TestClient, **values: int | None) -> Any:
    api.clock.advance(totp.STEP_SECONDS)  # a fresh code each time
    body = {"model_rpm": None, "documents_at_once": None, "pages_at_once": None, **values}
    return client.put(SPEED, json={**body, "code": api.code(operator.secret)}, headers=CSRF)


def test_the_operator_sets_them_with_a_fresh_code_within_the_servers_caps_audited(db: Database) -> None:
    api = Api(db, settings=make_settings(model_rpm=600, worker_replicas=3, ocr_page_concurrency=8))
    operator, client = _operator(api)
    with saved(db):
        assert client.get(SPEED).json() == {
            "model_rpm": {"value": None, "most": 600},
            "documents_at_once": {"value": None, "most": 3},
            "pages_at_once": {"value": None, "most": 8},
        }
        wrong = client.put(
            SPEED,
            json={"model_rpm": 100, "documents_at_once": None, "pages_at_once": None, "code": "000000"},
            headers=CSRF,
        )
        assert (wrong.status_code, wrong.json()["error"]["code"]) == (403, "step_up_failed")
        for over in (
            {"model_rpm": 601},
            {"documents_at_once": 4},
            {"pages_at_once": 9},
            {"pages_at_once": 0},
        ):
            assert _put(api, operator, client, **over).status_code in (400, 422)
        assert client.get(SPEED).json()["model_rpm"]["value"] is None  # nothing changed
        done = _put(api, operator, client, model_rpm=100, documents_at_once=1, pages_at_once=2)
        assert done.status_code == 200
        assert [value["value"] for value in done.json().values()] == [100, 1, 2]
        reset = _put(api, operator, client, documents_at_once=1)  # NULL: the server's setting
        assert [value["value"] for value in reset.json().values()] == [None, 1, None]
    audited = operator.sql(
        "SELECT action, target_type, details FROM platform_audit"
        " WHERE operator_id = :o AND action = 'speed.set' ORDER BY id"
    )
    assert [(row[0], row[1]) for row in audited] == [("speed.set", "platform")] * 2
    assert audited[0][2] == {
        "old_model_rpm": None, "new_model_rpm": 100,
        "old_documents_at_once": None, "new_documents_at_once": 1,
        "old_pages_at_once": None, "new_pages_at_once": 2,
    }  # fmt: skip
    assert audited[1][2]["old_model_rpm"] == 100 and audited[1][2]["new_model_rpm"] is None


def test_someone_who_is_not_the_operator_is_refused(db: Database) -> None:
    api = Api(db)
    admin = api.admin_sign_in(api.customer()[0])  # an organisation's admin, signed in
    for reply in (
        admin.client.get(SPEED),
        admin.client.put(
            SPEED,
            json={"model_rpm": 1, "documents_at_once": None, "pages_at_once": None, "code": "000000"},
            headers=CSRF,
        ),
    ):
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")
