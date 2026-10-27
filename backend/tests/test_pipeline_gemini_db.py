"""The pipeline with the Gemini reader against PostgreSQL (PRD 5 RT2, F1, F4, F5, G7 and
the run's reader counters): what tests/test_pipeline_gemini.py proves with fakes, through
the real document, pipeline, runs and page-cache stores. The reader and the checker are
still the scripted fakes; nothing calls a model."""

import functools
import io
import itertools
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments, mark_for_reprocess, request_page_read_again
from kaagaz.db.engine import Database
from kaagaz.db.page_reads import PgPageReads
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.runs import PgRuns
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.pipeline import DocumentProcessor
from kaagaz.preparing.pages import prepare_pages
from kaagaz.reading.ocr import MeteredOcrEngine
from kaagaz.reading.wiring import build_reader_socket
from kaagaz.scanning.gate import ScanGate, Verdict
from tests.db_support import new_customer
from tests.fakes import FakeScanner, MemoryPageImages, MemoryQueue, MemoryStorage, fake_sheet_pages
from tests.pdf_builder import make_pdf
from tests.test_pipeline import PIPELINE_POLICY
from tests.test_pipeline_gemini import (
    MODEL,
    SETTINGS,
    SHARP,
    TEXT_PAGE,
    TOTAL,
    FakeChecker,
    ScriptedReader,
    page_read,
)

pytestmark = pytest.mark.db

T0 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)


def processor(db: Database, storage: MemoryStorage, reader: ScriptedReader) -> DocumentProcessor:
    documents = PgDocuments(db)
    prepare = functools.partial(
        prepare_pages, dpi=72, max_pages=10, max_image_pixels=40_000_000, blank_stddev=3.0, low_res_dpi=150
    )
    meter = MeteredOcrEngine(reader, timer=lambda: 0.0)
    # Fixed (#239, 27 Oct 2026): a second on each reading of the clock, so each run starts
    # after the last and COUNTERS' ORDER BY started_at is the runs' order (at one fixed time
    # the order fell to the random ids).
    ticks = itertools.count()
    return DocumentProcessor(
        gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, documents),
        statuses=documents,
        documents=documents,
        socket=build_reader_socket(SETTINGS, meter),
        prepare=prepare,
        sheet_pages=fake_sheet_pages,
        page_images=MemoryPageImages(),
        store=PgPipelineStore(db),
        runs=PgRuns(db),
        progress=documents,
        ocr_meter=meter,
        clock=lambda: T0 + timedelta(seconds=next(ticks)),
        timer=lambda: 0.0,
        ocr_model=MODEL,
        checker=FakeChecker(),
        checker_model="rapidocr 3.9.2",
        page_cache=PgPageReads(db),
        read_again=documents,
    )


def upload(db: Database, storage: MemoryStorage, customer: str, data: bytes) -> str:
    return (
        UploadService(PIPELINE_POLICY, PgDocuments(db), storage, MemoryQueue())
        .accept(customer, io.BytesIO(data))
        .document_id
    )


def mark(db: Database, customer: str, doc: str, page_no: int) -> None:
    """As the API's read one page again: the document back to received, then the mark."""
    with db.customer(customer) as conn:
        assert mark_for_reprocess(conn, customer, doc, per_hour=100) is not None
        assert request_page_read_again(conn, customer, doc, page_no, now=T0)


def rows(db: Database, customer: str, sql: str, doc: str) -> list[tuple[object, ...]]:
    with db.customer(customer) as conn:
        return [tuple(row) for row in conn.execute(text(sql), {"c": customer, "d": doc}).all()]


PAGES = (
    "SELECT page_no, read_by, read_quality, read_issues, unsure_words, illegible_words, unpinned_lines,"
    " read_again_requested_at IS NOT NULL FROM pages WHERE customer_id = :c AND document_id = :d"
    " ORDER BY page_no"
)
COUNTERS = (
    "SELECT status, pages_gemini, pages_local, pages_cached, pages_read_again, pages_unreadable,"
    " input_tokens, output_tokens FROM processing_runs WHERE customer_id = :c AND document_id = :d"
    " ORDER BY started_at, id"
)


def test_rt2_f1_f5_page_results_and_counters_are_saved_and_the_read_again_mark_is_read_then_cleared(
    db: Database,
) -> None:
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    reader = ScriptedReader({1: page_read(TOTAL), 2: page_read(TOTAL, cached=True)})
    run = processor(db, storage, reader)
    doc = upload(db, storage, customer, make_pdf([SHARP, SHARP]))
    run(Job(customer, doc), lambda: None)  # the first reading saves the pages
    mark(db, customer, doc, 1)
    reader.pages[1] = page_read(TOTAL, tokens=(2800, 6000))
    run(Job(customer, doc), lambda: None)  # page 1 read again, harder

    assert [pages.read_again for pages in reader.opened] == [frozenset(), frozenset({1})]
    assert rows(db, customer, PAGES, doc) == [
        (1, MODEL, "good", [], 0, 0, 0, False),  # the save that replaced the page cleared its mark
        (2, MODEL, "good", [], 0, 0, 0, False),
    ]
    assert rows(db, customer, COUNTERS, doc) == [
        ("ready", 1, 0, 1, 0, 0, 1700, 4000),
        ("ready", 1, 0, 1, 1, 0, 2800, 6000),
    ]


def test_f4_a_document_with_every_page_unreadable_is_ready_and_its_marks_are_cleared(db: Database) -> None:
    # Changed on purpose (#239, 27 Oct 2026): never failed "unreadable" (Tushar, 16 Oct 2026,
    # e01b937): ready, each such page flagged for review, as test_pipeline_gemini.py says.
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    reader = ScriptedReader({1: page_read(TOTAL)})
    run = processor(db, storage, reader)
    doc = upload(db, storage, customer, make_pdf([SHARP]))
    run(Job(customer, doc), lambda: None)
    mark(db, customer, doc, 1)
    reader.pages[1] = page_read(TOTAL, read_by="rapidocr", quality=None, issues=("reader_refused",))
    run(Job(customer, doc), lambda: None)

    assert rows(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", doc) == [
        ("ready", None)
    ]
    assert [row[2] for row in rows(db, customer, PAGES, doc)] == ["unreadable"]
    assert rows(
        db,
        customer,
        "SELECT count(*) FROM pages WHERE document_id = :d AND read_again_requested_at IS NOT NULL",
        doc,
    ) == [(0,)]
    assert rows(db, customer, COUNTERS, doc)[-1] == ("ready", 0, 1, 0, 0, 1, 0, 0)


def test_a_text_page_is_read_by_text_layer_and_good(db: Database) -> None:
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    run = processor(db, storage, ScriptedReader({2: page_read(TOTAL)}))
    doc = upload(db, storage, customer, make_pdf([TEXT_PAGE, SHARP]))
    run(Job(customer, doc), lambda: None)
    assert [row[:3] for row in rows(db, customer, PAGES, doc)] == [
        (1, "text_layer", "good"),
        (2, MODEL, "good"),
    ]
