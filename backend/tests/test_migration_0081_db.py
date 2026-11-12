"""Migration 0081 (#542, Tushar, 7 Nov 2026): a folder in Trash keeps its structure
(``trashed_folders``, ``documents.trashed_folder_id`` and ``trash_id``), and the purge's customer
list also names a customer with an old trashed folder. Up, down and up again: the schema,
comments, grants and function bodies as before each time. Customer separation:
test_isolation_trashed_folders; the policy and grants: test_isolation_schema."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0081 = _load("0081_trashed_folders.py")
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
_TRASHED = (
    "INSERT INTO trashed_folders (id, customer_id, parent_id, name, kind, created_by, created_at,"
    " trash_id, root, deleted_at, deleted_by)"
    " VALUES (:id, :c, NULL, 'Accounts', 'custom', :u, :at, :t, true, :at, :u)"
)
_ADMIN = "SELECT id FROM users WHERE customer_id = :c"
_FILED = "UPDATE documents SET trashed_folder_id = :f, trash_id = :t WHERE id = :d"


def _with_trash(db: Database) -> list[str]:
    with db.system() as conn:
        rows = conn.execute(
            text("SELECT * FROM kaagaz_customer_ids_with_trash(:b)"), {"b": LONG_AGO + timedelta(days=1)}
        )
        return [str(row[0]) for row in rows]


def test_the_revision_follows_0080() -> None:
    assert (M0081.revision, M0081.down_revision) == ("0081", "0080")


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0080")
    at_0080 = _state(db)
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user, deleted_at=LONG_AGO, deleted_by=user)
    empty, _ = new_customer(db)

    migrate(url, "0081")
    at_0081 = _state(db)
    assert {"trashed_folders", "trashed_folders_parent", "trashed_folders_trash"} <= base._names(
        at_0081, "relations"
    )
    folder, trash_id = str(uuid.uuid4()), str(uuid.uuid4())
    admin = as_owner(db, _ADMIN, {"c": empty})[0][0]
    as_owner(db, _TRASHED, {"id": folder, "c": empty, "u": admin, "at": LONG_AGO, "t": trash_id})
    as_owner(db, _FILED, {"f": folder, "t": trash_id, "d": document})
    # The purge finds a customer with an old document, and one with only an old trashed folder.
    assert sorted(_with_trash(db)) == sorted([customer, empty])

    base._downgrade(url, "0080")
    assert _state(db) == at_0080
    assert _with_trash(db) == [customer]
    assert as_owner(db, "SELECT deleted_at FROM documents WHERE id = :d", {"d": document}) == [(LONG_AGO,)]

    migrate(url, "0081")
    assert _state(db) == at_0081
