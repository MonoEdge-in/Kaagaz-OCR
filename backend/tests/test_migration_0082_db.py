"""Migration 0082 (A13, stability pass, 7 Nov 2026): the app may change a trashed folder's
``parent_id`` only (a column grant), so a folder deleted with ``contents=keep&to=X`` hands its
trashed children to X. Up, down (the grant revoked) and up again: the schema, comments and grants
as before each time. The behaviour: tests/test_api_trashed_folders.py; the grants:
test_isolation_schema."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import APP_ROLE, migrate
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0082 = _load("0082_trashed_folder_parent.py")


def _updatable(db: Database) -> set[str]:
    with db.system() as conn:
        rows = conn.execute(
            text(
                "SELECT attname::text FROM pg_attribute WHERE attrelid = 'trashed_folders'::regclass"
                " AND attnum > 0 AND NOT attisdropped"
                " AND has_column_privilege(:r, attrelid, attname, 'UPDATE')"
            ),
            {"r": APP_ROLE},
        )
        return {str(row[0]) for row in rows}


def test_the_revision_follows_0081() -> None:
    assert (M0082.revision, M0082.down_revision) == ("0082", "0081")


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0081")
    at_0081 = _state(db)
    assert _updatable(db) == set()

    migrate(url, "0082")
    at_0082 = _state(db)
    assert _updatable(db) == {"parent_id"}

    base._downgrade(url, "0081")
    assert _state(db) == at_0081
    assert _updatable(db) == set()

    migrate(url, "0082")
    assert _state(db) == at_0082
