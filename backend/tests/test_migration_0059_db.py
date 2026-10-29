"""The data dictionary's gaps (0059, #404) on a fresh database: up, down and up again. Before
it, the table and the 56 columns it names have no comment; after it, nothing in public lacks
one; the downgrade clears exactly those again and changes nothing else."""

import pytest

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0007_db import _dictionary
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0059 = _load("0059_dictionary_gaps.py")
NAMED = {("upload_sessions", None)} | {
    (table, column) for table, columns in M0059.COLUMNS.items() for column in columns
}


def _missing(db: Database) -> set[tuple[str, str | None]]:
    return {name for name, comment in _dictionary(db).items() if not (comment or "").strip()}


def test_0059_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, M0059.down_revision)
    before = base._schema(db)
    assert len(NAMED) == 57
    assert _missing(db) == NAMED  # every gap it fills, and only those

    migrate(url, "0059")
    assert _missing(db) == set()
    assert base._schema(db) == before  # comments only
    at_0059 = _dictionary(db)

    base._downgrade(url, M0059.down_revision)
    assert _missing(db) == NAMED
    assert base._schema(db) == before
    migrate(url, "0059")
    assert _dictionary(db) == at_0059
