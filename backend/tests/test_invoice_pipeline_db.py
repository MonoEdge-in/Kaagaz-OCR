"""The invoice stage's rows saved through the pipeline store, on a real PostgreSQL
(PRD 2 E5 and K3, test-engineer amendment 2).

The textile invoice goes through the invoice stage with a fake model (as in
test_extraction_invoice.py), and ``PgPipelineStore.save`` writes its values, its
invoices row and its invoice_lines in the one save transaction.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.review import list_queue
from kaagaz.ingestion.ports import DocumentRecord
from kaagaz.pipeline import DocumentContent, PageRecord
from kaagaz.reading.spans import PageInfo
from tests.db_support import new_customer
from tests.exit_fixtures import SELLER_GSTIN, TEXTILE_FIELDS, QuotingChat, textile_lines, textile_spans
from tests.test_extraction_invoice import with_model

pytestmark = pytest.mark.db


def new_document(db: Database, customer_id: str) -> str:
    document_id = str(uuid.uuid4())
    PgDocuments(db).add_if_absent(
        DocumentRecord(customer_id, document_id, uuid.uuid4().hex + uuid.uuid4().hex, 10, "pdf")
    )
    return document_id


def save_textile(db: Database, customer_id: str, *, silk_amount: str = "3,000.00") -> str:
    """A new document holding the textile invoice (or its mismatched copy), saved."""
    document_id = new_document(db, customer_id)
    chat = QuotingChat(TEXTILE_FIELDS, textile_lines(silk_amount=silk_amount))
    result = with_model(textile_spans(silk_amount=silk_amount), chat)
    pages = [PageRecord(PageInfo(page, 612.0, 792.0, "pt"), False) for page in (1, 2, 3)]
    content = DocumentContent(
        customer_id, document_id, pages, [], [], result.values, "pdfplumber", invoice=result.invoice
    )
    PgPipelineStore(db).save(content)
    return document_id


def test_the_textile_invoice_row_and_its_lines_are_saved_and_read_back(db: Database) -> None:
    customer, _ = new_customer(db)
    document = save_textile(db, customer)
    ids = {"c": customer, "d": document}
    with db.customer(customer) as conn:
        invoice = dict(
            conn.execute(
                text(
                    "SELECT id, document_id, invoice_number, invoice_date, seller_name, seller_gstin,"
                    " buyer_name, buyer_gstin, subtotal, tax_total, total, currency, label, check_status,"
                    " check_reasons FROM invoices WHERE customer_id = :c AND document_id = :d"
                ),
                ids,
            )
            .mappings()
            .one()
        )
        lines = conn.execute(
            text(
                "SELECT invoice_id, document_id, line_no, description, hsn, quantity, unit, rate, amount,"
                " label FROM invoice_lines WHERE customer_id = :c AND document_id = :d ORDER BY line_no"
            ),
            ids,
        ).all()
        line_values = conn.execute(
            text(
                "SELECT count(*) FROM extracted_values"
                " WHERE customer_id = :c AND document_id = :d AND record_key LIKE 'line:%'"
            ),
            ids,
        ).scalar_one()
    invoice_id, invoice_document = invoice.pop("id"), invoice.pop("document_id")
    assert str(invoice_document) == document
    assert invoice == {
        "invoice_number": "TX-2026-118",
        "invoice_date": date(2026, 8, 12),
        "seller_name": "Shree Ganesh Textiles",
        "seller_gstin": SELLER_GSTIN,
        "buyer_name": "Patil Garments",
        "buyer_gstin": "27PQRST5678K1Z2",
        "subtotal": Decimal("10000.00"),
        "tax_total": None,
        "total": Decimal("11800.00"),
        "currency": "INR",
        "label": "verified",
        "check_status": "passed",
        "check_reasons": [],
    }
    # Each line hangs under this invoice and this document (AR13's one foreign key).
    assert {(row.invoice_id, str(row.document_id)) for row in lines} == {(invoice_id, document)}
    assert [tuple(row)[2:] for row in lines] == [
        (1, "Cotton Poplin", "5208", Decimal(120), "m", Decimal("25.00"), Decimal("3000.00"), "verified"),
        (2, "Rayon Print", "5407", Decimal(80), "m", Decimal("37.50"), Decimal("3000.00"), "verified"),
        (
            3,
            "Sub/Fabric Lining (cotton twill)",
            "5208",
            Decimal(50),
            "m",
            Decimal("20.00"),
            Decimal("1000.00"),
            "verified",
        ),
        (4, "Silk Blend", "5007", Decimal(20), "m", Decimal("150.00"), Decimal("3000.00"), "verified"),
    ]
    assert line_values == 24  # 4 lines of 6 fields: the lineage of every typed cell (E5)


def test_mismatched_copy_values_are_in_review_list_queue_with_check_failed(db: Database) -> None:
    customer, _ = new_customer(db)
    document = save_textile(db, customer, silk_amount="3,500.00")
    PgDocuments(db).mark_ready(customer, document, 3)
    with db.customer(customer) as conn:
        rows, _ = list_queue(conn, customer, limit=200, offset=0, document_id=document)
        check = conn.execute(
            text(
                "SELECT check_status, check_reasons, label FROM invoices"
                " WHERE customer_id = :c AND document_id = :d"
            ),
            {"c": customer, "d": document},
        ).one()
    assert tuple(check) == ("failed", ["lines_sum_mismatch"], "needs_review")
    failed = {(row["record_key"], row["field"]) for row in rows if row["label_reason"] == "check_failed"}
    assert failed == {
        ("line:1", "amount"),
        ("line:2", "amount"),
        ("line:3", "amount"),
        ("line:4", "amount"),
        ("document", "subtotal"),
    }
