"""The exit-test files themselves (tests/exit_fixtures.py): each is the file PRD 2 §2 describes,
read by the real readers, so the exit test (test_exit_files_db.py) starts from the right files.

These test the fixtures, not new product code, so they were never red.
"""

import io
import unittest

import pdfplumber
import pypdfium2
from PIL import Image, ImageStat

from kaagaz.ingestion import sniff
from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.pdf_reader import PdfReader
from kaagaz.reading.socket import NoReader
from kaagaz.reading.spans import BoxLocation, CellLocation, Span
from kaagaz.reading.xlsx_reader import XlsxLimits, XlsxReader
from tests.exit_fixtures import (
    BROKEN_FONT_OCR,
    BROKEN_FONT_TEXT,
    HINDI_BILL_LINES,
    LAND_RECORD_LINES,
    LEGACY_FONT,
    PROFIT_LABEL,
    PROFIT_VALUE,
    ScriptedOcr,
    exit_files,
)

FILES = exit_files()


def pdf_text(data: bytes, ocr: ScriptedOcr | None = None) -> list[Span]:
    return PdfReader(ocr, max_pages=30, dpi=100, max_image_pixels=10**8).read(data)


class ExitFilesTest(unittest.TestCase):
    def test_the_six_files_and_the_mismatched_copy(self) -> None:
        self.assertEqual(
            {key: (file.name, file.detected_type) for key, file in FILES.items()},
            {
                "textile_invoice": ("textile-invoice.pdf", "pdf"),
                "textile_invoice_mismatched": ("textile-invoice-copy.pdf", "pdf"),
                "hindi_retail_bill": ("retail-bill.jpg", "jpeg"),
                "marathi_land_record": ("land-record.pdf", "pdf"),
                "bank_statement": ("bank-statement.xlsx", "zip"),
                "broken_font_pdf": ("office-order.pdf", "pdf"),
                "report": ("monthly-report.pdf", "pdf"),
            },
        )
        for key, file in FILES.items():
            with self.subTest(key=key):
                self.assertEqual(sniff.detect_type(file.data[:4096]), file.detected_type)
                self.assertLess(len(file.data), 200_000)  # far below CI's 2 MB file limit

    def test_the_mismatched_copy_differs_only_in_item_4s_amount(self) -> None:
        original = [span.text for span in pdf_text(FILES["textile_invoice"].data)]
        copy = [span.text for span in pdf_text(FILES["textile_invoice_mismatched"].data)]
        changed = [(a, b) for a, b in zip(original, copy, strict=True) if a != b]
        self.assertEqual(
            changed, [("4 Silk Blend 5007 20 m 150.00 3,000.00", "4 Silk Blend 5007 20 m 150.00 3,500.00")]
        )


class ReportTest(unittest.TestCase):
    """C7: 20 pages, a footer on each, the profit sentence across pages 15 and 16."""

    def test_twenty_text_pages_each_with_its_footer_and_40_letters_and_digits(self) -> None:
        spans = pdf_text(FILES["report"].data)
        by_page: dict[int, list[str]] = {}
        for span in spans:
            by_page.setdefault(span.location.page, []).append(span.text)
        self.assertEqual(sorted(by_page), list(range(1, 21)))
        for page, texts in by_page.items():
            with self.subTest(page=page):
                self.assertEqual(texts[-1], f"Page {page} of 20")
                self.assertGreaterEqual(sum(ch.isalnum() for ch in "".join(texts)), 40)

    def test_page_15_ends_with_the_label_and_page_16_starts_with_the_value(self) -> None:
        spans = pdf_text(FILES["report"].data)
        page_15 = [span.text for span in spans if span.location.page == 15]
        page_16 = [span.text for span in spans if span.location.page == 16]
        self.assertEqual(page_15[-2:], [PROFIT_LABEL, "Page 15 of 20"])
        self.assertEqual(page_16[0], PROFIT_VALUE)


class ScansTest(unittest.TestCase):
    def test_the_land_record_has_no_text_layer_and_ocr_reads_its_lines(self) -> None:
        file = FILES["marathi_land_record"]
        with self.assertRaises(NoReader):
            pdf_text(file.data)
        ocr = ScriptedOcr(file.ocr_pages)
        spans = pdf_text(file.data, ocr)
        self.assertEqual([span.text for span in spans], list(LAND_RECORD_LINES))
        self.assertEqual({span.location.page for span in spans}, {1})
        self.assertEqual({span.confidence for span in spans}, {0.95})
        self.assertEqual(len(ocr.seen), 1)

    def test_the_hindi_bill_is_a_photo_that_ocr_reads(self) -> None:
        file = FILES["hindi_retail_bill"]
        with Image.open(io.BytesIO(file.data)) as photo:
            self.assertEqual((photo.format, photo.size), ("JPEG", (620, 877)))
            self.assertGreater(ImageStat.Stat(photo.convert("L")).stddev[0], 3.0)  # not blank
        ocr = ScriptedOcr(file.ocr_pages)
        spans = ImageReader(ocr, max_pages=5, max_image_pixels=10**8).read(file.data)
        self.assertEqual([span.text for span in spans], list(HINDI_BILL_LINES))
        box = spans[0].location
        assert isinstance(box, BoxLocation)
        self.assertEqual((box.page, round(box.y0, 2)), (1, 0.08))

    def test_the_scripted_engine_scales_its_boxes_and_reads_nothing_past_its_pages(self) -> None:
        ocr = ScriptedOcr([[("a", (0.1, 0.2, 0.5, 0.25))]])
        (line,) = ocr.read(Image.new("RGB", (200, 400), "white"))
        self.assertEqual((line.x0, line.y0, line.x1, line.y1), (20.0, 80.0, 100.0, 100.0))
        self.assertEqual(ocr.read(Image.new("RGB", (10, 10), "white")), [])
        self.assertEqual(ocr.seen, [(200, 400), (10, 10)])


class BrokenFontTest(unittest.TestCase):
    def test_the_text_layer_is_legacy_font_ascii_in_a_named_font(self) -> None:
        data = FILES["broken_font_pdf"].data
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            self.assertEqual({char["fontname"] for char in pdf.pages[0].chars}, {LEGACY_FONT})
        self.assertEqual([span.text for span in pdf_text(data)], list(BROKEN_FONT_TEXT))

    def test_it_draws_as_a_page_with_ink_so_it_can_go_to_ocr(self) -> None:
        document = pypdfium2.PdfDocument(FILES["broken_font_pdf"].data)
        try:
            picture = document[0].render(scale=100 / 72).to_pil()
        finally:
            document.close()
        self.assertGreater(ImageStat.Stat(picture.convert("L")).stddev[0], 3.0)
        self.assertEqual(len(FILES["broken_font_pdf"].ocr_pages[0]), len(BROKEN_FONT_OCR))


class BankStatementTest(unittest.TestCase):
    def test_the_statement_is_one_sheet_of_cells(self) -> None:
        limits = XlsxLimits(1000, 10**7, 10**8, 100_000, 32_767)
        spans = list(XlsxReader(limits).read(FILES["bank_statement"].data))
        cells = {
            (span.location.row, span.location.column): span.text
            for span in spans
            if isinstance(span.location, CellLocation)
        }
        self.assertEqual(cells[(1, 1)], "Date")
        self.assertEqual(cells[(3, 2)], "NEFT from Patil Garments")
        self.assertEqual(cells[(3, 4)], "11800")
        self.assertEqual({span.location.page for span in spans}, {1})
