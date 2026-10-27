import io
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from PIL import Image, ImageDraw, ImageFont

from kaagaz.db.engine import Database
from kaagaz.ingestion import sniff
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading import ocr, wiring
from kaagaz.reading.ocr import (
    DocumentPages,
    MeteredOcrEngine,
    OcrLine,
    OcrTable,
    PageEngine,
    PageRead,
    RapidOcrEngine,
    as_page_engine,
    build_ocr_engine,
    is_word_mark,
    ocr_spans,
    reading_document,
)
from kaagaz.reading.rapid_pool import RapidOcrPool, shared_pool
from kaagaz.reading.spans import BoxLocation, ReadError, Span
from kaagaz.reading.wiring import GEMINI_OCR_MODULE, build_reader_socket
from kaagaz.settings import Settings, SettingsError
from tests.settings_factory import make_settings

# rapidocr is the optional ocr extra (pyproject.toml). CI installs it (".[dev,ocr]") and runs
# this module; an install without it skips the module instead of failing to collect (#239).
rapidocr = pytest.importorskip("rapidocr", reason='needs the ocr extra: pip install -e ".[dev,ocr]"')


def box_of(span: Span) -> BoxLocation:
    assert isinstance(span.location, BoxLocation)
    return span.location


class RapidOcrTest(unittest.TestCase):
    """One real run of the installed engine on large, clear printed text."""

    engine: RapidOcrEngine

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = RapidOcrEngine()

    def test_finds_clear_text_with_a_box_inside_the_image_and_a_confidence(self) -> None:
        image = Image.new("RGB", (900, 220), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=72)
        draw.text((60, 70), "INVOICE 1042", fill="black", font=font)
        left, top, right, bottom = draw.textbbox((60, 70), "INVOICE 1042", font=font)

        lines = self.engine.read(image)
        self.assertIn("INVOICE", " ".join(line.text for line in lines))
        for line in lines:
            self.assertTrue(0 <= line.x0 < line.x1 <= image.width and 0 <= line.y0 < line.y1 <= image.height)
            assert line.confidence is not None  # RapidOCR always gives one
            self.assertTrue(0.0 <= line.confidence <= 1.0)

        # The engine's boxes, as one line span of page fractions.
        (span,) = ocr_spans(lines, 1, image.width, image.height)
        self.assertEqual(span.text.replace(" ", ""), "INVOICE1042")
        box = box_of(span)
        self.assertTrue(box.is_valid())
        drawn_middle_x = (left + right) / 2 / image.width
        drawn_middle_y = (top + bottom) / 2 / image.height
        self.assertTrue(box.x0 < drawn_middle_x < box.x1 and box.y0 < drawn_middle_y < box.y1)
        self.assertLess(box.x0, left / image.width + 0.05)
        self.assertGreater(box.x1, right / image.width - 0.05)
        assert span.confidence is not None
        self.assertTrue(0.5 <= span.confidence <= 1.0)

    def test_a_blank_page_gives_no_lines(self) -> None:
        self.assertEqual(self.engine.read(Image.new("RGB", (300, 200), "white")), [])

    def test_name_and_version_are_the_installed_engine(self) -> None:
        self.assertEqual((self.engine.name, self.engine.version), ("rapidocr", metadata.version("rapidocr")))


class LazyImportTest(unittest.TestCase):
    def test_building_the_engine_does_not_import_rapidocr(self) -> None:
        # A fresh interpreter: this test process may have imported it already.
        code = (
            "import sys\n"
            "from kaagaz.reading.ocr import RapidOcrEngine\n"
            "RapidOcrEngine()\n"
            "sys.stdout.write(str('rapidocr' in sys.modules))\n"
        )
        result = subprocess.run(  # noqa: S603 - fixed arguments, this interpreter
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            cwd=pathlib.Path(__file__).resolve().parent.parent,
        )
        self.assertEqual(result.stdout, "False")


class FakeRapidOcr:
    """Stands in for one ``rapidocr.RapidOCR``: the same reading of every picture."""

    def __init__(self, texts: list[str], scores: list[float]) -> None:
        self.texts = texts
        self.scores = scores
        self.pages = 0

    def __call__(self, picture: Image.Image) -> SimpleNamespace:
        self.pages += 1
        boxes = [
            [[0, 10 * i], [90, 10 * i], [90, 10 * i + 8], [0, 10 * i + 8]] for i in range(len(self.texts))
        ]
        return SimpleNamespace(boxes=boxes, txts=self.texts, scores=self.scores)


# A 7/12 land record as the default recogniser reads it (Latin and Chinese letters),
# and as the Devanagari one reads it.
GARBLED = (["Gat 克拉马克 48", "Mauje 莫杰"], [0.62, 0.58])
DEVANAGARI = (["गट क्रमांक ४८", "मौजे शिरूर"], [0.91, 0.88])
CLEAN = (["INVOICE 1042"], [0.97])
DEVANAGARI_VERSION = metadata.version("rapidocr") + ";rec=devanagari_PP-OCRv5_rec_mobile"


class DevanagariReadAgainTest(unittest.TestCase):
    """A suspect page is read again with the Devanagari recogniser, from a file on disk,
    with fake engines: no real model and no network."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.model = pathlib.Path(folder.name) / "devanagari_PP-OCRv5_rec_mobile.onnx"
        self.model.write_bytes(b"model")
        self.built: list[dict[str, Any]] = []
        self.first = FakeRapidOcr(*GARBLED)
        self.second = FakeRapidOcr(*DEVANAGARI)
        for patch in (
            mock.patch.object(ocr, "build_rapidocr", self.build),
            mock.patch.object(ocr, "devanagari_model_path", lambda: self.model),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.engine = RapidOcrEngine()
        self.page = Image.new("RGB", (100, 40), "white")

    def build(self, params: dict[str, Any]) -> FakeRapidOcr:
        self.built.append(params)
        return self.second if "Rec.model_path" in params else self.first

    def texts(self, lines: list[OcrLine]) -> list[str]:
        return [line.text for line in lines]

    def test_a_suspect_page_is_read_again_and_the_devanagari_reading_is_kept(self) -> None:
        self.assertEqual(self.texts(self.engine.read(self.page)), ["गट क्रमांक ४८", "मौजे शिरूर"])
        self.assertEqual((self.first.pages, self.second.pages), (1, 1))
        params = self.built[1]
        self.assertEqual(params["Rec.model_path"], str(self.model))
        self.assertIs(params["Rec.lang_type"], rapidocr.LangRec.DEVANAGARI)
        self.assertIs(params["Rec.ocr_version"], rapidocr.OCRVersion.PPOCRV5)
        self.assertIs(params["Rec.model_type"], rapidocr.ModelType.MOBILE)
        self.assertEqual(self.engine.version, DEVANAGARI_VERSION)

    def test_a_clean_page_never_builds_the_second_engine(self) -> None:
        self.first = FakeRapidOcr(*CLEAN)
        self.assertEqual(self.texts(self.engine.read(self.page)), ["INVOICE 1042"])
        self.assertEqual(self.built, [{"Global.log_level": "error"}])
        self.assertEqual(self.second.pages, 0)
        self.assertEqual(self.engine.version, metadata.version("rapidocr"))

    def test_a_second_reading_that_is_not_better_is_dropped(self) -> None:
        cases = {
            "it found nothing": ([], []),
            "it is less sure": (["गट क्रमांक ४८", "मौजे शिरूर"], [0.55, 0.5]),
            "it has as many suspect letters": (["Gat 克拉马克 48", "Mauje 莫杰"], [0.9, 0.9]),
        }
        for why, (texts, scores) in cases.items():
            with self.subTest(why):
                self.second = FakeRapidOcr(texts, scores)
                engine = RapidOcrEngine()
                self.assertEqual(self.texts(engine.read(self.page)), ["Gat 克拉马克 48", "Mauje 莫杰"])
                self.assertEqual(self.second.pages, 1)
                self.assertEqual(engine.version, metadata.version("rapidocr"))

    def test_a_missing_model_keeps_the_first_reading_and_downloads_nothing(self) -> None:
        self.model.unlink()
        with (
            mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")) as connect,
            mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")) as raw,
            self.assertLogs("kaagaz.reading.ocr", "WARNING") as logs,
        ):
            lines = self.engine.read(self.page)
        self.assertEqual(self.texts(lines), ["Gat 克拉马克 48", "Mauje 莫杰"])
        self.assertEqual([record.__dict__.get("code") for record in logs.records], ["ocr_model_missing"])
        self.assertNotIn("Mauje", logs.output[0])  # never the page's text
        self.assertEqual(len(self.built), 1)  # the second engine was never built, so nothing fetched
        self.assertFalse(connect.called or raw.called)
        self.assertEqual(self.engine.version, metadata.version("rapidocr"))

    def test_the_second_engine_is_built_once_and_the_version_is_per_document(self) -> None:
        self.engine.read(self.page)
        self.engine.read(self.page)
        self.assertEqual((len(self.built), self.second.pages), (2, 2))
        self.assertEqual(self.engine.version, DEVANAGARI_VERSION)
        self.assertEqual(self.engine.version, DEVANAGARI_VERSION)  # asked again: the same document
        self.first.texts, self.first.scores = CLEAN  # the next document is in English
        self.engine.read(self.page)
        self.assertEqual(self.engine.version, metadata.version("rapidocr"))


class BuildOcrEngineTest(unittest.TestCase):
    def test_none_means_no_engine(self) -> None:
        self.assertIsNone(build_ocr_engine(make_settings(ocr_engine="none")))

    def test_rapidocr_setting_builds_rapidocr(self) -> None:
        self.assertIsInstance(build_ocr_engine(make_settings(ocr_engine="rapidocr")), RapidOcrEngine)


class OcrSpansTest(unittest.TestCase):
    def test_pixels_become_page_fractions(self) -> None:
        (span,) = ocr_spans([OcrLine("Total 500", 50, 20, 150, 40, 0.97)], 2, 200, 100)
        self.assertEqual((span.text, span.raw, span.confidence), ("Total 500", "Total 500", 0.97))
        self.assertEqual(span.location, BoxLocation(2, 0.25, 0.2, 0.75, 0.4))

    def test_boxes_past_the_edge_are_clamped_to_the_page(self) -> None:
        (span,) = ocr_spans([OcrLine("edge", -5, -5, 250, 120, 0.9)], 1, 200, 100)
        self.assertEqual(span.location, BoxLocation(1, 0.0, 0.0, 1.0, 1.0))

    def test_pieces_on_one_line_join_with_the_lowest_confidence(self) -> None:
        lines = [OcrLine("1042", 110, 10, 150, 30, 0.99), OcrLine("INVOICE", 10, 12, 100, 31, 0.8)]
        (span,) = ocr_spans(lines, 1, 1000, 100)
        self.assertEqual((span.text, span.confidence), ("INVOICE 1042", 0.8))

    def test_separate_lines_stay_separate(self) -> None:
        lines = [OcrLine("first", 10, 10, 90, 30, 0.9), OcrLine("second", 10, 50, 90, 70, 0.9)]
        self.assertEqual([s.text for s in ocr_spans(lines, 1, 100, 100)], ["first", "second"])

    def test_nothing_is_guessed_for_bad_lines(self) -> None:
        lines = [
            OcrLine("   ", 10, 10, 50, 20, 0.9),  # no text
            OcrLine("nan", 10, 40, 50, 50, float("nan")),  # no real confidence
            OcrLine("off page", 500, 500, 600, 600, 0.9),  # nothing inside the page
            OcrLine("flat", 10, 80, 50, 80, 0.9),  # no height
        ]
        self.assertEqual(ocr_spans(lines, 1, 100, 100), [])

    def test_confidence_is_clamped_to_zero_to_one(self) -> None:
        (span,) = ocr_spans([OcrLine("x", 10, 10, 20, 20, 1.0000001)], 1, 100, 100)
        self.assertEqual(span.confidence, 1.0)

    def test_every_line_span_says_it_was_read_by_ocr(self) -> None:
        lines = [OcrLine("first", 10, 10, 90, 30, 0.9), OcrLine("second", 10, 50, 90, 70, None)]
        spans = ocr_spans(lines, 1, 100, 100)
        self.assertEqual(
            [(s.origin, s.kind, s.confidence) for s in spans], [("ocr", None, 0.9), ("ocr", None, None)]
        )


class BlockModeTest(unittest.TestCase):
    """The Gemini reader gives one line per block, already in reading order."""

    def test_each_block_is_one_span_in_the_given_order_with_kind_table_and_origin(self) -> None:
        lines = [
            OcrLine("Tax   Invoice", 20, 5, 100, 15, None, "heading"),
            OcrLine(
                "Qty", 120, 5, 160, 15, None, "table_cell", OcrTable("t1", 0, 1, True)
            ),  # same line: kept apart
            OcrLine("higher up, read later", 0, 0, 10, 4, None, "footer"),  # never re-sorted
        ]
        spans = ocr_spans(lines, 3, 200, 100)
        self.assertEqual(
            [(s.text, s.raw, s.kind, s.origin, s.confidence) for s in spans],
            [
                ("Tax Invoice", "Tax   Invoice", "heading", "ocr", None),
                ("Qty", "Qty", "table_cell", "ocr", None),
                ("higher up, read later", "higher up, read later", "footer", "ocr", None),
            ],
        )
        self.assertEqual(spans[0].location, BoxLocation(3, 0.1, 0.05, 0.5, 0.15))
        self.assertEqual((spans[1].table_id, spans[1].row, spans[1].col, spans[1].header), ("t1", 0, 1, True))
        self.assertEqual((spans[0].table_id, spans[0].row, spans[0].col, spans[0].header), (None,) * 4)

    def test_blocks_with_nothing_to_point_at_are_left_out(self) -> None:
        lines = [
            OcrLine("  ", 10, 10, 50, 20, None, "paragraph"),
            OcrLine("off page", 500, 500, 600, 600, None, "paragraph"),
            OcrLine("nan", 10, 40, 50, 50, float("nan"), "paragraph"),
            OcrLine("kept", 10, 60, 50, 70, 0.4, "paragraph"),
        ]
        self.assertEqual([(s.text, s.confidence) for s in ocr_spans(lines, 1, 100, 100)], [("kept", 0.4)])


class MeteredOcrEngineTest(unittest.TestCase):
    """PRD 2 R6: the pages sent to OCR and their seconds, for the run's seconds_ocr."""

    class Engine:
        name = "fake-ocr"

        def __init__(self, clock: list[float]) -> None:
            self.version = "1"
            self.clock = clock
            self.down = False

        def read(self, image: Image.Image) -> list[OcrLine]:
            self.clock[0] += 3.0
            if self.down:
                raise BackendUnavailable("ocr")
            return [OcrLine("x", 0, 0, 1, 1, None)]

    def test_it_counts_pages_and_seconds_and_take_starts_again_from_zero(self) -> None:
        clock = [10.0]
        engine = self.Engine(clock)
        meter = MeteredOcrEngine(engine, timer=lambda: clock[0])
        page = Image.new("RGB", (4, 4))
        self.assertEqual(meter.read(page), [OcrLine("x", 0, 0, 1, 1, None)])
        engine.down = True
        with self.assertRaises(BackendUnavailable):
            meter.read(page)  # sent, so counted
        self.assertEqual(meter.take(), (2, 6.0))
        self.assertEqual(meter.take(), (0, 0.0))

    def test_it_has_the_engines_name_and_its_latest_version(self) -> None:
        engine = self.Engine([0.0])
        meter = MeteredOcrEngine(engine, timer=lambda: 0.0)
        engine.version = "2"  # an engine's version may change after a page
        self.assertEqual((meter.name, meter.version), ("fake-ocr", "2"))


def page_read(page_no: int, *lines: OcrLine) -> PageRead:
    return PageRead(
        page_no=page_no,
        lines=lines,
        quality="good",
        handwritten=False,
        scripts=("latin",),
        issues=(),
        read_by="google/gemini-3.1-pro-preview",
    )


class FakePageEngine:
    """Stands in for the Gemini reader (PRD 5 G1): a ``PageEngine``."""

    name = "gemini"
    version = "google/gemini-3.1-pro-preview;prompt=1"

    def __init__(self) -> None:
        self.pages: list[int] = []
        self.down = False

    def read(self, image: Image.Image) -> list[OcrLine]:
        return list(self.read_page(image, page_no=1).lines)

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        self.pages.append(page_no)
        if self.down:
            raise BackendUnavailable("gemini")
        return page_read(
            page_no, OcrLine(f"page {page_no}", 0, 0, 4, 4, None, "paragraph", box_source="found")
        )


class HeldPageEngine(FakePageEngine):
    """A ``PageEngine`` that says when a page is being read and holds it until ``go``."""

    def __init__(self) -> None:
        super().__init__()
        self.reading = threading.Event()
        self.go = threading.Event()

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        self.reading.set()
        if not self.go.wait(5):
            raise AssertionError("the page was never let go")
        return super().read_page(image, page_no=page_no)


class NoPageCache:
    def get(self, key: object) -> None:
        return None

    def put(self, key: object, result: object, input_tokens: int, output_tokens: int) -> None:
        raise AssertionError("nothing is saved here")


DOCUMENT_A = DocumentPages(
    customer_id="11111111-1111-1111-1111-111111111111",
    document_id="22222222-2222-2222-2222-222222222222",
    cache=NoPageCache(),
)


class LinesOnly:
    """An engine with only ``read``, as RapidOCR and the older test fakes."""

    name = "rapidocr"
    version = "3.9.2"

    def __init__(self, lines: list[OcrLine]) -> None:
        self.lines = lines

    def read(self, image: Image.Image) -> list[OcrLine]:
        return self.lines


PAGE = Image.new("RGB", (4, 4), "white")


class AsPageEngineTest(unittest.TestCase):
    """PRD 5: every engine reads numbered pages; a plain one as the local reader (RT2)."""

    def test_a_page_engine_is_used_as_it_is(self) -> None:
        engine = FakePageEngine()
        self.assertIs(as_page_engine(engine), engine)

    def test_a_plain_engine_reads_a_numbered_page_as_the_local_reader(self) -> None:
        line = OcrLine("INVOICE 1042", 0, 0, 4, 2, 0.97)
        pages = as_page_engine(LinesOnly([line]))
        self.assertEqual(
            pages.read_page(PAGE, page_no=4),
            PageRead(
                page_no=4,
                lines=(line,),
                quality=None,
                handwritten=False,
                scripts=(),
                issues=(),
                read_by="rapidocr",
            ),
        )
        self.assertEqual((pages.name, pages.version, pages.read(PAGE)), ("rapidocr", "3.9.2", [line]))

    def test_a_plain_engine_that_finds_no_line_says_no_text(self) -> None:
        """F1: no lines on a page that is not blank (the readers send only pages with ink)."""
        read = as_page_engine(LinesOnly([])).read_page(PAGE, page_no=2)
        self.assertEqual((read.lines, read.issues), ((), ("no_text",)))

    def test_fx3_rt2_a_page_says_the_id_of_the_engine_that_read_it(self) -> None:
        """Review FX3: read_by is the id of the engine that read the page (its name with
        "_" for "-"), not always "rapidocr"."""
        engine = LinesOnly([OcrLine("x", 0, 0, 1, 1, None, "paragraph")])
        engine.name = "other-reader"
        self.assertEqual(as_page_engine(engine).read_page(PAGE, page_no=1).read_by, "other_reader")

    def test_fx3_rt2_keyless_rapidocr_pages_say_rapidocr_without_read_locally(self) -> None:
        """OCR_ENGINE=rapidocr (the keyless mode): RapidOCR is the main reader there, so
        its pages are "rapidocr" with no read_locally (nothing was handed to a backup)."""
        read = as_page_engine(LinesOnly([OcrLine("x", 0, 0, 1, 1, 0.9)])).read_page(PAGE, page_no=1)
        self.assertEqual((read.read_by, read.issues), ("rapidocr", ()))
        self.assertNotIn("read_locally", read.issues)
        self.assertNotIn("reader_refused", read.issues)


class MeteredPageReadsTest(unittest.TestCase):
    """PRD 5 G7, F1: the meter keeps each page's PageRead for the run, like take()."""

    def test_a_page_engine_is_passed_through_and_its_reads_are_taken_once(self) -> None:
        clock = [0.0]
        engine = FakePageEngine()

        def timer() -> float:
            clock[0] += 1.0
            return clock[0]

        meter = MeteredOcrEngine(engine, timer=timer)
        first, second = meter.read_page(PAGE, page_no=1), meter.read_page(PAGE, page_no=2)
        self.assertEqual(engine.pages, [1, 2])
        self.assertEqual(meter.take_page_reads(), {1: first, 2: second})
        self.assertEqual(meter.take_page_reads(), {})
        self.assertEqual(meter.take(), (2, 2.0))
        self.assertEqual((meter.name, meter.version), (engine.name, engine.version))

    def test_a_plain_engines_pages_are_kept_as_read_locally(self) -> None:
        meter = MeteredOcrEngine(LinesOnly([OcrLine("x", 0, 0, 1, 1, 0.9)]), timer=lambda: 0.0)
        meter.read_page(PAGE, page_no=3)
        (read,) = meter.take_page_reads().values()
        self.assertEqual((read.page_no, read.read_by, read.quality), (3, "rapidocr", None))

    def test_a_page_read_again_keeps_its_last_read(self) -> None:
        """The second reading at the DPI ceiling is the one the page's spans came from."""
        meter = MeteredOcrEngine(FakePageEngine(), timer=lambda: 0.0)
        meter.read_page(PAGE, page_no=5)
        again = meter.read_page(PAGE, page_no=5)
        self.assertEqual(meter.take_page_reads(), {5: again})
        self.assertEqual(meter.take(), (2, 0.0))

    def test_a_failed_page_is_counted_as_sent_but_has_no_read(self) -> None:
        engine = FakePageEngine()
        engine.down = True
        meter = MeteredOcrEngine(engine, timer=lambda: 0.0)
        with self.assertRaises(BackendUnavailable):
            meter.read_page(PAGE, page_no=1)
        self.assertEqual((meter.take(), meter.take_page_reads()), ((1, 0.0), {}))

    def test_fx1_a_page_that_ends_after_its_document_is_never_kept_in_the_next_run(self) -> None:
        """Review FX1, defence in depth: a page still being read when the run's reads are
        taken (the next job's ``_start_run``) or its document closes is dropped when it
        ends, never kept or counted in the next document's run."""
        for ends in ("take_page_reads", "document"):
            with self.subTest(ends):
                engine = HeldPageEngine()
                meter = MeteredOcrEngine(engine, timer=lambda: 0.0)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    if ends == "document":
                        with reading_document(meter, DOCUMENT_A):
                            late = pool.submit(meter.read_page, PAGE, page_no=2)
                            self.assertTrue(engine.reading.wait(5))
                    else:
                        late = pool.submit(meter.read_page, PAGE, page_no=2)
                        self.assertTrue(engine.reading.wait(5))
                        meter.take_page_reads()
                    meter.take()  # the next job's _start_run
                    engine.go.set()
                    self.assertEqual(late.result(timeout=5).page_no, 2)  # its caller still gets it
                self.assertEqual(meter.take_page_reads(), {})
                self.assertEqual(meter.take(), (0, 0.0))

    def test_pages_read_on_many_threads_at_once_are_all_counted(self) -> None:
        """G8: pages of one document are read OCR_PAGE_CONCURRENCY at a time."""
        meter = MeteredOcrEngine(FakePageEngine(), timer=time.perf_counter)
        start = threading.Barrier(8)

        def read(first: int) -> None:
            start.wait(timeout=5)
            for page_no in range(first, first + 50):
                meter.read_page(PAGE, page_no=page_no)

        threads = [threading.Thread(target=read, args=(1 + 50 * k,)) for k in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sorted(meter.take_page_reads()), list(range(1, 401)))
        self.assertEqual(meter.take()[0], 400)


GEMINI_SETTINGS = make_settings(ocr_engine="gemini", cpu_workers=3)


class GeminiFactoryTest(unittest.TestCase):
    """G1: OCR_ENGINE=gemini builds the Gemini reader through one factory, with the
    process's RapidOCR pool; the module is imported by its path only then."""

    def setUp(self) -> None:
        self.calls: list[tuple[Settings, RapidOcrPool]] = []
        self.engine = FakePageEngine()

    def factory(self, settings: Settings, *, pool: RapidOcrPool) -> PageEngine:
        self.calls.append((settings, pool))
        return self.engine

    def test_the_factory_builds_the_engine_with_the_shared_pool(self) -> None:
        self.assertIs(build_ocr_engine(GEMINI_SETTINGS, gemini=self.factory), self.engine)
        build_ocr_engine(GEMINI_SETTINGS, gemini=self.factory)
        (settings, pool), (_, again) = self.calls
        self.assertIs(settings, GEMINI_SETTINGS)
        self.assertIs(pool, again)  # one pool per process: CPU_WORKERS is per process (G8)
        self.assertIs(pool, shared_pool(3))
        self.assertEqual(pool.size, 3)

    def test_the_products_engines_take_it_from_the_gemini_ocr_module(self) -> None:
        """wiring.build_ocr_engine: the factory imports the module by its path."""
        module = ModuleType(GEMINI_OCR_MODULE)
        factory = self.factory

        class GeminiEngine:
            @classmethod
            def from_settings(
                cls, settings: Settings, *, pool: RapidOcrPool, db: Database | None = None
            ) -> PageEngine:
                return factory(settings, pool=pool)

        module.__dict__["GeminiEngine"] = GeminiEngine
        with mock.patch.dict(sys.modules, {GEMINI_OCR_MODULE: module}):
            engine = wiring.build_ocr_engine(GEMINI_SETTINGS)
            self.assertIs(engine, self.engine)
            as_page_engine(self.engine).read_page(PAGE, page_no=2)
        self.assertEqual((len(self.calls), self.engine.pages), (1, [2]))

    def test_without_a_factory_there_is_no_gemini_reader(self) -> None:
        """The reader socket's module never imports the Gemini reader's (no import cycle)."""
        with self.assertRaises(SettingsError):
            build_ocr_engine(GEMINI_SETTINGS)

    def test_the_worker_engines_and_the_meter_pass_numbered_pages_to_it(self) -> None:
        engine = build_ocr_engine(GEMINI_SETTINGS, gemini=self.factory)
        assert engine is not None
        meter = MeteredOcrEngine(engine, timer=lambda: 0.0)
        read = meter.read_page(PAGE, page_no=7)
        self.assertEqual((read.read_by, self.engine.pages), ("google/gemini-3.1-pro-preview", [7]))
        self.assertEqual((meter.name, meter.take_page_reads()), ("gemini", {7: read}))

    def test_other_engines_never_build_it(self) -> None:
        for engine in ("rapidocr", "none"):
            with self.subTest(engine):
                build_ocr_engine(make_settings(ocr_engine=engine), gemini=self.factory)
        self.assertEqual(self.calls, [])


class KeylessModeTest(unittest.TestCase):
    def test_rapidocr_reads_a_scan_without_loading_the_gemini_reader_or_its_client(self) -> None:
        """OCR_ENGINE=rapidocr and MODEL_ENABLED=false (laptops, CI): the engines are built
        and a photo is read with the Gemini modules unimportable, so no Gemini call can
        be made; one page at a time, on this thread, as before."""

        def model(picture: Image.Image) -> SimpleNamespace:
            threads.add(threading.current_thread().name)
            return SimpleNamespace(
                boxes=[[[10, 10], [90, 10], [90, 30], [10, 30]]], txts=["INVOICE"], scores=[0.9]
            )

        def never(*args: object, **kwargs: object) -> PageEngine:
            raise AssertionError("the Gemini reader must not be built")

        threads: set[str] = set()
        photo = Image.new("RGB", (200, 100), "white")
        ImageDraw.Draw(photo).rectangle((10, 10, 90, 30), fill="black")
        png = io.BytesIO()
        photo.save(png, format="PNG")
        settings = make_settings(ocr_engine="rapidocr", model_enabled=False)
        unimportable = {GEMINI_OCR_MODULE: None, "kaagaz.ai.openrouter": None}
        with (
            mock.patch.dict(sys.modules, unimportable),
            mock.patch.object(ocr, "build_rapidocr", lambda params: model),
            mock.patch.object(wiring, "build_gemini_engine", never),
        ):
            engine = wiring.build_ocr_engine(settings)
            assert engine is not None
            socket = build_reader_socket(settings, MeteredOcrEngine(engine, timer=lambda: 0.0))
            spans = socket.read(sniff.PNG, png.getvalue())
        self.assertEqual([(span.text, span.origin) for span in spans], [("INVOICE", "ocr")])
        self.assertEqual(threads, {threading.current_thread().name})


class RapidOcrOnePageAtATimeTest(unittest.TestCase):
    """A RapidOCR instance keeps state between calls: pages from several page threads
    (the Gemini reader's local reads, G8 and Q7) wait their turn."""

    def test_pages_from_many_threads_are_read_one_at_a_time(self) -> None:
        state = {"now": 0, "most": 0}
        lock = threading.Lock()

        def model(picture: Image.Image) -> SimpleNamespace:
            with lock:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
            time.sleep(0.01)
            with lock:
                state["now"] -= 1
            return SimpleNamespace(boxes=[[[0, 0], [9, 0], [9, 5], [0, 5]]], txts=["INVOICE"], scores=[0.9])

        with mock.patch.object(ocr, "build_rapidocr", lambda params: model):
            engine = RapidOcrEngine()
            threads = [threading.Thread(target=engine.read, args=(PAGE,)) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertEqual(state["most"], 1)


class WordMarksTest(unittest.TestCase):
    """PRD 5 F3: unsure and illegible words become spans of their own, with sub-boxes
    split from the line's pinned box in proportion to character position."""

    def test_unsure_and_illegible_words_get_sub_boxes_after_their_line(self) -> None:
        # 20 characters over 200 pixels: 10 pixels a character, from x = 100.
        line = OcrLine("Total 1,180 [?] paid", 100, 0, 300, 10, None, "paragraph", None, ("1,180",), "found")
        spans = ocr_spans([line], 1, 1000, 100)
        self.assertEqual(
            [(s.text, s.quality, s.box_source, s.kind, s.origin, is_word_mark(s)) for s in spans],
            [
                ("Total 1,180 [?] paid", None, "found", "paragraph", "ocr", False),
                ("1,180", "unsure", "found", None, "ocr", True),
                ("[?]", "illegible", "found", None, "ocr", True),
            ],
        )
        self.assertEqual(spans[0].location, BoxLocation(1, 0.1, 0.0, 0.3, 0.1))
        unsure, illegible = (box_of(span) for span in spans[1:])
        self.assertEqual((unsure.page, unsure.y0, unsure.y1), (1, 0.0, 0.1))
        self.assertAlmostEqual(unsure.x0, 0.16)  # characters 6 to 11
        self.assertAlmostEqual(unsure.x1, 0.21)
        self.assertAlmostEqual(illegible.x0, 0.22)  # characters 12 to 15
        self.assertAlmostEqual(illegible.x1, 0.25)

    def test_an_unpinned_line_and_its_marks_keep_the_models_box_source(self) -> None:
        """G5: a line no found box confirmed keeps box_source "model", and so do its marks."""
        line = OcrLine("पाटील [?] यांचे [?]", 0, 0, 170, 10, None, "paragraph", None, ("पाटील",), "model")
        spans = ocr_spans([line], 2, 170, 10)
        self.assertEqual(
            [(s.text, s.quality, s.box_source) for s in spans],
            [
                ("पाटील [?] यांचे [?]", None, "model"),
                ("पाटील", "unsure", "model"),
                ("[?]", "illegible", "model"),
                ("[?]", "illegible", "model"),
            ],
        )
        self.assertEqual([round(box_of(s).x0, 4) for s in spans[1:]], [0.0, 0.3158, 0.8421])

    def test_an_unsure_word_is_found_as_a_whole_word_first(self) -> None:
        line = OcrLine(
            "100 10 10", 0, 0, 90, 10, None, "table_cell", OcrTable("t1", 0, 0, False), ("10", "10")
        )
        marks = [s for s in ocr_spans([line], 1, 90, 10) if is_word_mark(s)]
        self.assertEqual([round(box_of(s).x0, 4) for s in marks], [0.4444, 0.7778])  # never inside "100"
        self.assertEqual([(s.table_id, s.kind) for s in marks], [(None, None)] * 2)

    def test_a_word_next_to_punctuation_and_a_word_not_in_the_line(self) -> None:
        line = OcrLine("Rs.1,180.", 0, 0, 90, 10, None, "paragraph", None, ("1,180", "Mumbai", " ", "[?]"))
        marks = [s for s in ocr_spans([line], 1, 90, 10) if is_word_mark(s)]
        self.assertEqual([(s.text, round(box_of(s).x0, 4)) for s in marks], [("1,180", 0.3333)])

    def test_marks_follow_the_line_rule_too(self) -> None:
        """A reader with no kinds (line mode): the lines join as before, marks after them."""
        lines = [
            OcrLine("INVOICE", 10, 12, 100, 31, 0.8),
            OcrLine("1O42", 110, 10, 150, 30, 0.6, unsure=("1O42",)),
        ]
        spans = ocr_spans(lines, 1, 1000, 100)
        self.assertEqual(
            [(s.text, s.quality, is_word_mark(s)) for s in spans],
            [("INVOICE 1O42", None, False), ("1O42", "unsure", True)],
        )
        self.assertEqual(spans[1].confidence, 0.6)

    def test_lines_without_marks_give_spans_as_before(self) -> None:
        line = OcrLine("Tax Invoice", 20, 5, 100, 15, None, "heading")
        (span,) = ocr_spans([line], 3, 200, 100)
        self.assertEqual((span.quality, span.box_source, is_word_mark(span)), (None, None, False))

    def test_a_box_source_or_unsure_list_an_engine_should_never_give_is_refused(self) -> None:
        bad = {
            "unknown box source": OcrLine("x", 0, 0, 5, 5, None, "paragraph", box_source="guessed"),
            "unsure not a tuple": OcrLine("x", 0, 0, 5, 5, None, "paragraph", unsure=cast(tuple[str], ["x"])),
            "unsure not text": OcrLine("x", 0, 0, 5, 5, None, "paragraph", unsure=cast(tuple[str], (1,))),
        }
        for name, line in bad.items():
            with self.subTest(name), self.assertRaises(ReadError) as ctx:
                ocr_spans([line], 1, 10, 10)
            self.assertEqual(ctx.exception.code, "invalid_reader_output")

    def test_only_an_ocr_span_with_no_kind_can_be_a_word_mark(self) -> None:
        where = BoxLocation(1, 0.1, 0.1, 0.2, 0.2)
        self.assertFalse(
            is_word_mark(Span("a", "a", where, kind="paragraph", origin="ocr", quality="unsure"))
        )
        self.assertFalse(is_word_mark(Span("a", "a", where, origin="ocr", quality="ok")))
        self.assertTrue(is_word_mark(Span("a", "a", where, origin="ocr", quality="illegible")))


if __name__ == "__main__":
    unittest.main()
