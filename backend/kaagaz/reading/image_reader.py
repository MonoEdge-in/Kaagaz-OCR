"""Read a photo or scan (png, jpeg, tiff) with the OCR engine.

Each page is decoded exactly as the page picture is made
(``kaagaz/preparing/pages.py``: the same limits, EXIF orientation applied,
RGB on white), so every box points at the same spot the viewer shows. Every
frame of a TIFF is a page; any other image is one page.

Each line the engine finds becomes one span with its box as a fraction of the
page and the engine's confidence, so an unsure reading can be labelled needs
review. An image in which the engine finds no text at all fails with
``no_text``, the same as a scanned PDF: "unreadable" is a correct answer,
never a guess.

``on_page`` (if given) is called after each page, so a long TIFF can renew
its lease between pages.

PROVISIONAL (PRD 2 R5 for photos): a frame whose short side is more than
``ocr_dpi`` x 8.27 in (A4's short side) is shrunk to that for the engine only,
never enlarged; the stored picture is not changed, and boxes are fractions, so
they still fit it.

Refusals are short codes raised outside any ``except`` block:
``corrupt_file``, ``too_many_pages`` (more than ``max_pages`` frames) and
``image_too_large`` (more than ``max_image_pixels``, checked from the image
header before its pixels are decoded).

Time is the engine's time per page. Memory is one decoded page at a time.
"""

from collections.abc import Callable

from PIL import Image

from kaagaz.preparing.pages import image_pages
from kaagaz.reading.ocr import OcrEngine, ocr_spans
from kaagaz.reading.spans import ReadError, Span

_A4_SHORT_SIDE_INCHES = 8.27


class ImageReader:
    def __init__(self, ocr: OcrEngine, *, max_pages: int, max_image_pixels: int, ocr_dpi: int) -> None:
        # No defaults: limits are Vrushit's decision (PRD section 6).
        if min(max_pages, max_image_pixels, ocr_dpi) < 1:
            raise ValueError("every limit must be at least 1")
        self._ocr = ocr
        self._max_pages = max_pages
        self._max_image_pixels = max_image_pixels
        self._max_short_side = max(1, round(ocr_dpi * _A4_SHORT_SIDE_INCHES))
        self.name = ocr.name  # "paddleocr-vl" or "rapidocr" in the product

    @property
    def version(self) -> str:
        return self._ocr.version  # asked for after reading: PaddleOCR-VL's comes from its reply

    def read(self, data: bytes, *, on_page: Callable[[], None] | None = None) -> list[Span]:
        spans: list[Span] = []
        pages = image_pages(data, max_pages=self._max_pages, max_image_pixels=self._max_image_pixels)
        for number, image in enumerate(pages, start=1):
            page = _at_most(image, self._max_short_side)
            spans.extend(ocr_spans(self._ocr.read(page), number, page.width, page.height))
            page.close()
            image.close()
            if on_page is not None:
                on_page()
        if not spans:
            raise ReadError("no_text")
        return spans


def _at_most(image: Image.Image, short_side: int) -> Image.Image:
    """The image with its short side at most ``short_side`` pixels (LANCZOS), never enlarged."""
    scale = short_side / min(image.size)
    if scale >= 1:
        return image
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)
