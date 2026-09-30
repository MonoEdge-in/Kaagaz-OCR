"""The invoice stage in the real pipeline, on a real PostgreSQL (PRD 2 E1 to E5, K1 to K3).

The textile invoice is uploaded, scanned (a fake scanner), read by the real PDF
readers and understood by a fake model (``QuotingChat``: the card, then one
reply per invoice window), then saved by ``PgPipelineStore``: its values, its
invoices row and its invoice_lines, in the one save transaction.
"""

import functools
import io
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.review import list_queue
from kaagaz.db.runs import PgRuns
from kaagaz.extraction.invoice import WindowSettings
from kaagaz.extraction.understanding import ModelBudget
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.pipeline import DocumentProcessor
from kaagaz.preparing.pages import prepare_pages
from kaagaz.reading.wiring import build_reader_socket
from kaagaz.scanning.gate import ScanGate, Verdict
from tests.db_support import new_customer
from tests.exit_fixtures import (
    BUYER_GSTIN,
    SELLER_GSTIN,
    TEXTILE_FIELDS,
    QuotingChat,
    textile_invoice_pdf,
    textile_lines,
)
from tests.fakes import FakeScanner, MemoryPageImages, MemoryQueue, MemoryStorage, fake_sheet_pages
from tests.pdf_builder import Page, Text, make_pdf
from tests.test_extraction_card import reply as card_reply
from tests.test_pipeline import PIPELINE_POLICY
from tests.test_pipeline_db import moving_clock
from tests.test_pipeline_runs import SETTINGS, Timer

pytestmark = pytest.mark.db

BUDGET = ModelBudget(
    card_max_chars=12000,
    card_max_tokens=1024,
    num_ctx=8192,
    invoice_windows=WindowSettings(1700, 400, 10, 2048),
)
INVOICE_CARD = card_reply(
    doc_type="invoice",
    parties=[{"role": "seller", "name_quote": "Shree Ganesh Textiles"}],
    identifiers=[{"kind": "invoice_number", "value_quote": "TX-2026-118"}],
    title="A textile invoice",
    summary="A textile invoice from Shree Ganesh Textiles.",
)
ROW = (
    "SELECT invoice_number, invoice_date, seller_name, seller_gstin, buyer_name, buyer_gstin, subtotal,"
    " tax_total, total, currency, label, check_status, check_reasons FROM invoices"
    " WHERE customer_id = :c AND document_id = :d"
)
LINES = (
    "SELECT l.line_no, l.description, l.hsn, l.quantity, l.unit, l.rate, l.amount, l.label,"
    " l.document_id::text, l.invoice_id = i.id FROM invoice_lines l"
    " JOIN invoices i ON i.customer_id = l.customer_id AND i.id = l.invoice_id"
    " WHERE l.customer_id = :c AND l.document_id = :d ORDER BY l.line_no"
)


def processor(
    db: Database,
    storage: MemoryStorage,
    chat: QuotingChat | None,
    *,
    clock: Callable[[], datetime] = moving_clock,
    timezone: str = "Asia/Kolkata",
) -> DocumentProcessor:
    """The pipeline with the real PDF readers, runs and stores; the model stage when ``chat``."""
    documents = PgDocuments(db)
    prepare = functools.partial(
        prepare_pages, dpi=72, max_pages=10, max_image_pixels=10_000_000, blank_stddev=3.0, low_res_dpi=150
    )
    return DocumentProcessor(
        gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, documents),
        statuses=documents,
        documents=documents,
        socket=build_reader_socket(SETTINGS, None),
        prepare=prepare,
        sheet_pages=fake_sheet_pages,
        page_images=MemoryPageImages(),
        store=PgPipelineStore(db),
        runs=PgRuns(db),
        progress=documents,
        clock=clock,
        timer=Timer(),
        chat=chat,
        chat_model=None if chat is None else chat.chat_model,
        model_budget=None if chat is None else BUDGET,
        timezone=timezone,
    )


def read(db: Database, data: bytes, chat: QuotingChat | None, **options: Any) -> tuple[str, str]:
    """(customer, document) for ``data``, uploaded and read to the end."""
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    upload = UploadService(PIPELINE_POLICY, PgDocuments(db), storage, MemoryQueue())
    doc = upload.accept(customer, io.BytesIO(data)).document_id
    processor(db, storage, chat, **options)(Job(customer, doc), lambda: None)
    return customer, doc


def rows(db: Database, customer: str, sql: str, doc: str) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        return [tuple(row) for row in conn.execute(text(sql), {"c": customer, "d": doc}).all()]


def textile_chat(*, silk_amount: str = "3,000.00") -> QuotingChat:
    return QuotingChat(TEXTILE_FIELDS, textile_lines(silk_amount=silk_amount), card=INVOICE_CARD)


def line_row(
    no: int, description: str, hsn: str, qty: int, rate: str, amount: str, doc: str
) -> tuple[Any, ...]:
    """A saved line: needs review, because a text line's values share its box (the lead's A2
    decision, PROVISIONAL); under this invoice and this document."""
    return (
        no,
        description,
        hsn,
        Decimal(qty),
        "m",
        Decimal(rate),
        Decimal(amount),
        "needs_review",
        doc,
        True,
    )


def test_the_textile_invoice_is_read_into_its_row_and_lines(db: Database) -> None:
    customer, doc = read(db, textile_invoice_pdf(), textile_chat())

    assert rows(db, customer, ROW, doc) == [
        (
            "TX-2026-118",
            date(2026, 8, 12),
            "Shree Ganesh Textiles",
            SELLER_GSTIN,
            "Patil Garments",
            BUYER_GSTIN,
            Decimal("10000.00"),
            None,  # no total-tax line: CGST + SGST are added for the check only (ML 6(a))
            Decimal("11800.00"),
            "INR",
            # Needs review: the seller's name is printed on page 2 too, so its quote is ambiguous.
            "needs_review",
            "passed",
            [],
        )
    ]
    assert rows(db, customer, LINES, doc) == [
        line_row(1, "Cotton Poplin", "5208", 120, "25.00", "3000.00", doc),
        line_row(2, "Rayon Print", "5407", 80, "37.50", "3000.00", doc),
        line_row(3, "Sub/Fabric Lining (cotton twill)", "5208", 50, "20.00", "1000.00", doc),
        line_row(4, "Silk Blend", "5007", 20, "150.00", "3000.00", doc),
    ]
    document_reasons = dict(
        rows(
            db,
            customer,
            "SELECT field, label_reason FROM extracted_values"
            " WHERE document_id = :d AND record_key = 'document'",
            doc,
        )
    )
    # Resolution 1: verified where the readers agree, labelled.py agrees and the check passed.
    assert {document_reasons[field] for field in ("total_amount", "subtotal", "invoice_number")} == {
        "readers_agree"
    }
    assert document_reasons["party_name"] == "model_chosen"
    line_reasons = rows(
        db,
        customer,
        "SELECT DISTINCT label_reason FROM extracted_values"
        " WHERE document_id = :d AND record_key LIKE 'line:%'",
        doc,
    )
    assert line_reasons == [("model_chosen",)]
    runs = rows(
        db, customer, "SELECT model_calls, error_code FROM processing_runs WHERE document_id = :d", doc
    )
    assert runs == [(2, None)]  # the card and one invoice window


def test_mismatched_copy_values_are_in_review_list_queue_with_check_failed(db: Database) -> None:
    customer, doc = read(
        db, textile_invoice_pdf(silk_amount="3,500.00"), textile_chat(silk_amount="3,500.00")
    )

    checked = rows(
        db, customer, "SELECT check_status, check_reasons, label FROM invoices WHERE document_id = :d", doc
    )
    assert checked == [("failed", ["lines_sum_mismatch"], "needs_review")]
    with db.customer(customer) as conn:
        queued, _ = list_queue(conn, customer, limit=500, offset=0, document_id=doc)
    failed = {(row["record_key"], row["field"]) for row in queued if row["label_reason"] == "check_failed"}
    assert failed == {
        ("line:1", "amount"),
        ("line:2", "amount"),
        ("line:3", "amount"),
        ("line:4", "amount"),
        ("document", "subtotal"),
    }


def test_the_model_off_twin_builds_the_row_from_labelled_values(db: Database) -> None:
    """E5: with MODEL_ENABLED=false the row still comes, from the labelled.py fields, with no lines."""
    customer, doc = read(db, textile_invoice_pdf(), None)

    assert rows(db, customer, ROW, doc) == [
        (
            "TX-2026-118",
            date(2026, 8, 12),
            None,  # no seller label on this invoice
            None,  # two GSTINs in labelled.py's one field: which is the seller's is never guessed
            "Patil Garments",
            None,
            Decimal("10000.00"),
            None,  # the CGST line is a component, not the tax total
            Decimal("11800.00"),
            "INR",
            "verified",
            "passed",
            [],
        )
    ]
    assert rows(db, customer, LINES, doc) == []
    assert rows(db, customer, "SELECT doc_type, card FROM documents WHERE id = :d", doc) == [(None, None)]


def test_a_receipt_card_gets_no_invoice_row(db: Database) -> None:
    """fallback = doc_type in (None, "invoice") (PROVISIONAL): a card of another type has no row."""
    chat = QuotingChat({}, [], card=card_reply(doc_type="receipt"))
    customer, doc = read(db, textile_invoice_pdf(), chat)
    assert rows(db, customer, ROW, doc) == []
    assert len(chat.calls) == 1  # the card only


def dated_invoice(day: str) -> bytes:
    lines = ["Invoice No: A-1", f"Invoice Date: {day}", "Grand Total: 100.00"]
    return make_pdf([Page(texts=[Text(72, 740 - 24 * n, line) for n, line in enumerate(lines)])])


def test_today_is_the_worker_clock_in_the_time_zone(db: Database) -> None:
    """ML 6(j): at 20:00 UTC it is already 1 October in India, so 2 October is tomorrow there."""
    late = datetime(2026, 9, 30, 20, 0, tzinfo=UTC)
    reasons = "SELECT check_reasons FROM invoices WHERE document_id = :d"
    customer, doc = read(db, dated_invoice("02/10/2026"), None, clock=lambda: late, timezone="Asia/Kolkata")
    assert rows(db, customer, reasons, doc) == [([],)]
    customer, doc = read(db, dated_invoice("02/10/2026"), None, clock=lambda: late, timezone="UTC")
    assert rows(db, customer, reasons, doc) == [(["bad_date"],)]
