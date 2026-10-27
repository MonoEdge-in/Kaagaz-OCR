"""The worker's writes (PgDocuments.mark_exhausted and record_attempt) across customers.

Customer B naming customer A's document changes nothing: row level security
and the explicit customer filter both keep A's document as it was.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord
from tests.db_support import new_customer

pytestmark = pytest.mark.db


def test_another_customer_cannot_mark_a_document_exhausted(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    documents = PgDocuments(db)
    record, _ = documents.add_if_absent(DocumentRecord(a, str(uuid.uuid4()), "b" * 64, 10, "pdf"))

    for attempt in range(1, 6):  # changed on purpose (issue 407): each try in turn, to the last
        documents.record_attempt(a, record.document_id, attempt, 5)
    documents.mark_exhausted(
        b, record.document_id, now=datetime(2026, 9, 30, 12, 0, tzinfo=UTC), max_attempts=5
    )

    assert documents.get_status(a, record.document_id) is None  # still received, not failed
    assert documents.get(b, record.document_id) is None


def test_another_customer_cannot_record_a_try_on_a_document(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    documents = PgDocuments(db)
    record, _ = documents.add_if_absent(DocumentRecord(a, str(uuid.uuid4()), "c" * 64, 10, "pdf"))

    # Changed on purpose (issue 407): try 1, which a's own count would take, so only b is refused.
    documents.record_attempt(b, record.document_id, 1, 5)

    with db.customer(a) as conn:
        row = conn.execute(
            text("SELECT attempts, max_attempts FROM documents WHERE id = :d"), {"d": record.document_id}
        ).one()
    assert tuple(row) == (0, None)
