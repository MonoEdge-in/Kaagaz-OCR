"""Shared OpenRouter admission across independent app clients and rate-limit fairness."""

import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database, make_engine
from tests.db_support import new_customer

pytestmark = pytest.mark.db
_PROCESS_DB: Database | None = None


def _admit(
    db: Database, customer: str | None = None, user: str | None = None, platform_rpm: int = 1200
) -> None:
    deadline = time.monotonic() + 10
    request_id = str(uuid.uuid4())
    while True:
        wait = db.model_rate_slot(request_id, customer, user, deadline - time.monotonic(), platform_rpm)
        if wait < 0:
            raise AssertionError("rate admission exceeded its deadline")
        if wait == 0:
            return
        time.sleep(wait)


def _clock(db: Database) -> datetime:
    with db.engine.begin() as conn:
        found: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    return found


def _next_at(db: Database, sql: str, **params: str) -> datetime:
    with db.engine.begin() as conn:
        found: datetime = conn.execute(text(sql), params).scalar_one()
    return found


def _spaced(next_at: datetime, before: datetime, after: datetime, seconds: float) -> bool:
    """``next_at`` is ``seconds`` after a reservation made between ``before`` and ``after``
    (the database's clock, as the slot function's). Fixed (#239, 27 Oct 2026): reading
    next_at - clock_timestamp() afterwards counted the test's own delay, so a slow machine
    failed it."""
    gap = timedelta(seconds=seconds)
    return before + gap <= next_at <= after + gap


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


def test_an_organisation_cap_does_not_block_other_organisations(db: Database) -> None:
    capped_org, capped_user = new_customer(db, name="Capped RPM organisation")
    open_org, open_user = new_customer(db, name="Open RPM organisation")
    with db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO model_rate_overrides (customer_id, rpm) VALUES (:c, 1)"),
            {"c": capped_org},
        )

    _admit(db, capped_org, capped_user)
    started = time.monotonic()
    _admit(db, open_org, open_user)
    assert time.monotonic() - started < 1


def test_a_user_cap_does_not_block_another_user_in_the_same_organisation(db: Database) -> None:
    customer, capped_user = new_customer(db, name="Capped RPM user")
    with db.customer(customer) as conn:
        open_user = conn.execute(
            text(
                "INSERT INTO users (customer_id, email, display_name, role, password_hash) "
                "VALUES (:c, :email, 'Open', 'member', 'x') RETURNING id"
            ),
            {"c": customer, "email": f"rpm-open-{uuid.uuid4().hex}@example.test"},
        ).scalar_one()
    with db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO user_model_rate_overrides (customer_id, user_id, rpm) VALUES (:c, :u, 1)"),
            {"c": customer, "u": capped_user},
        )

    _admit(db, customer, capped_user)
    started = time.monotonic()
    _admit(db, customer, str(open_user))
    assert time.monotonic() - started < 1


def test_platform_rpm_controls_shared_spacing(db: Database) -> None:
    before = _clock(db)
    _admit(db, platform_rpm=60)
    after = _clock(db)
    assert _spaced(_next_at(db, "SELECT next_at FROM model_rate_scheduler WHERE id"), before, after, 1.0)


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
    before = _clock(db)
    _admit(db, customer, user)
    after = _clock(db)
    org = "SELECT next_at FROM model_rate_state WHERE customer_id = :c AND scope = 'organisation'"
    assert _spaced(_next_at(db, org, c=customer), before, after, 0.6)  # 100 a minute


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

    before = _clock(db)
    _admit(db, customer, user)
    after = _clock(db)
    org = "SELECT next_at FROM model_rate_state WHERE customer_id = :c AND scope = 'organisation'"
    mine = "SELECT next_at FROM model_rate_state WHERE customer_id = :c AND user_id = :u AND scope = 'user'"
    assert _spaced(_next_at(db, org, c=customer), before, after, 0.1)  # 600 a minute
    assert _spaced(_next_at(db, mine, c=customer, u=user), before, after, 0.5)  # 120 a minute
