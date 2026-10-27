"""Customer B never reads, writes, changes or deletes customer A's page blocks or table cells
(#383, #382, migration 0056; real PostgreSQL).

B uses A's exact document id. The ``customer_only`` policy hides A's rows from B whatever B's
statement says and refuses a row B writes in A's name; the (customer_id, document_id) foreign
key refuses a row of B's under A's document; B's replacing save with A's id deletes none of
A's. Marked ``isolation`` by its file name.
"""

import json
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from kaagaz.chunking.layout import page_layout
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from tests.db_support import count_rows, new_customer, violation
from tests.test_pipeline_store_db import content, new_document
from tests.test_tables import TABLE

pytestmark = pytest.mark.db

REGION = json.dumps({"kind": "box", "page": 1, "box": [0.1, 0.1, 0.5, 0.2]})
BLOCK = (
    "INSERT INTO page_blocks (customer_id, document_id, page_no, seq, kind, region)"
    " VALUES (:c, :d, 9, 0, 'text', CAST(:region AS jsonb))"
)
CELL = (
    "INSERT INTO table_cells (customer_id, document_id, page_no, table_id, row_no, col_no, is_header,"
    " text, region) VALUES (:c, :d, 9, 't9', 0, 0, false, 'x', CAST(:region AS jsonb))"
)
TABLES = ("page_blocks", "table_cells")
COUNT = {
    "page_blocks": "SELECT count(*) FROM page_blocks WHERE document_id = :d",
    "table_cells": "SELECT count(*) FROM table_cells WHERE document_id = :d",
}
DELETE = {
    "page_blocks": "DELETE FROM page_blocks WHERE document_id = :d",
    "table_cells": "DELETE FROM table_cells WHERE document_id = :d",
}


def _a_with_a_layout(db: Database) -> tuple[str, str, str]:
    """(A, B, A's document saved with a table block and its cells)."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    doc = new_document(db, a)
    PgPipelineStore(db).save(replace(content(a, doc), layout=page_layout(TABLE)))
    return a, b, doc


def _seen_by(db: Database, customer_id: str, document_id: str) -> dict[str, int]:
    with db.customer(customer_id) as conn:
        return {table: count_rows(conn, COUNT[table], {"d": document_id}) for table in TABLES}


def test_another_customer_sees_no_block_or_cell(db: Database) -> None:
    a, b, doc = _a_with_a_layout(db)
    assert _seen_by(db, b, doc) == {"page_blocks": 0, "table_cells": 0}
    assert _seen_by(db, a, doc) == {"page_blocks": 1, "table_cells": len(TABLE)}


@pytest.mark.parametrize("statement", [BLOCK, CELL])
def test_the_composite_foreign_key_refuses_another_customers_document(db: Database, statement: str) -> None:
    _, b, doc = _a_with_a_layout(db)
    with pytest.raises(IntegrityError) as refused, db.customer(b) as conn:
        conn.execute(text(statement), {"c": b, "d": doc, "region": REGION})
    assert violation(refused.value)[0] == "23503"


@pytest.mark.parametrize("statement", [BLOCK, CELL])
def test_a_row_in_another_customers_name_is_refused_by_the_policy(db: Database, statement: str) -> None:
    a, b, doc = _a_with_a_layout(db)
    with pytest.raises(DBAPIError) as refused, db.customer(b) as conn:
        conn.execute(text(statement), {"c": a, "d": doc, "region": REGION})
    assert violation(refused.value)[0] == "42501"
    assert _seen_by(db, a, doc) == {"page_blocks": 1, "table_cells": len(TABLE)}


def test_another_customer_can_neither_delete_them_nor_clear_them_with_the_same_id(db: Database) -> None:
    a, b, doc = _a_with_a_layout(db)
    with db.customer(b) as conn:
        deleted = [conn.execute(text(DELETE[table]), {"d": doc}).rowcount for table in TABLES]
    assert deleted == [0, 0]
    with pytest.raises(IntegrityError):
        PgPipelineStore(db).save(replace(content(b, doc), layout=page_layout(TABLE)))
    PgPipelineStore(db).clear(b, doc)
    assert _seen_by(db, a, doc) == {"page_blocks": 1, "table_cells": len(TABLE)}
