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

Refusals are short codes raised outside any ``except`` block:
``corrupt_file``, ``too_many_pages`` (more than ``max_pages`` frames) and
``image_too_large`` (more than ``max_image_pixels``, checked from the image
header before its pixels are decoded).

Time is the engine's time per page. Memory is one decoded page at a time.
"""

from kaagaz.preparing.pages import image_pages
from kaagaz.reading.ocr import OcrEngine, ocr_spans
from kaagaz.reading.spans import ReadError, Span


class ImageReader:
    def __init__(self, ocr: OcrEngine, *, max_pages: int, max_image_pixels: int) -> None:
        # No defaults: limits are Vrushit's decision (PRD section 6).
        if min(max_pages, max_image_pixels) < 1:
            raise ValueError("every limit must be at least 1")
        self._ocr = ocr
        self._max_pages = max_pages
        self._max_image_pixels = max_image_pixels
        self.name = ocr.name  # "rapidocr" in the product
        self.version = ocr.version

    def read(self, data: bytes) -> list[Span]:
        spans: list[Span] = []
        pages = image_pages(data, max_pages=self._max_pages, max_image_pixels=self._max_image_pixels)
        for number, image in enumerate(pages, start=1):
            spans.extend(ocr_spans(self._ocr.read(image), number, image.width, image.height))
            image.close()
        if not spans:
            raise ReadError("no_text")
        return spans
