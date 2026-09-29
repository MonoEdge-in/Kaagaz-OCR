"""The trash purge against a real PostgreSQL (PRD 1 F5, kaagaz/jobs/purge.py).

Files live in the in-memory storage (tests/fakes.py). Time comes from a fixed
clock, never the database clock, so nothing waits.

The purge covers every customer in the database, and the test database is
shared, so these tests use times long before any other test's Trash (the
earliest there is 2000): the purge can only find rows these tests made. Each
test first purges anything an earlier run of these tests left behind.
"""

import io
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobNotFound
from kaagaz.jobs.purge import purge_expired
from tests.db_support import insert_document, new_customer
from tests.fakes import MemoryStorage

pytestmark = pytest.mark.db

NOW = datetime(1990, 1, 1, 12, 0, tzinfo=UTC)
TRASH_DAYS = 30
LIMIT = NOW - timedelta(days=TRASH_DAYS)  # in Trash since before this: purged


def clock() -> datetime:
    return NOW


def drain(db: Database) -> None:
    """Purge what an earlier run of these tests left, so each test counts only its own."""
    purge_expired(db, MemoryStorage(), clock, TRASH_DAYS, batch=1_000_000)


def add_family(
    db: Database, storage: MemoryStorage, customer: str, versions: int = 1, **columns: object
) -> list[str]:
    """A family of ``versions`` documents, each with its file stored. Version 1 first."""
    with db.customer(customer) as conn:
        first = insert_document(conn, customer, **columns)
        ids = [first] + [
            insert_document(conn, customer, family_id=first, version_no=number, is_current=False, **columns)
            for number in range(2, versions + 1)
        ]
    for document in ids:
        storage.put(customer, document, io.BytesIO(b"file"))
    return ids


def trashed(
    db: Database, storage: MemoryStorage, customer: str, at: datetime, versions: int = 1
) -> list[str]:
    return add_family(db, storage, customer, versions, deleted_at=at)


def rows(db: Database, customer: str) -> set[str]:
    with db.customer(customer) as conn:
        found = conn.execute(text("SELECT id FROM documents WHERE customer_id = :c"), {"c": customer})
        return {str(row[0]) for row in found}


def stored(storage: MemoryStorage, customer: str, ids: list[str]) -> set[str]:
    kept = set()
    for document in ids:
        try:
            storage.get(customer, document)
        except BlobNotFound:
            continue
        kept.add(document)
    return kept


class FailingStorage(MemoryStorage):
    """Storage that cannot delete the files of the documents in ``failing``."""

    def __init__(self) -> None:
        super().__init__()
        self.failing: set[str] = set()

    def delete(self, customer_id: str, document_id: str) -> None:
        if document_id in self.failing:
            raise BackendUnavailable("storage")
        super().delete(customer_id, document_id)


class HeldStorage(MemoryStorage):
    """Deleting ``held``'s files waits for ``release``, after setting ``entered``.
    Every delete is recorded, so a family taken twice would show."""

    def __init__(self) -> None:
        super().__init__()
        self.held = ""
        self.entered = threading.Event()
        self.release = threading.Event()
        self.deleted: list[str] = []

    def delete(self, customer_id: str, document_id: str) -> None:
        if document_id == self.held:
            self.entered.set()
            if not self.release.wait(timeout=10):
                raise AssertionError("the test never released the held delete")
        super().delete(customer_id, document_id)
        self.deleted.append(document_id)


def test_a_family_in_trash_too_long_goes_with_its_files_and_the_rest_stays(db: Database) -> None:
    drain(db)
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    old = trashed(db, storage, customer, LIMIT - timedelta(seconds=1), versions=2)
    on_the_limit = trashed(db, storage, customer, LIMIT)
    recent = trashed(db, storage, customer, NOW - timedelta(days=1))
    live = add_family(db, storage, customer)

    assert purge_expired(db, storage, clock, TRASH_DAYS) == 1

    kept = on_the_limit + recent + live
    assert rows(db, customer) == set(kept)
    assert stored(storage, customer, old + kept) == set(kept)
    assert purge_expired(db, storage, clock, TRASH_DAYS) == 0


def test_the_purge_stops_after_batch_families_oldest_first(db: Database) -> None:
    drain(db)
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    families = [trashed(db, storage, customer, LIMIT - timedelta(days=days)) for days in (3, 2, 1)]

    assert purge_expired(db, storage, clock, TRASH_DAYS, batch=2) == 2
    assert rows(db, customer) == set(families[2])

    assert purge_expired(db, storage, clock, TRASH_DAYS, batch=2) == 1
    assert rows(db, customer) == set()


def test_a_storage_error_rolls_back_that_family_and_stops_the_run(db: Database) -> None:
    drain(db)
    customer, _ = new_customer(db)
    storage = FailingStorage()
    first = trashed(db, storage, customer, LIMIT - timedelta(days=3))
    second = trashed(db, storage, customer, LIMIT - timedelta(days=2), versions=2)
    third = trashed(db, storage, customer, LIMIT - timedelta(days=1))
    storage.failing = set(second)  # whichever version the purge deletes first

    with pytest.raises(BackendUnavailable):
        purge_expired(db, storage, clock, TRASH_DAYS)

    # The first family is gone for good; the second is whole again; the third was never reached.
    assert rows(db, customer) == set(second + third)
    assert stored(storage, customer, first + second + third) == set(second + third)

    storage.failing.clear()  # storage is back: the next run carries on
    assert purge_expired(db, storage, clock, TRASH_DAYS) == 2
    assert rows(db, customer) == set()


def test_two_purgers_never_take_the_same_family(db: Database) -> None:
    drain(db)
    customer, _ = new_customer(db)
    storage = HeldStorage()
    families = [trashed(db, storage, customer, LIMIT - timedelta(days=days)) for days in (3, 2, 1)]
    storage.held = families[0][0]

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(purge_expired, db, storage, clock, TRASH_DAYS)
        try:
            assert storage.entered.wait(timeout=10)
            # The first purger holds the oldest family, inside its transaction.
            second = purge_expired(db, storage, clock, TRASH_DAYS)
        finally:
            storage.release.set()
        assert first.result(timeout=10) == 1

    assert second == 2
    assert sorted(storage.deleted) == sorted(family[0] for family in families)
    assert rows(db, customer) == set()


def test_the_clock_must_give_a_time_zone(db: Database) -> None:
    with pytest.raises(ValueError, match="time zone"):
        purge_expired(db, MemoryStorage(), lambda: datetime(1990, 1, 1), TRASH_DAYS)  # noqa: DTZ001 - the point of the test
