"""The reader choice (0058, #398) on a fresh database: up, down and up again. The scan task takes
the level ``save`` (Ask and the switches never do); no row is written; the downgrade reads a
saved ``save`` as ``low`` and puts 0038's CHECKs back exactly."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0058 = _load("0058_reader_choice.py")
_SAVE = text(
    "INSERT INTO ai_task_settings (customer_id, user_id, task, level, locked)"
    " VALUES (:c, NULL, :t, :l, false)"
)
_LEVELS = text("SELECT task, level FROM ai_task_settings ORDER BY task")
_COMMENT = text("SELECT col_description('ai_task_settings'::regclass, 5)")  # level, 0038's 5th column


def test_0058_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, M0058.down_revision)
    before = base._schema(db)
    customer_id, _user = new_customer(db)
    with db.customer(customer_id) as conn:
        conn.execute(_SAVE, {"c": customer_id, "t": "ask", "l": "high"})  # saved before 0058
    with pytest.raises(IntegrityError), db.customer(customer_id) as conn:
        conn.execute(_SAVE, {"c": customer_id, "t": "scan", "l": "save"})  # not a level yet

    migrate(url, "0058")
    at_0058 = base._schema(db)
    with db.system() as conn:
        assert "Save cost" in conn.execute(_COMMENT).scalar_one()
    with db.customer(customer_id) as conn:
        assert [tuple(row) for row in conn.execute(_LEVELS)] == [("ask", "high")]  # nothing rewritten
        conn.execute(_SAVE, {"c": customer_id, "t": "scan", "l": "save"})
    for task in ("ask", "ensemble"):  # Save is the scan task's only
        with pytest.raises(IntegrityError), db.customer(customer_id) as conn:
            conn.execute(text("DELETE FROM ai_task_settings WHERE task = :t"), {"t": task})
            conn.execute(_SAVE, {"c": customer_id, "t": task, "l": "save"})

    base._downgrade(url, M0058.down_revision)
    assert base._schema(db) == before
    with db.system() as conn:
        assert (
            conn.execute(_COMMENT).scalar_one()
            == "none, low, high or max (scan, ask); off or on (ensemble, autofile)."
        )
    with db.customer(customer_id) as conn:  # a saved Save reads every page with the AI
        assert [tuple(row) for row in conn.execute(_LEVELS)] == [("ask", "high"), ("scan", "low")]
    migrate(url, "0058")
    assert base._schema(db) == at_0058
