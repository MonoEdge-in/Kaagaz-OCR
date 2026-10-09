"""Read a PDF's text layer with pdfplumber, and scanned pages with OCR.

The first reader for PDFs. A digital PDF (an invoice exported from billing
software) carries its text with positions, so it is read directly, without
any AI: words are grouped into lines (``kaagaz/reading/lines.py``) and each
line becomes one span, its box a fraction of the page as the viewer shows it
(the crop box, with the page's rotation applied). Such spans have no
confidence: the text is exactly what the file holds.

A text layer has no layout labels, so headings are found from the fonts
(``_with_kinds``, PROVISIONAL), for pieces (PRD 2 C1, C3) and the card (D1):
a line is a ``heading`` when its largest character is at least
``_HEADING_SIZE`` times the page's median line size, or when it is bold, the
same size as the body or larger, alone on its line and not ending like a
sentence or a label, on a page where fewer than half the lines are bold. Either
way it has a letter and at most ``_HEADING_MAX_WORDS`` words. On page 1 the
first of the largest headings by size is the ``title``. Everything else has no
kind, as before.

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
* With ``dpi_rule`` ``text_height`` (OCR_DPI_RULE, council 5 Oct 2026,
  PROVISIONAL), that DPI is only the ceiling: each page is drawn at the DPI
  its text needs (``kaagaz/reading/ocr_dpi.py``), measured on a small probe
  drawn the same way, with the scan cap only on a page R2 would call a scan
  (``scan_like``: images over ``scan_image_coverage``, under 200 visible
  characters). A page on which the
  engine finds nothing at a lowered DPI is read once more at the ceiling.
  ``fixed`` (the default) draws every page at the ceiling, as before.

``on_page`` (if given) is called after every page, text pages too, so the
worker's heartbeat and the progress move once per page (PRD 2 X3).

Pages in parallel (PRD 5 G8): given a ``PagePool`` (``pages``), the engine's
``read_page`` for up to its size of pages runs on the pool's threads while
this thread goes on parsing and drawing the next pages; pdfplumber, pypdfium2,
drawing, the blank check and the second reading at the ceiling all stay on
this thread, and pages are finished, ``on_page`` included, strictly in page
order (``socket.PagesInOrder``), so the spans are the same as without a pool.
The engine is asked through ``read_page`` (``ocr.as_page_engine``), so the
meter keeps each page's ``PageRead``.

Refusals, as short codes raised outside any ``except`` block (the library
error can quote the file, so nothing is chained on):

* ``encrypted``: the file needs a password to open;
* ``corrupt_file``: pdfplumber or pdfminer cannot parse it;
* ``too_many_pages``: more than ``max_pages``, checked before any page is read;
* ``image_too_large``: a page to OCR would be too many pixels even at 72 dpi.

Time is O(characters + images) per page, plus the engine's time per OCR'd
page (with ``text_height``, plus one probe picture, and a second reading of a
page that gave nothing at a lowered DPI). Memory is one page at a time: the
pdfplumber and pypdfium2 documents are open together, each page's parsed
objects are released after it is read, and an OCR'd page's picture after the
engine has read it; with a pool, up to its size of pictures plus the one being
drawn.
engine has read it.
"""

import io
import math
import statistics
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError
from pdfminer.pdfexceptions import PDFException
from pdfminer.psexceptions import PSException
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException
from PIL import Image

from kaagaz.ingestion import sniff
from kaagaz.preparing.page_checks import is_blank, largest_image_dpi
from kaagaz.preparing.pages import check_page_count, open_pdf, render_pdf_page
from kaagaz.reading.lines import PageFrame, WordBox, line_spans
from kaagaz.reading.ocr import MAX_OCR_PIXELS, OcrEngine, PageEngine, PageRead, as_page_engine, ocr_spans
from kaagaz.reading.ocr import MAX_OCR_PIXELS, OcrEngine, ocr_spans
from kaagaz.reading.ocr_dpi import (
    DPI_RULES,
    FIXED,
    MIN_DPI,
    TEXT_HEIGHT,
    chosen_dpi,
    line_height_px,
    probe_dpi,
)
from kaagaz.reading.pdf_signals import page_signals, words_left_visible
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import NoReader, OnPage, PagePool, PagesInOrder
from kaagaz.reading.spans import BoxLocation, ReadError, Span
from kaagaz.reading.text_quality import (
    PageSignals,
    TextLayerRules,
    scan_like,
    text_layer_fallback_allowed,
    text_layer_ok,
)

# PROVISIONAL: the lowest DPI a page is drawn at for OCR; a page too big even then is refused.
_MIN_OCR_DPI = 72

# PROVISIONAL, not measured on real documents yet: a text-layer heading (the module notes).
_HEADING_SIZE = 1.2  # times the page's median line size
_HEADING_MAX_WORDS = 10
# A bold line ending so is a sentence or a label ("Total:"), never a heading.
_NOT_HEADING_ENDS = tuple(".,:;!?\N{DEVANAGARI DANDA}")

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

# One page's spans and how it was read (its route), or None when it gave nothing unread.
_PageResult = tuple[list[Span], RouteDecision | None]


@dataclass(frozen=True, slots=True)
class _ScanPage:
    """A page drawn for the engine, waiting to be read: its picture, the DPI it was drawn
    at and the ceiling, the route check's decision, and the text layer to fall back to."""

    image: Image.Image
    dpi: int
    ceiling: int
    decision: RouteDecision
    fallback: list[Span]


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
        dpi_rule: str = FIXED,
        pages: PagePool | None = None,
    ) -> None:
        # No defaults: limits are Vrushit's decision (PRD section 6).
        if min(max_pages, dpi, max_image_pixels) < 1:
            raise ValueError("every limit must be at least 1")
        if not blank_stddev >= 0:
            raise ValueError("blank_stddev must be 0 or more")
        if dpi_rule not in DPI_RULES:
            raise ValueError(f"dpi_rule must be one of {sorted(DPI_RULES)}")
        self._dpi_rule = dpi_rule
        self._ocr = ocr
        self._reads: PageEngine | None = None if ocr is None else as_page_engine(ocr)
        self._pool = pages
        self._max_pages = max_pages
        self._dpi = dpi
        self._max_ocr_pixels = min(max_image_pixels, MAX_OCR_PIXELS)
        self._lowered_dpi: int | None = None  # the last read's lowest DPI, when the pixel limits cut dpi
        self._rule_dpi: int | None = None  # its lowest DPI the text-height rule chose under that
        self._rereads = 0  # its pages read again at the ceiling
        self._rules = rules
        self._blank_stddev = blank_stddev

    @property
    def version(self) -> str:
        """Asked for after reading, so the engine is named as it was, with the DPI when a
        page had to be drawn lower to fit the pixel limits and, with the text-height rule,
        the lowest DPI it chose (``;text_dpi=``) and the pages read again (``;ocr_reread=``)."""
        version = str(pdfplumber.__version__)
        if self._ocr is None:
            return version
        marks = "" if self._lowered_dpi is None else f";ocr_dpi={self._lowered_dpi}"
        marks += "" if self._rule_dpi is None else f";text_dpi={self._rule_dpi}"
        marks += f";ocr_reread={self._rereads}" if self._rereads else ""
        return f"{version}+{self._ocr.name}-{self._ocr.version}{marks}"

    def read(self, data: bytes, *, on_page: OnPage | None = None) -> list[Span]:
        self._lowered_dpi = self._rule_dpi = None
        self._rereads = 0
        spans: list[Span] = []

        def finish(page: _PageResult) -> None:
            page_spans, read = page
            spans.extend(page_spans)
            if on_page is not None:
                on_page(read)

        with PagesInOrder(self._reads, self._pool) as pages:

            def visit(pdf: Any, document: Any, number: int) -> None:
                page = self._read_page(pdf, document, number)
                if isinstance(page, _ScanPage):
                    self._read_scan(page, document, number, pages, finish)
                else:
                    pages.ready(lambda: finish(page))

            # The pages left are finished while the documents are still open: the second
            # reading at the ceiling draws its page again.
            _for_each_page(data, self._max_pages, visit, end=pages.drain)
        if not spans:
            if self._ocr is None:
                raise NoReader(sniff.PDF)
            raise ReadError("no_text")
        return spans

    def _read_page(self, pdf: Any, document: Any, number: int) -> _PageResult | _ScanPage:
        """One page's spans, by the rules in the module notes, and how it was read
        (its route and the check's reason), or None when it gave nothing unread; or,
        for a page the engine must read, the page drawn for it (``_read_scan``)."""
        pdfium_page = document[number - 1]
        try:
            rules = self._rules
            text_spans, decision, signals = _parsed(lambda: _text_layer(pdf, pdfium_page, number, rules))
            if decision.route == "text_layer":
                return text_spans, decision
            if self._ocr is None:  # kept as before; a page with no text was not read
                return text_spans, _kept(decision) if text_spans else None
            ceiling = _fitting_dpi(pdfium_page.get_size(), self._dpi, self._max_ocr_pixels)
            scan = scan_like(" ".join(span.text for span in text_spans), signals, rules)
            dpi = self._page_dpi(pdfium_page, ceiling, scan)
            image = render_pdf_page(pdfium_page, dpi=dpi, max_image_pixels=self._max_ocr_pixels)
        finally:
            pdfium_page.close()
        if ceiling < self._dpi:
            self._lowered_dpi = min(ceiling, self._lowered_dpi or ceiling)
        if dpi < ceiling:
            self._rule_dpi = min(dpi, self._rule_dpi or dpi)
        fallback = text_spans if text_layer_fallback_allowed(decision) else []
        if is_blank(image, self._blank_stddev):  # R3: a blank page is never sent to OCR
            image.close()
            return fallback, _kept(decision) if fallback else RouteDecision("blank", "blank")
        return _ScanPage(image, dpi, ceiling, decision, fallback)

    def _read_scan(
        self,
        page: _ScanPage,
        document: Any,
        number: int,
        pages: PagesInOrder,
        finish: Callable[[_PageResult], None],
    ) -> None:
        """Hand ``page`` to the engine through ``pages``; when its turn comes, on this
        thread, read it once more at the ceiling if it gave nothing at a lowered DPI, and
        fall back to its text layer if it still gave nothing (the module notes)."""
        reads = self._reads
        if reads is None:
            raise ValueError("a page is drawn for OCR only with an engine")
        width, height = page.image.size

        def on_read(read: PageRead) -> None:
            scanned = ocr_spans(list(read.lines), number, width, height)
            if not scanned and page.dpi < page.ceiling:  # nothing at a lowered DPI: once more at the ceiling
                self._rereads += 1
                scanned = _read_again(reads, document, number, page.ceiling, self._max_ocr_pixels)
            if scanned or not page.fallback:
                finish((scanned, page.decision))
            else:
                finish((page.fallback, _kept(page.decision)))

        pages.read(page.image, number, on_read)

    def _page_dpi(self, pdfium_page: Any, ceiling: int, scan: bool) -> int:
        """The DPI the page is drawn at for OCR: ``ceiling`` (today's, from OCR_DPI and
        the pixel limits) with ``fixed``, or by the text-height rule, with the scan cap
        only on a ``scan`` page (R2's ``scan_like``: its images cover more than
        ``scan_image_coverage`` and it has under 200 visible characters, so vector text
        over a full-page background image is never capped at the image's DPI).

        ponytail: the coverage is the union of the page's images (R2's own number), so
        a large low-resolution image beside a sharp one caps the page at the largest
        image's DPI."""
        if self._dpi_rule == FIXED or ceiling <= MIN_DPI:
            return ceiling
        probe = probe_dpi(pdfium_page.get_size(), ceiling)
        if probe is None:
            return ceiling
        picture = render_pdf_page(pdfium_page, dpi=probe, max_image_pixels=self._max_ocr_pixels)
        try:
            line_px = line_height_px(picture, dpi=probe)
        finally:
            picture.close()
        capped = scan and self._dpi_rule == TEXT_HEIGHT
        return chosen_dpi(ceiling, line_px, probe, largest_image_dpi(pdfium_page) if capped else None)


def _read_again(reads: PageEngine, document: Any, number: int, dpi: int, max_pixels: int) -> list[Span]:
    """Page ``number`` drawn again at ``dpi`` and read by ``reads``, on this thread.
    O(pixels) plus the engine."""
    pdfium_page = document[number - 1]
    try:
        image = render_pdf_page(pdfium_page, dpi=dpi, max_image_pixels=max_pixels)
    finally:
        pdfium_page.close()
    try:
        width, height = image.size
        return ocr_spans(list(reads.read_page(image, page_no=number).lines), number, width, height)
    finally:
        image.close()
            image.close()
        if not scanned and dpi < ceiling:  # nothing found at a lowered DPI: once more at the ceiling
            self._rereads += 1
            scanned = _read_again(self._ocr, document, number, ceiling, self._max_ocr_pixels)
        if scanned or not fallback:
            return scanned, decision
        return fallback, _kept(decision)

    def _page_dpi(self, pdfium_page: Any, ceiling: int, scan: bool) -> int:
        """The DPI the page is drawn at for OCR: ``ceiling`` (today's, from OCR_DPI and
        the pixel limits) with ``fixed``, or by the text-height rule, with the scan cap
        only on a ``scan`` page (R2's ``scan_like``: its images cover more than
        ``scan_image_coverage`` and it has under 200 visible characters, so vector text
        over a full-page background image is never capped at the image's DPI).

        ponytail: the coverage is the union of the page's images (R2's own number), so
        a large low-resolution image beside a sharp one caps the page at the largest
        image's DPI."""
        if self._dpi_rule == FIXED or ceiling <= MIN_DPI:
            return ceiling
        probe = probe_dpi(pdfium_page.get_size(), ceiling)
        if probe is None:
            return ceiling
        picture = render_pdf_page(pdfium_page, dpi=probe, max_image_pixels=self._max_ocr_pixels)
        try:
            line_px = line_height_px(picture, dpi=probe)
        finally:
            picture.close()
        capped = scan and self._dpi_rule == TEXT_HEIGHT
        return chosen_dpi(ceiling, line_px, probe, largest_image_dpi(pdfium_page) if capped else None)


def _read_again(ocr: OcrEngine, document: Any, number: int, dpi: int, max_pixels: int) -> list[Span]:
    """Page ``number`` drawn again at ``dpi`` and read by ``ocr``. O(pixels) plus the engine."""
    pdfium_page = document[number - 1]
    try:
        image = render_pdf_page(pdfium_page, dpi=dpi, max_image_pixels=max_pixels)
    finally:
        pdfium_page.close()
    try:
        return ocr_spans(ocr.read(image), number, image.width, image.height)
    finally:
        image.close()


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


def _for_each_page(
    data: bytes,
    max_pages: int,
    visit: Callable[[Any, Any, int], None],
    *,
    end: Callable[[], None] | None = None,
) -> None:
    """``visit(pdfplumber document, pypdfium2 document, page number)`` for each page
    in turn, both documents open together, after the page count check; then ``end()``,
    with both still open."""
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
            if end is not None:
                end()
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
        signals = page_signals(page, pdfium_page)
        spans = _page_spans(page, number, signals.hidden_boxes)
    finally:
        page.close()  # release this page's parsed objects
    return spans, text_layer_ok(" ".join(span.text for span in spans), signals, rules), signals


def _kept(decision: RouteDecision) -> RouteDecision:
    """A page whose text layer failed the check but was kept: text_layer, with why."""
    return RouteDecision("text_layer", decision.reason)


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


def _page_spans(
    page: pdfplumber.page.Page, number: int, hidden: tuple[tuple[float, float, float, float], ...] = ()
) -> list[Span]:
    """The page's lines, without the words under ``hidden`` (page fractions): text
    that can't be seen never becomes a line (the council's PR 3 review, high 1)."""
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
    frame = PageFrame(left, top, right - left, bottom - top)
    spans = line_spans(words_left_visible(words, frame, hidden), number, frame, origin="text_layer")
    return _with_kinds(spans, page.chars, frame)


def _with_kinds(spans: list[Span], chars: list[dict[str, Any]], frame: PageFrame) -> list[Span]:
    """``spans`` with the headings and the title marked, by the rule in the module notes.
    Each character counts for the span its centre lies in; the spans are put in 100 bands
    by height, as ``words_left_visible`` does. O(characters x spans per band + S log S)."""
    boxes = [span.location for span in spans if isinstance(span.location, BoxLocation)]
    if len(boxes) != len(spans) or frame.width <= 0 or frame.height <= 0:
        return spans  # line_spans gives only boxes, on a page with a size
    bands: dict[int, list[int]] = {}
    for k, box in enumerate(boxes):
        for band in range(int(box.y0 * 100), min(100, int(box.y1 * 100)) + 1):
            bands.setdefault(band, []).append(k)
    sizes: dict[int, float] = {}  # span -> its largest character
    bold: dict[int, bool] = {}  # span -> every character is bold
    for char in chars:
        if not str(char["text"]).strip():
            continue
        x = ((float(char["x0"]) + float(char["x1"])) / 2 - frame.left) / frame.width
        y = ((float(char["top"]) + float(char["bottom"])) / 2 - frame.top) / frame.height
        for k in bands.get(int(y * 100), ()):
            box = boxes[k]
            if box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1:
                sizes[k] = max(sizes.get(k, 0.0), float(char["size"]))
                bold[k] = bold.get(k, True) and "bold" in str(char["fontname"]).casefold()
                break
    if not sizes:
        return spans
    body = statistics.median(sizes.values())
    bold_page = 2 * sum(bold.values()) >= len(bold)
    headings = set()
    for k, size in sizes.items():
        text = spans[k].text
        if len(text.split()) > _HEADING_MAX_WORDS or not any(ch.isalpha() for ch in text):
            continue
        strong = bold[k] and not bold_page and size >= body and not text.endswith(_NOT_HEADING_ENDS)
        if size >= _HEADING_SIZE * body or (strong and _alone(boxes, k)):
            headings.add(k)
    if not headings:
        return spans
    largest = max(sizes[k] for k in headings)
    title = min(k for k in headings if sizes[k] == largest) if boxes[0].page == 1 else None
    return [
        replace(span, kind="title" if k == title else "heading") if k in headings else span
        for k, span in enumerate(spans)
    ]


def _alone(boxes: list[BoxLocation], k: int) -> bool:
    """No other span shares span k's line: none overlaps it by half the smaller height, as
    ``line_spans`` joins a line, and it gives one line's spans one after another."""
    box = boxes[k]
    for other in (boxes[k - 1] if k else None, boxes[k + 1] if k + 1 < len(boxes) else None):
        if other is None:
            continue
        smaller = min(box.y1 - box.y0, other.y1 - other.y0)
        if min(box.y1, other.y1) - max(box.y0, other.y0) >= smaller / 2:
            return False
    return True
