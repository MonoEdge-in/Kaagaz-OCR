import io
import math
import unittest

from PIL import Image

from kaagaz.ingestion import sniff
from kaagaz.preparing.page_checks import PageCheck
from kaagaz.preparing.pages import PreparedPages, prepare_pages, sheet_pages
from kaagaz.reading.spans import BoxLocation, CellLocation, PageInfo, ReadError, Span
from tests.pdf_builder import Page, Picture, Text, make_pdf
from tests.picture_support import text_like_picture

BLANK_PAGE_STDDEV = 3.0  # the PRD's values
LOW_RES_DPI = 150


def prepare(
    kind: str, data: bytes, *, dpi: int = 72, max_pages: int = 10, max_image_pixels: int = 5_000_000
) -> PreparedPages:
    return prepare_pages(
        kind,
        data,
        dpi=dpi,
        max_pages=max_pages,
        max_image_pixels=max_image_pixels,
        blank_stddev=BLANK_PAGE_STDDEV,
        low_res_dpi=LOW_RES_DPI,
    )


def encode(image: Image.Image, fmt: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def picture(png: bytes) -> Image.Image:
    assert png.startswith(b"\x89PNG\r\n\x1a\n"), "page pictures are PNG"
    return Image.open(io.BytesIO(png))


def refused_with(test: unittest.TestCase, kind: str, data: bytes, code: str, **kwargs: int) -> None:
    with test.assertRaises(ReadError) as ctx:
        prepare(kind, data, **kwargs)
    test.assertEqual(ctx.exception.code, code)
    test.assertIsNone(ctx.exception.__context__)  # nothing chained


class PdfPagesTest(unittest.TestCase):
    def test_sizes_in_points_and_one_png_per_page_at_the_dpi(self) -> None:
        data = make_pdf([Page(texts=[Text(72, 700, "one")]), Page(width=300, height=400)])
        prepared = prepare(sniff.PDF, data, dpi=144)
        self.assertEqual(prepared.pages, [PageInfo(1, 612.0, 792.0, "pt"), PageInfo(2, 300.0, 400.0, "pt")])
        self.assertEqual(sorted(prepared.images), [1, 2])
        self.assertEqual(picture(prepared.images[1]).size, (1224, 1584))
        self.assertEqual(picture(prepared.images[2]).size, (600, 800))

    def test_the_picture_shows_the_page(self) -> None:
        black = Image.new("RGB", (4, 4), "black")
        data = make_pdf([Page(width=100, height=100, picture=Picture(black, 0, 50, 100, 50))])
        image = picture(prepare(sniff.PDF, data).images[1]).convert("RGB")
        self.assertEqual(image.getpixel((50, 25)), (0, 0, 0))  # top half: the black picture
        self.assertEqual(image.getpixel((50, 75)), (255, 255, 255))  # bottom half: white paper

    def test_a_rotated_page_is_shown_turned(self) -> None:
        prepared = prepare(sniff.PDF, make_pdf([Page(rotate=90)]))
        self.assertEqual(prepared.pages, [PageInfo(1, 792.0, 612.0, "pt")])
        self.assertEqual(picture(prepared.images[1]).size, (792, 612))

    def test_refusals(self) -> None:
        refused_with(self, sniff.PDF, make_pdf([Page()], password_protected=True), "encrypted")
        refused_with(self, sniff.PDF, b"%PDF-1.7\nnot really a pdf", "corrupt_file")
        refused_with(self, sniff.PDF, make_pdf([Page(), Page()]), "too_many_pages", max_pages=1)
        pixels = math.ceil(612 * 2) * math.ceil(792 * 2)
        refused_with(
            self, sniff.PDF, make_pdf([Page()]), "image_too_large", dpi=144, max_image_pixels=pixels - 1
        )


class ImagePagesTest(unittest.TestCase):
    def test_sizes_in_pixels_and_a_normalised_png(self) -> None:
        for fmt, mode in (("PNG", "RGBA"), ("JPEG", "L"), ("TIFF", "RGB")):
            with self.subTest(fmt=fmt):
                prepared = prepare(fmt.lower(), encode(Image.new(mode, (40, 30)), fmt))
                self.assertEqual(prepared.pages, [PageInfo(1, 40.0, 30.0, "px")])
                self.assertEqual(picture(prepared.images[1]).mode, "RGB")

    def test_exif_orientation_is_applied(self) -> None:
        exif = Image.Exif()
        exif[0x0112] = 8  # "rotate 90 degrees anticlockwise to show"
        prepared = prepare(sniff.JPEG, encode(Image.new("RGB", (60, 20)), "JPEG", exif=exif))
        self.assertEqual(prepared.pages, [PageInfo(1, 20.0, 60.0, "px")])
        self.assertEqual(picture(prepared.images[1]).size, (20, 60))

    def test_every_tiff_frame_is_a_page(self) -> None:
        frames = [Image.new("RGB", (10 * (i + 1), 10)) for i in range(3)]
        data = encode(frames[0], "TIFF", save_all=True, append_images=frames[1:])
        prepared = prepare(sniff.TIFF, data)
        self.assertEqual([(p.page, p.width) for p in prepared.pages], [(1, 10.0), (2, 20.0), (3, 30.0)])
        self.assertEqual(sorted(prepared.images), [1, 2, 3])

    def test_refusals(self) -> None:
        png = encode(Image.new("RGB", (100, 100)), "PNG")
        refused_with(self, sniff.PNG, png, "image_too_large", max_image_pixels=9999)
        refused_with(self, sniff.PNG, b"\x89PNG\r\n\x1a\nnot really", "corrupt_file")
        frames = [Image.new("RGB", (5, 5)) for _ in range(3)]
        tiff = encode(frames[0], "TIFF", save_all=True, append_images=frames[1:])
        refused_with(self, sniff.TIFF, tiff, "too_many_pages", max_pages=2)


class OtherTypesTest(unittest.TestCase):
    def test_types_without_pages_to_draw_give_nothing(self) -> None:
        for kind in (sniff.TEXT, sniff.ZIP, sniff.OLE2):
            with self.subTest(kind):
                self.assertEqual(prepare(kind, b"a,b\n"), PreparedPages([], {}))


class SheetPagesTest(unittest.TestCase):
    def test_one_page_per_sheet_sized_by_the_last_row_and_column_used(self) -> None:
        spans = [
            Span("a", "a", CellLocation(2, 1, 1)),
            Span("b", "b", CellLocation(1, 3, 2)),
            Span("c", "c", CellLocation(1, 1, 5)),
            Span("d", "d", CellLocation(2, 7, 1)),
        ]
        self.assertEqual(
            sheet_pages(spans),
            [PageInfo(1, 5.0, 3.0, "cells"), PageInfo(2, 1.0, 7.0, "cells")],
        )

    def test_boxes_and_empty_input_give_no_sheets(self) -> None:
        self.assertEqual(sheet_pages([]), [])
        self.assertEqual(sheet_pages([Span("x", "x", BoxLocation(1, 0.1, 0.1, 0.2, 0.2))]), [])


class PageChecksTest(unittest.TestCase):
    """PRD 2 R3 and R4: each page's blank flag and effective DPI, filled by the preparer."""

    def check_of(self, kind: str, data: bytes, page: int = 1, dpi: int = 72) -> PageCheck:
        return prepare(kind, data, dpi=dpi).checks[page]

    def test_a_600px_image_placed_540pt_wide_is_80_dpi_and_low_resolution(self) -> None:
        data = make_pdf([Page(picture=Picture(text_like_picture(600, 800), 36, 72, 540, 720))])
        check = self.check_of(sniff.PDF, data)
        self.assertEqual((check.effective_dpi, check.low_resolution), (80, True))

    def test_a_2000px_image_placed_540pt_wide_is_267_dpi_and_not_low(self) -> None:
        data = make_pdf([Page(picture=Picture(text_like_picture(2000, 2667), 36, 72, 540, 720))])
        check = self.check_of(sniff.PDF, data)
        self.assertEqual((check.effective_dpi, check.low_resolution), (267, False))

    def test_a_photo_is_its_short_side_over_a4s_short_side(self) -> None:
        check = self.check_of(sniff.PNG, encode(text_like_picture(827, 1169), "PNG"))
        self.assertEqual((check.effective_dpi, check.low_resolution), (100, True))  # 827 px / 8.27 in

    def test_a_page_with_no_image_has_no_dpi(self) -> None:
        check = self.check_of(sniff.PDF, make_pdf([Page(texts=[Text(72, 700, "Invoice total 1,180.00")])]))
        self.assertEqual((check.effective_dpi, check.low_resolution), (None, False))

    def test_the_blank_flag_is_set_for_an_empty_page_only(self) -> None:
        data = make_pdf([Page(), Page(texts=[Text(72, 700, "Invoice total 1,180.00")])])
        checks = prepare(sniff.PDF, data).checks
        self.assertEqual((checks[1].blank, checks[2].blank), (True, False))
        self.assertTrue(self.check_of(sniff.PNG, encode(Image.new("RGB", (60, 40), "white"), "PNG")).blank)

    def test_one_line_page_and_footer_only_page_are_not_blank(self) -> None:
        """The lead's R3 decision (ML 13's dark-pixel rule, PROVISIONAL): any dark ink,
        a footer alone included, keeps a page from blank, so it goes to OCR and
        nothing is lost. Test-engineer 15 planned this as "..._and_footer_only_page_is"
        (blank); the lead ruled the other way, so the name says what it asserts."""
        one_line = make_pdf([Page(texts=[Text(72, 700, "Invoice total 1,180.00")])])
        footer_only = make_pdf([Page(texts=[Text(260, 30, "Page 1 of 1", size=8)])])
        for data in (one_line, footer_only):
            with self.subTest(data=len(data)):
                self.assertFalse(self.check_of(sniff.PDF, data, dpi=150).blank)
        self.assertTrue(self.check_of(sniff.PDF, make_pdf([Page()]), dpi=150).blank)

    def test_spreadsheets_have_no_checks(self) -> None:
        self.assertEqual(prepare(sniff.TEXT, b"a,b\n").checks, {})


if __name__ == "__main__":
    unittest.main()
