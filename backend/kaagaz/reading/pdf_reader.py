"""Read a PDF's text layer with pdfplumber, and scanned pages with OCR.

The first reader for PDFs. A digital PDF (an invoice exported from billing
software) carries its text with positions, so it is read directly, without
any AI: words are grouped into lines (``kaagaz/reading/lines.py``) and each
line becomes one span, its box a fraction of the page as the viewer shows it
(the crop box, with the page's rotation applied). Such spans have no
confidence: the text is exactly what the file holds.

A page with no words at all has no text layer: it is a scan inside a PDF.
With an OCR engine, that page is drawn with pypdfium2 at ``dpi`` (the wiring
gives ``OCR_DPI``, PRD 2 R5; the picture is never stored) and read by the
engine. A page too big at that DPI for ``max_image_pixels`` or ``MAX_OCR_PIXELS``
(an A1 drawing) is drawn at the highest DPI that fits, down to 72, and the lowest
DPI used is added to ``version`` (``;ocr_dpi=164``), which is stored with the
run's output. The engine's spans carry its confidence, and ``on_page`` (if given) is
called after it, so a long scan can renew its lease between pages. Without
an engine, the page gives nothing. If the whole document gives nothing:

* without OCR: ``NoReader("pdf")``, so it is stored and marked needs_reader;
* with OCR: ``ReadError("no_text")``: it was read and holds no text.

PROVISIONAL (#3): "no text layer" means no words on the page. A scanned page
with a small typed footer counts as a text page and is not OCR'd.

Refusals, as short codes raised outside any ``except`` block (the library
error can quote the file, so nothing is chained on):

* ``encrypted``: the file needs a password to open;
* ``corrupt_file``: pdfplumber or pdfminer cannot parse it;
* ``too_many_pages``: more than ``max_pages``, checked before any page is read;
* ``image_too_large``: a page to OCR would be too many pixels even at 72 dpi.

Time is O(characters) for text pages, plus the engine's time per OCR'd page.
Memory is one page at a time: each page's parsed objects are released after
it is read, and an OCR'd page's picture after the engine has read it.
"""

import io
import math
import struct
import zlib
from collections.abc import Callable

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError
from pdfminer.pdfexceptions import PDFException
from pdfminer.psexceptions import PSException
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException

from kaagaz.ingestion import sniff
from kaagaz.preparing.pages import check_page_count, open_pdf, render_pdf_page
from kaagaz.reading.lines import PageFrame, WordBox, line_spans
from kaagaz.reading.ocr import MAX_OCR_PIXELS, OcrEngine, ocr_spans
from kaagaz.reading.socket import NoReader
from kaagaz.reading.spans import ReadError, Span

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

    def __init__(self, ocr: OcrEngine | None, *, max_pages: int, dpi: int, max_image_pixels: int) -> None:
        # No defaults: limits are Vrushit's decision (PRD section 6).
        if min(max_pages, dpi, max_image_pixels) < 1:
            raise ValueError("every limit must be at least 1")
        self._ocr = ocr
        self._max_pages = max_pages
        self._dpi = dpi
        self._max_ocr_pixels = min(max_image_pixels, MAX_OCR_PIXELS)
        self._lowered_dpi: int | None = None  # the last read's lowest DPI, when below dpi

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
        spans, pages_without_text = self._read_text_layer(data)
        if pages_without_text and self._ocr is not None:
            spans.extend(self._read_scanned_pages(data, pages_without_text, self._ocr, on_page))
            spans.sort(key=lambda span: span.location.page)  # stable: line order kept
        if not spans:
            if self._ocr is None:
                raise NoReader(sniff.PDF)
            raise ReadError("no_text")
        return spans

    def _read_text_layer(self, data: bytes) -> tuple[list[Span], list[int]]:
        """Spans of every text page, and the numbers of the pages without text."""
        code = None
        spans: list[Span] = []
        pages_without_text: list[int] = []
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                check_page_count(len(pdf.pages), self._max_pages)
                for number, page in enumerate(pdf.pages, start=1):
                    page_spans = _page_spans(page, number)
                    page.close()  # release this page's parsed objects
                    if page_spans:
                        spans.extend(page_spans)
                    else:
                        pages_without_text.append(number)
        except PdfminerException as error:
            wrapped = error.args[0] if error.args else None
            code = "encrypted" if isinstance(wrapped, PDFEncryptionError) else "corrupt_file"
        except _DAMAGED:
            code = "corrupt_file"
        if code is not None:
            raise ReadError(code)  # outside the except blocks: nothing chained
        return spans, pages_without_text

    def _read_scanned_pages(
        self, data: bytes, numbers: list[int], ocr: OcrEngine, on_page: Callable[[], None] | None
    ) -> list[Span]:
        spans: list[Span] = []
        document = open_pdf(data)
        try:
            for number in numbers:
                page = document[number - 1]
                try:
                    dpi = _fitting_dpi(page.get_size(), self._dpi, self._max_ocr_pixels)
                    image = render_pdf_page(page, dpi=dpi, max_image_pixels=self._max_ocr_pixels)
                finally:
                    page.close()
                if dpi < self._dpi:
                    self._lowered_dpi = min(dpi, self._lowered_dpi or dpi)
                spans.extend(ocr_spans(ocr.read(image), number, image.width, image.height))
                image.close()
                if on_page is not None:
                    on_page()
        finally:
            document.close()
        return spans


def _fitting_dpi(size: tuple[float, float], dpi: int, max_pixels: int) -> int:
    """The highest DPI from ``dpi`` down to 72, one at a time, at which the page's picture
    has at most ``max_pixels``, counted as render_pdf_page counts it. O(dpi)."""
    width, height = size
    while (
        dpi > _MIN_OCR_DPI and math.ceil(width * (dpi / 72.0)) * math.ceil(height * (dpi / 72.0)) > max_pixels
    ):
        dpi -= 1
    return dpi


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
