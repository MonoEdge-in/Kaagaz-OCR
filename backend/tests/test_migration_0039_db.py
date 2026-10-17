"""Migration 0039 (per-organisation feature switches): up, down (0038's schema exactly, its
platform list too) and up again, the table's shape, and customer separation (an organisation
reads only its own rows, three columns of them, and writes none). Grants, policies and the
operator's functions: test_isolation_schema; the API and each blocked feature:
test_ai_settings_db.
"""

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
_OFF = "INSERT INTO org_feature_switches (customer_id, feature, enabled) VALUES (:c, :f, false)"


def test_0039_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0038")
    at_0038 = m0002._schema(database)
    migrate(url, "head")
    assert _current(url) == _head()
    at_head = m0002._schema(database)
    assert "org_feature_switches" in m0002._names(at_head, "relations")
    m0002._downgrade(url, "0038")
    assert m0002._schema(database) == at_0038
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_an_organisation_switch_keeps_its_shape(db: Database) -> None:
    customer, _ = new_customer(db)
    for feature in ("teleport", "self_signup"):  # not a feature; sign-up has no organisation switch
        with pytest.raises(DBAPIError):
            _owner(db, _OFF, {"c": customer, "f": feature})
    _owner(db, _OFF, {"c": customer, "f": "ask"})
    with pytest.raises(DBAPIError):  # one row per organisation and feature
        _owner(db, _OFF, {"c": customer, "f": "ask"})


def test_an_organisation_reads_only_its_own_switches_and_writes_none(db: Database) -> None:
    mine, _ = new_customer(db)
    other, _ = new_customer(db)
    _owner(db, _OFF, {"c": other, "f": "ask"})
    select = "SELECT customer_id, feature, enabled FROM org_feature_switches"
    with db.customer(mine) as conn:
        assert conn.execute(text(select)).all() == []  # row level security: never another's row
    with db.customer(other) as conn:
        assert [tuple(row)[1:] for row in conn.execute(text(select))] == [("ask", False)]
    with pytest.raises(DBAPIError), db.customer(other) as conn:  # never who flipped it
        conn.execute(text("SELECT updated_by FROM org_feature_switches"))
    with pytest.raises(DBAPIError), db.customer(mine) as conn:  # only the operator's function writes
        conn.execute(text(_OFF), {"c": mine, "f": "review"})
