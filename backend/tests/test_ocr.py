import pathlib
import socket
import subprocess
import sys
import tempfile
import unittest
from importlib import metadata
from types import SimpleNamespace
from typing import Any
from unittest import mock

from PIL import Image, ImageDraw, ImageFont
from rapidocr import LangRec, ModelType, OCRVersion

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading import ocr
from kaagaz.reading.ocr import (
    MeteredOcrEngine,
    OcrLine,
    OcrTable,
    RapidOcrEngine,
    build_ocr_engine,
    ocr_spans,
)
from kaagaz.reading.spans import BoxLocation, Span
from tests.settings_factory import make_settings


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
            mock.patch.object(ocr, "_rapidocr", self.build),
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
        self.assertIs(params["Rec.lang_type"], LangRec.DEVANAGARI)
        self.assertIs(params["Rec.ocr_version"], OCRVersion.PPOCRV5)
        self.assertIs(params["Rec.model_type"], ModelType.MOBILE)
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

    def test_paddleocr_vl_setting_builds_the_paddle_engine_without_calling_it(self) -> None:
        engine = build_ocr_engine(make_settings(ocr_engine="paddleocr_vl", ocr_url="http://ocr:8080"))
        self.assertIsInstance(engine, ocr.FallbackOcrEngine)  # RapidOCR behind it (test_ocr_fallback.py)
        assert engine is not None
        self.assertEqual((engine.name, engine.version), ("paddleocr-vl", "unknown"))


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
    """A layout engine (PaddleOCR-VL) gives one line per block, already in reading order."""

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
        engine.version = "2"  # PaddleOCR-VL's comes from its last reply
        self.assertEqual((meter.name, meter.version), ("fake-ocr", "2"))


if __name__ == "__main__":
    unittest.main()
