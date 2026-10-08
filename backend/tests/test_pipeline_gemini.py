"""The pipeline with the Gemini reader (PRD 5): the page cache around a document's
reading (G7, F5), each page's reading result (RT2, F1, F2, F4), the checker (V1 to V4),
word marks (F3), the run's reader counters, a reader that is down (G6, Q7), and the
keyless mode.

The reader is a scripted ``PageEngine`` behind the real ``MeteredOcrEngine`` and the
real readers (tests/pdf_builder.py builds the PDFs); stores are the in-memory fakes
(tests/fakes.py). No model and no RapidOCR are called: the checker is a fake too.
"""

import contextlib
import functools
import io
import logging
import sys
import unittest
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import ModuleType
from typing import Any
from unittest import mock

from PIL import Image

from kaagaz.db.engine import Database, make_engine
from kaagaz.db.page_reads import PageKey
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.jobs.worker import JobFailed
from kaagaz.pipeline import CHECK_MARGIN, DocumentProcessor, PageRecord, RunTotals, build_processor
from kaagaz.preparing.pages import prepare_pages
from kaagaz.reading.ocr import DocumentPages, MeteredOcrEngine, OcrLine, PageRead
from kaagaz.reading.rapid_pool import RapidOcrPool, shared_pool
from kaagaz.reading.wiring import GEMINI_OCR_MODULE, build_reader_socket
from kaagaz.scanning.gate import DocumentStatus, ScanGate, Verdict
from kaagaz.settings import Settings
from tests.fakes import (
    FakeScanner,
    FakeVision,
    MemoryDocuments,
    MemoryPageImages,
    MemoryPipelineStore,
    MemoryProgress,
    MemoryQueue,
    MemoryRuns,
    MemoryStatuses,
    MemoryStorage,
    ModelDown,
    fake_sheet_pages,
)
from tests.pdf_builder import Page, Picture, Text, make_pdf
from tests.picture_support import text_like_picture
from tests.settings_factory import make_settings
from tests.support import SeqIds
from tests.test_pipeline import PIPELINE_POLICY

CUSTOMER = "cust-a"
T0 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
MODEL = "gemini-3.1-pro-preview"
SETTINGS = make_settings(page_image_dpi=72, ocr_dpi=72)
# A scan (a picture of text, no text layer): drawn at 72 DPI it is 612 x 792 pixels, and its
# picture is 153 px over 8.5 in, about 18 DPI, so it is low resolution (R4).
SCANNED = Page(picture=Picture(text_like_picture(153, 198), 0, 0, 612, 792))
SHARP = Page(picture=Picture(text_like_picture(1836, 2376), 0, 0, 612, 792))  # 216 DPI
WHITE = Page(picture=Picture(Image.new("RGB", (153, 198), "white"), 0, 0, 612, 792))
TEXT_PAGE = Page(texts=[Text(72, 700, "Invoice No: INV-17 from Sharma Traders, Pune, for 12 bags")])
TOTAL = OcrLine("Total: 1,180.00", 60, 400, 400, 420, None, "paragraph", box_source="found")


def beat() -> None:
    """A heartbeat that does nothing."""


def page_read(
    *lines: OcrLine,
    read_by: str = MODEL,
    quality: str | None = "good",
    issues: tuple[str, ...] = (),
    cached: bool = False,
    tokens: tuple[int, int] = (1700, 4000),
    continues: bool = False,
) -> PageRead:
    """A page as ``read_by`` read it (page_no is set when it is read): the local reader
    and the page cache bill no tokens."""
    billed = read_by == MODEL and not cached
    return PageRead(
        page_no=0,
        lines=lines,
        quality=quality,
        handwritten=False,
        scripts=("latin",),
        issues=issues,
        read_by=read_by,
        input_tokens=tokens[0] if billed else 0,
        output_tokens=tokens[1] if billed else 0,
        cached=cached,
        continues_on_next_page=continues,
    )


class ScriptedReader:
    """A ``PageEngine`` with a page cache context, like the Gemini reader: page n gives
    ``pages[n]`` (an exception is raised). Notes the document open while each page is
    read, and every document opened."""

    name = "gemini"
    version = f"{MODEL};prompt=g1"

    def __init__(self, pages: dict[int, PageRead | Exception]) -> None:
        self.pages = pages
        self.open: DocumentPages | None = None
        self.opened: list[DocumentPages] = []
        self.read_inside: list[DocumentPages | None] = []

    def read(self, image: Image.Image) -> list[OcrLine]:
        return list(self.read_page(image, page_no=1).lines)

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        self.read_inside.append(self.open)
        scripted = self.pages[page_no]
        if isinstance(scripted, Exception):
            raise scripted
        return replace(scripted, page_no=page_no)

    @contextlib.contextmanager
    def document(self, pages: DocumentPages) -> Iterator[None]:
        self.open = pages
        self.opened.append(pages)
        try:
            yield
        finally:
            self.open = None


class FakeChecker:
    """RapidOCR's recogniser (``Checker``): ``text(crop size)`` for each crop; notes the
    crops' sizes and whether the Devanagari model was asked for."""

    def __init__(self, text: str | Callable[[tuple[int, int]], str] = "Total: 1,180.00") -> None:
        self._text = text
        self.crops: list[tuple[tuple[int, int], bool]] = []

    def read_crop(self, image: Image.Image, *, devanagari: bool) -> str:
        self.crops.append((image.size, devanagari))
        return self._text(image.size) if callable(self._text) else self._text


class MemoryCache:
    """The page cache (``PageCache``): nothing in it unless a test puts it there."""

    def __init__(self) -> None:
        self.rows: dict[PageKey, tuple[dict[str, Any], int, int]] = {}

    def get(self, key: PageKey) -> tuple[dict[str, Any], int, int] | None:
        return self.rows.get(key)

    def put(self, key: PageKey, result: dict[str, Any], input_tokens: int, output_tokens: int) -> None:
        self.rows[key] = (result, input_tokens, output_tokens)


class MemoryReadAgain:
    """``ReadAgainStore`` as PgDocuments: marked pages per document, cleared behind the fence."""

    def __init__(self, runs: MemoryRuns) -> None:
        self.marks: dict[tuple[str, str], set[int]] = {}
        self._runs = runs
        self.down = False

    def pages_to_read_again(self, customer_id: str, document_id: str) -> tuple[int, ...]:
        return tuple(sorted(self.marks.get((customer_id, document_id), set())))

    def clear_read_again(self, customer_id: str, document_id: str, *, run_id: str | None = None) -> None:
        if self.down:
            raise BackendUnavailable("database")
        if run_id is not None and not self._runs.is_open(customer_id, document_id, run_id):
            return
        self.marks.pop((customer_id, document_id), None)


@dataclass
class Rig:
    upload: UploadService
    statuses: MemoryStatuses
    store: MemoryPipelineStore
    runs: MemoryRuns
    progress: MemoryProgress
    read_again: MemoryReadAgain
    cache: MemoryCache
    processor: DocumentProcessor
    checker: FakeChecker | None
    reader: ScriptedReader | None = None
    jobs: list[Job] = field(default_factory=list)

    def job(self, data: bytes) -> Job:
        job = Job(CUSTOMER, self.upload.accept(CUSTOMER, io.BytesIO(data)).document_id)
        self.jobs.append(job)
        return job

    def run(self, data: bytes) -> Job:
        job = self.job(data)
        self.processor(job, beat)
        return job

    def pages(self, job: Job) -> list[PageRecord]:
        return self.store.pages_for(job.customer_id, job.document_id)

    def totals(self, job: Job) -> RunTotals:
        (run,) = self.runs.for_document(job.customer_id, job.document_id)
        assert run.totals is not None
        return run.totals

    def status(self, job: Job) -> DocumentStatus | None:
        return self.statuses.get_status(job.customer_id, job.document_id)


def rig(
    engine: Any = None,
    *,
    checker: FakeChecker | None = None,
    ocr_model: str | None = MODEL,
    max_check_crops: int = 200,
    vision: FakeVision | None = None,
    settings: Settings = SETTINGS,
) -> Rig:
    statuses, storage = MemoryStatuses(), MemoryStorage()
    repo = MemoryDocuments(statuses)
    runs = MemoryRuns(repo)
    repo.runs = runs
    statuses.runs = runs
    store, progress = MemoryPipelineStore(runs), MemoryProgress(repo, statuses)
    read_again, cache = MemoryReadAgain(runs), MemoryCache()
    meter = None if engine is None else MeteredOcrEngine(engine, timer=lambda: 0.0)
    prepare = functools.partial(
        prepare_pages,
        dpi=settings.page_image_dpi,
        max_pages=10,
        max_image_pixels=40_000_000,
        blank_stddev=settings.blank_page_stddev,
        low_res_dpi=settings.low_res_dpi,
    )
    processor = DocumentProcessor(
        gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, statuses),
        statuses=statuses,
        documents=repo,
        socket=build_reader_socket(settings, meter),
        prepare=prepare,
        sheet_pages=fake_sheet_pages,
        page_images=MemoryPageImages(),
        store=store,
        vision=vision,
        model_errors=(ModelDown,),
        runs=runs,
        progress=progress,
        ocr_meter=meter,
        clock=lambda: T0,
        timer=lambda: 0.0,
        max_check_crops=max_check_crops,
        vision_model="qwen-vision" if vision is not None else None,
        ocr_model=ocr_model,
        checker=checker,
        checker_model="rapidocr 3.9.2" if checker is not None else None,
        page_cache=cache,
        read_again=read_again,
    )
    upload = UploadService(PIPELINE_POLICY, repo, storage, MemoryQueue(), new_id=SeqIds())
    reader = engine if isinstance(engine, ScriptedReader) else None
    return Rig(upload, statuses, store, runs, progress, read_again, cache, processor, checker, reader)


def reasons(system: Rig, job: Job) -> list[tuple[str, str]]:
    return [(value.field, value.label_reason) for value in system.store.values_for(CUSTOMER, job.document_id)]


class PageCacheTest(unittest.TestCase):
    """G7, F5: the document's page cache is open around its reading, and only then."""

    def test_g7_the_page_cache_is_open_while_the_pages_are_read_and_closed_after(self) -> None:
        reader = ScriptedReader({1: page_read(TOTAL), 2: page_read(TOTAL)})
        system = rig(reader, checker=FakeChecker())
        job = system.run(make_pdf([SCANNED, SCANNED]))
        (opened,) = reader.opened
        self.assertEqual((opened.customer_id, opened.document_id), (CUSTOMER, job.document_id))
        self.assertIs(opened.cache, system.cache)
        self.assertEqual(opened.read_again, frozenset())
        self.assertEqual(reader.read_inside, [opened, opened])
        self.assertIsNone(reader.open)  # closed before the next job

    def test_g7_each_job_opens_its_own_document(self) -> None:
        reader = ScriptedReader({1: page_read(TOTAL)})
        system = rig(reader, checker=FakeChecker())
        first, second = system.run(make_pdf([SCANNED])), system.run(make_pdf([SCANNED, WHITE]))
        self.assertEqual(
            [pages.document_id for pages in reader.opened], [first.document_id, second.document_id]
        )

    def test_g7_the_page_cache_is_closed_when_the_reading_fails(self) -> None:
        reader = ScriptedReader({1: page_read(TOTAL), 2: BackendUnavailable("gemini")})
        system = rig(reader, checker=FakeChecker())
        with self.assertRaises(BackendUnavailable):
            system.run(make_pdf([SCANNED, SCANNED]))
        self.assertEqual(len(reader.opened), 1)
        self.assertIsNone(reader.open)

    def test_f5_the_pages_to_read_again_go_to_the_reader_and_are_counted(self) -> None:
        reader = ScriptedReader({1: page_read(TOTAL, cached=True), 2: page_read(TOTAL)})
        system = rig(reader, checker=FakeChecker())
        job = system.job(make_pdf([SCANNED, SCANNED]))
        system.read_again.marks[(CUSTOMER, job.document_id)] = {2}
        system.processor(job, beat)
        self.assertEqual(reader.opened[0].read_again, frozenset({2}))
        totals = system.totals(job)
        self.assertEqual((totals.pages_read_again, totals.pages_cached, totals.pages_gemini), (1, 1, 1))

    def test_f5_a_try_a_backend_stopped_keeps_the_marks_for_the_next_try(self) -> None:
        reader = ScriptedReader({1: BackendUnavailable("gemini")})
        system = rig(reader, checker=FakeChecker())
        job = system.job(make_pdf([SCANNED]))
        system.read_again.marks[(CUSTOMER, job.document_id)] = {1}
        with self.assertRaises(BackendUnavailable):
            system.processor(job, beat)
        self.assertEqual(system.read_again.pages_to_read_again(CUSTOMER, job.document_id), (1,))

    def test_f5_a_document_that_fails_for_good_clears_its_marks(self) -> None:
        reader = ScriptedReader(
            {1: page_read(issues=("no_text",)), 2: page_read(TOTAL, issues=("reader_refused",))}
        )
        system = rig(reader, checker=FakeChecker())
        job = system.job(make_pdf([SCANNED, SCANNED]))
        system.read_again.marks[(CUSTOMER, job.document_id)] = {1}
        with self.assertRaises(JobFailed):
            system.processor(job, beat)
        self.assertEqual(system.read_again.pages_to_read_again(CUSTOMER, job.document_id), ())

    def test_f5_marks_that_cannot_be_cleared_are_logged_and_the_failure_goes_on(self) -> None:
        reader = ScriptedReader({1: page_read(issues=("no_text",))})
        system = rig(reader, checker=FakeChecker())
        job = system.job(make_pdf([SCANNED]))
        system.read_again.down = True
        with self.assertLogs("kaagaz.pipeline", logging.WARNING) as logs, self.assertRaises(JobFailed):
            system.processor(job, beat)
        self.assertIn("read_again_not_cleared", [record.getMessage() for record in logs.records])

    def test_f5_without_a_page_cache_nothing_is_opened_or_asked(self) -> None:
        reader = ScriptedReader({1: page_read(TOTAL)})
        system = rig(reader, checker=FakeChecker())
        system.processor._page_cache = None
        system.run(make_pdf([SCANNED]))
        self.assertEqual((reader.opened, reader.read_inside), ([], [None]))


class PageResultTest(unittest.TestCase):
    """RT2, F1, F2, F4, CX1: each page's reading result, saved with it."""

    def test_rt2_f1_every_page_is_saved_with_who_read_it_and_its_result(self) -> None:
        unsure = OcrLine("Seller: Sharma Traders", 60, 100, 400, 120, None, "paragraph", unsure=("Sharma",))
        illegible = OcrLine("Note [?] here", 60, 200, 400, 220, None, "paragraph", box_source="model")
        reader = ScriptedReader(
            {2: page_read(unsure, illegible, TOTAL, issues=("illegible_words", "unpinned"))}
        )
        system = rig(reader, checker=FakeChecker())
        job = system.run(make_pdf([TEXT_PAGE, SCANNED, WHITE]))
        text, scan, blank = system.pages(job)
        self.assertEqual((text.read_by, text.read_quality, text.read_issues), ("text_layer", "good", ()))
        self.assertEqual((blank.read_by, blank.read_quality, blank.read_issues), (None, "good", ()))
        self.assertEqual(
            (scan.read_by, scan.read_quality, scan.read_issues),
            (MODEL, "uncertain", ("low_resolution", "illegible_words", "unpinned")),  # the F1 table's order
        )
        self.assertEqual((scan.unsure_words, scan.illegible_words, scan.unpinned_lines), (1, 1, 1))

    def test_f1_a_sharp_scan_read_cleanly_is_good(self) -> None:
        system = rig(ScriptedReader({1: page_read(TOTAL)}), checker=FakeChecker())
        (page,) = system.pages(system.run(make_pdf([SHARP])))
        self.assertEqual((page.read_quality, page.read_issues, page.low_resolution), ("good", (), False))

    def test_f2_a_value_the_checker_reads_differently_adds_readers_disagree(self) -> None:
        system = rig(ScriptedReader({1: page_read(TOTAL)}), checker=FakeChecker("Total: 1,130.00"))
        job = system.run(make_pdf([SHARP]))
        (page,) = system.pages(job)
        self.assertEqual((page.read_quality, page.read_issues), ("uncertain", ("readers_disagree",)))
        self.assertEqual(reasons(system, job), [("total_amount", "readers_disagree")])

    def test_f2_a_page_the_reader_refused_is_unreadable_and_the_document_still_ready(self) -> None:
        reader = ScriptedReader(
            {
                1: page_read(TOTAL),
                2: page_read(TOTAL, read_by="rapidocr", quality=None, issues=("reader_refused",)),
            }
        )
        system = rig(reader, checker=FakeChecker())
        job = system.run(make_pdf([SHARP, SHARP]))
        self.assertIs(system.status(job), DocumentStatus.READY)  # F4: never held up by one bad page
        self.assertEqual([page.read_quality for page in system.pages(job)], ["good", "unreadable"])
        self.assertEqual(system.totals(job).pages_unreadable, 1)

    def test_f2_poor_with_more_than_30_percent_illegible_is_unreadable(self) -> None:
        line = OcrLine("[?] [?] ok", 60, 400, 400, 420, None, "paragraph", box_source="found")
        reader = ScriptedReader(
            {1: page_read(line, quality="poor", issues=("illegible_words",)), 2: page_read(TOTAL)}
        )
        system = rig(reader, checker=FakeChecker())
        job = system.run(make_pdf([SHARP, SHARP]))
        self.assertEqual([page.read_quality for page in system.pages(job)], ["unreadable", "good"])

    def test_f4_a_document_whose_every_page_is_unreadable_fails_unreadable(self) -> None:
        refused = page_read(TOTAL, read_by="rapidocr", quality=None, issues=("reader_refused",))
        system = rig(ScriptedReader({1: refused, 3: refused}), checker=FakeChecker())
        job = system.job(make_pdf([SCANNED, WHITE, SCANNED]))
        with self.assertRaises(JobFailed) as failed:
            system.processor(job, beat)
        self.assertEqual((failed.exception.code, failed.exception.retryable), ("unreadable", False))
        self.assertIs(system.status(job), DocumentStatus.FAILED)
        totals = system.totals(job)
        self.assertEqual(
            (totals.status, totals.error_code, totals.pages_unreadable), ("failed", "unreadable", 2)
        )
        self.assertEqual(system.checker.crops if system.checker else None, [])  # nothing checked or called

    def test_f4_a_readable_text_page_keeps_the_document_ready(self) -> None:
        refused = page_read(TOTAL, read_by="rapidocr", quality=None, issues=("reader_refused",))
        system = rig(ScriptedReader({2: refused}), checker=FakeChecker())
        job = system.run(make_pdf([TEXT_PAGE, SCANNED]))
        self.assertIs(system.status(job), DocumentStatus.READY)

    def test_cx1_continues_on_next_page_is_kept_on_the_page(self) -> None:
        reader = ScriptedReader({1: page_read(TOTAL, continues=True), 2: page_read(TOTAL)})
        system = rig(reader, checker=FakeChecker())
        job = system.run(make_pdf([SHARP, SHARP]))
        self.assertEqual([page.continues_on_next_page for page in system.pages(job)], [True, False])


class CheckerTest(unittest.TestCase):
    """V1 to V4 through the pipeline."""

    def test_v1_v2_each_value_box_is_cropped_with_a_4_percent_margin_and_agreement_verifies(self) -> None:
        checker = FakeChecker()
        system = rig(ScriptedReader({1: page_read(TOTAL)}), checker=checker)
        job = system.run(make_pdf([SHARP]))
        # The box is x 60..400, y 400..420 of a 612 x 792 page; 4% of its size on each side is 13.6
        # and 0.8 px: x 46.4..413.6 and y 399.2..420.8, rounded out to 368 x 22.
        ((size, devanagari),) = checker.crops
        self.assertEqual(CHECK_MARGIN, 0.04)
        self.assertEqual((size, devanagari), ((368, 22), False))
        self.assertEqual(reasons(system, job), [("total_amount", "readers_agree")])
        value = system.store.values_for(CUSTOMER, job.document_id)[0]
        self.assertEqual((value.label, value.second_reading), ("verified", "1,180.00"))
        totals = system.totals(job)
        self.assertEqual((totals.vision_crops, totals.model_vision), (1, "rapidocr 3.9.2"))

    def test_v1_a_devanagari_line_is_read_with_the_devanagari_model(self) -> None:
        line = OcrLine("Seller: रामचंद्र पाटील", 60, 400, 400, 420, None, "paragraph", box_source="found")
        checker = FakeChecker("Seller: रामचंद्र पाटील")
        system = rig(ScriptedReader({1: page_read(line)}), checker=checker)
        system.run(make_pdf([SHARP]))
        self.assertEqual([devanagari for _, devanagari in checker.crops], [True])

    def test_v3_a_devanagari_value_is_verified_only_by_exact_agreement(self) -> None:
        line = OcrLine("Seller: रामचंद्र पाटील", 60, 400, 400, 420, None, "paragraph", box_source="found")
        for reading, expected in (
            ("Seller: रामचंद्र पाटील", "readers_agree"),
            ("Seller: रामचंद्र पाटिल", "readers_disagree"),
            ("", "ocr_script"),
        ):
            with self.subTest(reading=reading):
                system = rig(ScriptedReader({1: page_read(line)}), checker=FakeChecker(reading))
                job = system.run(make_pdf([SHARP]))
                self.assertEqual(dict(reasons(system, job)).get("party_name"), expected)

    def test_v1_the_crops_stop_at_max_check_crops(self) -> None:
        lines = [
            OcrLine(
                f"Total: {n},180.00", 60, 100 * n, 400, 100 * n + 20, None, "paragraph", box_source="found"
            )
            for n in (1, 2, 3)
        ]
        checker = FakeChecker()
        system = rig(ScriptedReader({1: page_read(*lines)}), checker=checker, max_check_crops=2)
        job = system.run(make_pdf([SHARP]))
        self.assertEqual(len(checker.crops), 2)
        self.assertEqual(system.totals(job).vision_crops, 2)

    def test_v1_a_page_rapidocr_read_is_never_checked_by_rapidocr(self) -> None:
        """No self-agreement: a page read locally (Q7) keeps no second reading."""
        local = page_read(TOTAL, read_by="rapidocr", quality=None, issues=("read_locally",))
        checker = FakeChecker()
        system = rig(ScriptedReader({1: local}), checker=checker)
        job = system.run(make_pdf([SHARP]))
        self.assertEqual(checker.crops, [])
        self.assertEqual(reasons(system, job), [("total_amount", "local_reader")])  # Q7
        totals = system.totals(job)
        self.assertEqual((totals.pages_local, totals.pages_gemini, totals.model_vision), (1, 0, None))

    def test_v1_other_scanned_values_keep_the_vision_reading(self) -> None:
        vision = FakeVision("Total: 1,180.00")
        local = page_read(TOTAL, read_by="rapidocr", quality=None)
        system = rig(ScriptedReader({1: page_read(TOTAL), 2: local}), checker=FakeChecker(), vision=vision)
        job = system.run(make_pdf([SHARP, SHARP]))
        self.assertEqual(len(vision.pictures), 1)  # page 2 only: page 1 went to the checker
        self.assertEqual(system.totals(job).model_vision, "rapidocr 3.9.2")

    def test_v4_an_unsure_word_wins_over_the_checkers_agreement(self) -> None:
        line = OcrLine(
            "Total: 1,180.00", 60, 400, 400, 420, None, "paragraph", unsure=("1,180.00",), box_source="found"
        )
        system = rig(ScriptedReader({1: page_read(line)}), checker=FakeChecker())
        job = system.run(make_pdf([SHARP]))
        self.assertEqual(reasons(system, job), [("total_amount", "unsure_text")])
        (page,) = system.pages(job)
        self.assertEqual((page.read_quality, page.unsure_words), ("uncertain", 1))

    def test_g5_a_value_on_an_unpinned_line_is_box_unanchored(self) -> None:
        line = replace(TOTAL, box_source="model")
        system = rig(ScriptedReader({1: page_read(line, issues=("unpinned",))}), checker=FakeChecker())
        job = system.run(make_pdf([SHARP]))
        self.assertEqual(reasons(system, job), [("total_amount", "box_unanchored")])


class WordMarksTest(unittest.TestCase):
    """F3: word marks stay in the stored reader output and never become pieces or values."""

    def test_f3_word_marks_are_stored_but_left_out_of_pieces_and_values(self) -> None:
        line = OcrLine(
            "Total: 1,180.00", 60, 400, 400, 420, None, "paragraph", unsure=("1,180.00",), box_source="found"
        )
        system = rig(ScriptedReader({1: page_read(line)}), checker=FakeChecker())
        job = system.run(make_pdf([SHARP]))
        (first,) = [
            output for output in system.store.outputs_for(CUSTOMER, job.document_id) if output.role == "first"
        ]
        self.assertEqual(
            [(span.text, span.quality) for span in first.spans],
            [("Total: 1,180.00", None), ("1,180.00", "unsure")],
        )
        pieces = system.store.for_document(CUSTOMER, job.document_id)
        self.assertEqual([piece.text.splitlines()[-1] for piece in pieces], ["Total: 1,180.00"])
        self.assertEqual(len(system.store.values_for(CUSTOMER, job.document_id)), 1)


class RunCountersTest(unittest.TestCase):
    """processing_runs' reader counters (READER_COUNTERS) from the page reads."""

    def test_the_run_counts_model_local_cached_pages_and_tokens(self) -> None:
        reader = ScriptedReader(
            {
                1: page_read(TOTAL, tokens=(1700, 4000)),
                2: page_read(TOTAL, cached=True),
                3: page_read(TOTAL, read_by="rapidocr", quality=None, issues=("read_locally",)),
                4: page_read(TOTAL, tokens=(1800, 3500)),
            }
        )
        system = rig(reader, checker=FakeChecker())
        job = system.run(make_pdf([SHARP, SHARP, SHARP, SHARP]))
        totals = system.totals(job)
        self.assertEqual(
            (totals.pages_gemini, totals.pages_local, totals.pages_cached, totals.pages_read_again),
            (2, 1, 1, 0),
        )
        self.assertEqual(
            (totals.input_tokens, totals.output_tokens, totals.pages_unreadable), (3500, 7500, 0)
        )

    def test_a_run_a_backend_stopped_still_counts_the_pages_it_read(self) -> None:
        reader = ScriptedReader({1: page_read(TOTAL, tokens=(10, 20)), 2: BackendUnavailable("gemini")})
        system = rig(reader, checker=FakeChecker())
        job = system.job(make_pdf([SCANNED, SCANNED]))
        with self.assertRaises(BackendUnavailable):
            system.processor(job, beat)
        totals = system.totals(job)
        self.assertEqual((totals.pages_gemini, totals.input_tokens, totals.output_tokens), (1, 10, 20))

    def test_the_counters_start_from_zero_for_each_run(self) -> None:
        reader = ScriptedReader({1: page_read(TOTAL, tokens=(10, 20))})
        system = rig(reader, checker=FakeChecker())
        first, second = system.run(make_pdf([SHARP])), system.run(make_pdf([SHARP, WHITE]))
        self.assertEqual([system.totals(job).input_tokens for job in (first, second)], [10, 10])


class ReaderDownTest(unittest.TestCase):
    """G6, Q7: a reader that is down, or a key problem, makes the document wait."""

    def test_q7_an_outage_waits_for_the_next_try(self) -> None:
        system = rig(ScriptedReader({1: BackendUnavailable("gemini")}), checker=FakeChecker())
        job = system.job(make_pdf([SCANNED]))
        with self.assertRaises(BackendUnavailable):
            system.processor(job, beat)
        totals = system.totals(job)
        self.assertEqual((totals.status, totals.error_code), ("failed", "gemini_unavailable"))
        self.assertNotIn((CUSTOMER, job.document_id), system.progress.progress)  # "Waiting to try again"
        self.assertIsNot(system.status(job), DocumentStatus.FAILED)

    def test_g6_a_key_problem_waits_and_is_logged_at_error_with_its_code_only(self) -> None:
        system = rig(ScriptedReader({1: BackendUnavailable("gemini_key")}), checker=FakeChecker())
        job = system.job(make_pdf([SCANNED]))
        with self.assertLogs("kaagaz.pipeline", logging.ERROR) as logs, self.assertRaises(BackendUnavailable):
            system.processor(job, beat)
        (record,) = logs.records
        self.assertEqual(
            (record.levelno, getattr(record, "code", None)), (logging.ERROR, "gemini_key_unavailable")
        )
        self.assertEqual(system.totals(job).error_code, "gemini_key_unavailable")
        self.assertIsNot(system.status(job), DocumentStatus.FAILED)


class KeylessTest(unittest.TestCase):
    """OCR_ENGINE=rapidocr, MODEL_ENABLED=false: no model call, and values as before."""

    def test_keyless_a_rapidocr_scan_is_never_checked_and_its_values_need_review(self) -> None:
        local = page_read(TOTAL, read_by="rapidocr", quality=None)
        checker = FakeChecker()
        system = rig(ScriptedReader({1: local}), checker=checker, ocr_model=None)
        job = system.run(make_pdf([SHARP]))
        self.assertEqual(checker.crops, [])
        self.assertEqual(reasons(system, job), [("total_amount", "single_reader")])
        (page,) = system.pages(job)
        self.assertEqual((page.read_by, page.read_quality), ("rapidocr", "good"))
        totals = system.totals(job)
        self.assertEqual((totals.pages_gemini, totals.pages_local, totals.input_tokens), (0, 1, 0))


class PictureStorage(MemoryStorage):
    """MemoryStorage plus page pictures, like kaagaz.adapters.storage.S3Storage."""

    def put_page_image(self, customer_id: str, document_id: str, page_no: int, png: bytes) -> None:
        """Kept nowhere: no job runs in the wiring tests."""


class FakeGeminiModule:
    """Stands in for kaagaz.reading.gemini_ocr: notes each engine built and its pool."""

    def __init__(self) -> None:
        self.built: list[RapidOcrPool] = []
        self.module = ModuleType(GEMINI_OCR_MODULE)
        outer = self

        class GeminiEngine(ScriptedReader):
            @classmethod
            def from_settings(cls, settings: Settings, *, pool: RapidOcrPool) -> ScriptedReader:
                outer.built.append(pool)
                return cls({})

        self.module.__dict__["GeminiEngine"] = GeminiEngine


class WiringTest(unittest.TestCase):
    def build(self, settings: Settings) -> tuple[DocumentProcessor, FakeGeminiModule]:
        fake = FakeGeminiModule()
        db = Database(make_engine("postgresql://unused@localhost/unused"), settings.db_app_role)
        self.addCleanup(db.engine.dispose)
        with mock.patch.dict(sys.modules, {GEMINI_OCR_MODULE: fake.module}):
            processor = build_processor(
                settings=settings,
                db=db,
                storage=PictureStorage(),
                scanner=FakeScanner(Verdict.CLEAN),
                model=None,
            )
        return processor, fake

    def test_g1_ocr_engine_gemini_is_built_through_the_reading_wiring_with_the_shared_pool(self) -> None:
        settings = make_settings(ocr_engine="gemini", cpu_workers=2, max_check_crops=150)
        processor, fake = self.build(settings)
        self.assertEqual(fake.built, [shared_pool(2)])
        self.assertIs(processor._checker, shared_pool(2))
        self.assertEqual((processor._ocr_model, processor._max_check_crops), (MODEL, 150))
        self.assertIsNotNone(processor._page_cache)

    def test_keyless_rapidocr_builds_no_gemini_reader_checker_or_page_cache(self) -> None:
        processor, fake = self.build(make_settings(ocr_engine="rapidocr", model_enabled=False))
        self.assertEqual(fake.built, [])
        self.assertEqual(
            (processor._checker, processor._ocr_model, processor._page_cache), (None, None, None)
        )


if __name__ == "__main__":
    unittest.main()
