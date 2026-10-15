"""Migration 0025 (auto-filing, feature 28, council 15 Oct 2026): the customers' grant and
switch, the folders' type key with its unique index, the documents' state, the operator's
new function and its organisation list with the two new columns.

Up, down (0024's schema exactly, the list's old columns back) and up again, with data in
every new column; every new column, the index and both functions carry a comment (0007's
rule; its coverage test covers the columns too); each CHECK refuses a bad row; the index
refuses a second folder for one type in one customer, never in two. Grants and pinned
signatures: test_isolation_schema; the rules: test_autofile_db and test_isolation_autofile.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, insert_folder, migrate, new_customer, violation
from tests.test_migration_0004_db import _current, _head
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
FUNCTIONS = {"kaagaz_op_set_org_autofile", "kaagaz_op_customers"}
INDEX = "folders_autofile_key"
NEW_COLUMNS = {
    ("customers", "autofile_granted"),
    ("customers", "autofile_on"),
    ("folders", "autofile_key"),
    ("documents", "autofile"),
}
_LIST_COLUMNS = (
    "SELECT pg_get_function_result(p.oid) FROM pg_proc p"
    " WHERE p.pronamespace = 'public'::regnamespace AND p.proname = 'kaagaz_op_customers'"
)


def _fill(database: Database) -> None:
    """One customer with the grant on, a keyed folder and a filed document, as the owner."""
    customer, admin = new_customer(database)
    with database.engine.begin() as conn:
        conn.execute(
            text("UPDATE customers SET autofile_granted = true, autofile_on = true WHERE id = :c"),
            {"c": customer},
        )
    with database.customer(customer) as conn:
        folder = insert_folder(conn, customer, admin, "Invoices")
        conn.execute(
            text("UPDATE folders SET autofile_key = 'type:invoice' WHERE customer_id = :c AND id = :f"),
            {"c": customer, "f": folder},
        )
        insert_document(conn, customer, folder_id=folder, autofile="filed", uploaded_by=admin)


def test_0025_up_down_up_with_data_in_the_new_columns(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0024")
    at_0024 = m0002._schema(database)
    with database.system() as conn:
        list_0024: object = conn.execute(text(_LIST_COLUMNS)).scalar_one()
    assert not {(table, column) for table, column, *_ in at_0024["columns"]} & NEW_COLUMNS

    migrate(url, "head")
    assert _current(url) == _head()
    at_head = m0002._schema(database)
    assert {(table, column) for table, column, *_ in at_head["columns"]} >= NEW_COLUMNS
    assert m0002._names(at_head, "functions") >= FUNCTIONS
    with database.system() as conn:
        assert (
            "autofile_granted boolean, autofile_on boolean" in conn.execute(text(_LIST_COLUMNS)).scalar_one()
        )
    _fill(database)

    m0002._downgrade(url, "0024")
    assert m0002._schema(database) == at_0024
    with database.system() as conn:
        assert conn.execute(text(_LIST_COLUMNS)).scalar_one() == list_0024

    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_every_new_column_the_index_and_both_functions_have_a_comment(db: Database) -> None:
    comments = _owner(
        db,
        "SELECT p.proname::text, obj_description(p.oid, 'pg_proc') FROM pg_proc p"
        " WHERE p.pronamespace = 'public'::regnamespace AND p.proname = ANY(CAST(:f AS text[]))"
        " UNION ALL SELECT c.relname::text, obj_description(c.oid, 'pg_class') FROM pg_class c"
        " WHERE c.relnamespace = 'public'::regnamespace AND c.relname = :i"
        " UNION ALL SELECT c.relname::text || '.' || a.attname::text, col_description(c.oid, a.attnum)"
        " FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid"
        " WHERE c.relnamespace = 'public'::regnamespace AND a.attname IN ('autofile_granted', 'autofile_on',"
        " 'autofile_key', 'autofile') AND c.relkind = 'r'",
        {"f": sorted(FUNCTIONS), "i": INDEX},
    )
    expected = FUNCTIONS | {INDEX} | {f"{table}.{column}" for table, column in NEW_COLUMNS}
    assert sorted(name for name, _ in comments) == sorted(expected)
    assert [name for name, comment in comments if not (comment or "").strip()] == []


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "UPDATE documents SET autofile = 'moved' WHERE customer_id = :c AND id = :d",
            "documents_autofile_check",
        ),
        (
            "UPDATE folders SET autofile_key = 'Invoices' WHERE customer_id = :c AND id = :f",
            "folders_autofile_key_check",
        ),
        (
            "UPDATE customers SET autofile_granted = false, autofile_on = true WHERE id = :c",
            "customers_autofile_check",
        ),
    ],
)
def test_each_check_refuses_a_bad_row(db: Database, statement: str, constraint: str) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, admin, "Mine")
        document = insert_document(conn, customer)
    with pytest.raises(DBAPIError) as refused, db.engine.begin() as conn:  # as the owner: no grant stops it
        conn.execute(text(statement), {"c": customer, "d": document, "f": folder})
    assert violation(refused.value) == ("23514", constraint)


def test_one_folder_per_type_in_a_customer_and_the_same_type_in_another(db: Database) -> None:
    a, a_admin = new_customer(db)
    b, b_admin = new_customer(db)
    keyed = (
        "INSERT INTO folders (customer_id, parent_id, name, created_by, autofile_key)"
        " VALUES (:c, NULL, :n, :u, 'type:invoice')"
    )
    with db.customer(a) as conn:
        conn.execute(text(keyed), {"c": a, "n": "Invoices", "u": a_admin})
    with db.customer(b) as conn:
        conn.execute(text(keyed), {"c": b, "n": "Invoices", "u": b_admin})  # allowed
    with pytest.raises(DBAPIError) as refused, db.customer(a) as conn:
        conn.execute(text(keyed), {"c": a, "n": f"Bills {uuid.uuid4().hex[:6]}", "u": a_admin})
    assert violation(refused.value) == ("23505", INDEX)
