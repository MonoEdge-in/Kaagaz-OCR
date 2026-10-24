"""A model with no thinking levels can be saved again (0048), up, down and up, with the two
functions' bodies (``_schema`` leaves them out): 0048's at head, 0037's exactly after the
downgrade."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0048 = _load("0048_ai_save_models_without_levels.py")


def _bodies(db: Database) -> list[str]:
    with db.system() as conn:
        rows = conn.execute(
            text(
                "SELECT prosrc FROM pg_proc WHERE pronamespace = 'public'::regnamespace"
                " AND proname IN ('kaagaz_op_set_org_ai', 'kaagaz_op_set_ai_defaults') ORDER BY proname DESC"
            )
        ).all()
    return [str(row[0]) for row in rows]  # set_org_ai, then set_ai_defaults: the migration's order


def _sources(statements: list[str]) -> list[str]:
    return [statement.split("$$")[1] for statement in statements]


def test_0048_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0047")
    at_0047 = base._schema(db)
    assert _bodies(db) == _sources(M0048.DOWNGRADE)
    migrate(url, "head")
    at_head = base._schema(db)
    assert _bodies(db) == _sources(M0048.UPGRADE)
    base._downgrade(url, "0047")
    assert base._schema(db) == at_0047
    assert _bodies(db) == _sources(M0048.DOWNGRADE)
    migrate(url, "head")
    assert base._schema(db) == at_head
    assert _bodies(db) == _sources(M0048.UPGRADE)
