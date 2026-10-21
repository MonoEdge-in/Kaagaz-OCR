"""Migration 0038 (AI settings by task, the feature switches and the platform test model):
up, down (0037's schema exactly) and up again, and the tables' constraints. Grants, policies
and the operator's functions: test_isolation_schema; comments: test_migration_0007_db.
"""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate, new_customer
from tests.test_migration_0004_db import _current, _head
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
NEW = {"ai_task_settings", "feature_switches", "platform_model_override"}
_ROW = (
    "INSERT INTO ai_task_settings (customer_id, user_id, task, level, locked)"
    " VALUES (:c, CAST(:u AS uuid), :t, :l, :k)"
)


def test_0038_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0037")
    at_0037 = m0002._schema(database)
    migrate(url, "head")
    assert _current(url) == _head()
    at_head = m0002._schema(database)
    assert m0002._names(at_head, "relations") >= NEW
    m0002._downgrade(url, "0037")
    assert m0002._schema(database) == at_0037
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def _refused(db: Database, customer: str, **row: Any) -> None:
    with pytest.raises(DBAPIError), db.customer(customer) as conn:
        conn.execute(text(_ROW), {"c": customer, "u": None, "k": False, **row})


def test_a_bad_task_setting_is_refused(db: Database) -> None:
    customer, admin = new_customer(db)
    _refused(db, customer, t="scan", l="on")  # the level of the other kind
    _refused(db, customer, t="ensemble", l="high")
    _refused(db, customer, t="search", l="low")  # not a task
    _refused(db, customer, t="ask", l="medium")  # not a level
    _refused(db, customer, t="ask", l="low", u=admin, k=True)  # a lock on a member's row
    with db.customer(customer) as conn:
        conn.execute(text(_ROW), {"c": customer, "u": None, "t": "ask", "l": "low", "k": True})
        conn.execute(text(_ROW), {"c": customer, "u": admin, "t": "ask", "l": "high", "k": False})
    _refused(db, customer, t="ask", l="none")  # a second organisation row for the task
    _refused(db, customer, t="ask", l="none", u=admin)  # a second choice of one member


def test_the_switches_and_the_test_model_keep_their_shape(db: Database) -> None:
    for statement in (
        "INSERT INTO feature_switches (feature) VALUES ('teleport')",
        "INSERT INTO platform_model_override (id, scan_model) VALUES (false, NULL)",
        "INSERT INTO platform_model_override (scan_model, chat_model) VALUES (NULL, NULL)",
        "INSERT INTO platform_model_override (scan_model) VALUES ('google/not-in-the-catalogue')",
    ):
        with pytest.raises(DBAPIError):
            _owner(db, statement, {})
    with db.system() as conn:  # the app reads two columns of each, never who changed them
        conn.execute(text("SELECT feature, enabled FROM feature_switches")).all()
        conn.execute(text("SELECT scan_model, chat_model FROM platform_model_override")).all()
    with pytest.raises(DBAPIError), db.system() as conn:
        conn.execute(text("SELECT changed_by FROM feature_switches"))
    with pytest.raises(DBAPIError), db.system() as conn:
        conn.execute(text("SELECT started_by, note FROM platform_model_override"))
    with pytest.raises(DBAPIError), db.system() as conn:
        conn.execute(text("INSERT INTO feature_switches (feature, enabled) VALUES ('ask', false)"))
