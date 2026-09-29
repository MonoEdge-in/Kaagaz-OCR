"""The worker's last delivery against a real PostgreSQL: PgDocuments.mark_exhausted.

Only a document still in progress (received or clean) is marked failed with
``retries_exhausted``. A rejected document keeps the reason it was rejected
for, and a ready, needs_reader or failed one keeps its state and its code.
"""

import uuid

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord
from kaagaz.scanning.gate import DocumentStatus
from tests.db_support import new_customer

pytestmark = pytest.mark.db


def add_document(documents: PgDocuments, customer_id: str) -> str:
    record = DocumentRecord(customer_id, str(uuid.uuid4()), uuid.uuid4().hex * 2, 10, "text")
    stored, _ = documents.add_if_absent(record)
    return stored.document_id


def stored_state(db: Database, customer_id: str, document_id: str) -> tuple[str, str | None]:
    with db.customer(customer_id) as conn:
        row = conn.execute(
            text("SELECT status, failure_code FROM documents WHERE customer_id = :c AND id = :d"),
            {"c": customer_id, "d": document_id},
        ).one()
    return str(row[0]), None if row[1] is None else str(row[1])


def test_a_document_still_in_progress_is_stored_as_failed_retries_exhausted(db: Database) -> None:
    customer, _ = new_customer(db)
    documents = PgDocuments(db)
    received = add_document(documents, customer)
    clean = add_document(documents, customer)
    documents.set_status(customer, clean, DocumentStatus.CLEAN)

    for document_id in (received, clean):
        documents.mark_exhausted(customer, document_id)
        assert stored_state(db, customer, document_id) == ("failed", "retries_exhausted")


def test_retries_exhausted_never_overwrites_a_rejection(db: Database) -> None:
    customer, _ = new_customer(db)
    documents = PgDocuments(db)
    rejected = add_document(documents, customer)
    documents.set_status(customer, rejected, DocumentStatus.REJECTED)
    documents.set_failed(customer, rejected, "virus_found")  # as the pipeline records it

    documents.mark_exhausted(customer, rejected)
    assert stored_state(db, customer, rejected) == ("rejected", "virus_found")


def test_retries_exhausted_never_fails_a_finished_document(db: Database) -> None:
    customer, _ = new_customer(db)
    documents = PgDocuments(db)
    ready = add_document(documents, customer)
    documents.set_status(customer, ready, DocumentStatus.READY)
    needs_reader = add_document(documents, customer)
    documents.set_status(customer, needs_reader, DocumentStatus.NEEDS_READER)
    failed = add_document(documents, customer)
    documents.set_failed(customer, failed, "corrupt_file")  # a failure for good keeps its own code

    for document_id in (ready, needs_reader, failed):
        documents.mark_exhausted(customer, document_id)
    assert stored_state(db, customer, ready) == ("ready", None)
    assert stored_state(db, customer, needs_reader) == ("needs_reader", None)
    assert stored_state(db, customer, failed) == ("failed", "corrupt_file")
