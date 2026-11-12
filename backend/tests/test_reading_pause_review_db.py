"""The reading pause on a real database, after the council's review (#478, 3 Nov 2026): the release
sweep goes one organisation at a time, oldest first, only into the room left, never from the
Trash, and ends a hold only once its job is queued; the super admin's email is noted only once
sent; the upgrade sweep leaves a paused organisation alone; a retry of a document let in today is
never held; a held document is never "stuck"; the Usage page's cheap query; the budget's month is
India's; and eight workers at once on an existing row let exactly three in."""

import logging
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from kaagaz.db import reading_days
from kaagaz.db.documents import NotReprocessable, mark_for_reprocess
from kaagaz.db.engine import Database
from kaagaz.db.reading_days import IST, State
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs import reading_pause
from kaagaz.jobs.reading_pause import DAILY_LIMIT, ReadingPause, ReadingPaused, release_held
from kaagaz.jobs.upgrade import upgrade_oldest
from tests.db_support import insert_document, new_customer
from tests.fakes import MemoryQueue
from tests.test_api_usage import add_spend
from tests.test_migration_0023_db import as_owner
from tests.test_reading_pause_db import NIGHT, counter, document, gate, held, mine, set_budget, set_limit
from tests.test_upgrade_sweep_db import read_locally, status_of, sweep_to

pytestmark = pytest.mark.db


def hold(db: Database, customer: str, *, uploaded: datetime | None = None, **columns: object) -> str:
    """A received document held by the daily limit (uploaded at ``uploaded``)."""
    with db.customer(customer) as conn:
        extra = {} if uploaded is None else {"created_at": uploaded}
        made = insert_document(conn, customer, status="received", **extra, **columns)
        reading_days.hold(conn, customer, made, DAILY_LIMIT)
    return made


def count_today(db: Database, customer: str, at: datetime, pages: int) -> None:
    with db.customer(customer) as conn:
        reading_days.count(conn, customer, reading_days.india_day(at), pages=pages, cost_usd=0.0)


# --- the release sweep ----------------------------------------------------------------------


def test_one_organisations_error_never_stops_the_others(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    broken, _ = new_customer(db)
    fine, _ = new_customer(db)
    stays, goes = hold(db, broken), hold(db, fine)

    def send(job: Job) -> None:
        if job.customer_id == broken:
            raise BackendUnavailable("queue")

    with caplog.at_level(logging.ERROR, logger=reading_pause.__name__):
        sent = release_held(db, send, now=NIGHT)
    assert mine(sent, fine) == [goes] and held(db, fine, goes) is None
    assert mine(sent, broken) == [] and held(db, broken, stays) == DAILY_LIMIT
    [record] = [each for each in caplog.records if each.__dict__.get("customer_id") == broken]
    assert record.__dict__["code"] == "BackendUnavailable"


def test_a_queue_that_is_down_keeps_every_hold_it_did_not_queue(db: Database) -> None:
    customer, _ = new_customer(db)
    first = hold(db, customer, uploaded=datetime(2001, 1, 1, tzinfo=UTC))
    second = hold(db, customer, uploaded=datetime(2001, 1, 2, tzinfo=UTC))
    queue = MemoryQueue()
    queue.fail_enqueue = True
    assert mine(release_held(db, queue.enqueue, now=NIGHT), customer) == []
    assert (held(db, customer, first), held(db, customer, second)) == (DAILY_LIMIT, DAILY_LIMIT)

    sends: list[Job] = []

    def once(job: Job) -> None:  # the queue goes down after the first of ours
        if job.customer_id == customer and any(each.customer_id == customer for each in sends):
            raise BackendUnavailable("queue")
        sends.append(job)

    assert mine(release_held(db, once, now=NIGHT), customer) == [first]
    assert held(db, customer, first) is None  # queued: its hold ended
    assert held(db, customer, second) == DAILY_LIMIT  # never queued: still held, sent next pass
    assert mine(release_held(db, sends.append, now=NIGHT), customer) == [second]


def test_a_document_in_the_trash_is_never_released(db: Database) -> None:
    customer, user = new_customer(db)
    binned = hold(db, customer, deleted_at=datetime(2026, 11, 1, tzinfo=UTC), deleted_by=user)
    sent: list[Job] = []
    assert mine(release_held(db, sent.append, now=NIGHT), customer) == []
    assert held(db, customer, binned) == DAILY_LIMIT  # out of the Trash, it is read then


def test_only_the_room_left_is_released_oldest_first(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    customer, _ = new_customer(db)
    set_limit(db, customer, 3)
    count_today(db, customer, NIGHT, 1)  # room for 2 more documents
    days = [hold(db, customer, uploaded=datetime(2001, 1, day, tzinfo=UTC)) for day in (4, 1, 3, 2)]
    oldest = [days[1], days[3], days[2], days[0]]
    sent: list[Job] = []
    assert mine(release_held(db, sent.append, now=NIGHT), customer) == sorted(oldest[:2])
    assert [held(db, customer, each) for each in oldest] == [None, None, DAILY_LIMIT, DAILY_LIMIT]

    other, _ = new_customer(db)  # no limit: the pass's own cap
    mine_held = [hold(db, other, uploaded=datetime(2001, 1, day, tzinfo=UTC)) for day in (1, 2, 3)]
    monkeypatch.setattr(reading_pause, "RELEASE_PER_PASS", 2)
    assert mine(release_held(db, sent.append, now=NIGHT), other) == sorted(mine_held[:2])
    assert held(db, other, mine_held[2]) == DAILY_LIMIT


def test_a_hold_on_a_document_no_longer_waiting_ends_and_is_not_queued(db: Database) -> None:
    customer, _ = new_customer(db)
    read = hold(db, customer)
    as_owner(db, "UPDATE documents SET status = 'ready' WHERE id = :d", {"d": read})
    sent: list[Job] = []
    assert mine(release_held(db, sent.append, now=NIGHT), customer) == []
    assert held(db, customer, read) is None


# --- the super admin's email ------------------------------------------------------------------


def test_the_day_is_noted_as_told_only_once_the_email_is_sent(db: Database) -> None:
    customer, _ = new_customer(db)
    set_limit(db, customer, 1)
    count_today(db, customer, NIGHT, 1)
    tries: list[str] = []
    answers = iter([False, True])

    def alert(reason: str, org: State) -> bool:
        tries.append(reason)
        return next(answers, True)

    pause = ReadingPause(db, clock=lambda: NIGHT, alert=alert)
    for _ in range(3):
        with pytest.raises(ReadingPaused), pause.admit(customer, document(db, customer)):
            pass
    assert tries == [DAILY_LIMIT, DAILY_LIMIT]  # failed, tried again, then told: never a third


# --- the upgrade sweep --------------------------------------------------------------------------


def test_the_upgrade_sweep_leaves_a_paused_organisation_alone(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        oldest = read_locally(conn, customer, year=1900)  # older than any other test's
    set_limit(db, customer, 1)
    count_today(db, customer, datetime.now(UTC), 1)  # the sweep counts on the real India day
    queue = MemoryQueue()
    for _ in range(1000):
        taken = upgrade_oldest(db, queue)
        if taken is None:
            break
        assert taken.customer_id != customer
    assert status_of(db, customer, oldest) == "ready"  # never turned into a held document
    set_limit(db, customer, None)
    sweep_to(db, queue, Job(customer, oldest), {customer})


# --- a retry of a document let in -------------------------------------------------------------


def test_a_retry_of_a_document_let_in_today_is_never_held(db: Database) -> None:
    customer, _ = new_customer(db)
    set_limit(db, customer, 1)
    started = document(db, customer)
    pause = gate(db, NIGHT)
    with pytest.raises(BackendUnavailable), pause.admit(customer, started):
        count_today(db, customer, NIGHT, 1)  # its first page is read: the limit is reached
        raise BackendUnavailable("reader")  # then the reader drops out mid-document
    assert counter(db, customer, NIGHT) == (1, Decimal(0))
    with pause.admit(customer, started):  # the retry: read to its end, never held half read
        pass
    assert held(db, customer, started) is None and counter(db, customer, NIGHT) == (1, Decimal(0))
    with pytest.raises(ReadingPaused), pause.admit(customer, started):
        pass  # read to its end, it is a new reading again ("read again"): the limit holds
    other, tomorrow, later = document(db, customer), NIGHT + timedelta(days=1), NIGHT + timedelta(days=2)
    with pytest.raises(BackendUnavailable), gate(db, tomorrow).admit(customer, other):
        raise BackendUnavailable("reader")
    count_today(db, customer, later, 1)
    with pytest.raises(ReadingPaused), gate(db, later).admit(customer, other):
        pass  # let in on another India day: that day's limit asks again


# --- Retry is never offered on a held document --------------------------------------------------


def test_a_held_document_is_never_stuck(db: Database) -> None:
    customer, _ = new_customer(db)
    long_ago = datetime.now(UTC) - timedelta(hours=7)
    waiting = hold(db, customer, uploaded=long_ago)
    with pytest.raises(NotReprocessable), db.customer(customer) as conn:
        mark_for_reprocess(conn, customer, waiting, per_hour=100)
    as_owner(db, "UPDATE documents SET reading_held = NULL WHERE id = :d", {"d": waiting})
    with db.customer(customer) as conn:  # the same document, not held: stuck, so Retry works
        assert mark_for_reprocess(conn, customer, waiting, per_hour=100) == "received"


# --- the Usage page's numbers --------------------------------------------------------------------


def test_the_usage_pages_query_gives_the_same_pages_and_limit(db: Database) -> None:
    customer, _ = new_customer(db)
    day = reading_days.india_day(NIGHT)
    for limit, pages, budget in ((None, 0, None), (2000, 1240, "50.00"), (5, 0, None)):
        set_limit(db, customer, limit)
        set_budget(db, customer, budget)
        if pages:
            count_today(db, customer, NIGHT, pages)
        with db.customer(customer) as conn:
            full = reading_days.state(conn, customer, day)
            assert reading_days.today(conn, customer, day) == (full.pages_today, full.page_limit)


# --- the budget's month and the race ------------------------------------------------------------


def test_a_ledger_row_at_00_10_ist_on_1_nov_counts_for_november(db: Database) -> None:
    customer, _ = new_customer(db)
    set_budget(db, customer, "100.00")
    first_of_november = datetime(2026, 11, 1, 0, 10, tzinfo=IST)  # 31 Oct 18:40 in UTC
    assert first_of_november.astimezone(UTC).month == 10
    add_spend(db, customer, at=first_of_november, cost=2.0)
    add_spend(db, customer, at=first_of_november - timedelta(minutes=20), cost=7.0)  # 31 Oct 23:50 IST
    with db.customer(customer) as conn:
        reading_days.count(conn, customer, date(2026, 10, 31), pages=1, cost_usd=40.0)
        november = reading_days.state(conn, customer, first_of_november.date())
        october = reading_days.state(conn, customer, date(2026, 10, 31))
    assert november.spent == Decimal("2.000000")  # neither 31 Oct's ledger row nor its counter
    assert october.spent == Decimal("40.000000")  # the larger: 31 Oct's counter over its $7 row


def test_eight_workers_on_an_existing_row_limit_three(db: Database) -> None:
    customer, _ = new_customer(db)
    set_limit(db, customer, 3)
    count_today(db, customer, NIGHT, 0)  # the day's row is there already, at 0 pages
    documents = [document(db, customer) for _ in range(8)]
    pause = gate(db, NIGHT)
    start, decided, finish = threading.Barrier(8), threading.Barrier(8, timeout=20), threading.Event()
    outcomes: dict[str, str] = {}
    pages_while_read: list[tuple[int, Decimal] | None] = []

    def read(document_id: str) -> None:
        start.wait()
        try:
            with pause.admit(customer, document_id):
                outcomes[document_id] = "read"
                decided.wait()
                finish.wait(20)  # still being read while every other one asks
        except ReadingPaused as paused:
            outcomes[document_id] = paused.reason
            decided.wait()

    threads = [threading.Thread(target=read, args=(each,)) for each in documents]
    for thread in threads:
        thread.start()
    while len(outcomes) < 8 and any(thread.is_alive() for thread in threads):
        threading.Event().wait(0.05)
    pages_while_read.append(counter(db, customer, NIGHT))
    finish.set()
    for thread in threads:
        thread.join(30)
    assert sorted(outcomes.values()) == [DAILY_LIMIT] * 5 + ["read"] * 3
    assert pages_while_read == [(3, Decimal(0))]  # the three claimed pages, never a fourth
    assert sorted(held(db, customer, each) or "read" for each in documents) == sorted(outcomes.values())
    assert counter(db, customer, NIGHT) == (0, Decimal(0))  # each gave its claimed page back
    rows = as_owner(db, "SELECT count(*) FROM reading_days WHERE customer_id = :c", {"c": customer})
    assert rows == [(1,)]
