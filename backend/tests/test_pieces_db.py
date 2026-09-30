"""Pieces saved in PostgreSQL: across pages, with their context headers, Latin
spellings and the document's languages (PRD 2 C1, C4, C6, C7, L2, L3).

The processor runs with the real document and pipeline stores; storage, the
scanner and the queue are in-memory fakes.
"""

import io
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db import listing
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.ingestion import sniff
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.pipeline import DocumentProcessor, ReadingSocket
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import BoxLocation, Span
from kaagaz.scanning.gate import ScanGate, Verdict
from tests.db_support import new_customer
from tests.fakes import (
    FakePreparer,
    FakeScanner,
    MemoryPageImages,
    MemoryQueue,
    MemoryStorage,
    PresetSocket,
    fake_sheet_pages,
)
from tests.support import PDF_BYTES
from tests.test_cross_page_pieces import AMOUNT, PROFIT, pdf_reader, report_pdf
from tests.test_pipeline import PIPELINE_POLICY

pytestmark = pytest.mark.db

PIECES = (
    "SELECT seq, page_from, page_to, text, context_header, text_latin FROM pieces"
    " WHERE document_id = :d ORDER BY seq"
)


def process(db: Database, customer: str, data: bytes, socket: ReadingSocket, *, name: str) -> str:
    """Upload ``data`` as ``name`` for ``customer``, run the job twice (a redelivery), and give its id."""
    documents, storage = PgDocuments(db), MemoryStorage()
    upload = UploadService(PIPELINE_POLICY, documents, storage, MemoryQueue())
    doc = upload.accept(customer, io.BytesIO(data), display_name=name).document_id
    processor = DocumentProcessor(
        gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, documents),
        statuses=documents,
        documents=documents,
        socket=socket,
        prepare=FakePreparer().prepare,
        sheet_pages=fake_sheet_pages,
        page_images=MemoryPageImages(),
        store=PgPipelineStore(db),
    )
    processor(Job(customer, doc), lambda: None)
    processor(Job(customer, doc), lambda: None)  # a redelivery replaces, never doubles
    return doc


def rows(db: Database, customer: str, sql: str, doc: str) -> list[dict[str, Any]]:
    with db.customer(customer) as conn:
        return [dict(row._mapping) for row in conn.execute(text(sql), {"d": doc})]


def devanagari_spans() -> list[Span]:
    def line(words: str, top: float, *, page: int = 1, kind: str | None = None) -> Span:
        box = BoxLocation(page, 0.1, top, 0.9, top + 0.02)
        return Span(words, words, box, kind=kind, origin="text_layer")

    return [
        line("Sharma Traders", 0.1),
        line("खरेदीदार", 0.5, kind="heading"),  # "buyer": a heading starts a piece and names its section
        line("पाटील शिंदे", 0.05, page=2),
    ]


def test_the_c7_report_is_saved_with_one_piece_across_pages_15_and_16(db: Database) -> None:
    customer, _ = new_customer(db)
    socket = ReaderSocket({sniff.PDF: pdf_reader()})
    doc = process(db, customer, report_pdf(), socket, name="Sept report.pdf")

    pieces = rows(db, customer, PIECES, doc)
    assert [p["seq"] for p in pieces] == list(range(len(pieces)))  # C6: one document-wide order
    assert [p["page_from"] for p in pieces] == sorted(p["page_from"] for p in pieces)
    (piece,) = [p for p in pieces if PROFIT in p["text"]]
    (expenses,) = [p for p in pieces if "Expenses of the month" in p["text"]]
    assert piece["seq"] == expenses["seq"] + 1  # PRD 3 fetches neighbours with seq +- 1
    assert [p for p in pieces if AMOUNT in p["text"]] == [piece]
    assert (piece["page_from"], piece["page_to"], piece["text_latin"]) == (15, 16, None)
    assert piece["context_header"] == (
        "Document: Sept report.pdf | Pages 15-16"
        " | Before: Expenses of the month Sept were checked by the accountant."
    )
    assert not [p for p in pieces if "of 20" in p["text"]]  # no footer in any piece...
    (output,) = rows(db, customer, "SELECT output::text AS o FROM reader_outputs WHERE document_id = :d", doc)
    assert "Page 15 of 20" in output["o"]  # ...but every footer is kept as read
    languages = rows(db, customer, "SELECT languages FROM documents WHERE id = :d", doc)
    assert languages == [{"languages": ["en"]}]


def test_headers_latin_spellings_and_languages_are_saved_and_read_back(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = process(db, customer, PDF_BYTES, PresetSocket({sniff.PDF: devanagari_spans()}), name="bill.pdf")

    assert rows(db, customer, PIECES, doc) == [
        {
            "seq": 0,
            "page_from": 1,
            "page_to": 1,
            "text": "Sharma Traders",
            "context_header": "Document: bill.pdf | Page 1",
            "text_latin": None,
        },
        {
            "seq": 1,
            "page_from": 1,
            "page_to": 2,
            "text": "खरेदीदार\nपाटील शिंदे",
            # The section is not a column of its own: it is kept in the header (C3, C4).
            "context_header": "Document: bill.pdf | Section: खरेदीदार | Pages 1-2 | Before: Sharma Traders",
            "text_latin": "kharedidar\npatil shinde",
        },
    ]
    languages = rows(db, customer, "SELECT languages FROM documents WHERE id = :d", doc)
    assert languages == [{"languages": ["en", "other"]}]


def test_a_rename_rewrites_the_document_part_of_every_header(db: Database) -> None:
    customer, _ = new_customer(db)
    doc = process(db, customer, PDF_BYTES, PresetSocket({sniff.PDF: devanagari_spans()}), name="bill.pdf")
    with db.customer(customer) as conn:
        listing.rename_document(conn, customer, doc, name="Sharma bill | 2026.pdf")
    headers = [p["context_header"] for p in rows(db, customer, PIECES, doc)]
    assert headers == [
        "Document: Sharma bill \u00a6 2026.pdf | Page 1",
        "Document: Sharma bill \u00a6 2026.pdf | Section: खरेदीदार | Pages 1-2 | Before: Sharma Traders",
    ]
