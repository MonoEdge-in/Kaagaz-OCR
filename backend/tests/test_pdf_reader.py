import math
import unittest

import pdfplumber
from PIL import Image

from kaagaz.ingestion import sniff
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.socket import NoReader
from kaagaz.reading.spans import BoxLocation, ReadError, Span
from tests.pdf_builder import Page, Text, make_pdf

WIDTH, HEIGHT = 612.0, 792.0


class FakeOcr:
    """Records the pictures it is given and answers with fixed lines."""

    name = "fake-ocr"
    version = "7"

    def __init__(self, lines: list[OcrLine] | None = None) -> None:
        self.lines = lines or []
        self.seen: list[tuple[int, int, str]] = []

    def read(self, image: Image.Image) -> list[OcrLine]:
        self.seen.append((image.width, image.height, image.mode))
        return self.lines


def read(
    data: bytes,
    ocr: FakeOcr | None = None,
    *,
    max_pages: int = 10,
    dpi: int = 72,
    max_image_pixels: int = 10_000_000,
) -> list[Span]:
    reader = PdfReader(ocr, max_pages=max_pages, dpi=dpi, max_image_pixels=max_image_pixels)
    return reader.read(data)


def box_of(span: Span) -> BoxLocation:
    assert isinstance(span.location, BoxLocation)
    return span.location


def refused_with(test: unittest.TestCase, data: bytes, code: str, **kwargs: int) -> None:
    with test.assertRaises(ReadError) as ctx:
        read(data, None, **kwargs)
    test.assertEqual(ctx.exception.code, code)
    test.assertIsNone(ctx.exception.__context__)  # nothing chained
    test.assertIsNone(ctx.exception.__cause__)


class TextLayerTest(unittest.TestCase):
    def test_each_line_becomes_one_span_in_reading_order(self) -> None:
        data = make_pdf(
            [Page(texts=[Text(72, 650, "Total 1,234.00"), Text(72, 700, "Invoice No: INV-1042")])]
        )
        spans = read(data)
        self.assertEqual([s.text for s in spans], ["Invoice No: INV-1042", "Total 1,234.00"])
        self.assertTrue(all(s.confidence is None for s in spans))  # a direct read
        self.assertTrue(all(s.raw == s.text for s in spans))

    def test_box_is_where_the_text_is_as_page_fractions_from_the_top_left(self) -> None:
        (span,) = read(make_pdf([Page(texts=[Text(72, 700, "Invoice No: INV-1042", size=12)])]))
        box = box_of(span)
        self.assertEqual(box.page, 1)
        self.assertAlmostEqual(box.x0, 72 / WIDTH, places=3)
        baseline = (HEIGHT - 700) / HEIGHT
        self.assertLess(box.y0, baseline)  # the text sits above its baseline...
        self.assertLessEqual(baseline, box.y1 + 0.01)  # ...and the box reaches down to it
        self.assertAlmostEqual(box.y1 - box.y0, 12 / HEIGHT, delta=4 / HEIGHT)  # one line high
        self.assertTrue(box.is_valid())

    def test_words_drawn_separately_on_one_line_join_one_span(self) -> None:
        data = make_pdf(
            [Page(texts=[Text(72, 700, "Grand"), Text(110, 700, "Total"), Text(145, 700, "500")])]
        )
        self.assertEqual([s.text for s in read(data)], ["Grand Total 500"])

    def test_two_columns_on_one_baseline_stay_two_spans(self) -> None:
        data = make_pdf([Page(texts=[Text(72, 700, "Invoice No: 7"), Text(400, 700, "Date: 12/08/2026")])])
        spans = read(data)
        self.assertEqual([s.text for s in spans], ["Invoice No: 7", "Date: 12/08/2026"])
        self.assertLess(box_of(spans[0]).x1, box_of(spans[1]).x0)

    def test_pages_are_numbered_from_one(self) -> None:
        data = make_pdf([Page(texts=[Text(72, 700, "first")]), Page(texts=[Text(72, 700, "second")])])
        self.assertEqual([(s.text, box_of(s).page) for s in read(data)], [("first", 1), ("second", 2)])

    def test_rotated_page_boxes_follow_the_page_as_shown(self) -> None:
        # Stored upright on a /Rotate 90 page: shown 792 wide and 612 high,
        # with the text 72 pt from the left and 540 pt down from the top.
        data = make_pdf([Page(rotate=90, texts=[Text(540, 72, "Landscape total", turned=True)])])
        (span,) = read(data)
        box = box_of(span)
        self.assertEqual(span.text, "Landscape total")
        self.assertAlmostEqual(box.x0, 72 / 792, places=2)
        self.assertLess(box.y0, 540 / 612)
        self.assertLessEqual(540 / 612, box.y1 + 0.01)
        self.assertLess(box.y1 - box.y0, 0.05)  # a line, not a column

    def test_text_outside_the_page_is_left_out(self) -> None:
        data = make_pdf([Page(texts=[Text(72, 700, "seen"), Text(-500, 700, "hidden")])])
        self.assertEqual([s.text for s in read(data)], ["seen"])


class PagesWithoutTextTest(unittest.TestCase):
    def test_no_text_anywhere_and_no_ocr_needs_a_reader(self) -> None:
        with self.assertRaises(NoReader) as ctx:
            read(make_pdf([Page(), Page()]))
        self.assertEqual(ctx.exception.detected_type, sniff.PDF)

    def test_without_ocr_a_page_without_text_gives_nothing(self) -> None:
        spans = read(make_pdf([Page(texts=[Text(72, 700, "typed page")]), Page()]))
        self.assertEqual([(s.text, box_of(s).page) for s in spans], [("typed page", 1)])

    def test_with_ocr_only_the_page_without_text_is_drawn_and_read(self) -> None:
        ocr = FakeOcr([OcrLine("SCANNED 77", 61.2, 79.2, 306.0, 118.8, 0.8)])
        pages = [Page(texts=[Text(72, 700, "typed page")]), Page()]
        spans = read(make_pdf(pages), ocr, dpi=144)
        self.assertEqual(ocr.seen, [(1224, 1584, "RGB")])  # page 2 only, at 144 dpi
        self.assertEqual([(s.text, box_of(s).page) for s in spans], [("typed page", 1), ("SCANNED 77", 2)])
        scanned = spans[1]
        self.assertEqual(scanned.confidence, 0.8)
        box = box_of(scanned)
        for got, want in zip((box.x0, box.y0, box.x1, box.y1), (0.05, 0.05, 0.25, 0.075), strict=True):
            self.assertAlmostEqual(got, want, places=6)

    def test_ocr_pages_come_back_in_page_order(self) -> None:
        ocr = FakeOcr([OcrLine("scan", 10, 10, 100, 30, 0.9)])
        spans = read(make_pdf([Page(), Page(texts=[Text(72, 700, "typed")])]), ocr)
        self.assertEqual([(s.text, box_of(s).page) for s in spans], [("scan", 1), ("typed", 2)])

    def test_with_ocr_and_still_no_text_it_is_unreadable(self) -> None:
        with self.assertRaises(ReadError) as ctx:
            read(make_pdf([Page()]), FakeOcr([]))
        self.assertEqual(ctx.exception.code, "no_text")

    def test_on_page_is_called_after_each_scanned_page_in_order(self) -> None:
        ocr = FakeOcr([OcrLine("scan", 10, 10, 100, 30, 0.9)])
        pages_read_at_each_call: list[int] = []
        reader = PdfReader(ocr, max_pages=10, dpi=72, max_image_pixels=10_000_000)
        data = make_pdf([Page(), Page(texts=[Text(72, 700, "typed")]), Page()])

        reader.read(data, on_page=lambda: pages_read_at_each_call.append(len(ocr.seen)))
        # Pages 1 and 3 are drawn and read; each call comes right after one of them.
        # The typed page is read directly, which is quick, and gives no call.
        self.assertEqual(pages_read_at_each_call, [1, 2])

    def test_page_too_large_to_draw_is_refused_before_drawing(self) -> None:
        ocr = FakeOcr([OcrLine("scan", 10, 10, 100, 30, 0.9)])
        pixels = math.ceil(WIDTH) * math.ceil(HEIGHT)
        with self.assertRaises(ReadError) as ctx:
            read(make_pdf([Page()]), ocr, max_image_pixels=pixels - 1)
        self.assertEqual(ctx.exception.code, "image_too_large")
        self.assertEqual(ocr.seen, [])


class RefusalsTest(unittest.TestCase):
    def test_password_protected_file(self) -> None:
        refused_with(self, make_pdf([Page(texts=[Text(72, 700, "x")])], password_protected=True), "encrypted")

    def test_broken_files(self) -> None:
        for data in (b"%PDF-1.7\nnot really a pdf", b"%PDF-", make_pdf([Page()])[:60]):
            with self.subTest(data=data[:20]):
                refused_with(self, data, "corrupt_file")

    def test_too_many_pages(self) -> None:
        refused_with(self, make_pdf([Page(), Page(), Page()]), "too_many_pages", max_pages=2)

    def test_limits_must_be_positive(self) -> None:
        for limits in (
            {"max_pages": 0, "dpi": 72, "max_image_pixels": 1},
            {"max_pages": 1, "dpi": 0, "max_image_pixels": 1},
            {"max_pages": 1, "dpi": 72, "max_image_pixels": 0},
        ):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                PdfReader(None, **limits)


class NameTest(unittest.TestCase):
    def test_name_and_version_say_whether_ocr_was_available(self) -> None:
        without = PdfReader(None, max_pages=1, dpi=72, max_image_pixels=1)
        with_ocr = PdfReader(FakeOcr(), max_pages=1, dpi=72, max_image_pixels=1)
        self.assertEqual((without.name, with_ocr.name), ("pdfplumber", "pdfplumber"))
        self.assertEqual(without.version, pdfplumber.__version__)
        self.assertEqual(with_ocr.version, f"{pdfplumber.__version__}+fake-ocr-7")


if __name__ == "__main__":
    unittest.main()
