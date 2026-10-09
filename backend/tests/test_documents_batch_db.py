"""PRD 5 Q1: a new upload keeps the drop it came in (``documents.batch_id``), against a
real PostgreSQL. A duplicate stays in its own drop; a new version is in none."""

import uuid

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord
from tests.db_support import new_customer

pytestmark = pytest.mark.db


def stored_batch(db: Database, customer_id: str, document_id: str) -> str | None:
    with db.customer(customer_id) as conn:
        value = conn.execute(
            text("SELECT batch_id FROM documents WHERE customer_id = :c AND id = :d"),
            {"c": customer_id, "d": document_id},
        ).scalar_one()
    return None if value is None else str(value)


def test_add_if_absent_writes_the_batch_and_a_duplicate_keeps_its_own(db: Database) -> None:
    customer, _ = new_customer(db)
    documents = PgDocuments(db)
    batch, other_batch = str(uuid.uuid4()), str(uuid.uuid4())
    sha = uuid.uuid4().hex * 2
    first = DocumentRecord(customer, str(uuid.uuid4()), sha, 10, "text", batch_id=batch)
    loose = DocumentRecord(customer, str(uuid.uuid4()), uuid.uuid4().hex * 2, 10, "text")

    assert documents.add_if_absent(first)[1] is True
    assert documents.add_if_absent(loose)[1] is True
    again = DocumentRecord(customer, str(uuid.uuid4()), sha, 10, "text", batch_id=other_batch)
    stored, created = documents.add_if_absent(again)

    assert created is False and stored.document_id == first.document_id
    assert stored_batch(db, customer, first.document_id) == batch
    assert stored_batch(db, customer, loose.document_id) is None
