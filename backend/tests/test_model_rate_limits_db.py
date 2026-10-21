"""Shared OpenRouter admission across independent app clients and rate-limit fairness."""

import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database, make_engine
from tests.db_support import new_customer

pytestmark = pytest.mark.db
_PROCESS_DB: Database | None = None


def _admit(db: Database, customer: str | None = None, user: str | None = None) -> None:
    deadline = time.monotonic() + 10
    request_id = str(uuid.uuid4())
    while True:
        wait = db.model_rate_slot(request_id, customer, user, deadline - time.monotonic())
        if wait < 0:
            raise AssertionError("rate admission exceeded its deadline")
        if wait == 0:
            return
        time.sleep(wait)


def _init_process_database(url: str) -> None:
    global _PROCESS_DB
    _PROCESS_DB = Database(make_engine(url), "kaagaz_app")


def _admit_in_process(_: int) -> None:
    if _PROCESS_DB is None:
        raise AssertionError("process database was not initialized")
    _admit(_PROCESS_DB)


def test_global_1200_rpm_scheduler_is_shared_across_independent_clients(db: Database) -> None:
    url = db.engine.url.render_as_string(hide_password=False)
    before = time.monotonic()
    with ProcessPoolExecutor(max_workers=4, initializer=_init_process_database, initargs=(url,)) as pool:
        list(pool.map(_admit_in_process, range(40)))
    elapsed = time.monotonic() - before
    assert elapsed >= 1.85  # 40 global admissions need 39 * 50 ms, even across clients.


def test_a_busy_organisation_does_not_block_another_from_progress(db: Database) -> None:
    busy_org, _ = new_customer(db, name="Busy RPM organisation")
    waiting_org, _ = new_customer(db, name="Fair RPM organisation")
    started = time.monotonic()
    stop = threading.Event()
    busy_admissions: list[None] = []

    def keep_busy() -> None:
        while not stop.is_set():
            _admit(db, busy_org)
            busy_admissions.append(None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        busy = pool.submit(keep_busy)
        time.sleep(0.06)
        fair = pool.submit(_admit, db, waiting_org)
        fair.result(timeout=1)
        stop.set()
        busy.result(timeout=4)

    assert time.monotonic() - started < 1
    assert busy_admissions


def test_dynamic_rate_change_is_used_by_the_next_reservation(db: Database) -> None:
    customer, user = new_customer(db, name="Dynamic RPM organisation")
    # Existing organisations with no override use the backwards-compatible platform fair share.
    with db.engine.begin() as conn:
        rpm = conn.execute(
            text("SELECT (SELECT rpm FROM model_rate_overrides WHERE customer_id = :c)"),
            {"c": customer},
        ).scalar_one()
    assert rpm is None

    _admit(db, customer, user)
    with db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO model_rate_overrides (customer_id, rpm) VALUES (:c, 100)"), {"c": customer}
        )
    _admit(db, customer, user)
    with db.engine.begin() as conn:
        interval = conn.execute(
            text(
                "SELECT extract(epoch FROM next_at - clock_timestamp()) FROM model_rate_state "
                "WHERE customer_id = :c AND scope = 'organisation'"
            ),
            {"c": customer},
        ).scalar_one()
    assert 0.55 <= interval <= 0.65


def test_user_override_is_limited_by_organisation_override(db: Database) -> None:
    customer, user = new_customer(db, name="Nested RPM limits")
    with db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO model_rate_overrides (customer_id, rpm) VALUES (:c, 600)"),
            {"c": customer},
        )
        conn.execute(
            text("INSERT INTO user_model_rate_overrides (customer_id, user_id, rpm) VALUES (:c, :u, 120)"),
            {"c": customer, "u": user},
        )

    _admit(db, customer, user)
    with db.engine.begin() as conn:
        org_next = conn.execute(
            text(
                "SELECT extract(epoch FROM next_at - clock_timestamp()) FROM model_rate_state "
                "WHERE customer_id = :c AND scope = 'organisation'"
            ),
            {"c": customer},
        ).scalar_one()
        user_next = conn.execute(
            text(
                "SELECT extract(epoch FROM next_at - clock_timestamp()) FROM model_rate_state "
                "WHERE customer_id = :c AND user_id = :u AND scope = 'user'"
            ),
            {"c": customer, "u": user},
        ).scalar_one()
    assert 0.08 <= org_next <= 0.12
    assert 0.48 <= user_next <= 0.52
