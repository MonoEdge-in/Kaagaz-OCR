"""Dynamic's count (0060, #405) on a fresh database: up, down and up again. The operator's
feature list differs from 0059's only by the dropped ``, true``; its owner, comment, grants,
SECURITY DEFINER and search path are kept. What the count does: test_ai_settings_db."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0060 = _load("0060_dynamic_count.py")
_DEFINITION = text(
    "SELECT pg_get_functiondef(p.oid), pg_get_userbyid(p.proowner), obj_description(p.oid, 'pg_proc')"
    " FROM pg_proc p WHERE p.oid = 'public.kaagaz_op_feature_switches(uuid)'::regprocedure"
)


def _definition(db: Database) -> tuple[str, str, str]:
    with db.system() as conn:
        body, owner, comment = conn.execute(_DEFINITION).one()
    return body, owner, comment


def test_0060_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, M0060.down_revision)
    before, at_0059 = base._schema(db), _definition(db)
    assert at_0059[0].count(M0060.OLD) == 1

    migrate(url, "0060")
    at_0060 = _definition(db)
    assert at_0060 == (at_0059[0].replace(M0060.OLD, M0060.NEW), *at_0059[1:])  # only that change
    assert base._schema(db) == before  # SECURITY DEFINER, search path and grants kept

    base._downgrade(url, M0060.down_revision)
    assert _definition(db) == at_0059
    assert base._schema(db) == before
    migrate(url, "0060")
    assert _definition(db) == at_0060
