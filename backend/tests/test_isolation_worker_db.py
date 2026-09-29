"""The worker's last-delivery mark (PgDocuments.mark_exhausted) across customers.

Customer B naming customer A's document changes nothing: row level security
and the explicit customer filter both keep A's document as it was.
"""

import uuid

import pytest

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

    documents.mark_exhausted(b, record.document_id)

    assert documents.get_status(a, record.document_id) is None  # still received, not failed
    assert documents.get(b, record.document_id) is None
