"""Customer isolation for invoices (PRD 2 E5), at the store level.

Customer B can never read, write or link to customer A's invoice, its lines or
their values, even with A's exact ids, in the store or through the API detail's
``invoice`` (``listing.invoice_for``).
"""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.pipeline import DocumentContent, LinkIdentifier, PageRecord
from kaagaz.reading.spans import PageInfo
from tests.db_support import new_customer
from tests.exit_fixtures import TEXTILE_FIELDS, QuotingChat, textile_lines
from tests.test_api_support import Api
from tests.test_extraction_invoice import textile
from tests.test_pipeline_store_db import links, new_document, rows

pytestmark = pytest.mark.db

NUMBER = LinkIdentifier("invoice_number", "tx-2026-118")
INVOICE = "SELECT id, invoice_number, total, label, check_status FROM invoices WHERE document_id = :d"
LINES = (
    "SELECT invoice_id, line_no, description, amount FROM invoice_lines WHERE document_id = :d"
    " ORDER BY line_no"
)
LINE_VALUES = (
    "SELECT field, value_text FROM extracted_values WHERE document_id = :d AND record_key LIKE 'line:%'"
)
_INSERT_LINE = text(
    "INSERT INTO invoice_lines (customer_id, invoice_id, document_id, line_no, label)"
    " VALUES (:who, :invoice, :d, 99, 'verified')"
)


def textile_content(customer_id: str, document_id: str, *, silk_amount: str = "3,000.00") -> DocumentContent:
    """The textile invoice as the invoice stage leaves it, with its invoice number to link by."""
    result = textile(
        QuotingChat(TEXTILE_FIELDS, textile_lines(silk_amount=silk_amount)), silk_amount=silk_amount
    )
    pages = [PageRecord(PageInfo(page, 612.0, 792.0, "pt"), False) for page in (1, 2, 3)]
    return DocumentContent(
        customer_id,
        document_id,
        pages,
        [],
        [],
        result.values,
        "pdfplumber",
        invoice=result.invoice,
        links=(NUMBER,),
    )


def a_with_an_invoice(db: Database) -> tuple[str, str, str, dict[str, Any]]:
    """(customer A, customer B, A's document, A's invoice row)."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    a_doc = new_document(db, a)
    PgPipelineStore(db).save(textile_content(a, a_doc))
    (invoice,) = rows(db, a, INVOICE, a_doc)
    return a, b, a_doc, invoice


def a_sees(db: Database, a: str, a_doc: str) -> tuple[Any, ...]:
    return rows(db, a, INVOICE, a_doc), rows(db, a, LINES, a_doc), rows(db, a, LINE_VALUES, a_doc)


def test_b_can_never_read_a_invoice_its_lines_or_their_values(db: Database) -> None:
    a, b, a_doc, invoice = a_with_an_invoice(db)
    invoice_rows, line_rows, line_values = a_sees(db, a, a_doc)
    assert (len(invoice_rows), len(line_rows), len(line_values)) == (1, 4, 24)

    assert a_sees(db, b, a_doc) == ([], [], [])
    with db.customer(b) as conn:
        by_id: int = conn.execute(
            text("SELECT count(*) FROM invoice_lines WHERE invoice_id = :i"), {"i": invoice["id"]}
        ).scalar_one()
        assert by_id == 0
        assert (
            conn.execute(
                text("SELECT count(*) FROM invoices WHERE id = :i"), {"i": invoice["id"]}
            ).scalar_one()
            == 0
        )


def test_b_saving_an_invoice_under_a_document_id_is_refused_and_a_keeps_its_invoice(db: Database) -> None:
    a, b, a_doc, _ = a_with_an_invoice(db)
    before = a_sees(db, a, a_doc)
    store = PgPipelineStore(db)

    with pytest.raises(IntegrityError):  # the (customer, document) foreign key
        store.save(textile_content(b, a_doc, silk_amount="3,500.00"))
    store.clear(b, a_doc)

    assert a_sees(db, a, a_doc) == before
    assert before[0][0]["check_status"] == "passed"


def test_b_adding_a_line_to_a_invoice_is_refused(db: Database) -> None:
    a, b, a_doc, invoice = a_with_an_invoice(db)
    before = a_sees(db, a, a_doc)
    params = {"invoice": invoice["id"], "d": a_doc}

    # As B, a line in A's name is refused by the policy (42501)...
    with pytest.raises(DBAPIError) as refused, db.customer(b) as conn:
        conn.execute(_INSERT_LINE, {**params, "who": a})
    assert getattr(refused.value.orig, "sqlstate", None) == "42501"
    # ...and a line in B's own name under A's invoice by the composite foreign key (23503).
    with pytest.raises(IntegrityError) as missing, db.customer(b) as conn:
        conn.execute(_INSERT_LINE, {**params, "who": b})
    assert getattr(missing.value.orig, "sqlstate", None) == "23503"

    assert a_sees(db, a, a_doc) == before


def test_the_same_invoice_number_at_b_never_links_to_a(db: Database) -> None:
    a, b, a_doc, _ = a_with_an_invoice(db)
    a_copy, b_doc = new_document(db, a), new_document(db, b)
    store = PgPipelineStore(db)
    store.save(textile_content(a, a_copy))
    store.save(textile_content(b, b_doc))

    assert links(db, a, a_doc) == {
        (a_doc, a_copy, "invoice_number", "tx-2026-118"),
        (a_copy, a_doc, "invoice_number", "tx-2026-118"),
    }
    assert links(db, b, b_doc) == set()
    assert links(db, b, a_doc) == set()


def test_b_never_sees_a_invoice_through_the_api(db: Database) -> None:
    """B uses A's document id on the detail and gets 404; B's own detail never carries A's invoice."""
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    a_doc = api.add_document(a.customer_id, name="bill.pdf")
    b_doc = api.add_document(b.customer_id, name="own.pdf")
    api.add_page(a.customer_id, a_doc)
    api.add_page(b.customer_id, b_doc)
    with db.customer(a.customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO invoices (customer_id, document_id, invoice_number, total, label, check_status)"
                " VALUES (:c, :d, 'ZENITH-7', 999, 'verified', 'passed')"
            ),
            {"c": a.customer_id, "d": a_doc},
        )
    assert a.client.get(f"/api/documents/{a_doc}").json()["invoice"]["invoice_number"] == "ZENITH-7"

    detail = b.client.get(f"/api/documents/{a_doc}")
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "not_found"
    own = b.client.get(f"/api/documents/{b_doc}")
    assert own.status_code == 200 and own.json()["invoice"] is None
    for reply_text in (detail.text, own.text):
        assert "ZENITH" not in reply_text
