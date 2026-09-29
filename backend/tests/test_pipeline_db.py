"""The processor with the real document and pipeline stores (PostgreSQL).

File storage, the scanner and the queue are still in-memory fakes; readers are
the real CSV reader behind the real socket.
"""

import io

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.jobs.worker import JobFailed
from kaagaz.pipeline import DocumentProcessor
from kaagaz.scanning.gate import DocumentStatus, ScanGate, Verdict
from tests.db_support import new_customer
from tests.fakes import (
    FakePreparer,
    FakeScanner,
    MemoryPageImages,
    MemoryQueue,
    MemoryStorage,
    fake_sheet_pages,
)
from tests.test_pipeline import PIPELINE_POLICY, real_socket

pytestmark = pytest.mark.db

CSV = b"Party,Amount,PAN\nSharma Traders,1180,ABCDE1234F\nMehta Textiles,250,PQRST6789K\n"  # FAKE numbers


def setup(
    db: Database, verdict: Verdict = Verdict.CLEAN
) -> tuple[UploadService, DocumentProcessor, FakeScanner]:
    documents, storage, scanner = PgDocuments(db), MemoryStorage(), FakeScanner(verdict)
    upload = UploadService(PIPELINE_POLICY, documents, storage, MemoryQueue())
    processor = DocumentProcessor(
        gate=ScanGate(storage, scanner, storage, documents),
        statuses=documents,
        documents=documents,
        socket=real_socket(),
        prepare=FakePreparer().prepare,
        sheet_pages=fake_sheet_pages,
        page_images=MemoryPageImages(),
        store=PgPipelineStore(db),
    )
    return upload, processor, scanner


def one(db: Database, customer: str, sql: str, doc: str) -> tuple[object, ...]:
    with db.customer(customer) as conn:
        return tuple(conn.execute(text(sql), {"c": customer, "d": doc}).one())


def test_a_csv_becomes_ready_with_pages_pieces_and_values_as_read(db: Database) -> None:
    customer, _ = new_customer(db)
    upload, processor, _ = setup(db)
    doc = upload.accept(customer, io.BytesIO(CSV)).document_id
    processor(Job(customer, doc), lambda: None)
    processor(Job(customer, doc), lambda: None)  # a redelivery replaces

    assert one(db, customer, "SELECT status, page_count, failure_code FROM documents WHERE id = :d", doc) == (
        "ready",
        1,
        None,
    )
    assert one(db, customer, "SELECT count(*) FROM pieces WHERE document_id = :d", doc) == (3,)
    assert one(db, customer, "SELECT count(*) FROM extracted_values WHERE document_id = :d", doc) == (6,)
    assert one(
        db,
        customer,
        "SELECT value_text FROM extracted_values WHERE document_id = :d AND field = 'pan'"
        " AND record_key = 'sheet:1:row:2'",
        doc,
    ) == ("ABCDE1234F",)
    assert one(
        db,
        customer,
        "SELECT count(*) FROM extracted_values WHERE document_id = :d AND value_number = 1180",
        doc,
    ) == (1,)
    with db.customer(customer) as conn:
        stored = conn.execute(
            text(
                "SELECT string_agg(text, ' ') FROM pieces WHERE document_id = :d"
                " UNION ALL SELECT output::text FROM reader_outputs WHERE document_id = :d"
            ),
            {"d": doc},
        ).all()
    joined = " ".join(str(row[0]) for row in stored)
    assert "ABCDE1234F" in joined and "PQRST6789K" in joined


def test_a_broken_file_is_marked_failed_with_its_code(db: Database) -> None:
    customer, _ = new_customer(db)
    upload, processor, _ = setup(db)
    doc = upload.accept(customer, io.BytesIO(b'a,"never closed\n')).document_id
    with pytest.raises(JobFailed):
        processor(Job(customer, doc), lambda: None)
    assert one(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", doc) == (
        "failed",
        "malformed_csv",
    )


def test_an_infected_file_is_rejected_with_its_reason_kept(db: Database) -> None:
    customer, _ = new_customer(db)
    upload, processor, scanner = setup(db, Verdict.INFECTED)
    doc = upload.accept(customer, io.BytesIO(CSV)).document_id
    with pytest.raises(JobFailed):
        processor(Job(customer, doc), lambda: None)
    with pytest.raises(JobFailed):
        processor(Job(customer, doc), lambda: None)  # redelivered: refused as "rejected"
    assert one(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", doc) == (
        "rejected",
        "virus_found",
    )
    assert len(scanner.scanned) == 1


def test_a_learned_correction_in_the_database_is_applied(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO learned_corrections (customer_id, field, raw_value, corrected_value, created_by)"
                " VALUES (:c, 'party', 'Mehta Textiles', 'Mehta Textiles Pvt Ltd', :u)"
            ),
            {"c": customer, "u": user},
        )
    upload, processor, _ = setup(db)
    doc = upload.accept(customer, io.BytesIO(CSV)).document_id
    processor(Job(customer, doc), lambda: None)
    assert one(
        db,
        customer,
        "SELECT value_text, value_raw, label, label_reason FROM extracted_values"
        " WHERE document_id = :d AND field = 'party' AND record_key = 'sheet:1:row:3'",
        doc,
    ) == ("Mehta Textiles Pvt Ltd", "Mehta Textiles", "verified", "learned_correction")


def test_status_after_needs_reader(db: Database) -> None:
    customer, _ = new_customer(db)
    upload, processor, _ = setup(db)
    doc = upload.accept(customer, io.BytesIO(b"%PDF-1.7\n" + b"0" * 200)).document_id
    processor(Job(customer, doc), lambda: None)
    assert PgDocuments(db).get_status(customer, doc) is DocumentStatus.NEEDS_READER


def _as_version(db: Database, customer: str, document: str, *, of: str, number: int) -> str:
    """Make an uploaded document version ``number`` of ``of``'s family, not current,
    as a version upload leaves it."""
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "UPDATE documents SET family_id = :f, version_no = :n, is_current = false"
                " WHERE customer_id = :c AND id = :d"
            ),
            {"c": customer, "d": document, "f": of, "n": number},
        )
    return document


def test_a_new_version_takes_over_once_ready_and_a_failed_one_changes_nothing(db: Database) -> None:
    customer, _ = new_customer(db)
    upload, processor, _ = setup(db)
    first = upload.accept(customer, io.BytesIO(CSV)).document_id
    processor(Job(customer, first), lambda: None)
    second = upload.accept(customer, io.BytesIO(CSV + b"Gupta Stores,90,\n")).document_id
    broken = upload.accept(customer, io.BytesIO(b'a,"never closed\n')).document_id
    _as_version(db, customer, second, of=first, number=2)
    _as_version(db, customer, broken, of=first, number=3)

    processor(Job(customer, second), lambda: None)
    with pytest.raises(JobFailed):
        processor(Job(customer, broken), lambda: None)

    sql = "SELECT status, is_current FROM documents WHERE id = :d"
    assert one(db, customer, sql, first) == ("ready", False)
    assert one(db, customer, sql, second) == ("ready", True)
    assert one(db, customer, sql, broken) == ("failed", False)
