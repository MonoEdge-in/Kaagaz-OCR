"""PgPipelineStore against a real PostgreSQL: replace, never append; one transaction.

The concurrency tests force the order with ``wait_until_blocked``, never with sleeps.
"""

import json
import re
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from kaagaz.chunking.piece import Piece
from kaagaz.db import documents as documents_module
from kaagaz.db import pipeline_store
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.trash import lock_family
from kaagaz.extraction.card import read_card
from kaagaz.extraction.labels import label_quoted
from kaagaz.extraction.learned import LearnedCorrection
from kaagaz.extraction.quotes import reader_text
from kaagaz.extraction.understanding import document_links
from kaagaz.extraction.values import ExtractedValue
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import DocumentRecord
from kaagaz.pipeline import (
    DocumentContent,
    DocumentFacts,
    InvoiceLineRow,
    InvoiceRow,
    LinkIdentifier,
    PageRecord,
    ReaderOutput,
)
from kaagaz.reading.spans import BoxLocation, CellLocation, PageInfo, Span
from tests.db_support import insert_document, new_customer, wait_until_blocked
from tests.fakes import FakeChat
from tests.test_api_review import _named_database
from tests.test_extraction_card import fact as card_fact
from tests.test_extraction_card import line as card_line
from tests.test_extraction_card import reply as card_reply

pytestmark = pytest.mark.db

BOX = BoxLocation(1, 0.1, 0.4, 0.9, 0.42)


def new_document(db: Database, customer_id: str) -> str:
    document_id = str(uuid.uuid4())
    PgDocuments(db).add_if_absent(
        DocumentRecord(customer_id, document_id, uuid.uuid4().hex + uuid.uuid4().hex, 10, "pdf")
    )
    return document_id


def invoice(total: str) -> InvoiceRow:
    """An invoice with two lines; ``total`` as the document shows it ("1,180.00")."""
    cloth = InvoiceLineRow(
        1, "Cotton cloth", "5208", Decimal(10), "m", Decimal(100), Decimal(1000), "verified"
    )
    freight = InvoiceLineRow(2, None, None, None, None, None, Decimal(180), "needs_review")
    parties = ("Sharma Traders", "27AAPFU0939F1ZV", "Patil Stores", None)
    amounts = (Decimal(1000), Decimal(180), Decimal(total.replace(",", "")))
    checks = ("needs_review", "failed", ("total_mismatch",))
    return InvoiceRow("INV 7", date(2026, 8, 12), *parties, *amounts, *checks, lines=(cloth, freight))


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
        invoice=invoice(total),
    )


def numbered(customer_id: str, document_id: str, number: str = "INV 7") -> DocumentContent:
    """``content`` plus an invoice number value, with that number as its link identifier."""
    base = content(customer_id, document_id)
    value = ExtractedValue("document", "invoice_number", number, number, BOX)
    link = LinkIdentifier("invoice_number", number.replace(" ", "").lower())
    return replace(base, values=[*base.values, value], links=(link,))


def rows(db: Database, customer_id: str, sql: str, document_id: str) -> list[dict[str, Any]]:
    with db.customer(customer_id) as conn:
        return [dict(r) for r in conn.execute(text(sql), {"d": document_id}).mappings().all()]


COUNTS = {
    "pages": text("SELECT count(*) FROM pages WHERE document_id = :d"),
    "reader_outputs": text("SELECT count(*) FROM reader_outputs WHERE document_id = :d"),
    "pieces": text("SELECT count(*) FROM pieces WHERE document_id = :d"),
    "extracted_values": text("SELECT count(*) FROM extracted_values WHERE document_id = :d"),
    "invoices": text("SELECT count(*) FROM invoices WHERE document_id = :d"),
    "invoice_lines": text("SELECT count(*) FROM invoice_lines WHERE document_id = :d"),
    "document_links": text(
        "SELECT count(*) FROM document_links WHERE document_id = :d OR linked_document_id = :d"
    ),
    "processing_runs": text("SELECT count(*) FROM processing_runs WHERE document_id = :d"),
}
EMPTY = dict.fromkeys(COUNTS, 0)
FULL = {**EMPTY, "pages": 2, "reader_outputs": 2, "pieces": 2, "extracted_values": 2, "invoices": 1}
FULL["invoice_lines"] = 2


def counts(db: Database, customer_id: str, document_id: str) -> dict[str, int]:
    """Row counts per table for the document, as ``customer_id`` sees them."""
    with db.customer(customer_id) as conn:
        return {table: conn.execute(sql, {"d": document_id}).scalar_one() for table, sql in COUNTS.items()}


def test_save_writes_every_table(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    PgPipelineStore(db).save(content(customer, doc))

    assert counts(db, customer, doc) == FULL
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


def test_reader_output_is_the_span_list(db: Database) -> None:
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

    assert counts(db, customer, doc) == FULL
    texts = rows(db, customer, "SELECT text FROM pieces WHERE document_id = :d ORDER BY seq", doc)
    assert [t["text"] for t in texts] == ["Sharma Traders", "Total: 2,000.00"]


def test_a_failing_save_keeps_the_earlier_rows(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    store = PgPipelineStore(db)
    store.save(content(customer, doc))

    with pytest.raises(IntegrityError):  # "Bad Field" breaks the field name check, after most inserts
        store.save(content(customer, doc, total="9.00", field="Bad Field"))

    assert counts(db, customer, doc) == FULL
    texts = rows(db, customer, "SELECT text FROM pieces WHERE document_id = :d ORDER BY seq", doc)
    assert texts[1]["text"] == "Total: 1,180.00"


def test_a_verified_card_value_is_refused_before_anything_is_written(db: Database) -> None:
    """Council fix 4: the store is the last check that no card value is ever verified."""
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    base = content(customer, doc)
    card = ExtractedValue(
        "card", "profit", "12,000", "12,000", BOX, label="verified", label_reason="readers_agree"
    )
    with pytest.raises(ValueError, match="card"):
        PgPipelineStore(db).save(replace(base, values=[*base.values, card]))
    assert counts(db, customer, doc) == EMPTY


def test_clear_removes_everything_for_the_document_only(db: Database) -> None:
    customer, _ = new_customer(db)
    doc, other = new_document(db, customer), new_document(db, customer)
    store = PgPipelineStore(db)
    store.save(content(customer, doc))
    store.save(content(customer, other))
    store.clear(customer, doc)
    assert counts(db, customer, doc) == EMPTY
    assert counts(db, customer, other)["pieces"] == 2


def test_saving_only_pages_is_allowed(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    empty = DocumentContent(
        customer, doc, [PageRecord(PageInfo(1, 800.0, 600.0, "px"), True)], [], [], [], ""
    )
    PgPipelineStore(db).save(empty)
    assert counts(db, customer, doc) == {**EMPTY, "pages": 1}


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


# --- PRD 2 (WP2.1): routes, the card, invoices and links ----------------------

FACTS = DocumentFacts("invoice", ("en", "hi"), "A bill from Sharma.", date(2026, 8, 12), {"title": "Bill"})
FACT_COLUMNS = "SELECT doc_type, languages, summary, document_date, card FROM documents WHERE id = :d"
NO_FACTS: list[dict[str, Any]] = [
    {"doc_type": None, "languages": [], "summary": None, "document_date": None, "card": None}
]
LINKS = (
    "SELECT document_id::text, linked_document_id::text, identifier_kind, identifier_value"
    " FROM document_links WHERE document_id = :d OR linked_document_id = :d"
)


def links(db: Database, customer_id: str, document_id: str) -> set[tuple[Any, ...]]:
    """Every link row with ``document_id`` at either end, as ``customer_id`` sees them."""
    return {tuple(row.values()) for row in rows(db, customer_id, LINKS, document_id)}


def both_ways(first: str, second: str) -> set[tuple[Any, ...]]:
    return {(first, second, "invoice_number", "inv7"), (second, first, "invoice_number", "inv7")}


def read(db: Database, content: DocumentContent) -> None:
    """Save ``content`` and mark its document ready, as a run of the pipeline ends."""
    PgPipelineStore(db).save(content)
    PgDocuments(db).mark_ready(content.customer_id, content.document_id, 2)


def linked_pair(db: Database) -> tuple[str, str, str]:
    """A new customer with two documents that share an invoice number, read one after the
    other (so linked)."""
    customer, _ = new_customer(db)
    first, second = new_document(db, customer), new_document(db, customer)
    for doc in (first, second):
        read(db, numbered(customer, doc))
    return customer, first, second


def test_save_writes_routes_dpi_label_spots_context_and_latin(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    base = content(customer, doc)
    page = replace(base.pages[0], route="ocr", effective_dpi=150, low_resolution=True)
    piece = replace(base.pieces[0], context_header="Invoice | 2026-08-12", text_latin="sharma")
    value = replace(base.values[0], label_location=BoxLocation(1, 0.1, 0.38, 0.3, 0.4))
    PgPipelineStore(db).save(
        replace(
            base, pages=[page, base.pages[1]], pieces=[piece, base.pieces[1]], values=[value, base.values[1]]
        )
    )

    pages = "SELECT route, effective_dpi, low_resolution FROM pages WHERE document_id = :d ORDER BY page_no"
    assert rows(db, customer, pages, doc) == [
        {"route": "ocr", "effective_dpi": 150, "low_resolution": True},
        {"route": None, "effective_dpi": None, "low_resolution": False},
    ]
    pieces = "SELECT context_header, text_latin FROM pieces WHERE document_id = :d ORDER BY seq"
    assert rows(db, customer, pieces, doc) == [
        {"context_header": "Invoice | 2026-08-12", "text_latin": "sharma"},
        {"context_header": "", "text_latin": None},
    ]
    spots = (
        "SELECT label_page_no, label_region FROM extracted_values WHERE document_id = :d ORDER BY record_key"
    )
    assert rows(db, customer, spots, doc) == [
        {"label_page_no": 1, "label_region": {"kind": "box", "page": 1, "box": [0.1, 0.38, 0.3, 0.4]}},
        {"label_page_no": None, "label_region": None},
    ]


def test_save_writes_the_card_columns_and_clears_them_on_the_next_save(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    store = PgPipelineStore(db)
    store.save(replace(content(customer, doc), facts=FACTS))

    assert rows(db, customer, FACT_COLUMNS, doc) == [
        {
            "doc_type": "invoice",
            "languages": ["en", "hi"],
            "summary": "A bill from Sharma.",
            "document_date": date(2026, 8, 12),
            "card": {"title": "Bill"},
        }
    ]
    store.save(content(customer, doc))  # a run with the model off has no card
    assert rows(db, customer, FACT_COLUMNS, doc) == NO_FACTS


def test_save_writes_the_invoice_and_its_lines_and_replaces_them(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    store = PgPipelineStore(db)
    store.save(content(customer, doc))
    store.save(content(customer, doc, total="1,190.00"))

    assert counts(db, customer, doc) == FULL
    [row] = rows(
        db,
        customer,
        "SELECT id, invoice_number, invoice_date, seller_name, seller_gstin, buyer_name, buyer_gstin,"
        " subtotal, tax_total, total, currency, label, check_status, check_reasons"
        " FROM invoices WHERE document_id = :d",
        doc,
    )
    invoice_id = row.pop("id")
    assert row == {
        "invoice_number": "INV 7",
        "invoice_date": date(2026, 8, 12),
        "seller_name": "Sharma Traders",
        "seller_gstin": "27AAPFU0939F1ZV",
        "buyer_name": "Patil Stores",
        "buyer_gstin": None,
        "subtotal": Decimal("1000.00"),
        "tax_total": Decimal("180.00"),
        "total": Decimal("1190.00"),
        "currency": "INR",
        "label": "needs_review",
        "check_status": "failed",
        "check_reasons": ["total_mismatch"],
    }
    lines = rows(
        db,
        customer,
        "SELECT invoice_id, line_no, description, hsn, quantity, unit, rate, amount, label"
        " FROM invoice_lines WHERE document_id = :d ORDER BY line_no",
        doc,
    )
    assert [tuple(line.values()) for line in lines] == [
        (invoice_id, 1, "Cotton cloth", "5208", Decimal(10), "m", Decimal(100), Decimal(1000), "verified"),
        (invoice_id, 2, None, None, None, None, None, Decimal(180), "needs_review"),
    ]


@pytest.mark.parametrize("second_first", [False, True])
def test_links_are_written_both_ways_whichever_is_read_first(db: Database, second_first: bool) -> None:
    customer, _ = new_customer(db)
    first, second = new_document(db, customer), new_document(db, customer)
    for doc in (second, first) if second_first else (first, second):
        read(db, numbered(customer, doc))

    assert links(db, customer, first) == both_ways(first, second)
    PgPipelineStore(db).save(numbered(customer, first))  # replaced, never doubled
    assert links(db, customer, first) == both_ways(first, second)


def test_links_go_only_to_current_ready_documents(db: Database) -> None:
    """A link target is current, out of Trash and ready (LIVE_CURRENT): never a document
    still being read, a failed one, or an older version of its family."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        target = insert_document(conn, customer)
        failed = insert_document(conn, customer, status="failed")
        older = insert_document(conn, customer, is_current=False)
        insert_document(conn, customer, family_id=older, version_no=2)  # its family's current version
    reading = new_document(db, customer)
    store = PgPipelineStore(db)
    for doc in (target, failed, older, reading):
        store.save(numbered(customer, doc))
    doc = new_document(db, customer)
    store.save(numbered(customer, doc))

    assert links(db, customer, doc) == {
        (doc, target, "invoice_number", "inv7"),
        (target, doc, "invoice_number", "inv7"),
    }


def test_a_save_with_no_links_removes_links_both_ways(db: Database) -> None:
    customer, first, second = linked_pair(db)
    PgPipelineStore(db).save(content(customer, second))
    assert links(db, customer, first) == set()


def test_reverse_link_goes_when_the_target_changes_its_number(db: Database) -> None:
    customer, first, second = linked_pair(db)
    PgPipelineStore(db).save(numbered(customer, second, "INV 8"))
    assert links(db, customer, first) == set()
    assert links(db, customer, second) == set()


def test_no_link_to_a_trashed_or_same_family_document(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        trashed = insert_document(conn, customer, deleted_at=datetime(2026, 9, 29, tzinfo=UTC))
        older = insert_document(conn, customer)
        newer = insert_document(conn, customer, family_id=older, version_no=2, is_current=False)
    store = PgPipelineStore(db)
    for doc in (trashed, older, newer):
        store.save(numbered(customer, doc))

    assert links(db, customer, older) == set()
    assert links(db, customer, trashed) == set()


def test_clear_removes_card_invoice_and_links(db: Database) -> None:
    customer, first, second = linked_pair(db)
    store = PgPipelineStore(db)
    store.save(replace(numbered(customer, first), facts=FACTS))
    store.clear(customer, first)

    assert counts(db, customer, first) == EMPTY
    assert rows(db, customer, FACT_COLUMNS, first) == NO_FACTS
    assert links(db, customer, second) == set()


def new_session() -> str:
    """A session name no other test, and no other database on the server, uses."""
    return f"kaagaz-{uuid.uuid4().hex[:16]}"


def test_save_waits_for_the_family_lock(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    session = new_session()
    named = _named_database(session)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with db.customer(customer) as conn:
                lock_family(conn, customer, doc)
                saving = pool.submit(PgPipelineStore(named).save, content(customer, doc))
                wait_until_blocked(db, application_name=session)
                assert counts(db, customer, doc) == EMPTY
            saving.result(timeout=10)
    finally:
        named.engine.dispose()
    assert counts(db, customer, doc) == FULL


def test_links_two_saves_complete_without_deadlock(db: Database) -> None:
    """Both saves wait on the customer's link lock, then run one after the other. The
    key is built from the canonical id, so a customer id in capitals takes the same lock."""
    customer, first, second = linked_pair(db)
    sessions = [new_session(), new_session()]
    named = [_named_database(session) for session in sessions]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            with db.customer(customer) as conn:
                lock = "SELECT pg_advisory_xact_lock(hashtextextended('kaagaz.document_links:'"
                lock += " || CAST(:c AS uuid)::text, 0))"
                conn.execute(text(lock), {"c": customer})
                saving = [
                    pool.submit(PgPipelineStore(database).save, numbered(who, doc))
                    for database, who, doc in zip(
                        named, (customer.upper(), customer), (first, second), strict=True
                    )
                ]
                for session in sessions:
                    wait_until_blocked(db, application_name=session)
            for future in saving:
                future.result(timeout=10)
    finally:
        for database in named:
            database.engine.dispose()
    assert links(db, customer, first) == both_ways(first, second)


def test_save_skips_a_target_deleted_before_its_lock(db: Database) -> None:
    customer, _ = new_customer(db)
    target, doc = new_document(db, customer), new_document(db, customer)
    read(db, numbered(customer, target))
    session = new_session()
    named = _named_database(session)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with db.customer(customer) as conn:
                delete = text("DELETE FROM documents WHERE customer_id = :c AND id = :d")
                conn.execute(delete, {"c": customer, "d": target})
                saving = pool.submit(PgPipelineStore(named).save, numbered(customer, doc))
                wait_until_blocked(db, application_name=session)  # on the target's lock
            saving.result(timeout=10)
    finally:
        named.engine.dispose()
    assert links(db, customer, doc) == set()
    assert counts(db, customer, doc)["pieces"] == 2


@pytest.mark.parametrize("step", ["save", "clear"])
def test_links_wait_for_an_old_partner_being_deleted_before_any_link_is_touched(
    db: Database, step: str
) -> None:
    """A delete forever or a purge of B locks B FOR UPDATE, then its cascade deletes
    B's link rows. A's save or clear locks B FOR KEY SHARE before it touches a link
    row, so it waits for that delete instead of holding rows the cascade needs."""
    customer, first, second = linked_pair(db)
    session = new_session()
    named = _named_database(session)
    store = PgPipelineStore(named)
    steps = {
        "save": lambda: store.save(content(customer, first)),
        "clear": lambda: store.clear(customer, first),
    }
    ids = {"c": customer, "d": second}
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with db.customer(customer) as conn:
                conn.execute(
                    text("SELECT id FROM documents WHERE customer_id = :c AND id = :d FOR UPDATE"), ids
                )
                replacing = pool.submit(steps[step])
                wait_until_blocked(db, application_name=session)
                conn.execute(text("DELETE FROM documents WHERE customer_id = :c AND id = :d"), ids)
            replacing.result(timeout=10)
    finally:
        named.engine.dispose()
    assert links(db, customer, first) == set()


def fail_first(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, times: int, state: str = "40P01"
) -> list[str]:
    """Make ``module.lock_family`` fail its first ``times`` calls with a real error from
    the server with SQLSTATE ``state`` (40P01: PostgreSQL broke a deadlock). Returns the calls."""
    assert re.fullmatch(r"[0-9A-Z]{5}", state)
    raising = text(f"DO $$ BEGIN RAISE EXCEPTION 'failing' USING ERRCODE = '{state}'; END $$")
    calls: list[str] = []
    real: Callable[[Connection, str, str], str | None] = module.lock_family

    def failing(conn: Connection, customer_id: str, document_id: str) -> str | None:
        calls.append(document_id)
        if len(calls) <= times:
            conn.execute(raising)
        return real(conn, customer_id, document_id)

    monkeypatch.setattr(module, "lock_family", failing)
    return calls


@pytest.mark.parametrize("step", ["save", "clear", "mark_ready"])
def test_a_deadlock_is_retried_and_the_step_succeeds(
    db: Database, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    store = PgPipelineStore(db)
    store.save(content(customer, doc))
    calls = fail_first(monkeypatch, documents_module if step == "mark_ready" else pipeline_store, 1)
    steps = {
        "save": lambda: store.save(content(customer, doc, total="2,000.00")),
        "clear": lambda: store.clear(customer, doc),
        "mark_ready": lambda: PgDocuments(db).mark_ready(customer, doc, 2),
    }
    steps[step]()

    assert len(calls) == 2
    totals = rows(db, customer, "SELECT total FROM invoices WHERE document_id = :d", doc)
    status = rows(db, customer, "SELECT status FROM documents WHERE id = :d", doc)[0]["status"]
    assert (totals, status) == {
        "save": ([{"total": Decimal(2000)}], "received"),
        "clear": ([], "received"),
        "mark_ready": ([{"total": Decimal(1180)}], "ready"),
    }[step]


def test_a_deadlock_on_every_try_is_an_outage_after_four_tries(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    calls = fail_first(monkeypatch, pipeline_store, 99)

    with pytest.raises(BackendUnavailable):
        PgPipelineStore(db).save(content(customer, doc))

    assert len(calls) == 4  # the first try and 3 retries (AR7)
    assert counts(db, customer, doc) == EMPTY


def test_only_a_deadlock_is_retried(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """55P03 (a lock that could not be taken) is an outage at once: one try, no retry."""
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    calls = fail_first(monkeypatch, pipeline_store, 99, state="55P03")

    with pytest.raises(BackendUnavailable):
        PgPipelineStore(db).save(content(customer, doc))

    assert len(calls) == 1
    assert counts(db, customer, doc) == EMPTY


# --- PRD 2 (WP2.5): the card through the store ----------------------------------


def card_content(
    customer_id: str, document_id: str, spans: list[Span], answer: dict[str, Any]
) -> DocumentContent:
    """What a card run saves: the card's facts, its values labelled, and its links
    (read_card, label_quoted and document_links, as the understanding stage does)."""
    reader = reader_text(spans)
    result = read_card(
        FakeChat(answer), reader, max_chars=12000, max_tokens=1024, num_ctx=8192, run_id=str(uuid.uuid4())
    )
    assert result is not None
    values = label_quoted(result.values, spans)
    return DocumentContent(
        customer_id=customer_id,
        document_id=document_id,
        pages=[PageRecord(PageInfo(1, 612.0, 792.0, "pt"), True)],
        outputs=[ReaderOutput("first", "pdfplumber", "0.11", tuple(spans))],
        pieces=[],
        values=values,
        reader="pdfplumber",
        facts=result.facts,
        links=document_links(result.facts, values),
    )


def test_worst_case_card_output_saves(db: Database) -> None:
    """Test-engineer amendment 2: a 700-character summary with no space, more than 20 key
    facts with one repeated name, 20 Devanagari owners at the quote cap, 31 identifiers
    (one of 250 characters) and Devanagari labels. Nothing reaches a CHECK."""
    names = [("पाटील " * 46) + f"{n:02d}" for n in range(1, 21)]
    numbers = ["A" * 250] + [f"REF-{n:02d}" for n in range(1, 31)]
    labels = [("नफा " * 70) + f"{n:02d}" for n in range(1, 26)]
    amounts = [("रुपये " * 46) + f"{n:02d}" for n in range(1, 26)]
    spans = [card_line(text, y=0.1) for text in names + numbers]
    for label, amount in zip(labels, amounts, strict=True):
        spans += [card_line(label, y=0.5), card_line(amount, y=0.6)]
    answer = card_reply(
        languages=["mr", "hi", "mr"],
        parties=[{"role": "owner", "name_quote": name} for name in names],
        identifiers=[{"kind": "reference_number", "value_quote": number} for number in numbers],
        key_facts=[card_fact("profit", amount, label) for label, amount in zip(labels, amounts, strict=True)],
        title="A land record",
        summary="a" * 700,
    )
    customer, _ = new_customer(db)
    doc = new_document(db, customer)
    saved = card_content(customer, doc, spans, answer)
    PgPipelineStore(db).save(saved)

    [row] = rows(
        db,
        customer,
        "SELECT length(summary) AS summary, octet_length(card::text) AS card_bytes, languages"
        " FROM documents WHERE id = :d",
        doc,
    )
    assert (row["summary"], row["languages"]) == (600, ["hi", "mr"])
    assert row["card_bytes"] <= 65536
    values = rows(
        db,
        customer,
        "SELECT record_key, field, label, label_reason, label_page_no FROM extracted_values"
        " WHERE document_id = :d",
        doc,
    )
    assert len(values) == len(saved.values) > 40
    assert {(v["record_key"], v["label"], v["label_reason"]) for v in values} == {
        ("card", "needs_review", "model_chosen")
    }
    card = saved.facts.card
    assert card is not None
    named = [v["field"] for v in values if v["field"] != "reference_number"]  # identifiers keep their kind
    assert len(set(named)) == len(named) == len(card["parties"]) + len(card["key_facts"])
    assert {"owner_name_20", "profit_2"} <= set(named)  # repeated owners and profits are numbered
    assert all(v["label_page_no"] == 1 for v in values if v["field"].startswith("profit"))
    assert len(saved.links) == 29  # the 250-character one is dropped, the 31st is past the cap


def test_links_from_card_identifiers_end_to_end(db: Database) -> None:
    """D5: two documents whose cards hold the same challan number, written differently, link
    both ways; the GSTIN they share never links them."""
    customer, _ = new_customer(db)
    first, second = new_document(db, customer), new_document(db, customer)
    gstin = {"kind": "gstin", "value_quote": "27AAPFU0939F1ZV"}
    read(
        db,
        card_content(
            customer,
            first,
            [card_line("Challan No: CH 4417 GSTIN 27AAPFU0939F1ZV", y=0.1)],
            card_reply(identifiers=[{"kind": "challan_number", "value_quote": "CH 4417"}, gstin]),
        ),
    )
    read(
        db,
        card_content(
            customer,
            second,
            [card_line("Against challan ch4417, GSTIN 27AAPFU0939F1ZV", y=0.1)],
            card_reply(identifiers=[{"kind": "challan_number", "value_quote": "ch4417"}, gstin]),
        ),
    )

    assert links(db, customer, first) == {
        (first, second, "challan_number", "ch4417"),
        (second, first, "challan_number", "ch4417"),
    }


def test_devanagari_and_ascii_digits_of_one_number_link(db: Database) -> None:
    """Council fix 10: the store's SQL folds Devanagari digits the way normalise_identifier
    does, so a survey number read as ४५/२ on one record and 45/2 on another links, in
    either order."""
    customer, _ = new_customer(db)
    first, second = new_document(db, customer), new_document(db, customer)
    for doc, printed in ((first, "गट नं. ४५/२"), (second, "Gat No. 45/2")):
        number = printed.split()[-1]
        answer = card_reply(identifiers=[{"kind": "survey_number", "value_quote": number}])
        read(db, card_content(customer, doc, [card_line(printed, y=0.1)], answer))

    assert links(db, customer, first) == {
        (first, second, "survey_number", "45/2"),
        (second, first, "survey_number", "45/2"),
    }
