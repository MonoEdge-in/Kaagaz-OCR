"""The purge finish deletes organisation word suggestions (0047), up and down."""

import pytest

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate

pytestmark = pytest.mark.db
fresh = base.fresh


def test_0047_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0046")
    at_0046 = base._schema(db)
    migrate(url, "head")
    at_head = base._schema(db)
    base._downgrade(url, "0046")
    assert base._schema(db) == at_0046
    migrate(url, "head")
    assert base._schema(db) == at_head
