"""Migration 0089 (#594, 14 Nov 2026): nothing opens to everyone by accident. Up, down (the schema
as at 0088) and up again on a fresh database; ``version_suggestions.previous_held_for`` carries its
comment and its key to ``users`` (same organisation), validated. What it does: test_isolation_open_paths."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

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


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0088")
    at_0088 = _state(db)
    migrate(url, "0089")
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

    migrate(url, "0089")
    assert _state(db) == at_0089
