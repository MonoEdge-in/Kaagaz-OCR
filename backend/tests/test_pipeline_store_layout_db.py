"""The page layout saved in the one replacing save (#383, #382, 0056), against a real
PostgreSQL: each page's blocks numbered in reading order with their boxes, each table as rows
and cells with its page and spot; a read again replaces them, and ``clear`` removes them."""

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.chunking.layout import Layout, page_layout
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.reading.spans import BoxLocation, Span, location_to_json
from tests.db_support import new_customer
from tests.test_pipeline_store_db import content, new_document
from tests.test_tables import SGST_45, TABLE

pytestmark = pytest.mark.db

NOTE = Span(
    "Checked, RK", "Checked, RK", BoxLocation(1, 0.8, 0.05, 0.95, 0.07), kind="note", origin="ocr", block=0
)
SPANS = [
    NOTE,
    *TABLE,
    Span("PAID", "PAID", BoxLocation(2, 0.6, 0.8, 0.8, 0.85), kind="stamp", origin="ocr", block=0),
]


def layout_rows(db: Database, customer_id: str, document_id: str) -> dict[str, list[Any]]:
    ids = {"c": customer_id, "d": document_id}
    with db.customer(customer_id) as conn:
        blocks = conn.execute(
            text(
                "SELECT page_no, seq, kind, region, table_id FROM page_blocks"
                " WHERE customer_id = :c AND document_id = :d ORDER BY page_no, seq"
            ),
            ids,
        ).all()
        cells = conn.execute(
            text(
                "SELECT page_no, table_id, row_no, col_no, col_span, is_header, text, region FROM table_cells"
                " WHERE customer_id = :c AND document_id = :d ORDER BY page_no, table_id, row_no, col_no"
            ),
            ids,
        ).all()
    return {"blocks": [tuple(row) for row in blocks], "cells": [tuple(row) for row in cells]}


def test_the_save_keeps_each_pages_blocks_and_each_table_as_rows_and_cells(db: Database) -> None:
    customer_id, _ = new_customer(db)
    document_id = new_document(db, customer_id)
    layout = page_layout(SPANS)

    PgPipelineStore(db).save(replace(content(customer_id, document_id), layout=layout))

    rows = layout_rows(db, customer_id, document_id)
    assert [row[:3] + row[4:] for row in rows["blocks"]] == [
        (1, 0, "note", None),
        (1, 1, "table", "t1"),
        (2, 0, "stamp", None),
    ]
    assert rows["blocks"][0][3] == location_to_json(NOTE.location)
    assert len(rows["cells"]) == len(TABLE)
    assert rows["cells"][2][:7] == (1, "t1", 0, 2, 2, True, "Amount")  # the merged header
    sgst = next(row for row in rows["cells"] if row[2:4] == (2, 3))
    assert (sgst[6], sgst[7]) == ("45", location_to_json(SGST_45.location))  # its page and spot


def test_a_read_again_replaces_the_layout_and_clear_removes_it(db: Database) -> None:
    customer_id, _ = new_customer(db)
    document_id = new_document(db, customer_id)
    store = PgPipelineStore(db)
    store.save(replace(content(customer_id, document_id), layout=page_layout(SPANS)))

    store.save(replace(content(customer_id, document_id), layout=page_layout([NOTE])))
    assert layout_rows(db, customer_id, document_id) == {
        "blocks": [(1, 0, "note", location_to_json(NOTE.location), None)],
        "cells": [],
    }

    store.save(replace(content(customer_id, document_id), layout=page_layout(SPANS)))
    store.clear(customer_id, document_id)
    assert layout_rows(db, customer_id, document_id) == {"blocks": [], "cells": []}


def test_a_document_with_no_layout_saves_as_before(db: Database) -> None:
    """A reader with no blocks or tables (a sheet, a document read before 0056) saves none."""
    customer_id, _ = new_customer(db)
    document_id = new_document(db, customer_id)
    PgPipelineStore(db).save(content(customer_id, document_id))
    assert content(customer_id, document_id).layout == Layout()
    assert layout_rows(db, customer_id, document_id) == {"blocks": [], "cells": []}
