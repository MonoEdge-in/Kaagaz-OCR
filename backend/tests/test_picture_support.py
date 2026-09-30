"""Guard for tests/picture_support.py: the text-like picture is never blank.

PRD 2 R3 skips blank pages, so every test that needs the OCR engine to be
called draws its page with ``text_like_picture``. If a change to the helper
(or to the blank rule) made it blank, those tests would stop calling the
engine and fail for the wrong reason. This file fails first, and says why.
"""

import io
import unittest

from PIL import Image

from kaagaz.preparing.page_checks import is_blank, measure_blank
from kaagaz.preparing.pages import open_pdf, render_pdf_page
from tests.pdf_builder import Page, Picture, make_pdf
from tests.picture_support import text_like_bars, text_like_picture

BLANK_PAGE_STDDEV = 3.0  # the PRD's BLANK_PAGE_STDDEV
# The sizes the migrated reader and pipeline tests use, plus the smallest allowed.
SIZES = [(8, 8), (20, 20), (30, 20), (32, 20), (50, 40), (200, 100), (300, 400), (400, 500), (612, 792)]


def encoded(image: Image.Image, fmt: str) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return Image.open(io.BytesIO(buffer.getvalue()))


class TextLikePictureTest(unittest.TestCase):
    def test_it_is_far_from_blank_at_every_size_and_mode(self) -> None:
        for width, height in SIZES:
            for mode in ("RGB", "L", "P", "CMYK", "RGBA"):
                with self.subTest(size=(width, height), mode=mode):
                    image = text_like_picture(width, height, mode=mode)
                    self.assertEqual((image.size, image.mode), ((width, height), mode))
                    self.assertFalse(is_blank(image, BLANK_PAGE_STDDEV))
                    # Ten times the threshold, so a small change to either side cannot flip it.
                    self.assertGreater(measure_blank(image).stddev, 10 * BLANK_PAGE_STDDEV)

    def test_it_stays_far_from_blank_through_png_jpeg_and_tiff(self) -> None:
        for fmt in ("PNG", "JPEG", "TIFF"):
            with self.subTest(fmt=fmt), encoded(text_like_picture(200, 100), fmt) as image:
                self.assertGreater(measure_blank(image).stddev, 10 * BLANK_PAGE_STDDEV)

    def test_a_pdf_page_showing_it_renders_far_from_blank(self) -> None:
        data = make_pdf([Page(picture=Picture(text_like_picture(612, 792), 0, 0, 612, 792))])
        for dpi in (72, 144, 300):
            document = open_pdf(data)
            try:
                page = document[0]
                try:
                    image = render_pdf_page(page, dpi=dpi, max_image_pixels=50_000_000)
                finally:
                    page.close()
            finally:
                document.close()
            with self.subTest(dpi=dpi):
                self.assertGreater(measure_blank(image).stddev, 10 * BLANK_PAGE_STDDEV)

    def test_it_is_the_same_every_time(self) -> None:
        self.assertEqual(text_like_picture(300, 400).tobytes(), text_like_picture(300, 400).tobytes())

    def test_the_bars_stay_inside_the_margins(self) -> None:
        for width, height in SIZES:
            with self.subTest(size=(width, height)):
                bars = text_like_bars(width, height)
                self.assertTrue(bars)
                for left, top, bar_width, bar_height in bars:
                    self.assertGreaterEqual(min(left, top, bar_width, bar_height), 1)
                    self.assertLessEqual(left + bar_width, width)
                    self.assertLessEqual(top + bar_height, height)

    def test_a_picture_too_small_to_look_like_text_is_refused(self) -> None:
        for width, height in ((7, 100), (100, 7)):
            with self.subTest(size=(width, height)), self.assertRaises(ValueError):
                text_like_picture(width, height)


if __name__ == "__main__":
    unittest.main()
