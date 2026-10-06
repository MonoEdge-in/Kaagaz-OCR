"""The break-glass ``purge-customer --now`` and the second sweep a day after a purge,
against PostgreSQL (super admin PR 4b, 0019). Files live in ``KeyStorage`` (the purge
tests'), keyed as ``S3Storage`` keys them."""

import io
from datetime import timedelta

import pytest

from kaagaz.adapters.storage import page_image_key, quarantine_key
from kaagaz.cli import main
from kaagaz.db.engine import Database
from kaagaz.jobs.purge import sweep_purged_customers
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer
from tests.test_isolation_customer_purge import KeyStorage, _gone, _now
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
# What scheduling does (0018), straight in the table as its owner.
DELETING = (
    "UPDATE customers SET status = 'deleting', disabled_at = now(), delete_after = now() + interval '7 days'"
    " WHERE id = :c"
)
_ROWS = "SELECT operator_id, action FROM platform_audit WHERE subject_customer_id = :c ORDER BY id"


def _cli(db: Database, storage: KeyStorage, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(["purge-customer", *args], db=db, environ={}, out=out, storage=storage), out.getvalue()


def _purged(db: Database, storage: KeyStorage) -> str:
    """A customer with one stored document, scheduled for deletion and purged by the CLI."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
    storage.put(customer, document, io.BytesIO(b"file"))
    _owner(db, DELETING, {"c": customer})
    line = f"customer {customer}: purged, document families removed: 1\n"
    assert _cli(db, storage, "--customer-id", customer, "--now") == (0, line)
    return customer


def test_0019_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0018")
    at_0018 = m0002._schema(database)
    migrate(url, "head")
    at_head = m0002._schema(database)
    assert m0002._names(at_head, "functions") >= {"kaagaz_purged_ids_to_sweep", "kaagaz_customer_swept"}
    m0002._downgrade(url, "0018")
    assert m0002._schema(database) == at_0018
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_purge_now_refuses_any_organisation_whose_deletion_is_not_scheduled(db: Database) -> None:
    storage, (a, _) = KeyStorage(), new_customer(db)
    with db.customer(a) as conn:
        document = insert_document(conn, a)
    storage.put(a, document, io.BytesIO(b"file"))
    for refused in (["--customer-id", "not-an-id", "--now"], ["--customer-id", a, "--now"]):
        assert _cli(db, storage, *refused) == (2, "")  # not an id; active
    assert main(["disable-customer", "--customer-id", a], db=db, environ={}, out=io.StringIO()) == 0
    assert _cli(db, storage, "--customer-id", a, "--now") == (2, "")  # disabled, not scheduled
    state = "SELECT status, delete_after FROM customers WHERE id = :c"
    assert _owner(db, state, {"c": a}) == [("disabled", None)]
    assert len(storage.listing(a)) == 1
    assert [row[1] for row in _owner(db, _ROWS, {"c": a})] == ["customer.disable"]  # the CLI's own


def test_purge_now_removes_a_scheduled_organisation_at_once_and_says_so(db: Database) -> None:
    storage = KeyStorage()
    a = _purged(db, storage)
    _gone(db, storage, a)
    assert [tuple(row) for row in _owner(db, _ROWS, {"c": a})] == [
        (None, "org.purge_now"),
        (None, "org.purged"),
    ]
    assert _cli(db, storage, "--customer-id", a, "--now") == (2, "")  # no such customer now


def test_a_day_after_a_purge_both_prefixes_are_swept_once_more_and_once_only(db: Database) -> None:
    storage = KeyStorage()
    a = _purged(db, storage)
    late = {page_image_key(a, a, 3): b"written by a job still running", quarantine_key(a, a): b"held"}
    storage.objects.update(late)

    sweep_purged_customers(db, storage, _now)  # within the day: not yet
    assert len(storage.listing(a)) == 2
    sweep_purged_customers(db, storage, lambda: _now() + timedelta(hours=25))
    assert storage.listing(a) == []
    storage.objects.update(late)
    sweep_purged_customers(db, storage, lambda: _now() + timedelta(hours=25))
    assert len(storage.listing(a)) == 2  # swept once only
    assert [row[1] for row in _owner(db, _ROWS, {"c": a})] == ["org.purge_now", "org.purged", "org.swept"]
