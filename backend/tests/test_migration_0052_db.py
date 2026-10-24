"""The PRD 6 switches (0052), up, down and up: the platform list takes the three features at
0052 and refuses them before it; the operator's two answer counts leave out clarify messages
at 0052 only; the downgrade removes the rows and the schema and bodies are as before."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0052 = _load("0052_prd6_switches.py")
OFF = "INSERT INTO feature_switches (feature, enabled) VALUES ('land_facts', false)"


def _off(db: Database) -> list[str]:
    with db.engine.begin() as conn:  # the owner, as the operator's function writes it
        return [str(row[0]) for row in conn.execute(text("SELECT feature FROM feature_switches"))]


def _clarify_left_out(db: Database) -> list[bool]:
    with db.engine.begin() as conn:
        return [
            M0052.NOT_CLARIFY
            in str(conn.execute(text(f"SELECT pg_get_functiondef('public.{f}'::regprocedure)")).scalar())
            for f in M0052.ANSWERS
        ]


def test_0052_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, M0052.down_revision)
    before = base._schema(db)
    assert _clarify_left_out(db) == [False, False]
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        conn.execute(text(OFF))
    migrate(url, "0052")
    at_0052 = base._schema(db)
    assert _clarify_left_out(db) == [True, True]
    with db.engine.begin() as conn:
        conn.execute(text(OFF))
    assert _off(db) == ["land_facts"]
    base._downgrade(url, M0052.down_revision)
    assert (base._schema(db), _off(db), _clarify_left_out(db)) == (before, [], [False, False])
    migrate(url, "0052")
    assert base._schema(db) == at_0052
