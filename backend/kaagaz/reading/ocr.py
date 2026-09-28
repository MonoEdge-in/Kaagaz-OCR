"""The scan reader engine (OCR), behind one small interface.

PROVISIONAL: which AI reads scanned pages is Vrushit's decision (CLAUDE.md
"Decisions only Vrushit makes"). RapidOCR (Apache-2.0) is the stand-in: it
runs on the laptop's CPU with the model files shipped inside its package, so
no page ever leaves the machine and nothing is downloaded at run time.

* ``OcrEngine`` is what the page readers depend on, so tests use a fake and a
  different engine means one new class here.
* ``RapidOcrEngine`` imports ``rapidocr`` only on its first page, because it
  is an optional extra (``pip install .[ocr]``): without it, the rest of the
  backend still starts, and ``OCR_ENGINE=none`` marks scans needs_reader.
  The model is loaded once, under a lock, and reused for every page.
* ``ocr_spans`` turns an engine's pixel boxes into line spans with page
  fractions, through the same line rule as the PDF readers
  (``kaagaz/reading/lines.py``), so "INVOICE" and "1042" found as two boxes
  on one line become one span, like a PDF text line would.

An engine never logs what it read: RapidOCR's own logger is set to errors
only, and its messages hold no page text.
"""

import math
import threading
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Protocol

from PIL import Image

from kaagaz.reading.lines import PageFrame, WordBox, line_spans
from kaagaz.reading.spans import Span
from kaagaz.settings import Settings, SettingsError


@dataclass(frozen=True, slots=True)
class OcrLine:
    """One piece of text an engine found, in pixels of the image it was given."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float


class OcrEngine(Protocol):
    name: str
    version: str

    def read(self, image: Image.Image) -> list[OcrLine]: ...


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self) -> None:
        version = _installed_version("rapidocr")
        if version is None:
            raise SettingsError("OCR_ENGINE=rapidocr needs the ocr extra installed")
        self.version = version
        self._engine: Any = None
        self._lock = threading.Lock()

    def read(self, image: Image.Image) -> list[OcrLine]:
        """Time is dominated by the model, roughly linear in the image's pixels."""
        result = self._model()(image.convert("RGB"))
        # A result with boxes but no text comes back as a detection-only
        # object without ``txts``, hence getattr.
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        if boxes is None or texts is None or scores is None:
            return []  # nothing found on the page
        lines = []
        for box, text, score in zip(boxes, texts, scores, strict=True):
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            lines.append(OcrLine(str(text), min(xs), min(ys), max(xs), max(ys), float(score)))
        return lines

    def _model(self) -> Any:
        with self._lock:
            if self._engine is None:
                from rapidocr import RapidOCR  # lazy: an optional extra

                self._engine = RapidOCR(params={"Global.log_level": "error"})
            return self._engine


def _installed_version(package: str) -> str | None:
    """The package's version, read from its metadata without importing it."""
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def build_ocr_engine(settings: Settings) -> OcrEngine | None:
    if settings.ocr_engine == "none":
        return None
    return RapidOcrEngine()


def ocr_spans(lines: list[OcrLine], page_no: int, width_px: int, height_px: int) -> list[Span]:
    """Pixel boxes to line spans with page fractions, clamped to 0..1.

    A line with no text, or a confidence that is not a number, is left out
    (never guessed). Confidence is clamped to 0..1. O(n log n) for n lines.
    """
    words = [
        WordBox(line.text, line.x0, line.y0, line.x1, line.y1, min(1.0, max(0.0, line.confidence)))
        for line in lines
        if line.text.strip() and math.isfinite(line.confidence)
    ]
    return line_spans(words, page_no, PageFrame(0.0, 0.0, float(width_px), float(height_px)))
