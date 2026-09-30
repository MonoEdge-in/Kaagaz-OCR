import pathlib
import subprocess
import sys
import unittest
from importlib import metadata

from PIL import Image, ImageDraw, ImageFont

from kaagaz.reading.ocr import OcrLine, RapidOcrEngine, build_ocr_engine, ocr_spans
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


if __name__ == "__main__":
    unittest.main()
