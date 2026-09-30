"""The whole flow with fakes: upload -> queue -> worker -> pages, pieces, values.

These tests prove the steps are wired in the right order. The real database
store is tested in test_pipeline_store_db.py and test_pipeline_db.py.
"""

import io
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from kaagaz.extraction.learned import LearnedCorrection
from kaagaz.ingestion import sniff
from kaagaz.ingestion.policy import UploadPolicy, limits_by_type
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.jobs.worker import Handler, JobFailed, Worker
from kaagaz.pipeline import DocumentProcessor, ReadingSocket
from kaagaz.reading.csv_reader import CsvReader
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import BoxLocation, CellLocation, PageInfo, Span, TooManyPages
from kaagaz.reading.xlsx_reader import XlsxLimits, XlsxReader
from kaagaz.scanning.gate import DocumentStatus, ScanGate, Verdict
from tests.fakes import (
    FakeEmbedder,
    FakePrepared,
    FakePreparer,
    FakeScanner,
    FakeVision,
    MemoryDocuments,
    MemoryPageImages,
    MemoryPipelineStore,
    MemoryStatuses,
    MemoryStorage,
    ModelDown,
    PresetSocket,
    fake_sheet_pages,
)
from tests.lease_queue import FakeClock, LeaseQueue
from tests.support import PDF_BYTES, TEST_POLICY, SeqIds
from tests.xlsx_builder import make_xlsx, make_zip, sheet_xml

CUSTOMER = "cust-a"
CSV = b"invoice,amount\nINV-1,100\nINV-2,250\n"
# Real .xlsx files are a few KB, so the pipeline tests allow more than TEST_POLICY.
PIPELINE_POLICY = UploadPolicy(
    max_bytes=100_000,
    allowed_types=TEST_POLICY.allowed_types,
    type_limits=limits_by_type(pdf=100_000, image=100_000, sheet=100_000),
)
XLSX_LIMITS = XlsxLimits(
    max_entries=100,
    max_member_bytes=100_000,
    max_total_bytes=500_000,
    max_cells=10_000,
    max_text_chars=10_000_000,
)
LEASE_SECONDS = 30
MAX_ATTEMPTS = 3
EXHAUSTED_AT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@dataclass
class System:
    upload: UploadService
    worker: Worker
    processor: DocumentProcessor
    queue: LeaseQueue
    clock: FakeClock
    statuses: MemoryStatuses
    store: MemoryPipelineStore
    storage: MemoryStorage
    page_images: MemoryPageImages
    scanner: FakeScanner
    repo: MemoryDocuments
    gate: ScanGate


def real_socket(max_cells: int = 10_000) -> ReaderSocket:
    return ReaderSocket({sniff.TEXT: CsvReader(max_cells=max_cells), sniff.ZIP: XlsxReader(XLSX_LIMITS)})


def make_worker(queue: LeaseQueue, handler: Handler, repo: MemoryDocuments) -> Worker:
    """The worker as worker_main wires it: the delivery after the last attempt marks the document."""
    return Worker(
        queue,
        handler,
        max_attempts=MAX_ATTEMPTS,
        on_exhausted=lambda job: repo.mark_exhausted(
            job.customer_id, job.document_id, now=EXHAUSTED_AT, max_attempts=MAX_ATTEMPTS
        ),
        on_attempt=lambda job, attempt: repo.record_attempt(
            job.customer_id, job.document_id, attempt, MAX_ATTEMPTS
        ),
    )


def build(
    verdict: Verdict = Verdict.CLEAN,
    *,
    scanner: FakeScanner | None = None,
    socket: ReadingSocket | None = None,
    preparer: FakePreparer | None = None,
    embed: FakeEmbedder | None = None,
    vision: FakeVision | None = None,
) -> System:
    clock = FakeClock()
    queue = LeaseQueue(clock, lease_seconds=LEASE_SECONDS, max_attempts=MAX_ATTEMPTS + 3)
    statuses, storage, store, page_images = (
        MemoryStatuses(),
        MemoryStorage(),
        MemoryPipelineStore(),
        MemoryPageImages(),
    )
    repo = MemoryDocuments(statuses)
    upload = UploadService(PIPELINE_POLICY, repo, storage, queue, new_id=SeqIds())
    scanner = scanner or FakeScanner(verdict)
    gate = ScanGate(storage, scanner, storage, statuses)
    processor = DocumentProcessor(
        gate=gate,
        statuses=statuses,
        documents=repo,
        socket=socket or real_socket(),
        prepare=(preparer or FakePreparer()).prepare,
        sheet_pages=fake_sheet_pages,
        page_images=page_images,
        store=store,
        embed=embed,
        vision=vision,
        model_errors=(ModelDown,),
    )
    return System(
        upload,
        make_worker(queue, processor, repo),
        processor,
        queue,
        clock,
        statuses,
        store,
        storage,
        page_images,
        scanner,
        repo,
        gate,
    )


def processor_with(system: System, socket: ReadingSocket) -> DocumentProcessor:
    """Another processor over the same stores, with its own socket."""
    return DocumentProcessor(
        gate=system.gate,
        statuses=system.statuses,
        documents=system.repo,
        socket=socket,
        prepare=FakePreparer().prepare,
        sheet_pages=fake_sheet_pages,
        page_images=system.page_images,
        store=system.store,
    )


class PipelineTest(unittest.TestCase):
    def test_clean_csv_becomes_one_piece_per_row_and_is_ready(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id

        self.assertTrue(system.worker.run_once())
        pieces = system.store.for_document(CUSTOMER, doc)
        self.assertEqual(
            [p.text for p in pieces],
            [
                "A: invoice | B: amount",
                "A: INV-1 | B: 100",
                "A: INV-2 | B: 250",
            ],
        )
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)
        self.assertEqual(system.queue.pending(), 0)

    def test_excel_file_becomes_one_piece_per_row(self) -> None:
        system = build()
        workbook = make_xlsx(
            [("Sales", sheet_xml('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>250</v></c></row>'))],
            shared_strings=["INV-2"],
        )
        doc = system.upload.accept(CUSTOMER, io.BytesIO(workbook)).document_id
        system.worker.run_once()
        self.assertEqual([p.text for p in system.store.for_document(CUSTOMER, doc)], ["A: INV-2 | B: 250"])
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.READY)

    def test_word_file_is_marked_needs_a_reader(self) -> None:
        system = build()
        docx = make_zip({"word/document.xml": b"<document/>"})
        doc = system.upload.accept(CUSTOMER, io.BytesIO(docx)).document_id
        system.worker.run_once()
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.NEEDS_READER)
        self.assertEqual(system.queue.failed, {})

    def test_pdf_is_marked_needs_a_reader_and_the_job_ends_normally(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.NEEDS_READER)
        self.assertEqual(system.store.for_document(CUSTOMER, doc), [])
        self.assertEqual(system.queue.failed, {})

    def test_infected_file_is_never_read(self) -> None:
        system = build(Verdict.INFECTED)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.worker.run_once()
        self.assertEqual(system.store.for_document(CUSTOMER, doc), [])
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.REJECTED)
        self.assertEqual([f.code for f in system.queue.failed.values()], ["virus_found"])

    def test_broken_csv_fails_cleanly_and_the_next_file_still_runs(self) -> None:
        system = build()
        broken = system.upload.accept(CUSTOMER, io.BytesIO(b'a,"never closed\n')).document_id
        good = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id

        system.worker.run_once()
        system.worker.run_once()
        self.assertEqual([f.code for f in system.queue.failed.values()], ["malformed_csv"])
        self.assertEqual(system.store.for_document(CUSTOMER, broken), [])
        self.assertEqual(len(system.store.for_document(CUSTOMER, good)), 3)

    def test_file_changed_after_upload_is_refused(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.storage.put(CUSTOMER, doc, io.BytesIO(b"swapped,content\n"))
        system.worker.run_once()
        self.assertEqual([f.code for f in system.queue.failed.values()], ["content_changed"])
        self.assertEqual(system.store.for_document(CUSTOMER, doc), [])

    def test_file_without_a_record_is_never_read_or_scanned(self) -> None:
        system = build()
        system.storage.put(CUSTOMER, "orphan", io.BytesIO(CSV))  # a file, no record
        with self.assertRaises(JobFailed) as ctx:
            system.processor(Job(CUSTOMER, "orphan"), lambda: None)
        self.assertEqual(ctx.exception.code, "document_missing")
        self.assertEqual(system.scanner.scanned, [])
        self.assertEqual(system.store.saves, 0)

    def test_document_rejected_during_processing_writes_no_pieces(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id

        class RejectingSocket(ReaderSocket):
            # Simulates an overlapping delivery rejecting the file mid-run.
            def read(
                self,
                detected_type: str,
                data: bytes,
                *,
                on_page: Callable[[RouteDecision | None], None] | None = None,
            ) -> list[Span]:
                system.statuses.set_status(CUSTOMER, doc, DocumentStatus.REJECTED)
                return super().read(detected_type, data, on_page=on_page)

        socket = RejectingSocket({sniff.TEXT: CsvReader(max_cells=100), sniff.ZIP: XlsxReader(XLSX_LIMITS)})
        processor = processor_with(system, socket)
        with self.assertRaises(JobFailed) as ctx:
            processor(Job(CUSTOMER, doc), lambda: None)
        self.assertEqual(ctx.exception.code, "not_scanned")
        self.assertEqual(system.store.for_document(CUSTOMER, doc), [])
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.REJECTED)

    def test_a_later_rejection_removes_pieces_from_an_earlier_clean_run(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.worker.run_once()
        self.assertEqual(len(system.store.for_document(CUSTOMER, doc)), 3)

        system.scanner.verdict = Verdict.INFECTED  # new signatures on a rerun
        with self.assertRaises(JobFailed):
            system.processor(Job(CUSTOMER, doc), lambda: None)
        self.assertEqual(system.store.for_document(CUSTOMER, doc), [])
        self.assertIsNone(system.store.content(CUSTOMER, doc))  # values and pages too

    def test_rejected_document_is_never_rescanned_or_read(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.statuses.set_status(CUSTOMER, doc, DocumentStatus.REJECTED)
        system.worker.run_once()
        self.assertEqual([f.code for f in system.queue.failed.values()], ["rejected"])
        self.assertEqual(system.store.for_document(CUSTOMER, doc), [])


class StatusAndFailureTest(unittest.TestCase):
    def test_a_type_with_no_reader_is_needs_reader_and_keeps_its_pages(self) -> None:
        page = FakePreparer({sniff.PDF: _prepared(pages=2)})
        system = build(preparer=page)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()

        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.NEEDS_READER)
        self.assertEqual([p.info.page for p in system.store.pages_for(CUSTOMER, doc)], [1, 2])
        self.assertEqual(system.store.values_for(CUSTOMER, doc), [])
        self.assertEqual(system.repo.page_counts[(CUSTOMER, doc)], 2)
        self.assertEqual(system.queue.failed, {})
        self.assertNotIn((CUSTOMER, doc), system.repo.failure_codes)

    def test_a_read_failure_marks_the_document_failed_with_its_code(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(b'a,"never closed\n')).document_id
        system.worker.run_once()
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.FAILED)
        self.assertEqual(system.repo.failure_codes[(CUSTOMER, doc)], "malformed_csv")

    def test_a_preparation_failure_marks_the_document_failed_and_reads_nothing(self) -> None:
        socket = PresetSocket({sniff.PDF: [_line("Total: 5", 0.1)]})
        system = build(socket=socket, preparer=FakePreparer(error="too_many_pages"))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        self.assertEqual(system.repo.failure_codes[(CUSTOMER, doc)], "too_many_pages")
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.FAILED)
        self.assertEqual(system.store.saves, 0)
        self.assertNotIn((CUSTOMER, doc), system.repo.page_counts)  # a plain ReadError has no count

    def test_a_pdf_with_too_many_pages_keeps_its_real_page_count_and_fails(self) -> None:
        socket = PresetSocket({sniff.PDF: [_line("Total: 5", 0.1)]})
        system = build(socket=socket, preparer=FakePreparer(error=TooManyPages(180)))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        self.assertEqual(system.repo.page_counts[(CUSTOMER, doc)], 180)  # so the message can name it
        self.assertEqual(system.repo.failure_codes[(CUSTOMER, doc)], "too_many_pages")
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.FAILED)
        self.assertEqual(system.store.saves, 0)
        self.assertEqual(system.page_images.images, {})

    def test_a_changed_file_is_marked_failed(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.storage.put(CUSTOMER, doc, io.BytesIO(b"swapped,content\n"))
        system.worker.run_once()
        self.assertEqual(system.repo.failure_codes[(CUSTOMER, doc)], "content_changed")

    def test_a_retryable_failure_marks_nothing(self) -> None:
        system = build(scanner=FakeScanner(error=True))  # the scanner is down
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.worker.run_once()
        self.assertIsNone(system.statuses.get_status(CUSTOMER, doc))
        self.assertNotIn((CUSTOMER, doc), system.repo.failure_codes)

    def test_a_retryable_failure_on_every_delivery_ends_failed_retries_exhausted(self) -> None:
        system = build(scanner=FakeScanner(error=True))  # the scanner stays down
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        for _ in range(MAX_ATTEMPTS + 1):
            system.worker.run_once()
            system.clock.advance(LEASE_SECONDS)

        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.FAILED)
        self.assertEqual(system.repo.failure_codes[(CUSTOMER, doc)], "retries_exhausted")
        self.assertEqual([f.code for f in system.queue.failed.values()], ["retries_exhausted"])
        self.assertEqual(len(system.scanner.scanned), MAX_ATTEMPTS)  # the marking delivery reads nothing

    def test_a_crash_on_every_delivery_still_ends_failed_retries_exhausted(self) -> None:
        class CrashingPreparer(FakePreparer):
            def prepare(self, detected_type: str, data: bytes) -> FakePrepared:
                raise RuntimeError("a bug, after the scan passed")

        system = build(preparer=CrashingPreparer())
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        for _ in range(MAX_ATTEMPTS):
            with self.assertRaises(RuntimeError):
                system.worker.run_once()
            system.clock.advance(LEASE_SECONDS)
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.CLEAN)  # "Reading…"
        system.worker.run_once()

        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.FAILED)
        self.assertEqual(system.repo.failure_codes[(CUSTOMER, doc)], "retries_exhausted")
        self.assertEqual([f.code for f in system.queue.failed.values()], ["retries_exhausted"])

    def test_an_infected_file_stays_rejected_and_keeps_its_reason(self) -> None:
        system = build(Verdict.INFECTED)
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.worker.run_once()
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.REJECTED)
        self.assertEqual(system.repo.failure_codes[(CUSTOMER, doc)], "virus_found")

        # A redelivery is refused at once and does not overwrite the reason.
        with self.assertRaises(JobFailed) as ctx:
            system.processor(Job(CUSTOMER, doc), lambda: None)
        self.assertEqual(ctx.exception.code, "rejected")
        self.assertIs(system.statuses.get_status(CUSTOMER, doc), DocumentStatus.REJECTED)
        self.assertEqual(system.repo.failure_codes[(CUSTOMER, doc)], "virus_found")


class SavedContentTest(unittest.TestCase):
    def test_spreadsheet_values_page_and_reader_output_are_saved(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.worker.run_once()

        values = system.store.values_for(CUSTOMER, doc)
        self.assertEqual(
            [(v.record_key, v.field, v.value_text, v.label, v.label_reason) for v in values],
            [
                ("sheet:1:row:2", "invoice", "INV-1", "verified", "direct_cell"),
                ("sheet:1:row:2", "amount", "100", "verified", "direct_cell"),
                ("sheet:1:row:3", "invoice", "INV-2", "verified", "direct_cell"),
                ("sheet:1:row:3", "amount", "250", "verified", "direct_cell"),
            ],
        )
        self.assertEqual(values[1].location, CellLocation(1, 2, 2))
        self.assertEqual(str(values[3].value_number), "250")
        self.assertIsNone(values[0].value_number)

        (page,) = system.store.pages_for(CUSTOMER, doc)
        self.assertEqual(
            (page.info.page, page.info.width, page.info.height, page.info.unit), (1, 2.0, 3.0, "cells")
        )
        self.assertFalse(page.has_image)
        self.assertEqual(system.repo.page_counts[(CUSTOMER, doc)], 1)

        (output,) = system.store.outputs_for(CUSTOMER, doc)
        self.assertEqual((output.role, output.reader, output.version), ("first", "csv", "1"))
        self.assertEqual(len(output.spans), 6)
        self.assertTrue(all(p.label == "verified" for p in system.store.for_document(CUSTOMER, doc)))

    def test_a_redelivered_job_replaces_pages_outputs_pieces_and_values(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.worker.run_once()
        first = system.store.content(CUSTOMER, doc)

        system.processor(Job(CUSTOMER, doc), lambda: None)  # delivered again
        again = system.store.content(CUSTOMER, doc)
        assert first is not None and again is not None
        self.assertEqual(system.store.saves, 2)
        self.assertEqual(again, first)
        self.assertEqual(len(again.values), 4)
        self.assertEqual(len(again.pieces), 3)
        self.assertEqual(len(again.pages), 1)
        self.assertEqual(len(again.outputs), 1)

    def test_identity_numbers_are_kept_exactly_as_read(self) -> None:
        system = build()
        # FAKE numbers, made up for this test.
        fake = (
            b"name,pan,aadhaar,amount\n"
            b"Asha,ABCDE1234F,2345 6789 0123,100\n"
            b"Ravi,PQRST6789K,3456-7890-1234,200\n"
        )
        doc = system.upload.accept(CUSTOMER, io.BytesIO(fake)).document_id
        system.worker.run_once()

        content = system.store.content(CUSTOMER, doc)
        assert content is not None
        stored = [s.text for o in content.outputs for s in o.spans]
        stored += [s.raw for o in content.outputs for s in o.spans]
        stored += [p.text for p in content.pieces]
        stored += [v.value_text for v in content.values] + [v.value_raw for v in content.values]
        joined = " ".join(stored)
        for number in ("ABCDE1234F", "PQRST6789K", "2345 6789 0123", "3456-7890-1234"):
            self.assertIn(number, joined)
        self.assertEqual(
            {v.field: v.value_text for v in content.values if v.record_key == "sheet:1:row:2"},
            {"name": "Asha", "pan": "ABCDE1234F", "aadhaar": "2345 6789 0123", "amount": "100"},
        )

    def test_a_learned_correction_is_applied_to_a_matching_value(self) -> None:
        system = build()
        system.store.learned[CUSTOMER] = [LearnedCorrection("invoice", "INV-1", "INV-001")]
        system.store.learned["cust-b"] = [LearnedCorrection("invoice", "INV-2", "B-ONLY")]
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.worker.run_once()

        by_key = {(v.record_key, v.field): v for v in system.store.values_for(CUSTOMER, doc)}
        fixed = by_key[("sheet:1:row:2", "invoice")]
        self.assertEqual((fixed.value_text, fixed.value_raw), ("INV-001", "INV-1"))
        self.assertEqual((fixed.label, fixed.label_reason), ("verified", "learned_correction"))
        other = by_key[("sheet:1:row:3", "invoice")]  # another customer's correction never applies
        self.assertEqual((other.value_text, other.label_reason), ("INV-2", "direct_cell"))

    def test_a_learned_correction_sets_the_number_from_the_corrected_text(self) -> None:
        system = build()
        system.store.learned[CUSTOMER] = [LearnedCorrection("amount", "250", "2,500.00")]
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV)).document_id
        system.worker.run_once()
        by_key = {(v.record_key, v.field): v for v in system.store.values_for(CUSTOMER, doc)}
        self.assertEqual(str(by_key[("sheet:1:row:3", "amount")].value_number), "2500.00")


class ContextAndScriptTest(unittest.TestCase):
    """C4, L2, L3 and L4 in the pipeline, with no model (no card, so no type or date)."""

    def test_pieces_get_headers_and_latin_spellings_and_the_languages_come_from_them(self) -> None:
        spans = [
            _direct("Sharma Traders", page=1, top=0.1),
            _direct("पाटील शिंदे", page=1, top=0.5),  # a gap: a new piece...
            _direct("शिंदे", page=2, top=0.05),  # ...that runs on to page 2 (C1)
        ]
        system = build(socket=PresetSocket({sniff.PDF: spans}))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES), display_name="report.pdf").document_id
        system.worker.run_once()

        content = system.store.content(CUSTOMER, doc)
        assert content is not None
        self.assertEqual(
            [(p.text, p.context_header, p.text_latin) for p in content.pieces],
            [
                ("Sharma Traders", "Document: report.pdf | Page 1", None),
                (
                    "पाटील शिंदे\nशिंदे",
                    "Document: report.pdf | Pages 1-2 | Before: Sharma Traders",
                    "patil shinde\nshinde",
                ),
            ],
        )
        self.assertEqual(content.facts.languages, ("en", "other"))
        self.assertIsNone(content.facts.doc_type)

    def test_devanagari_digits_are_read_as_numbers_and_the_text_is_kept_as_printed(self) -> None:
        system = build(socket=PresetSocket({sniff.PDF: [_direct("Total: ₹ १,१८०.५०", page=1, top=0.1)]}))
        doc = system.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id
        system.worker.run_once()
        (value,) = system.store.values_for(CUSTOMER, doc)
        self.assertEqual(
            (value.field, value.value_text, str(value.value_number)),
            ("total_amount", "₹ १,१८०.५०", "1180.50"),
        )

    def test_a_sheet_is_en_and_its_rows_name_the_sheet(self) -> None:
        system = build()
        doc = system.upload.accept(CUSTOMER, io.BytesIO(CSV), display_name="sales.csv").document_id
        system.worker.run_once()
        content = system.store.content(CUSTOMER, doc)
        assert content is not None
        self.assertEqual(content.facts.languages, ("en",))
        self.assertEqual(
            [p.context_header for p in content.pieces][:2],
            [
                "Document: sales.csv | Sheet 1",
                "Document: sales.csv | Sheet 1 | Before: A: invoice | B: amount",
            ],
        )


def _direct(text: str, *, page: int, top: float) -> Span:
    """A line read straight from a PDF's text layer."""
    return Span(text, text, BoxLocation(page, 0.1, top, 0.9, top + 0.02), origin="text_layer")


def _prepared(*, pages: int) -> FakePrepared:
    return FakePrepared([PageInfo(n, 612.0, 792.0, "pt") for n in range(1, pages + 1)], {})


def _line(text: str, confidence: float | None, page: int = 1, top: float = 0.1) -> Span:
    return Span(text, text, BoxLocation(page, 0.1, top, 0.9, top + 0.02), confidence)


if __name__ == "__main__":
    unittest.main()
