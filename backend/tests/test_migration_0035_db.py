"""Migration 0035 (two-step sign-in required for admins, 16 Oct 2026): 0029's grant column
and function go, the operator's organisation list loses the column, and a stored two-step
secret is untouched.

Up, down (0034's schema exactly, 0029's objects back) and up again. Grants and signatures:
test_isolation_schema.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate, new_customer
from tests.test_migration_0004_db import _current, _head

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
COLUMN = ("customers", "two_step_granted")
_LIST_COLUMNS = (
    "SELECT pg_get_function_result(p.oid) FROM pg_proc p"
    " WHERE p.pronamespace = 'public'::regnamespace AND p.proname = 'kaagaz_op_customers'"
)
_SECRET = "SELECT totp_secret_enc, totp_enabled_at IS NOT NULL FROM users WHERE id = :u"


def test_0035_up_down_up_keeps_stored_secrets(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0034")
    at_0034 = m0002._schema(database)
    with database.system() as conn:
        list_0034: object = conn.execute(text(_LIST_COLUMNS)).scalar_one()
    assert COLUMN in {(table, column) for table, column, *_ in at_0034["columns"]}
    customer, admin = new_customer(database)
    with database.customer(customer) as conn:
        conn.execute(
            text("UPDATE users SET totp_secret_enc = 'sealed', totp_enabled_at = now() WHERE id = :u"),
            {"u": admin},
        )

    migrate(url, "head")
    assert _current(url) == _head()
    at_head = m0002._schema(database)
    assert COLUMN not in {(table, column) for table, column, *_ in at_head["columns"]}
    assert "kaagaz_op_set_org_two_step" not in m0002._names(at_head, "functions")
    with database.system() as conn:
        assert conn.execute(text(_LIST_COLUMNS)).scalar_one().endswith("autofile_on boolean)")
    with database.customer(customer) as conn:
        assert tuple(conn.execute(text(_SECRET), {"u": admin}).one()) == ("sealed", True)

    m0002._downgrade(url, "0034")
    assert m0002._schema(database) == at_0034
    with database.system() as conn:
        assert conn.execute(text(_LIST_COLUMNS)).scalar_one() == list_0034

    migrate(url, "head")
    assert m0002._schema(database) == at_head
