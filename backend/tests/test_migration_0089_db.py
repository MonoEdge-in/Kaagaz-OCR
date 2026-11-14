"""Migration 0089 (#594, 14 Nov 2026): nothing opens to everyone by accident. Up, down (the schema
as at 0088) and up again on a fresh database; ``version_suggestions.previous_held_for`` carries its
comment and its key to ``users`` (same organisation), validated; ``customers.walls_set`` is worked
out for the organisations there (true only where a folder or trashed folder is Department only), and
the downgrade puts back 0087's ``kaagaz_access()``. What they do: test_isolation_open_paths."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_folder, migrate, new_customer
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db
fresh = base.fresh
M0089 = _load("0089_open_paths.py")
_COLUMN = (
    "SELECT col_description('version_suggestions'::regclass, a.attnum) FROM pg_attribute a"
    " WHERE a.attrelid = 'version_suggestions'::regclass AND a.attname = 'previous_held_for'"
)
_KEY = (
    "SELECT pg_get_constraintdef(oid), convalidated FROM pg_constraint"
    " WHERE conname = 'version_suggestions_previous_held_for_fk'"
)


def test_the_revision_follows_0088() -> None:
    assert (M0089.revision, M0089.down_revision) == ("0089", "0088")


_ACCESS = "SELECT pg_get_functiondef('public.kaagaz_access()'::regprocedure)"


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0088")
    walled, first = new_customer(db)
    open_one, user = new_customer(db)
    with db.customer(open_one) as conn:
        insert_folder(conn, open_one, user, "Sales")
    with db.customer(walled) as conn:
        finance = insert_folder(conn, walled, first, "Finance")
    _owner(db, "UPDATE folders SET kind = 'department', department_only = true WHERE id = :f", {"f": finance})
    at_0088 = _state(db)
    with db.system() as conn:
        access_0088 = conn.execute(text(_ACCESS)).scalar_one()
    migrate(url, "0089")
    walls = "SELECT id, walls_set FROM customers WHERE id IN (:a, :b)"
    assert {str(c): w for c, w in _owner(db, walls, {"a": walled, "b": open_one})} == {
        walled: True,
        open_one: False,
    }
    at_0089 = _state(db)
    with db.system() as conn:
        assert conn.execute(text(_COLUMN)).scalar_one() == M0089.HELD_COMMENT
        assert conn.execute(text(_KEY)).one() == (
            "FOREIGN KEY (customer_id, previous_held_for) REFERENCES users(customer_id, id)",
            True,
        )

    base._downgrade(url, "0088")
    assert _state(db) == at_0088
    with db.system() as conn:
        assert conn.execute(text(_COLUMN)).first() is None
        assert conn.execute(text(_ACCESS)).scalar_one() == access_0088

    migrate(url, "0089")
    assert _state(db) == at_0089
