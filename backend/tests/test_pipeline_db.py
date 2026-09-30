"""The processor with the real document and pipeline stores (PostgreSQL).

File storage, the scanner and the queue are still in-memory fakes; readers are
the real CSV reader behind the real socket.

The PRD 2 twins at the end (test-engineer 2) prove against PostgreSQL what
tests/test_pipeline_runs.py proves with fakes: a read again replaces every
table, an OCR outage mid-document and its retry, two deliveries at once, and a
stale delivery after a read again. The order is forced with events and
``wait_until_blocked``, never with sleeps.
"""

import functools
import io
import itertools
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments, mark_for_reprocess
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.runs import PgRuns
from kaagaz.db.trash import lock_family
from kaagaz.extraction.card import CARD_PROMPT_HASH
from kaagaz.extraction.understanding import ModelBudget
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.jobs.worker import JobFailed
from kaagaz.pipeline import DocumentProcessor, ReadingSocket
from kaagaz.preparing.pages import prepare_pages
from kaagaz.reading.ocr import MeteredOcrEngine
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.spans import Span
from kaagaz.reading.wiring import build_reader_socket
from kaagaz.scanning.gate import DocumentStatus, ScanGate, Verdict
from tests.db_support import new_customer, wait_until_blocked
from tests.fakes import (
    FakeChat,
    FakePreparer,
    FakeScanner,
    MemoryPageImages,
    MemoryQueue,
    MemoryStorage,
    fake_sheet_pages,
)
from tests.pdf_builder import Page, Text, make_pdf
from tests.test_api_review import _named_database
from tests.test_extraction_card import fact as card_fact
from tests.test_extraction_card import reply as card_reply
from tests.test_pipeline import PIPELINE_POLICY, real_socket
from tests.test_pipeline_runs import SCANNED, SETTINGS, TimedOcr, Timer
from tests.test_pipeline_store_db import counts, new_session

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


# --- PRD 2: runs, the run fence and a read again (test-engineer 2) --------------------

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
_TICKS = itertools.count()


def moving_clock() -> datetime:
    """The deliveries' clock: a second later on every call, so runs sort in the order they started."""
    return T0 + timedelta(seconds=next(_TICKS))


RUNS = "SELECT status, error_code FROM processing_runs WHERE document_id = :d ORDER BY started_at, id"


def with_runs(
    db: Database,
    storage: MemoryStorage,
    socket: ReadingSocket | None = None,
    *,
    ocr_on: bool = False,
    scanner: FakeScanner | None = None,
    **model: Any,
) -> DocumentProcessor:
    """A processor on ``db`` with the real runs, progress and run fence. ``model`` holds
    the model stage's arguments (``chat``, ``model_budget``), when there is one."""
    documents = PgDocuments(db)
    prepare = functools.partial(
        prepare_pages, dpi=72, max_pages=10, max_image_pixels=10_000_000, blank_stddev=3.0, low_res_dpi=150
    )
    return DocumentProcessor(
        gate=ScanGate(storage, scanner or FakeScanner(Verdict.CLEAN), storage, documents),
        statuses=documents,
        documents=documents,
        socket=socket or real_socket(),
        prepare=prepare,
        sheet_pages=fake_sheet_pages,
        page_images=MemoryPageImages(),
        store=PgPipelineStore(db),
        runs=PgRuns(db),
        progress=documents,
        clock=moving_clock,
        timer=Timer(),
        ocr_on=ocr_on,
        **({"chat_model": "qwen3:4b"} if "chat" in model else {}),
        **model,
    )


class GatedSocket:
    """The real socket, but ``read`` first says it has started (``entered``) and
    then waits for ``release``: a delivery held in its reading, with its run
    started and its scan done."""

    def __init__(self, inner: ReadingSocket) -> None:
        self._inner = inner
        self.entered, self.release = threading.Event(), threading.Event()

    def read(
        self,
        detected_type: str,
        data: bytes,
        *,
        on_page: Callable[[RouteDecision | None], None] | None = None,
    ) -> list[Span]:
        self.entered.set()
        assert self.release.wait(timeout=10), "the test never released the delivery"
        return self._inner.read(detected_type, data, on_page=on_page)

    def read_second(self, detected_type: str, data: bytes) -> list[Span] | None:
        return self._inner.read_second(detected_type, data)

    def reader_id(self, detected_type: str, second: bool = False) -> tuple[str, str]:
        return self._inner.reader_id(detected_type, second)


def runs_of(db: Database, customer: str, doc: str) -> list[tuple[object, ...]]:
    with db.customer(customer) as conn:
        return [tuple(row) for row in conn.execute(text(RUNS), {"d": doc}).all()]


def read_again(db: Database, customer: str, doc: str) -> None:
    with db.customer(customer) as conn:
        assert mark_for_reprocess(conn, customer, doc, per_hour=20) is not None


def test_reprocess_makes_a_new_run_and_replaces_every_table(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO learned_corrections (customer_id, field, raw_value, corrected_value, created_by)"
                " VALUES (:c, 'party', 'Mehta Textiles', 'Mehta Textiles Pvt Ltd', :u)"
            ),
            {"c": customer, "u": user},
        )
    storage = MemoryStorage()
    upload = UploadService(PIPELINE_POLICY, PgDocuments(db), storage, MemoryQueue())
    doc = upload.accept(customer, io.BytesIO(CSV)).document_id
    processor = with_runs(db, storage)
    processor(Job(customer, doc), lambda: None)
    with db.customer(customer) as conn:  # a reviewer's fix on one of the values
        conn.execute(
            text(
                "INSERT INTO corrections (customer_id, document_id, field, value_id, old_value, new_value,"
                " choice, corrected_by)"
                " SELECT customer_id, document_id, field, id, value_text, '251', 'typed', :u"
                " FROM extracted_values WHERE customer_id = :c AND document_id = :d AND field = 'amount'"
                " AND record_key = 'sheet:1:row:3'"
            ),
            {"c": customer, "d": doc, "u": user},
        )
    before = counts(db, customer, doc)

    read_again(db, customer, doc)
    processor(Job(customer, doc), lambda: None)

    assert counts(db, customer, doc) == {**before, "processing_runs": 2}
    assert before["pages"] == 1 and before["pieces"] == 3 and before["extracted_values"] == 6
    assert runs_of(db, customer, doc) == [("ready", None), ("ready", None)]
    assert one(db, customer, "SELECT status, attempts FROM documents WHERE id = :d", doc) == ("ready", 0)
    assert one(
        db,
        customer,
        "SELECT value_text, label_reason FROM extracted_values"
        " WHERE document_id = :d AND field = 'party' AND record_key = 'sheet:1:row:3'",
        doc,
    ) == ("Mehta Textiles Pvt Ltd", "learned_correction")  # the remembered fix, applied again
    assert one(
        db, customer, "SELECT count(*), count(value_id) FROM corrections WHERE document_id = :d", doc
    ) == (
        1,
        0,
    )  # the fix's history is kept; its value was replaced


def test_ocr_down_on_page_2_then_retry_leaves_one_set_and_two_runs(db: Database) -> None:
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    upload = UploadService(PIPELINE_POLICY, PgDocuments(db), storage, MemoryQueue())
    doc = upload.accept(customer, io.BytesIO(make_pdf([SCANNED, SCANNED]))).document_id
    engine = TimedOcr(Timer(), down_on_call=2)
    processor = with_runs(
        db, storage, build_reader_socket(SETTINGS, MeteredOcrEngine(engine, timer=Timer())), ocr_on=True
    )

    with pytest.raises(BackendUnavailable):
        processor(Job(customer, doc), lambda: None)
    assert counts(db, customer, doc)["pages"] == 0
    processor(Job(customer, doc), lambda: None)  # the job came back after its lease

    # One set: 2 pages, the first reading and the second (PDFium's, empty on scans),
    # one value per scanned page, and 2 runs. The two pages' lines are one piece:
    # a page break no longer starts a piece (PRD 2 C1).
    assert counts(db, customer, doc) == {
        "pages": 2,
        "reader_outputs": 2,
        "pieces": 1,
        "extracted_values": 2,
        "invoices": 0,
        "invoice_lines": 0,
        "document_links": 0,
        "processing_runs": 2,
    }
    with db.customer(customer) as conn:
        routes = conn.execute(
            text("SELECT route FROM pages WHERE document_id = :d ORDER BY page_no"), {"d": doc}
        )
        assert [row[0] for row in routes] == ["ocr", "ocr"]
    assert runs_of(db, customer, doc) == [("failed", "ocr_unavailable"), ("ready", None)]


def test_two_deliveries_overlapping_leave_one_set_of_rows(db: Database) -> None:
    """Both deliveries have started their runs (the second supersedes the first) and
    scanned; the test holds the family lock until both wait on it for their save.
    Whichever saves first, the fence lets only the open run save: one set of rows."""
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    upload = UploadService(PIPELINE_POLICY, PgDocuments(db), storage, MemoryQueue())
    doc = upload.accept(customer, io.BytesIO(CSV)).document_id
    names = new_session(), new_session()
    first_db, second_db = _named_database(names[0]), _named_database(names[1])
    first, second = GatedSocket(real_socket()), GatedSocket(real_socket())
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            stale = pool.submit(with_runs(first_db, storage, first), Job(customer, doc), lambda: None)
            assert first.entered.wait(timeout=10)
            newer = pool.submit(with_runs(second_db, storage, second), Job(customer, doc), lambda: None)
            assert second.entered.wait(timeout=10)
            with db.customer(customer) as conn:
                lock_family(conn, customer, doc)
                first.release.set()
                second.release.set()
                for name in names:
                    wait_until_blocked(db, application_name=name)
            stale.result(timeout=20)  # acked, not failed: the newer run does the work
            newer.result(timeout=20)
    finally:
        first_db.engine.dispose()
        second_db.engine.dispose()

    assert one(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", doc) == (
        "ready",
        None,
    )
    found = counts(db, customer, doc)
    assert (found["pages"], found["pieces"], found["extracted_values"], found["reader_outputs"]) == (
        1,
        3,
        6,
        1,
    )
    assert runs_of(db, customer, doc) == [("superseded", None), ("ready", None)]


def test_a_stale_delivery_after_a_reprocess_does_not_fail_the_document(db: Database) -> None:
    """Lead's resolution 2: the first delivery is held while its redelivery reads the
    document and it is sent to be read again. The stale delivery then finds it
    received (not_scanned), but its run is no longer open, so nothing is failed."""
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    upload = UploadService(PIPELINE_POLICY, PgDocuments(db), storage, MemoryQueue())
    doc = upload.accept(customer, io.BytesIO(CSV)).document_id
    held = GatedSocket(real_socket())
    with ThreadPoolExecutor(max_workers=1) as pool:
        stale = pool.submit(with_runs(db, storage, held), Job(customer, doc), lambda: None)
        assert held.entered.wait(timeout=10)
        with_runs(db, storage)(Job(customer, doc), lambda: None)  # the redelivery: ready
        read_again(db, customer, doc)
        held.release.set()
        stale.result(timeout=20)

    assert one(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", doc) == (
        "received",
        None,
    )
    with_runs(db, storage)(Job(customer, doc), lambda: None)  # the read-again job
    assert one(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", doc) == (
        "ready",
        None,
    )
    assert runs_of(db, customer, doc) == [("superseded", None), ("ready", None), ("ready", None)]


class GatedScanner(FakeScanner):
    """A clean verdict, given only once the test releases it: a delivery held in its scan."""

    def __init__(self) -> None:
        super().__init__(Verdict.CLEAN)
        self.entered, self.release = threading.Event(), threading.Event()

    def scan(self, data: bytes) -> Any:
        self.entered.set()
        assert self.release.wait(timeout=10), "the test never released the scan"
        return super().scan(data)


def test_a_stale_scan_never_moves_a_ready_document_back_to_clean(db: Database) -> None:
    """Council PR 3 review, medium 2: the gate's CLEAN write is fenced by the run too."""
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    upload = UploadService(PIPELINE_POLICY, PgDocuments(db), storage, MemoryQueue())
    doc = upload.accept(customer, io.BytesIO(CSV)).document_id
    scanner = GatedScanner()
    with ThreadPoolExecutor(max_workers=1) as pool:
        stale = pool.submit(with_runs(db, storage, scanner=scanner), Job(customer, doc), lambda: None)
        assert scanner.entered.wait(timeout=10)
        with_runs(db, storage)(Job(customer, doc), lambda: None)  # the redelivery: ready
        scanner.release.set()
        stale.result(timeout=20)  # acked, not failed

    assert one(db, customer, "SELECT status, failure_code FROM documents WHERE id = :d", doc) == (
        "ready",
        None,
    )
    assert runs_of(db, customer, doc) == [("superseded", None), ("ready", None)]


# --- PRD 2 D1 to D6: the model stage against PostgreSQL ---------------------------------


def _receipt(delivery: str) -> bytes:
    """A one-page digital PDF receipt; ``delivery`` makes each file's bytes its own."""
    lines = [
        "Sharma Traders",
        "Invoice No: INV 17",
        "Date: 12/08/2026",
        delivery,
        "Grand Total: Rs. 1,180.00",
    ]
    return make_pdf([Page(texts=[Text(72, 740 - 24 * n, text) for n, text in enumerate(lines)])])


RECEIPT_CARD = card_reply(
    doc_type="receipt",
    parties=[{"role": "seller", "name_quote": "Sharma Traders"}],
    document_date={"value_quote": "12/08/2026"},
    key_facts=[card_fact("net_payable", "Rs. 1,180.00", "Grand Total:")],
    summary="Sharma Traders was paid Rs. 1,180.00.",
)


def test_a_card_its_facts_and_its_links_are_saved_with_a_fake_model(db: Database) -> None:
    """Two receipts with the same invoice number, read with a fake model: each gets its
    card and facts, their card values are needs review, they link both ways, and each
    run records the chat model and its quotes."""
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    documents = PgDocuments(db)
    upload = UploadService(PIPELINE_POLICY, documents, storage, MemoryQueue())
    budget = ModelBudget(card_max_chars=12000, card_max_tokens=1024, num_ctx=8192, invoice_windows=None)
    docs = []
    for delivery in ("Cotton cloth delivered to the Pune shop in August", "Silk delivered in August too"):
        doc = upload.accept(customer, io.BytesIO(_receipt(delivery))).document_id
        processor = with_runs(
            db, storage, build_reader_socket(SETTINGS, None), chat=FakeChat(RECEIPT_CARD), model_budget=budget
        )
        processor(Job(customer, doc), lambda: None)
        docs.append(doc)
    first, second = docs

    facts = one(
        db,
        customer,
        "SELECT status, doc_type, languages, summary, document_date, card->>'prompt' FROM documents"
        " WHERE id = :d",
        first,
    )
    assert facts == (
        "ready",
        "receipt",
        ["en"],
        "Sharma Traders was paid Rs. 1,180.00.",
        date(2026, 8, 12),
        CARD_PROMPT_HASH,
    )
    with db.customer(customer) as conn:
        card_labels = conn.execute(
            text(
                "SELECT DISTINCT label, label_reason FROM extracted_values"
                " WHERE document_id = :d AND record_key = 'card'"
            ),
            {"d": first},
        ).all()
        headers = conn.execute(
            text("SELECT context_header FROM pieces WHERE document_id = :d"), {"d": first}
        ).all()
        linked = conn.execute(
            text(
                "SELECT document_id::text, linked_document_id::text, identifier_kind, identifier_value"
                " FROM document_links WHERE document_id = :d OR linked_document_id = :d"
            ),
            {"d": first},
        ).all()
        run = conn.execute(
            text(
                "SELECT status, model_chat, model_calls, quotes_returned FROM processing_runs"
                " WHERE document_id = :d"
            ),
            {"d": first},
        ).one()
    assert [tuple(row) for row in card_labels] == [("needs_review", "model_chosen")]
    assert headers and all("Type: receipt | Date: 2026-08-12" in row[0] for row in headers)
    assert {tuple(row) for row in linked} == {
        (first, second, "invoice_number", "inv17"),
        (second, first, "invoice_number", "inv17"),
    }
    assert tuple(run) == ("ready", "qwen3:4b", 1, 3)


def test_the_model_off_twin_saves_no_card_and_no_links(db: Database) -> None:
    customer, _ = new_customer(db)
    storage = MemoryStorage()
    upload = UploadService(PIPELINE_POLICY, PgDocuments(db), storage, MemoryQueue())
    docs = [
        upload.accept(customer, io.BytesIO(_receipt(delivery))).document_id
        for delivery in ("Cotton cloth delivered to the Pune shop in August", "Silk delivered in August too")
    ]
    for doc in docs:
        with_runs(db, storage, build_reader_socket(SETTINGS, None))(Job(customer, doc), lambda: None)

    assert one(
        db,
        customer,
        "SELECT status, doc_type, languages, summary, card FROM documents WHERE id = :d",
        docs[0],
    ) == ("ready", None, ["en"], None, None)
    assert one(db, customer, "SELECT count(*) FROM document_links WHERE document_id = :d", docs[0]) == (0,)
    assert one(
        db,
        customer,
        "SELECT count(*) FROM extracted_values WHERE document_id = :d AND record_key = 'card'",
        docs[0],
    ) == (0,)
    assert one(
        db, customer, "SELECT model_chat, model_calls FROM processing_runs WHERE document_id = :d", docs[0]
    ) == (None, 0)
