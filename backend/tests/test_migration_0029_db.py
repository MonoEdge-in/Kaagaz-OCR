"""Migration 0029 (two-step sign-in by grant, 16 Oct 2026): the customers' grant, the
operator's new function and its organisation list with the new column.

Up to 0029 (not head: 0031 drops the grant again), down (0028's schema exactly, the list's
old columns back) and up again, with the grant set. Comments: test_migration_0007; 0031:
test_migration_0031_db.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate, new_customer
from tests.test_migration_0004_db import _current

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
COLUMN = ("customers", "two_step_granted")
FUNCTIONS = {"kaagaz_op_set_org_two_step", "kaagaz_op_customers"}
_LIST_COLUMNS = (
    "SELECT pg_get_function_result(p.oid) FROM pg_proc p"
    " WHERE p.pronamespace = 'public'::regnamespace AND p.proname = 'kaagaz_op_customers'"
)


def test_0029_up_down_up_with_the_grant_set(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0028")
    at_0028 = m0002._schema(database)
    with database.system() as conn:
        list_0028: object = conn.execute(text(_LIST_COLUMNS)).scalar_one()
    assert COLUMN not in {(table, column) for table, column, *_ in at_0028["columns"]}

    migrate(url, "0029")
    assert _current(url) == "0029"
    at_head = m0002._schema(database)
    assert COLUMN in {(table, column) for table, column, *_ in at_head["columns"]}
    assert m0002._names(at_head, "functions") >= FUNCTIONS
    with database.system() as conn:
        assert (
            "autofile_on boolean, two_step_granted boolean)" in conn.execute(text(_LIST_COLUMNS)).scalar_one()
        )
    customer, _ = new_customer(database)
    with database.engine.begin() as conn:
        conn.execute(text("UPDATE customers SET two_step_granted = true WHERE id = :c"), {"c": customer})

    m0002._downgrade(url, "0028")
    assert m0002._schema(database) == at_0028
    with database.system() as conn:
        assert conn.execute(text(_LIST_COLUMNS)).scalar_one() == list_0028

    migrate(url, "0029")
    assert m0002._schema(database) == at_head
