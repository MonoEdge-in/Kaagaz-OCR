"""R3 (blank pages) and R4 (effective DPI): kaagaz/preparing/page_checks.py.

The blank cases model the pages the council measured on pdf_builder renders
at 150 dpi (a letter page, 1275 x 1650 pixels): an empty page (stddev 0.0),
one 12 pt line (9.01), a footer only (2.82), and a short 10 pt line. They are
drawn with bars, never with a font (tests/picture_support.py), and each test
checks the stddev its bars give, so the model stays tied to the measurement.
"""

import math
import unittest

import pypdfium2 as pdfium
from PIL import Image

from kaagaz.preparing.page_checks import (
    A4_SHORT_SIDE_INCHES,
    BLANK_DARK_PIXELS,
    effective_dpi,
    is_blank,
    largest_image_dpi,
    low_resolution,
    measure_blank,
    photo_dpi,
    placed_size,
)
from tests.pdf_builder import Page, Picture, _assemble, _image_object, _stream, make_pdf
from tests.picture_support import Bar, picture_with_bars

BLANK_PAGE_STDDEV = 3.0  # the PRD's BLANK_PAGE_STDDEV
LOW_RES_DPI = 150  # the PRD's LOW_RES_DPI
PAGE_150 = (1275, 1650)  # a letter page at 150 dpi, as the page picture is stored


def words(top: int, height: int, widths: list[int], *, left: int = 150, gap: int = 14) -> list[Bar]:
    """One line of word bars, left to right."""
    bars: list[Bar] = []
    for width in widths:
        bars.append((left, top, width, height))
        left += width + gap
    return bars


# One 12 pt line: ten words of 4 px strokes (measured on a render: stddev 9.01).
LINE_12PT = words(300, 4, [96, 60, 110, 44, 80, 100, 62, 108, 46, 84])
# A short 10 pt line such as "Total 500" (measured: stddev 2.76, 179 dark pixels).
SHORT_LINE_10PT = words(300, 4, [44, 32])
# A small footer such as "Page 1 of 1" at the foot of the page (council: stddev 2.82).
FOOTER = words(1580, 4, [34, 12, 20, 12], left=560, gap=10)


def page(
    bars: list[Bar], *, size: tuple[int, int] = PAGE_150, ink: tuple[int, int, int] = (24, 24, 24)
) -> Image.Image:
    return picture_with_bars(size[0], size[1], bars, ink=ink)


def stddev_only(image: Image.Image) -> bool:
    """R3 without the council's dark-pixel rule: the stddev alone."""
    return is_blank(image, BLANK_PAGE_STDDEV, dark_pixels=image.width * image.height + 1)


class BlankTest(unittest.TestCase):
    def test_an_empty_page_is_blank(self) -> None:
        image = page([])
        measure = measure_blank(image)
        self.assertEqual((measure.stddev, measure.dark_pixels), (0.0, 0))
        self.assertTrue(is_blank(image, BLANK_PAGE_STDDEV))

    def test_a_flat_grey_page_is_blank(self) -> None:
        self.assertTrue(is_blank(Image.new("RGB", (300, 400), "gray"), BLANK_PAGE_STDDEV))

    def test_one_12pt_line_is_not_blank(self) -> None:
        image = page(LINE_12PT)
        self.assertAlmostEqual(measure_blank(image).stddev, 9.01, delta=0.3)
        self.assertFalse(is_blank(image, BLANK_PAGE_STDDEV))
        self.assertFalse(stddev_only(image))  # its spread alone is enough

    def test_a_short_10pt_line_is_not_blank_thanks_to_the_dark_pixel_rule(self) -> None:
        image = page(SHORT_LINE_10PT)
        measure = measure_blank(image)
        self.assertLess(measure.stddev, BLANK_PAGE_STDDEV)
        self.assertTrue(stddev_only(image))  # R3's stddev alone would skip it...
        self.assertGreaterEqual(measure.dark_pixels, BLANK_DARK_PIXELS)
        self.assertFalse(is_blank(image, BLANK_PAGE_STDDEV))  # ...its dark ink keeps it

    def test_a_dark_footer_alone_is_blank_by_stddev_but_kept_by_the_dark_pixel_rule(self) -> None:
        # CONFLICT for the lead: test-engineer 1 counts a footer-only page as
        # blank (stddev 2.82), but ML 13's dark-pixel rule keeps any dark ink.
        # A real 8 pt "Page 1 of 1" render at 150 dpi has 117 dark pixels, so
        # under ML 13 a footer-only page is not blank and goes to OCR (ML 13:
        # "measure how many sparse digital pages R2 sends to OCR").
        image = page(FOOTER)
        measure = measure_blank(image)
        self.assertAlmostEqual(measure.stddev, 2.82, delta=0.1)
        self.assertTrue(stddev_only(image))
        self.assertGreaterEqual(measure.dark_pixels, BLANK_DARK_PIXELS)
        self.assertFalse(is_blank(image, BLANK_PAGE_STDDEV))

    def test_a_faint_mark_with_no_dark_ink_is_blank(self) -> None:
        # A light grey smudge: 30 below paper white, so never "dark" (48 below the mean).
        image = page([(400, 600, 150, 100)], ink=(225, 225, 225))
        measure = measure_blank(image)
        self.assertEqual(measure.dark_pixels, 0)
        self.assertGreater(measure.stddev, 1.0)
        self.assertTrue(is_blank(image, BLANK_PAGE_STDDEV))

    def test_scanner_specks_at_300_dpi_are_averaged_out(self) -> None:
        # Single grey specks every 40 px: each one is dark in the full picture,
        # but the 4 x 4 box of the small copy averages it to nearly white.
        specks = [(x, y, 1, 1) for x in range(20, 2550, 40) for y in range(20, 3300, 40)]
        image = page(specks, size=(2550, 3300), ink=(150, 150, 150))
        self.assertTrue(is_blank(image, BLANK_PAGE_STDDEV))
        self.assertEqual(measure_blank(image).dark_pixels, 0)
        full_size_dark = sum(image.convert("L").histogram()[:207])
        self.assertEqual(full_size_dark, len(specks))  # without the small copy: all dark

    def test_the_same_page_at_150_and_300_dpi_measures_alike(self) -> None:
        at_150 = measure_blank(page(LINE_12PT))
        doubled = [(2 * x, 2 * y, 2 * w, 2 * h) for x, y, w, h in LINE_12PT]
        at_300 = measure_blank(page(doubled, size=(2550, 3300)))
        self.assertAlmostEqual(at_150.stddev, at_300.stddev, delta=0.2)
        self.assertAlmostEqual(at_150.dark_pixels, at_300.dark_pixels, delta=at_150.dark_pixels * 0.05)

    def test_exactly_at_each_threshold(self) -> None:
        # 612 px wide: measured as it is. Single dark pixels, spread out.
        dots = [(10 + 20 * i, 400, 1, 1) for i in range(BLANK_DARK_PIXELS)]
        self.assertTrue(is_blank(page(dots[:-1], size=(612, 792)), BLANK_PAGE_STDDEV))  # 19: blank
        self.assertFalse(is_blank(page(dots, size=(612, 792)), BLANK_PAGE_STDDEV))  # 20: kept
        faint = page([(100, 100, 200, 50)], size=(612, 792), ink=(230, 230, 230))
        spread = measure_blank(faint).stddev
        self.assertFalse(is_blank(faint, spread))  # "under", so equal is not blank
        self.assertTrue(is_blank(faint, math.nextafter(spread, math.inf)))

    def test_any_picture_mode_is_measured_in_grey(self) -> None:
        for mode in ("L", "P", "CMYK", "RGBA", "1"):
            with self.subTest(mode=mode):
                self.assertTrue(
                    is_blank(Image.new("RGB", (100, 80), "white").convert(mode), BLANK_PAGE_STDDEV)
                )
                self.assertFalse(is_blank(page(LINE_12PT).convert(mode), BLANK_PAGE_STDDEV))

    def test_a_picture_with_no_pixels_is_blank(self) -> None:
        self.assertTrue(is_blank(Image.new("RGB", (0, 0)), BLANK_PAGE_STDDEV))


class EffectiveDpiTest(unittest.TestCase):
    def test_pixel_width_over_placed_width_in_inches(self) -> None:
        self.assertEqual(effective_dpi((600, 800), 540.0, 700.0), 80)  # the plan's verified case
        self.assertEqual(effective_dpi((2550, 3300), 612.0, 792.0), 300)  # a normal 300 dpi scan
        self.assertEqual(effective_dpi((850, 1100), 612.0, 792.0), 100)  # a 100 dpi scan

    def test_a_tiny_image_stretched_over_the_page_has_no_dpi(self) -> None:
        # 2 px over 8.5 inches is 0.24 dpi: stored as NULL, never 0 (effective_dpi > 0 CHECK).
        self.assertIsNone(effective_dpi((2, 2), 612.0, 792.0))
        self.assertIsNone(effective_dpi((8, 8), 580.0, 792.0))  # 0.99 dpi
        self.assertEqual(effective_dpi((8, 8), 576.0, 792.0), 1)  # exactly 1 dpi

    def test_no_size_negative_size_and_non_finite_sizes_have_no_dpi(self) -> None:
        for image_px, placed_width, placed_height in (
            ((600, 800), 0.0, 700.0),  # zero width
            ((600, 800), 540.0, 0.0),  # zero height
            ((600, 800), -540.0, 700.0),  # negative width (flipped bounds, unsorted)
            ((600, 800), 540.0, -700.0),
            ((600, 800), math.nan, 700.0),
            ((600, 800), math.inf, 700.0),
            ((600, 800), 540.0, math.nan),
            ((0, 800), 540.0, 700.0),  # an image with no pixels
            ((600, 0), 540.0, 700.0),
        ):
            with self.subTest(image_px=image_px, width=placed_width, height=placed_height):
                self.assertIsNone(effective_dpi(image_px, placed_width, placed_height))

    def test_a_dpi_past_the_integer_column_has_no_dpi(self) -> None:
        self.assertIsNone(effective_dpi((1_000_000, 1_000_000), 1e-6, 1e-6))  # 7.2e13 dpi
        self.assertIsNone(effective_dpi((600, 800), 5e-324, 700.0))  # infinite

    def test_a_photo_assumes_an_a4_short_side(self) -> None:
        self.assertEqual(A4_SHORT_SIDE_INCHES, 8.27)
        self.assertEqual(photo_dpi(2480, 3508), 300)  # A4 at 300 dpi
        self.assertEqual(photo_dpi(3508, 2480), 300)  # the short side, either way round
        self.assertEqual(photo_dpi(1240, 1754), 150)
        self.assertEqual(photo_dpi(100, 50), 6)
        self.assertIsNone(photo_dpi(5, 5))  # 0.6 dpi
        self.assertIsNone(photo_dpi(0, 50))

    def test_low_resolution_is_below_the_threshold(self) -> None:
        self.assertTrue(low_resolution(80, LOW_RES_DPI))
        self.assertTrue(low_resolution(149, LOW_RES_DPI))
        self.assertFalse(low_resolution(150, LOW_RES_DPI))
        self.assertFalse(low_resolution(300, LOW_RES_DPI))
        self.assertFalse(low_resolution(None, LOW_RES_DPI))  # unknown is not flagged

    def test_placed_size_follows_the_image_through_mirrors_and_turns(self) -> None:
        self.assertEqual(placed_size(540.0, 0.0, 0.0, 700.0), (540.0, 700.0))
        self.assertEqual(placed_size(-540.0, 0.0, 0.0, 700.0), (540.0, 700.0))  # mirrored
        self.assertEqual(placed_size(0.0, 540.0, -700.0, 0.0), (540.0, 700.0))  # a quarter turn
        self.assertEqual(placed_size(0.0, 0.0, 0.0, 700.0), (0.0, 700.0))
        width, height = placed_size(300.0, 400.0, -400.0, 300.0)  # turned, 500 x 500
        self.assertAlmostEqual(width, 500.0)
        self.assertAlmostEqual(height, 500.0)


def white(width: int, height: int) -> Image.Image:
    return Image.new("RGB", (width, height), "white")


def raw_pdf(content: bytes, images: list[bytes], *, form: bool = False) -> bytes:
    """One 612 x 792 page drawing ``content``, with image objects /Im1, /Im2... and,
    if ``form``, a form XObject /Fm1 that draws /Im1 over its unit square.
    pdf_builder draws one plain picture per page, so the other placements are here."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        _stream(b"", content),
        b"",
    ]
    names = []
    for number, image in enumerate(images, start=1):
        objects.append(image)
        names.append(b"/Im%d %d 0 R" % (number, len(objects)))
    if form:
        resources = b"/Resources << /XObject << /Im1 5 0 R >> >> "
        objects.append(_stream(b"/Type /XObject /Subtype /Form /BBox [0 0 1 1] " + resources, b"/Im1 Do"))
        names.append(b"/Fm1 %d 0 R" % len(objects))
    objects[3] = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R "
        b"/Resources << /XObject << " + b" ".join(names) + b" >> >> >>"
    )
    return _assemble(objects, b"")


def image(width: int, height: int) -> bytes:
    return _image_object(white(width, height))


# An image object with no /Width: pypdfium2 5.13 reports it 0 pixels wide.
NO_WIDTH = _stream(
    b"/Type /XObject /Subtype /Image /Height 8 /ColorSpace /DeviceRGB /BitsPerComponent 8 ", b""
)


def page_dpi(data: bytes) -> int | None:
    document = pdfium.PdfDocument(data)
    try:
        pdf_page = document[0]
        try:
            return largest_image_dpi(pdf_page)
        finally:
            pdf_page.close()
    finally:
        document.close()


class LargestImageDpiTest(unittest.TestCase):
    def test_a_page_with_no_image_has_no_dpi(self) -> None:
        self.assertIsNone(page_dpi(make_pdf([Page()])))

    def test_a_600px_image_placed_540pt_wide_is_80_dpi(self) -> None:
        data = make_pdf([Page(picture=Picture(white(600, 800), 36, 72, 540, 700))])
        self.assertEqual(page_dpi(data), 80)

    def test_a_full_page_scan_at_300_and_at_100_dpi(self) -> None:
        self.assertEqual(page_dpi(make_pdf([Page(picture=Picture(white(2550, 3300), 0, 0, 612, 792))])), 300)
        self.assertEqual(page_dpi(make_pdf([Page(picture=Picture(white(850, 1100), 0, 0, 612, 792))])), 100)

    def test_a_mirrored_image_is_80_dpi_too(self) -> None:
        data = make_pdf([Page(picture=Picture(white(600, 800), 576, 72, -540, 700))])
        self.assertEqual(page_dpi(data), 80)

    def test_a_turned_image_pairs_its_pixel_width_with_its_own_width(self) -> None:
        # Drawn a quarter turn: 700 pt across the page, but the image's own width is 540 pt.
        self.assertEqual(page_dpi(raw_pdf(b"q 0 540 -700 0 736 72 cm /Im1 Do Q", [image(600, 800)])), 80)

    def test_zero_width_and_tiny_stretched_images_have_no_dpi(self) -> None:
        self.assertIsNone(page_dpi(make_pdf([Page(picture=Picture(white(600, 800), 36, 72, 0, 700))])))
        self.assertIsNone(page_dpi(make_pdf([Page(picture=Picture(white(2, 2), 0, 0, 612, 792))])))

    def test_the_largest_image_by_placed_area_decides(self) -> None:
        content = b"q 100 0 0 100 0 0 cm /Im1 Do Q q 540 0 0 700 36 72 cm /Im2 Do Q"
        self.assertEqual(page_dpi(raw_pdf(content, [image(1000, 1000), image(600, 800)])), 80)

    def test_an_image_inside_a_form_xobject_is_left_out(self) -> None:
        # pypdfium2 5.13 reports a nested image in the form's own space (bounds
        # 0, 0, 1, 1 here), which is no page size: the page has no dpi, not a wrong one.
        data = raw_pdf(b"q 540 0 0 700 36 72 cm /Fm1 Do Q", [image(600, 800)], form=True)
        self.assertIsNone(page_dpi(data))

    def test_an_image_with_no_pixel_width_is_passed_over(self) -> None:
        content = b"q 540 0 0 700 36 72 cm /Im1 Do Q q 100 0 0 100 0 0 cm /Im2 Do Q"
        self.assertEqual(page_dpi(raw_pdf(content, [NO_WIDTH, image(1000, 1000)])), 720)
        self.assertIsNone(page_dpi(raw_pdf(b"q 540 0 0 700 36 72 cm /Im1 Do Q", [NO_WIDTH])))


if __name__ == "__main__":
    unittest.main()
