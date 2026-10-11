"""A document read partly by the main reader and partly by the local reader (PRD 5 Q7:
the Gemini reader hands a page it can't read to RapidOCR). The engine is a fake.
"""

import io

from PIL import Image

from kaagaz.reading.image_reader import ImageReader
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import BoxLocation
from tests.picture_support import text_like_picture

PAGE_TEXT = "Sunil Patil 27AABCS1234K1Z5"


class Engine:
    """Answers each page with the next of ``script``: a kind is a one-block page (the main
    reader), None is a RapidOCR line with a confidence."""

    name = "mixed"
    version = "mixed-1"

    def __init__(self, script: list[str | None]) -> None:
        self.script = script
        self.pages = 0

    def read(self, image: Image.Image) -> list[OcrLine]:
        step = self.script[self.pages % len(self.script)]
        self.pages += 1
        w, h = image.width, image.height
        return [OcrLine(PAGE_TEXT, 0.1 * w, 0.1 * h, 0.9 * w, 0.2 * h, None if step else 0.62, step)]


def tiff(pages: int) -> bytes:
    buffer = io.BytesIO()
    first, *rest = [text_like_picture(153, 198) for _ in range(pages)]
    first.save(buffer, format="TIFF", save_all=True, append_images=rest)
    return buffer.getvalue()


def test_a_document_with_pages_from_both_readers_gives_valid_spans() -> None:
    """ocr_spans decides per page: a main reader page stays one span per block, a RapidOCR
    page goes through the line rule with its real confidence (so a value from it is
    labelled by it), and the socket's checks pass for both."""
    engine = Engine(["paragraph", None])
    reader = ImageReader(engine, max_pages=5, max_image_pixels=10_000_000, ocr_dpi=72, blank_stddev=3.0)
    spans = ReaderSocket({"tiff": reader}).read("tiff", tiff(2))
    first, second = spans
    assert (first.kind, first.confidence, first.origin) == ("paragraph", None, "ocr")
    assert (second.kind, second.confidence, second.origin) == (None, 0.62, "ocr")
    assert [span.location.page for span in spans if isinstance(span.location, BoxLocation)] == [1, 2]
