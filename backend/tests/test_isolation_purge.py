"""The trash purge never touches another customer's documents or files (PRD 1 F5).

The purge is the one job that works across customers: it lists the customers
with old Trash through the SECURITY DEFINER function, then works inside one
customer's transaction at a time. Two customers here both have old Trash, live
documents and recent Trash. Each one's old Trash goes, with its files, and
nothing else of either customer changes. Every file delete must name the
document's own customer.
"""

from datetime import timedelta

import pytest

from kaagaz.db.engine import Database
from kaagaz.jobs.purge import purge_expired
from tests.db_support import new_customer
from tests.fakes import MemoryStorage
from tests.test_purge_db import LIMIT, NOW, TRASH_DAYS, add_family, clock, drain, rows, stored, trashed

pytestmark = pytest.mark.db


class RecordingStorage(MemoryStorage):
    """Records (customer_id, document_id) for every file delete."""

    def __init__(self) -> None:
        super().__init__()
        self.deleted: list[tuple[str, str]] = []

    def delete(self, customer_id: str, document_id: str) -> None:
        super().delete(customer_id, document_id)
        self.deleted.append((customer_id, document_id))


def test_the_purge_removes_each_customers_old_trash_and_nothing_else(db: Database) -> None:
    drain(db)
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    storage = RecordingStorage()
    old = {
        a: trashed(db, storage, a, LIMIT - timedelta(days=2), versions=2),
        b: trashed(db, storage, b, LIMIT - timedelta(days=1)),
    }
    kept = {
        customer: trashed(db, storage, customer, NOW - timedelta(days=1)) + add_family(db, storage, customer)
        for customer in (a, b)
    }

    assert purge_expired(db, storage, clock, TRASH_DAYS) == 2

    expected = [(customer, document) for customer, ids in old.items() for document in ids]
    assert sorted(storage.deleted) == sorted(expected)
    for customer in (a, b):
        assert rows(db, customer) == set(kept[customer])
        assert stored(storage, customer, old[customer] + kept[customer]) == set(kept[customer])
