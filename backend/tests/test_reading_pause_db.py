"""The reading pause on a real database (#478, Tushar, 3 Nov 2026; migration 0066): two workers
can't pass the daily page limit, a held document is queued again the next India morning or once
the limit or the budget is raised, a budget reached pauses reading, the operator's accuracy
checks never count, pages are counted as they are read, the super admin is emailed once a day,
and a held document is never the stuck-document sweep's."""

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from kaagaz.ai import usage
from kaagaz.db import reading_days
from kaagaz.db.documents import stuck_documents
from kaagaz.db.engine import Database
from kaagaz.db.reading_days import IST, State
from kaagaz.db.usage import UsageRow, insert_usage
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.reading_pause import BUDGET, DAILY_LIMIT, ReadingPause, ReadingPaused, release_held
from tests.db_support import insert_document, new_customer
from tests.test_migration_0023_db import as_owner

pytestmark = pytest.mark.db

NIGHT = datetime(2026, 11, 3, 23, 30, tzinfo=IST)  # late on 3 Nov in India (18:00 UTC)


def set_limit(db: Database, customer: str, pages: int | None) -> None:
    as_owner(
        db,
        "INSERT INTO customer_ai_settings (customer_id, daily_page_limit) VALUES (:c, :n)"
        " ON CONFLICT (customer_id) DO UPDATE SET daily_page_limit = EXCLUDED.daily_page_limit",
        {"c": customer, "n": pages},
    )


def set_budget(db: Database, customer: str, usd: str | None) -> None:
    as_owner(
        db,
        "UPDATE customers SET ai_budget_usd = CAST(:b AS numeric) WHERE id = :c",
        {"c": customer, "b": usd},
    )


def document(db: Database, customer: str) -> str:
    with db.customer(customer) as conn:
        return insert_document(conn, customer, status="received")


def counter(db: Database, customer: str, day: datetime) -> tuple[int, Decimal] | None:
    rows = as_owner(
        db,
        "SELECT pages, cost_usd FROM reading_days WHERE customer_id = :c AND day = :d",
        {"c": customer, "d": reading_days.india_day(day)},
    )
    return None if not rows else rows[0]


def held(db: Database, customer: str, document_id: str) -> str | None:
    [(reason,)] = as_owner(db, "SELECT reading_held FROM documents WHERE id = :d", {"d": document_id})
    assert as_owner(db, "SELECT customer_id::text FROM documents WHERE id = :d", {"d": document_id}) == [
        (customer,)
    ]
    return None if reason is None else str(reason)


def gate(db: Database, now: datetime, alerts: list[tuple[str, State]] | None = None) -> ReadingPause:
    return ReadingPause(
        db, clock=lambda: now, alert=None if alerts is None else lambda r, s: alerts.append((r, s))
    )


def mine(sent: list[Job], customer: str) -> list[str]:
    """The jobs a release sent for this customer (the shared database holds others' too)."""
    return sorted(job.document_id for job in sent if job.customer_id == customer)


def test_two_workers_at_once_never_both_pass_the_limit(db: Database) -> None:
    """The claim is one statement on the day's row: of two documents started at the same moment
    with one page left, exactly one is read and the other is held (five rounds)."""
    for _ in range(5):
        _race(db)


def _race(db: Database) -> None:
    customer, _ = new_customer(db)
    set_limit(db, customer, 1)
    documents = [document(db, customer), document(db, customer)]
    pause = gate(db, NIGHT)
    start, done = threading.Barrier(2), threading.Event()
    outcomes: dict[str, str] = {}

    def read(document_id: str) -> None:
        start.wait()
        try:
            with pause.admit(customer, document_id):
                outcomes[document_id] = "read"
                done.wait(10)  # still being read while the other one asks
        except ReadingPaused as paused:
            outcomes[document_id] = paused.reason
            done.set()

    threads = [threading.Thread(target=read, args=(each,)) for each in documents]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert sorted(outcomes.values()) == [DAILY_LIMIT, "read"]
    waiting = next(each for each, outcome in outcomes.items() if outcome == DAILY_LIMIT)
    assert held(db, customer, waiting) == DAILY_LIMIT
    assert counter(db, customer, NIGHT) == (0, Decimal(0))  # the read one gave its claimed page back


def test_a_document_let_in_is_read_to_its_end_and_counts_what_it_read(db: Database) -> None:
    now = datetime.now(UTC)  # the live counter counts on the real India day
    customer, _ = new_customer(db)
    set_limit(db, customer, 2)
    with gate(db, now).admit(customer, document(db, customer)):
        assert counter(db, customer, now) == (1, Decimal(0))  # its claimed page, while it is read
        with usage.usage_scope(db, customer_id=customer, user_id=None, kind="scan"):
            for _ in range(3):  # past the limit: a document let in finishes
                usage.record_call("google/m", None, 10, 5, 0, 0.01, thinking="low", resolution="high")
                usage.record_page("google/m", thinking="low", resolution="high")
    assert counter(db, customer, now) == (3, Decimal("0.03"))  # the claimed page given back
    with pytest.raises(ReadingPaused) as paused, gate(db, now).admit(customer, document(db, customer)):
        pass
    assert paused.value.code == "paused_daily_limit"


def test_held_documents_are_queued_the_next_india_morning(db: Database) -> None:
    customer, _ = new_customer(db)
    set_limit(db, customer, 1)
    with db.customer(customer) as conn:
        reading_days.count(conn, customer, reading_days.india_day(NIGHT), pages=1, cost_usd=0.0)
    waiting = document(db, customer)
    with pytest.raises(ReadingPaused), gate(db, NIGHT).admit(customer, waiting):
        pass
    sent: list[Job] = []
    assert mine(release_held(db, sent.append, now=NIGHT + timedelta(minutes=20)), customer) == []
    assert held(db, customer, waiting) == DAILY_LIMIT  # 23:50 in India: still today
    morning = NIGHT + timedelta(minutes=35)  # 00:05 on 4 Nov in India, still 3 Nov in UTC
    assert morning.astimezone(UTC).day == 3
    assert mine(release_held(db, sent.append, now=morning), customer) == [waiting]
    assert mine(sent, customer) == [waiting]
    assert held(db, customer, waiting) is None
    with gate(db, morning).admit(customer, waiting):  # and the new day lets it in
        pass


def test_raising_the_limit_or_the_budget_releases_them(db: Database) -> None:
    customer, _ = new_customer(db)
    set_limit(db, customer, 1)
    with db.customer(customer) as conn:
        reading_days.count(conn, customer, reading_days.india_day(NIGHT), pages=1, cost_usd=0.0)
    waiting = document(db, customer)
    with pytest.raises(ReadingPaused), gate(db, NIGHT).admit(customer, waiting):
        pass
    sent: list[Job] = []
    assert mine(release_held(db, sent.append, now=NIGHT), customer) == []
    set_limit(db, customer, 5)
    assert mine(release_held(db, sent.append, now=NIGHT), customer) == [waiting]

    now = datetime.now(UTC)  # the ledger's month is the real one
    set_budget(db, customer, "1.00")
    with db.customer(customer) as conn:
        insert_usage(conn, [_row(customer, "scan", 1.5)])
    again = document(db, customer)
    with pytest.raises(ReadingPaused) as paused, gate(db, now).admit(customer, again):
        pass
    assert paused.value.reason == BUDGET and held(db, customer, again) == BUDGET
    assert mine(release_held(db, sent.append, now=now), customer) == []  # still over the budget
    set_budget(db, customer, "5.00")
    assert mine(release_held(db, sent.append, now=now), customer) == [again]
    assert mine(sent, customer) == sorted([waiting, again])


def test_a_budget_reached_pauses_reading_on_the_ledger_or_the_live_counter(db: Database) -> None:
    now = datetime.now(UTC)
    for source in ("ledger", "live"):
        customer, _ = new_customer(db)
        set_budget(db, customer, "2.00")
        with gate(db, now).admit(customer, document(db, customer)):
            pass  # under it: read
        with db.customer(customer) as conn:
            if source == "ledger":
                insert_usage(conn, [_row(customer, "chat", 2.0)])  # Ask's spend counts too
            else:
                reading_days.count(conn, customer, reading_days.india_day(now), pages=1, cost_usd=2.0)
        with pytest.raises(ReadingPaused) as paused, gate(db, now).admit(customer, document(db, customer)):
            pass
        assert paused.value.reason == BUDGET


def test_the_operators_accuracy_checks_never_count(db: Database) -> None:
    now = datetime.now(UTC)
    customer, _ = new_customer(db)
    set_budget(db, customer, "1.00")
    with usage.usage_scope(db, customer_id=customer, user_id=None, kind=usage.ACCURACY_CHECK):
        usage.record_call("google/m", None, 10, 5, 0, 9.0, thinking="low", resolution="high")
        usage.record_page("google/m", thinking="low", resolution="high")
    assert counter(db, customer, now) is None  # no page and no dollar on the counter
    with gate(db, now).admit(customer, document(db, customer)):
        pass  # the $9 ledger row is the operator's: still under the budget


def test_mistral_pages_count_live_and_other_work_through_the_ledger(db: Database) -> None:
    """record_page is every reader's (gemini_ocr and mistral_reader call it): one page and the
    dollars before it, at once; Ask's calls reach the budget through the ledger."""
    now = datetime.now(UTC)
    customer, _ = new_customer(db)
    with usage.usage_scope(db, customer_id=customer, user_id=None, kind="scan"):
        usage.record_call(
            "mistral/mistral-ocr-4-1", "mistral", 0, 0, 0, 0.004, thinking="none", resolution="page"
        )
        usage.record_page("mistral/mistral-ocr-4-1", thinking="none", resolution="page")
        assert counter(db, customer, now) == (1, Decimal("0.004"))  # at once, not at the end
    with usage.usage_scope(db, customer_id=customer, user_id=None, kind="chat"):
        usage.record_call("google/m", None, 10, 5, 0, 0.5)
    assert counter(db, customer, now) == (1, Decimal("0.004"))
    [(spent,)] = as_owner(db, "SELECT sum(cost_usd) FROM model_usage WHERE customer_id = :c", {"c": customer})
    assert spent == Decimal("0.504")


def test_the_super_admin_is_told_once_a_day(db: Database) -> None:
    customer, _ = new_customer(db, name="Sharma Traders")
    set_limit(db, customer, 1)
    with db.customer(customer) as conn:
        reading_days.count(conn, customer, reading_days.india_day(NIGHT), pages=1, cost_usd=0.0)
    alerts: list[tuple[str, State]] = []
    for at in (NIGHT, NIGHT + timedelta(minutes=10)):
        with pytest.raises(ReadingPaused), gate(db, at, alerts).admit(customer, document(db, customer)):
            pass
    assert [(reason, state.name, state.pages_today, state.page_limit) for reason, state in alerts] == [
        (DAILY_LIMIT, "Sharma Traders", 1, 1)
    ]


def test_a_held_document_is_never_the_stuck_sweeps(db: Database) -> None:
    customer, _ = new_customer(db)
    set_limit(db, customer, 1)
    with db.customer(customer) as conn:
        reading_days.count(conn, customer, reading_days.india_day(NIGHT), pages=1, cost_usd=0.0)
    waiting = document(db, customer)
    with pytest.raises(ReadingPaused), gate(db, NIGHT).admit(customer, waiting):
        pass
    # Every document in progress is stale at -1 hour: only the hold keeps it out.
    with db.customer(customer) as conn:
        assert stuck_documents(conn, customer, stale_seconds=-3600, limit=10) == []
        conn.execute(text("UPDATE documents SET reading_held = NULL WHERE id = :d"), {"d": waiting})
        assert [row[0] for row in stuck_documents(conn, customer, stale_seconds=-3600, limit=10)] == [waiting]


def _row(customer: str, kind: str, cost: float) -> UsageRow:
    return UsageRow(
        customer_id=customer,
        user_id=None,
        document_id=None,
        conversation_id=None,
        kind=kind,
        model="google/m",
        provider_name=None,
        cached=False,
        calls=1,
        input_tokens=1,
        output_tokens=1,
        reasoning_tokens=0,
        cost_usd=cost,
    )
