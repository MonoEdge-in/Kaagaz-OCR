"""A correction outlives the value it fixed.

Re-reading a document deletes and re-inserts its extracted values (the
pipeline replaces, never appends), so the correction's link to the value
goes empty, but who changed what, and when, is kept.
"""

import uuid

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.db_support import new_customer

pytestmark = pytest.mark.db


def test_correction_is_kept_when_its_value_is_replaced(db: Database) -> None:
    customer, admin = new_customer(db)
    document = str(uuid.uuid4())
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes, detected_type)"
                " VALUES (:d, :c, 'bill.pdf', :h, 10, 'pdf')"
            ),
            {"d": document, "c": customer, "h": "b" * 64},
        )
        value: object = conn.execute(
            text(
                "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text,"
                " value_raw, page_no, region, reader, label, label_reason)"
                " VALUES (:c, :d, 'document', 'total_amount', '1,000', '1,O00', 1,"
                " CAST(:region AS jsonb), 'rapidocr', 'verified', 'corrected')"
                " RETURNING id"
            ),
            {"c": customer, "d": document, "region": '{"kind": "box", "page": 1, "box": [0, 0, 1, 1]}'},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO corrections (customer_id, document_id, field, value_id, old_value,"
                " new_value, choice, corrected_by)"
                " VALUES (:c, :d, 'total_amount', :v, '1,O00', '1,000', 'typed', :u)"
            ),
            {"c": customer, "d": document, "v": value, "u": admin},
        )

    with db.customer(customer) as conn:
        conn.execute(text("DELETE FROM extracted_values WHERE document_id = :d"), {"d": document})
        kept = conn.execute(
            text("SELECT field, value_id, old_value, new_value FROM corrections WHERE document_id = :d"),
            {"d": document},
        ).all()

    assert [tuple(row) for row in kept] == [("total_amount", None, "1,O00", "1,000")]
