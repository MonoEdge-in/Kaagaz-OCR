"""Migration 0012 (council 4 Oct 2026): ``users.delete_forever_allowed_at``, a NULL
timestamptz with its comment. Up, down (0011's schema exactly) and up again."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
_COLUMN = text(
    "SELECT format_type(atttypid, atttypmod), attnotnull, atthasdef, col_description(attrelid, attnum)"
    " FROM pg_attribute WHERE attrelid = 'public.users'::regclass AND attname = 'delete_forever_allowed_at'"
)


def test_0012_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0011")
    at_0011 = m0002._schema(database)
    migrate(url, "head")
    at_head = m0002._schema(database)
    with database.system() as conn:
        kind, not_null, has_default, comment = conn.execute(_COLUMN).one()
    assert (kind, not_null, has_default) == ("timestamp with time zone", False, False)
    assert "NULL = not allowed" in comment and "Ignored for admins" in comment

    m0002._downgrade(url, "0011")
    assert m0002._schema(database) == at_0011
    with database.system() as conn:
        assert conn.execute(_COLUMN).all() == []
    migrate(url, "head")
    assert m0002._schema(database) == at_head
