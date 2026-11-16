"""Row level security on the documents table, against a real PostgreSQL.

Customer B uses customer A's exact ids and fingerprint and must see nothing,
change nothing and be told nothing (docs/architecture.md, two locks).
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord
from kaagaz.scanning.gate import DocumentStatus
from tests.db_support import APP_ROLE, new_customer

pytestmark = pytest.mark.db

SHA = "a" * 64


def _record(customer_id: str) -> DocumentRecord:
    return DocumentRecord(customer_id, str(uuid.uuid4()), SHA, 10, "pdf", display_name="bill.pdf")


def test_other_customer_sees_nothing_by_id_or_fingerprint(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    documents = PgDocuments(db)
    record, created = documents.add_if_absent(_record(a))
    assert created

    assert documents.get(b, record.document_id) is None
    assert documents.find_by_fingerprint(b, SHA) is None
    # The same bytes are a new document for B, never a duplicate of A's.
    _, created_for_b = documents.add_if_absent(_record(b))
    assert created_for_b


def test_other_customer_cannot_change_status_or_delete(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    documents = PgDocuments(db)
    record, _ = documents.add_if_absent(_record(a))

    documents.set_status(b, record.document_id, DocumentStatus.READY)
    documents.remove(DocumentRecord(b, record.document_id, SHA, 10, "pdf"))

    assert documents.get(a, record.document_id) is not None
    assert documents.get_status(a, record.document_id) is None  # still received


def test_even_naming_another_customer_in_sql_finds_nothing(db: Database) -> None:
    """The database lock holds when the application lock is forgotten."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    record, _ = PgDocuments(db).add_if_absent(_record(a))
    # Changed on purpose (#595): the db fixture is the workers' role now; this is the app's.
    with Database(db.engine, APP_ROLE).customer(b) as conn:
        found = conn.execute(text("SELECT count(*) FROM documents WHERE id = :d"), {"d": record.document_id})
        assert found.scalar_one() == 0
        assert conn.execute(text("SELECT current_user")).scalar_one() == "kaagaz_app"


def test_writing_a_row_for_another_customer_is_refused(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    with pytest.raises(ProgrammingError), db.customer(b) as conn:
        conn.execute(
            text(
                "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes, detected_type,"
                " family_id) VALUES (:d, :c, 'x', :h, 1, 'pdf', :d)"
            ),
            {"d": str(uuid.uuid4()), "c": a, "h": SHA},
        )


def test_rejected_is_final(db: Database) -> None:
    a, _ = new_customer(db)
    documents = PgDocuments(db)
    record, _ = documents.add_if_absent(_record(a))
    documents.set_status(a, record.document_id, DocumentStatus.REJECTED)
    documents.set_status(a, record.document_id, DocumentStatus.CLEAN)
    assert documents.get_status(a, record.document_id) is DocumentStatus.REJECTED
