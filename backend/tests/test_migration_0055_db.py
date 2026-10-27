"""Migration 0055 (#387): the operator's ``kaagaz_op_org_numbers`` on a fresh database, up, down
and up again; nothing else in the schema changes, and the downgrade drops only the function."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate

pytestmark = pytest.mark.db
fresh = base.fresh
BEFORE = "0054"
_COMMENT = text(
    "SELECT obj_description('public.kaagaz_op_org_numbers(uuid, date, date, text)'::regprocedure)"
)


def test_0055_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, BEFORE)
    before = base._schema(db)
    migrate(url, "0055")
    after = base._schema(db)
    assert base._names(after, "functions") - base._names(before, "functions") == {"kaagaz_op_org_numbers"}
    assert {part: rows for part, rows in after.items() if part != "functions"} == {
        part: rows for part, rows in before.items() if part != "functions"
    }
    with db.system() as conn:
        assert "Counts and codes only" in conn.execute(_COMMENT).scalar_one()
    base._downgrade(url, BEFORE)
    assert base._schema(db) == before
    migrate(url, "0055")
    assert base._schema(db) == after
