"""Customer isolation for the document card (PRD 2 D3, D5).

The same invoice number on two customers' cards never links them; B can never
read A's card or card values, and B saving a card under A's document id is
refused and leaves A's card as it was.
"""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from tests.db_support import new_customer
from tests.test_api_support import Api
from tests.test_extraction_card import line, reply
from tests.test_pipeline_store_db import FACT_COLUMNS, card_content, links, new_document, rows

pytestmark = pytest.mark.db

SPANS = [line("Sharma Traders"), line("Invoice No: INV 17", y=0.2)]
CARD = reply(
    parties=[{"role": "seller", "name_quote": "Sharma Traders"}],
    identifiers=[{"kind": "invoice_number", "value_quote": "INV 17"}],
    summary="An invoice from Sharma Traders.",
)
CARD_VALUES = "SELECT field, value_text FROM extracted_values WHERE document_id = :d AND record_key = 'card'"
ALL_LINKS = text(
    "SELECT count(*) FROM document_links WHERE document_id = ANY(CAST(:ids AS uuid[]))"
    " OR linked_document_id = ANY(CAST(:ids AS uuid[]))"
)


def test_the_same_card_invoice_number_at_two_customers_never_links(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    a_doc, b_doc = new_document(db, a), new_document(db, b)
    store = PgPipelineStore(db)
    store.save(card_content(a, a_doc, SPANS, CARD))
    store.save(card_content(b, b_doc, SPANS, CARD))

    assert card_content(b, b_doc, SPANS, CARD).links  # B's card does carry the number
    assert links(db, a, a_doc) == set()
    assert links(db, b, b_doc) == set()
    with db.system() as conn:
        assert conn.execute(ALL_LINKS, {"ids": [a_doc, b_doc]}).scalar_one() == 0


def test_b_can_never_read_a_card_or_its_values(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    a_doc = new_document(db, a)
    PgPipelineStore(db).save(card_content(a, a_doc, SPANS, CARD))

    assert len(rows(db, a, CARD_VALUES, a_doc)) == 2
    assert rows(db, b, FACT_COLUMNS, a_doc) == []
    assert rows(db, b, CARD_VALUES, a_doc) == []


def test_b_saving_a_card_under_a_document_id_is_refused_and_a_keeps_its_card(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    a_doc = new_document(db, a)
    store = PgPipelineStore(db)
    store.save(card_content(a, a_doc, SPANS, CARD))
    before = (rows(db, a, FACT_COLUMNS, a_doc), rows(db, a, CARD_VALUES, a_doc))

    other = reply(
        doc_type="receipt", summary="Something else.", parties=[{"role": "buyer", "name_quote": "Sharma"}]
    )
    with pytest.raises(IntegrityError):
        store.save(card_content(b, a_doc, SPANS, other))

    assert (rows(db, a, FACT_COLUMNS, a_doc), rows(db, a, CARD_VALUES, a_doc)) == before
    assert before[0][0]["doc_type"] == "invoice"


def test_b_never_sees_a_card_or_summary_through_the_api(db: Database) -> None:
    """B uses A's document id on the detail and gets 404; neither the detail nor B's list
    ever carries A's card, summary or type."""
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    doc = api.add_document(a.customer_id, name="bill.pdf")
    api.add_page(a.customer_id, doc)
    card = {"version": 1, "doc_type": "invoice", "title": "Zenith secret bill", "parties": []}
    with db.customer(a.customer_id) as conn:
        conn.execute(
            text(
                "UPDATE documents SET doc_type = 'invoice', summary = 'Zenith secret summary.',"
                " card = CAST(:card AS jsonb) WHERE customer_id = :c AND id = :d"
            ),
            {"c": a.customer_id, "d": doc, "card": json.dumps(card)},
        )
    assert a.client.get(f"/api/documents/{doc}").json()["card"] == card

    detail = b.client.get(f"/api/documents/{doc}")
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "not_found"
    listed = b.client.get("/api/documents")
    assert listed.status_code == 200
    for reply_text in (detail.text, listed.text):
        assert "Zenith" not in reply_text
        assert doc not in reply_text
        assert "invoice" not in reply_text
