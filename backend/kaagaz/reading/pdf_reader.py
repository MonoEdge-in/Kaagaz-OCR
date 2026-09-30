"""Read a PDF's text layer with pdfplumber, and scanned pages with OCR.

The first reader for PDFs. A digital PDF (an invoice exported from billing
software) carries its text with positions, so it is read directly, without
any AI: words are grouped into lines (``kaagaz/reading/lines.py``) and each
line becomes one span, its box a fraction of the page as the viewer shows it
(the crop box, with the page's rotation applied). Such spans have no
confidence: the text is exactly what the file holds.

Each page is judged on its own, in one pass (PRD 2 R2, with the council's
ML 3 and ML 4; ``kaagaz/reading/text_quality.py``): its text-layer spans, joined
by one space, and the file's own signals for the page
(``kaagaz/reading/pdf_signals.py``) decide whether the text layer is kept.

* Kept: the page's spans are its text-layer spans (origin ``text_layer``).
* Not kept, and no OCR engine: the text layer is kept anyway, as before, and a
  page with no text gives nothing. If the whole document gives nothing, it is
  ``NoReader("pdf")``, so it is stored and marked needs_reader.
* Not kept, with an engine: the page is drawn with pypdfium2 at ``dpi`` (the
  wiring gives ``OCR_DPI``, PRD 2 R5; the picture is never stored). A page too
  big at that DPI for ``max_image_pixels`` or ``MAX_OCR_PIXELS`` (an A1
  drawing) is drawn at the highest DPI that fits, down to 72, and the lowest
  DPI used is added to ``version`` (``;ocr_dpi=164``), which is stored with the
  run's output. A blank
  picture (R3, ``blank_stddev``) is never sent to the engine. Otherwise the
  engine's spans are the page's. When the picture is blank, or the engine
  finds nothing, the page falls back to its text-layer spans, except for
  hidden text (ML 4): a hidden layer is never the fallback. If the whole
  document gives nothing, it is ``ReadError("no_text")``: it was read and
  holds no text.

``on_page`` (if given) is called after every page, text pages too, so the
worker's heartbeat and the progress move once per page (PRD 2 X3).

Refusals, as short codes raised outside any ``except`` block (the library
error can quote the file, so nothing is chained on):

* ``encrypted``: the file needs a password to open;
* ``corrupt_file``: pdfplumber or pdfminer cannot parse it;
* ``too_many_pages``: more than ``max_pages``, checked before any page is read;
* ``image_too_large``: a page to OCR would be too many pixels even at 72 dpi.

Time is O(characters + images) per page, plus the engine's time per OCR'd
page. Memory is one page at a time: the pdfplumber and pypdfium2 documents are
open together, each page's parsed objects are released after it is read, and
an OCR'd page's picture after the engine has read it.
"""

import io
import math
import struct
import zlib
from collections.abc import Callable
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError
from pdfminer.pdfexceptions import PDFException
from pdfminer.psexceptions import PSException
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException

from kaagaz.ingestion import sniff
from kaagaz.preparing.page_checks import is_blank
from kaagaz.preparing.pages import check_page_count, open_pdf, render_pdf_page
from kaagaz.reading.lines import PageFrame, WordBox, line_spans
from kaagaz.reading.ocr import MAX_OCR_PIXELS, OcrEngine, ocr_spans
from kaagaz.reading.pdf_signals import page_signals
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import NoReader
from kaagaz.reading.spans import ReadError, Span
from kaagaz.reading.text_quality import (
    PageSignals,
    TextLayerRules,
    text_layer_fallback_allowed,
    text_layer_ok,
)

# PROVISIONAL: the lowest DPI a page is drawn at for OCR; a page too big even then is refused.
_MIN_OCR_DPI = 72

# pdfplumber wraps most pdfminer errors in PdfminerException. pdfminer is
# lenient and on a damaged file can also fail with plain Python errors from
# deep inside its parser (a missing key, a wrong type, a bad index), so those
# are listed too. Nothing else is caught.
_DAMAGED = (
    MalformedPDFException,
    PDFException,
    PSException,
    KeyError,
    TypeError,
    ValueError,
    IndexError,
    AttributeError,
    EOFError,
    OSError,
    RecursionError,
    struct.error,
    zlib.error,
)


class PdfReader:
    name = "pdfplumber"

    def __init__(
        self,
        ocr: OcrEngine | None,
        *,
        max_pages: int,
        dpi: int,
        max_image_pixels: int,
        rules: TextLayerRules,
        blank_stddev: float,
    ) -> None:
        # No defaults: limits are Vrushit's decision (PRD section 6).
        if min(max_pages, dpi, max_image_pixels) < 1:
            raise ValueError("every limit must be at least 1")
        if not blank_stddev >= 0:
            raise ValueError("blank_stddev must be 0 or more")
        self._ocr = ocr
        self._max_pages = max_pages
        self._dpi = dpi
        self._max_ocr_pixels = min(max_image_pixels, MAX_OCR_PIXELS)
        self._lowered_dpi: int | None = None  # the last read's lowest DPI, when below dpi
        self._rules = rules
        self._blank_stddev = blank_stddev

    @property
    def version(self) -> str:
        """Asked for after reading, so an engine whose version comes from its reply
        (PaddleOCR-VL) is named as it was, with the DPI when a page had to be drawn lower."""
        version = str(pdfplumber.__version__)
        if self._ocr is None:
            return version
        lowered = "" if self._lowered_dpi is None else f";ocr_dpi={self._lowered_dpi}"
        return f"{version}+{self._ocr.name}-{self._ocr.version}{lowered}"

    def read(self, data: bytes, *, on_page: Callable[[], None] | None = None) -> list[Span]:
        self._lowered_dpi = None
        spans: list[Span] = []

        def visit(pdf: Any, document: Any, number: int) -> None:
            spans.extend(self._read_page(pdf, document, number))
            if on_page is not None:
                on_page()

        _for_each_page(data, self._max_pages, visit)
        if not spans:
            if self._ocr is None:
                raise NoReader(sniff.PDF)
            raise ReadError("no_text")
        return spans

    def _read_page(self, pdf: Any, document: Any, number: int) -> list[Span]:
        """One page's spans, by the rules in the module notes."""
        pdfium_page = document[number - 1]
        try:
            rules = self._rules
            text_spans, decision, _signals = _parsed(lambda: _text_layer(pdf, pdfium_page, number, rules))
            if decision.route == "text_layer" or self._ocr is None:
                return text_spans
            dpi = _fitting_dpi(pdfium_page.get_size(), self._dpi, self._max_ocr_pixels)
            image = render_pdf_page(pdfium_page, dpi=dpi, max_image_pixels=self._max_ocr_pixels)
        finally:
            pdfium_page.close()
        if dpi < self._dpi:
            self._lowered_dpi = min(dpi, self._lowered_dpi or dpi)
        fallback = text_spans if text_layer_fallback_allowed(decision) else []
        try:
            if is_blank(image, self._blank_stddev):
                return fallback  # R3: a blank page is never sent to OCR
            scanned = ocr_spans(self._ocr.read(image), number, image.width, image.height)
        finally:
            image.close()
        return scanned or fallback


def text_layer_checks(
    data: bytes, *, rules: TextLayerRules, max_pages: int
) -> list[tuple[RouteDecision, PageSignals]]:
    """What R2 says about each page's text layer, and the signals it saw, without
    drawing a page or calling OCR: for ``cli measure`` (the council's ML 12), which
    reaches it through ``kaagaz.reading.wiring``. Refusals as ``PdfReader.read``.
    O(characters + images) time, one page at a time."""
    checks: list[tuple[RouteDecision, PageSignals]] = []

    def visit(pdf: Any, document: Any, number: int) -> None:
        pdfium_page = document[number - 1]
        try:
            _spans, decision, signals = _parsed(lambda: _text_layer(pdf, pdfium_page, number, rules))
        finally:
            pdfium_page.close()
        checks.append((decision, signals))

    _for_each_page(data, max_pages, visit)
    return checks


def _for_each_page(data: bytes, max_pages: int, visit: Callable[[Any, Any, int], None]) -> None:
    """``visit(pdfplumber document, pypdfium2 document, page number)`` for each page
    in turn, both documents open together, after the page count check."""
    pdf = _parsed(lambda: pdfplumber.open(io.BytesIO(data)))
    try:
        count = _parsed(lambda: len(pdf.pages))
        check_page_count(count, max_pages)
        document = open_pdf(data)
        try:
            if len(document) != count:  # the two parsers disagree on the file's pages: read neither
                raise ReadError("corrupt_file")
            for number in range(1, count + 1):
                visit(pdf, document, number)
        finally:
            document.close()
    finally:
        pdf.close()


def _text_layer(
    pdf: Any, pdfium_page: Any, number: int, rules: TextLayerRules
) -> tuple[list[Span], RouteDecision, PageSignals]:
    """One page's text-layer spans, what R2 says about them, and the signals it saw."""
    page = pdf.pages[number - 1]
    try:
        spans = _page_spans(page, number)
        signals = page_signals(page, pdfium_page)
    finally:
        page.close()  # release this page's parsed objects
    return spans, text_layer_ok(" ".join(span.text for span in spans), signals, rules), signals


def _fitting_dpi(size: tuple[float, float], dpi: int, max_pixels: int) -> int:
    """The highest DPI from ``dpi`` down to 72, one at a time, at which the page's picture
    has at most ``max_pixels``, counted as render_pdf_page counts it. O(dpi)."""
    width, height = size
    while (
        dpi > _MIN_OCR_DPI and math.ceil(width * (dpi / 72.0)) * math.ceil(height * (dpi / 72.0)) > max_pixels
    ):
        dpi -= 1
    return dpi


def _parsed[T](call: Callable[[], T]) -> T:
    """``call()`` on pdfplumber, with its errors turned into ``encrypted`` or
    ``corrupt_file``, raised outside the except blocks, so nothing is chained."""
    code = "corrupt_file"
    try:
        return call()
    except PdfminerException as error:
        wrapped = error.args[0] if error.args else None
        if isinstance(wrapped, PDFEncryptionError):
            code = "encrypted"
    except _DAMAGED:
        pass
    raise ReadError(code)


def _page_spans(page: pdfplumber.page.Page, number: int) -> list[Span]:
    # Boxes are fractions of the crop box, the part of the page a viewer shows
    # (and the part pypdfium2 draws). pdfplumber gives both in the same
    # top-left coordinates, with the page's rotation applied.
    left, top, right, bottom = (float(value) for value in page.cropbox)
    words = [
        WordBox(
            str(word["text"]),
            float(word["x0"]),
            float(word["top"]),
            float(word["x1"]),
            float(word["bottom"]),
        )
        for word in page.extract_words()
    ]
    return line_spans(words, number, PageFrame(left, top, right - left, bottom - top), origin="text_layer")
