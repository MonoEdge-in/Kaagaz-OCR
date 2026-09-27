"""PgPipelineStore against a real PostgreSQL: replace, never append; one transaction."""

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.chunking.piece import Piece
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.extraction.learned import LearnedCorrection
from kaagaz.extraction.values import ExtractedValue
from kaagaz.ingestion.ports import DocumentRecord
from kaagaz.pipeline import DocumentContent, PageRecord, ReaderOutput
from kaagaz.reading.spans import BoxLocation, CellLocation, PageInfo, Span
from tests.db_support import new_customer

pytestmark = pytest.mark.db

BOX = BoxLocation(1, 0.1, 0.4, 0.9, 0.42)


def new_document(db: Database, customer_id: str) -> str:
    document_id = str(uuid.uuid4())
    PgDocuments(db).add_if_absent(
        DocumentRecord(customer_id, document_id, uuid.uuid4().hex + uuid.uuid4().hex, 10, "pdf")
    )
    return document_id


def content(
    customer_id: str, document_id: str, *, total: str = "1,180.00", field: str = "total_amount"
) -> DocumentContent:
    line = Span(f"Total: {total}", f" Total: {total} ", BOX, 0.95)
    cell = Span("100", "100", CellLocation(1, 2, 1))
    return DocumentContent(
        customer_id=customer_id,
        document_id=document_id,
        pages=[
            PageRecord(PageInfo(1, 612.0, 792.0, "pt"), True),
            PageRecord(PageInfo(2, 612.0, 792.0, "pt"), False),
        ],
        outputs=[
            ReaderOutput("first", "pdfplumber", "0.11", (line, cell)),
            ReaderOutput("second", "pdfium", "5", (line,)),
        ],
        pieces=[
            Piece(customer_id, document_id, "Sharma Traders", (BOX,), "verified"),
            Piece(
                customer_id, document_id, f"Total: {total}", (BOX, BoxLocation(2, 0, 0, 1, 1)), "needs_review"
            ),
        ],
        values=[
            ExtractedValue(
                "document",
                field,
                total,
                total,
                BOX,
                0.95,
                "1,130.00",
                "needs_review",
                "readers_disagree",
                Decimal("1180.00"),
                None,
            ),
            ExtractedValue(
                "sheet:1:row:2",
                "invoice_date",
                "12/08/2026",
                "12/08/2026",
                CellLocation(1, 2, 1),
                label="verified",
                label_reason="direct_cell",
                value_date=date(2026, 8, 12),
            ),
        ],
        reader="pdfplumber",
    )


def rows(db: Database, customer_id: str, sql: str, document_id: str) -> list[dict[str, Any]]:
    with db.customer(customer_id) as conn:
        return [dict(r) for r in conn.execute(text(sql), {"d": document_id}).mappings().all()]


COUNTS = {
    "pages": text("SELECT count(*) FROM pages WHERE document_id = :d"),
    "reader_outputs": text("SELECT count(*) FROM reader_outputs WHERE document_id = :d"),
    "pieces": text("SELECT count(*) FROM pieces WHERE document_id = :d"),
    "extracted_values": text("SELECT count(*) FROM extracted_values WHERE document_id = :d"),
}


def counts(db: Database, customer_id: str, document_id: str) -> dict[str, int]:
    """Row counts per table for the document, as ``customer_id`` sees them."""
    with db.customer(customer_id) as conn:
        return {table: conn.execute(sql, {"d": document_id}).scalar_one() for table, sql in COUNTS.items()}


def test_save_writes_every_table(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    PgPipelineStore(db).save(content(customer, doc))

    assert counts(db, customer, doc) == {"pages": 2, "reader_outputs": 2, "pieces": 2, "extracted_values": 2}
    pages = rows(
        db,
        customer,
        "SELECT page_no, width, unit, has_image FROM pages WHERE document_id = :d ORDER BY page_no",
        doc,
    )
    assert pages == [
        {"page_no": 1, "width": 612.0, "unit": "pt", "has_image": True},
        {"page_no": 2, "width": 612.0, "unit": "pt", "has_image": False},
    ]
    pieces = rows(
        db,
        customer,
        "SELECT seq, page_from, page_to, text, regions, label FROM pieces"
        " WHERE document_id = :d ORDER BY seq",
        doc,
    )
    assert [(p["seq"], p["page_from"], p["page_to"], p["label"]) for p in pieces] == [
        (0, 1, 1, "verified"),
        (1, 1, 2, "needs_review"),
    ]
    assert pieces[1]["regions"] == [
        {"kind": "box", "page": 1, "box": [0.1, 0.4, 0.9, 0.42]},
        {"kind": "box", "page": 2, "box": [0, 0, 1, 1]},
    ]


def test_values_keep_page_region_numbers_dates_and_the_reader(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    PgPipelineStore(db).save(content(customer, doc))

    values = rows(
        db,
        customer,
        "SELECT record_key, field, value_text, value_raw, second_reading, value_number, value_date, page_no,"
        " region, reader, confidence, label, label_reason FROM extracted_values WHERE document_id = :d"
        " ORDER BY record_key",
        doc,
    )
    total, when = values
    assert total["region"] == {"kind": "box", "page": 1, "box": [0.1, 0.4, 0.9, 0.42]}
    assert (total["page_no"], total["reader"], total["value_number"]) == (1, "pdfplumber", Decimal("1180.00"))
    assert (total["second_reading"], total["label"], total["label_reason"]) == (
        "1,130.00",
        "needs_review",
        "readers_disagree",
    )
    assert total["confidence"] == pytest.approx(0.95)
    assert when["region"] == {"kind": "cell", "page": 1, "row": 2, "column": 1}
    assert (when["value_date"], when["value_number"], when["confidence"]) == (date(2026, 8, 12), None, None)


def test_reader_output_is_the_masked_span_list(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    PgPipelineStore(db).save(content(customer, doc))
    outputs = rows(
        db,
        customer,
        "SELECT role, reader, reader_version, output FROM reader_outputs"
        " WHERE document_id = :d ORDER BY role",
        doc,
    )
    first = outputs[0]
    assert (first["role"], first["reader"], first["reader_version"]) == ("first", "pdfplumber", "0.11")
    assert first["output"] == [
        {
            "text": "Total: 1,180.00",
            "raw": " Total: 1,180.00 ",
            "region": {"kind": "box", "page": 1, "box": [0.1, 0.4, 0.9, 0.42]},
            "confidence": 0.95,
        },
        {
            "text": "100",
            "raw": "100",
            "region": {"kind": "cell", "page": 1, "row": 2, "column": 1},
            "confidence": None,
        },
    ]
    assert [o["role"] for o in outputs] == ["first", "second"]
    assert json.dumps(first["output"])  # plain JSON all the way down


def test_saving_again_replaces_and_never_appends(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    store = PgPipelineStore(db)
    store.save(content(customer, doc))
    store.save(content(customer, doc, total="2,000.00"))

    assert counts(db, customer, doc) == {"pages": 2, "reader_outputs": 2, "pieces": 2, "extracted_values": 2}
    texts = rows(db, customer, "SELECT text FROM pieces WHERE document_id = :d ORDER BY seq", doc)
    assert [t["text"] for t in texts] == ["Sharma Traders", "Total: 2,000.00"]


def test_a_failing_save_keeps_the_earlier_rows(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    store = PgPipelineStore(db)
    store.save(content(customer, doc))

    with pytest.raises(IntegrityError):  # "Bad Field" breaks the field name check, last insert
        store.save(content(customer, doc, total="9.00", field="Bad Field"))

    assert counts(db, customer, doc) == {"pages": 2, "reader_outputs": 2, "pieces": 2, "extracted_values": 2}
    texts = rows(db, customer, "SELECT text FROM pieces WHERE document_id = :d ORDER BY seq", doc)
    assert texts[1]["text"] == "Total: 1,180.00"


def test_clear_removes_everything_for_the_document_only(db: Database) -> None:
    customer, _ = new_customer(db)
    doc, other = new_document(db, customer), new_document(db, customer)
    store = PgPipelineStore(db)
    store.save(content(customer, doc))
    store.save(content(customer, other))
    store.clear(customer, doc)
    assert counts(db, customer, doc) == {"pages": 0, "reader_outputs": 0, "pieces": 0, "extracted_values": 0}
    assert counts(db, customer, other)["pieces"] == 2


def test_saving_only_pages_is_allowed(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    empty = DocumentContent(
        customer, doc, [PageRecord(PageInfo(1, 800.0, 600.0, "px"), True)], [], [], [], ""
    )
    PgPipelineStore(db).save(empty)
    assert counts(db, customer, doc) == {"pages": 1, "reader_outputs": 0, "pieces": 0, "extracted_values": 0}


def test_load_learned_returns_this_customers_corrections_for_the_fields_asked(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO learned_corrections (customer_id, field, raw_value, corrected_value, created_by)"
                " VALUES (:c, :f, :raw, :fixed, :u)"
            ),
            [
                {"c": customer, "f": "total_amount", "raw": "1,13O", "fixed": "1,130", "u": user},
                {
                    "c": customer,
                    "f": "party_name",
                    "raw": "Sharma Tradres",
                    "fixed": "Sharma Traders",
                    "u": user,
                },
                {"c": customer, "f": "gstin", "raw": "x", "fixed": "y", "u": user},
            ],
        )
    store = PgPipelineStore(db)
    learned = store.load_learned(customer, {"total_amount", "party_name", "subtotal"})
    assert sorted(learned, key=lambda item: item.field) == [
        LearnedCorrection("party_name", "Sharma Tradres", "Sharma Traders"),
        LearnedCorrection("total_amount", "1,13O", "1,130"),
    ]
    assert store.load_learned(customer, set()) == []
