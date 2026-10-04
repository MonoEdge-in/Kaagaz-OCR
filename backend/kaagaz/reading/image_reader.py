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

A blank frame (PRD 2 R3: its grey spread under ``blank_stddev``, with almost
no dark ink; ``kaagaz/preparing/page_checks.py``) is never sent to the engine
and gives nothing.

``on_page`` (if given) is called after each page, blank ones too, so a long
TIFF can renew its lease between pages and the progress moves.

PROVISIONAL (PRD 2 R5 for photos): a frame whose short side is more than
``ocr_dpi`` x 8.27 in (A4's short side) is shrunk to that for the engine only,
never enlarged; the stored picture is not changed, and boxes are fractions, so
they still fit it. A frame still over ``MAX_OCR_PIXELS`` is shrunk to fit it,
so its PNG stays under the ocr server's body cap.

Refusals are short codes raised outside any ``except`` block:
``corrupt_file``, ``too_many_pages`` (more than ``max_pages`` frames) and
``image_too_large`` (more than ``max_image_pixels``, checked from the image
header before its pixels are decoded).

Time is the engine's time per page. Memory is one decoded page at a time.
"""

import math

from PIL import Image

from kaagaz.preparing.page_checks import is_blank
from kaagaz.preparing.pages import image_pages
from kaagaz.reading.ocr import MAX_OCR_PIXELS, OcrEngine, ocr_spans
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import OnPage
from kaagaz.reading.spans import ReadError, Span

_A4_SHORT_SIDE_INCHES = 8.27
# A photo has no text layer (few_chars); a blank one is never sent (R3).
_READ_BY_OCR = RouteDecision("ocr", "few_chars")
_BLANK = RouteDecision("blank", "blank")


class ImageReader:
    def __init__(
        self, ocr: OcrEngine, *, max_pages: int, max_image_pixels: int, ocr_dpi: int, blank_stddev: float
    ) -> None:
        # No defaults: limits are Vrushit's decision (PRD section 6).
        if min(max_pages, max_image_pixels, ocr_dpi) < 1:
            raise ValueError("every limit must be at least 1")
        if not blank_stddev >= 0:
            raise ValueError("blank_stddev must be 0 or more")
        self._blank_stddev = blank_stddev
        self._ocr = ocr
        self._max_pages = max_pages
        self._max_image_pixels = max_image_pixels
        self._max_short_side = max(1, round(ocr_dpi * _A4_SHORT_SIDE_INCHES))
        self._max_ocr_pixels = MAX_OCR_PIXELS

    @property
    def name(self) -> str:
        return self._ocr.name  # "paddleocr-vl" or "rapidocr" in the product: the engine in use

    @property
    def version(self) -> str:
        return self._ocr.version  # asked for after reading: PaddleOCR-VL's comes from its reply

    def read(self, data: bytes, *, on_page: OnPage | None = None) -> list[Span]:
        spans: list[Span] = []
        pages = image_pages(data, max_pages=self._max_pages, max_image_pixels=self._max_image_pixels)
        for number, image in enumerate(pages, start=1):
            page = _for_ocr(image, self._max_short_side, self._max_ocr_pixels)
            blank = is_blank(page, self._blank_stddev)
            if not blank:
                spans.extend(ocr_spans(self._ocr.read(page), number, page.width, page.height))
            page.close()
            image.close()
            if on_page is not None:
                on_page(_BLANK if blank else _READ_BY_OCR)
        if not spans:
            raise ReadError("no_text")
        return spans


def _for_ocr(image: Image.Image, short_side: int, max_pixels: int) -> Image.Image:
    """The image with its short side at most ``short_side`` and at most ``max_pixels``
    in all (LANCZOS), never enlarged."""
    width, height = image.size
    short = min(width, height)
    if short > short_side:
        width, height = round(width * short_side / short), round(height * short_side / short)
    if width * height > max_pixels:
        scale = math.sqrt(max_pixels / (width * height))
        width, height = math.floor(width * scale), math.floor(height * scale)
    if (width, height) == image.size:
        return image
    return image.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)
