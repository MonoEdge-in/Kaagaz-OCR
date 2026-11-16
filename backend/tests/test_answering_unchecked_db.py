"""The documents whose card holds an unchecked figure (answering/unchecked.py), in
PostgreSQL: only this customer's current, ready documents out of Trash, and only
unchecked card values of the fields asked for."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.answering.unchecked import MAX_DOCUMENTS, PgUncheckedValues
from kaagaz.db.engine import Database
from tests.db_support import insert_document, new_customer

pytestmark = pytest.mark.db

REGION = json.dumps({"kind": "box", "page": 1, "box": [0.1, 0.1, 0.3, 0.12]})
_VALUE = text(
    "INSERT INTO extracted_values (id, customer_id, document_id, record_key, field, value_text,"
    " value_raw, page_no, region, reader, label, label_reason)"
    " VALUES (:id, :c, :d, :key, :field, '12000', '12000', 1, CAST(:region AS jsonb), 'test',"
    " :label, :reason)"
)


def _document(db: Database, customer: str, name: str, **columns: object) -> str:
    with db.customer(customer) as conn:
        return insert_document(conn, customer, display_name=name, **columns)


def _value(
    db: Database,
    customer: str,
    document: str,
    *,
    field: str = "profit",
    key: str = "card",
    label: str = "needs_review",
    reason: str = "model_chosen",
) -> None:
    params = {
        "id": str(uuid.uuid4()),
        "c": customer,
        "d": document,
        "key": key,
        "field": field,
        "region": REGION,
        "label": label,
        "reason": reason,
    }
    with db.customer(customer) as conn:
        conn.execute(_VALUE, params)


def test_only_unchecked_card_values_of_current_ready_live_documents_count(db: Database) -> None:
    customer, user = new_customer(db)
    report = _document(db, customer, "Sept report.pdf")
    _value(db, customer, report)
    reading = _document(db, customer, "reading.pdf", status="clean")
    _value(db, customer, reading)
    binned = _document(db, customer, "binned.pdf", deleted_at=datetime(2026, 9, 29, tzinfo=UTC))
    _value(db, customer, binned)
    checked = _document(db, customer, "register.xlsx")
    _value(db, customer, checked, key="document", label="verified", reason="direct_cell")
    other_field = _document(db, customer, "sales.pdf")
    _value(db, customer, other_field, field="sales")

    found = PgUncheckedValues(db).documents_with(customer, ["profit", "net_profit"], user_id=user)

    assert found == [{"id": report, "display_name": "Sept report.pdf"}]
    assert PgUncheckedValues(db).documents_with(customer, [], user_id=user) == []


@pytest.mark.isolation
def test_another_customers_cards_are_never_seen(db: Database) -> None:
    a, a_user = new_customer(db, name="Sharma Traders")
    b, b_user = new_customer(db, name="Mehta Textiles")
    report = _document(db, a, "sharma-report.pdf")
    _value(db, a, report)

    assert PgUncheckedValues(db).documents_with(b, ["profit"], user_id=b_user) == []
    assert [doc["id"] for doc in PgUncheckedValues(db).documents_with(a, ["profit"], user_id=a_user)] == [
        report
    ]


def test_at_most_a_few_documents_come_back(db: Database) -> None:
    customer, user = new_customer(db)
    for number in range(MAX_DOCUMENTS + 2):
        _value(db, customer, _document(db, customer, f"report-{number}.pdf"))
    assert len(PgUncheckedValues(db).documents_with(customer, ["profit"], user_id=user)) == MAX_DOCUMENTS
