"""Page layout and tables (0056, #383, #382) on a fresh database: up, down and up again. Two
new tables, each with row level security, its CHECKs, the app's SELECT, INSERT and DELETE
only, and a comment on every column; a document read before 0056 keeps working with none."""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0056 = _load("0056_page_layout.py")
REGION = json.dumps({"kind": "box", "page": 1, "box": [0.1, 0.1, 0.5, 0.2]})
BLOCK = text(
    "INSERT INTO page_blocks (customer_id, document_id, page_no, seq, kind, region, table_id)"
    " VALUES (:c, :d, 1, :seq, :kind, CAST(:region AS jsonb), :table_id)"
)
CELL = text(
    "INSERT INTO table_cells (customer_id, document_id, page_no, table_id, row_no, col_no, col_span,"
    " is_header, text, region)"
    " VALUES (:c, :d, 1, 't1', 0, :col, :span, true, 'Amount', CAST(:region AS jsonb))"
)
_UNCOMMENTED = text(
    "SELECT c.relname, a.attname FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid"
    " WHERE c.relname IN ('page_blocks', 'table_cells') AND a.attnum > 0 AND NOT a.attisdropped"
    " AND coalesce(col_description(c.oid, a.attnum), '') = ''"
)


def test_0056_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, M0056.down_revision)
    before = base._schema(db)
    customer_id, _user = new_customer(db)
    with db.customer(customer_id) as conn:
        document_id = insert_document(conn, customer_id)  # read before 0056

    migrate(url, "0056")
    at_0056 = base._schema(db)
    assert base._names(at_0056, "relations") - base._names(before, "relations") >= {
        "page_blocks",
        "table_cells",
    }
    with db.system() as conn:
        assert conn.execute(_UNCOMMENTED).all() == []
    ids = {"c": customer_id, "d": document_id, "region": REGION}
    with db.customer(customer_id) as conn:
        conn.execute(BLOCK, {**ids, "seq": 0, "kind": "table", "table_id": "t1"})
        conn.execute(CELL, {**ids, "col": 2, "span": 2})
    for statement, params in (
        (BLOCK, {"seq": 1, "kind": "heading", "table_id": None}),  # not a block type
        (BLOCK, {"seq": 1, "kind": "table", "table_id": None}),  # a table names its table
        (BLOCK, {"seq": 1, "kind": "text", "table_id": "t1"}),  # and only a table does
        (BLOCK, {"seq": 0, "kind": "text", "table_id": None}),  # one block per place on a page
        (CELL, {"col": 2, "span": 0}),
        (CELL, {"col": 999, "span": 2}),  # past MAX_TABLE_INDEX
    ):
        with pytest.raises(IntegrityError), db.customer(customer_id) as conn:
            conn.execute(statement, {**ids, **params})

    base._downgrade(url, M0056.down_revision)
    assert base._schema(db) == before
    migrate(url, "0056")
    assert base._schema(db) == at_0056
