"""A second, independent reading of a PDF's text layer, with pypdfium2.

PROVISIONAL: the second, independent reader is Vrushit's decision
(CLAUDE.md, due Tue 6 Oct). PDFium (Chrome's PDF engine) is the stand-in: it
parses the file and decodes the fonts with code that shares nothing with
pdfplumber and pdfminer, so when both read the same text at the same place,
that is real agreement, and when they differ, a person looks
(``readers_disagree``).

Each character comes from PDFium's text page with its box. Characters are
joined into words at spaces and line breaks, and words into lines by the same
rule the first reader uses (``kaagaz/reading/lines.py``), so a disagreement is
about the text read, never about where a line was cut. Boxes use PDFium's
loose character box (the font's full height), which matches how pdfplumber
measures a line.

Pages without a text layer give nothing: this reader never runs OCR, so a
scan's values stay single_reader unless another second reading is set up.

Coordinates: PDFium gives character boxes in PDF units (origin bottom left,
before the page's rotation). They are turned into the viewer's frame (the
crop box, rotation applied, origin top left) before the line rule runs.

Refusals are the same short codes as the first reader (``encrypted``,
``corrupt_file``, ``too_many_pages``), raised outside any ``except`` block.

Time is O(characters). Memory is one page's text page at a time.
"""

from dataclasses import dataclass
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from kaagaz.preparing.pages import check_page_count, open_pdf
from kaagaz.reading.lines import PageFrame, WordBox, line_spans
from kaagaz.reading.spans import ReadError, Span


class PdfiumReader:
    name = "pdfium"

    def __init__(self, *, max_pages: int) -> None:
        # No default: limits are Vrushit's decision (PRD section 6).
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self._max_pages = max_pages
        self.version = f"{pdfium.PYPDFIUM_INFO}+pdfium-{pdfium.PDFIUM_INFO.build}"

    def read(self, data: bytes) -> list[Span]:
        spans: list[Span] = []
        failed = False
        document = open_pdf(data)
        try:
            check_page_count(len(document), self._max_pages)
            for index in range(len(document)):
                page = document[index]
                try:
                    spans.extend(_page_spans(page, index + 1))
                finally:
                    page.close()
        except pdfium.PdfiumError:
            failed = True
        finally:
            document.close()
        if failed:
            raise ReadError("corrupt_file")  # outside the except block: nothing chained
        return spans


def _page_spans(page: Any, number: int) -> list[Span]:
    left, bottom, right, top = (float(value) for value in page.get_cropbox())
    frame = _Frame(left, bottom, right, top, int(page.get_rotation()) % 360)
    textpage = page.get_textpage()
    try:
        words = [frame.word(*run) for run in _runs(textpage)]
    finally:
        textpage.close()
    return line_spans(words, number, PageFrame(0.0, 0.0, frame.width, frame.height))


# A run of characters between spaces: (text, left, bottom, right, top) in PDF units.
_Run = tuple[str, float, float, float, float]


def _runs(textpage: Any) -> list[_Run]:
    runs: list[_Run] = []
    units: list[str] = []
    box = (0.0, 0.0, 0.0, 0.0)
    count = textpage.count_chars()
    for index in range(count + 1):  # one past the end flushes the last run
        char = _char(textpage, index) if index < count else " "
        if char.isspace():
            if units:
                runs.append((_text(units), *box))
            units = []
            continue
        c_left, c_bottom, c_right, c_top = textpage.get_charbox(index, loose=True)
        if units:
            box = (min(box[0], c_left), min(box[1], c_bottom), max(box[2], c_right), max(box[3], c_top))
        else:
            box = (c_left, c_bottom, c_right, c_top)
        units.append(char)
    return runs


def _char(textpage: Any, index: int) -> str:
    """The character at ``index``; a space for one PDFium made up (a line break
    or a space it inferred) or one with no Unicode value, so it ends a run."""
    code = int(pdfium_c.FPDFText_GetUnicode(textpage, index))
    if not 0 < code < 0x110000 or pdfium_c.FPDFText_IsGenerated(textpage, index) == 1:
        return " "
    return chr(code)


def _text(units: list[str]) -> str:
    """PDFium can hand a character outside the basic plane as two UTF-16 halves:
    rejoin them, and turn a lone half into U+FFFD instead of storing it."""
    return "".join(units).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


@dataclass(frozen=True, slots=True)
class _Frame:
    """The crop box in PDF units and the page's rotation (clockwise, as shown)."""

    left: float
    bottom: float
    right: float
    top: float
    rotation: int

    @property
    def width(self) -> float:
        across = self.right - self.left
        return self.top - self.bottom if self.rotation in (90, 270) else across

    @property
    def height(self) -> float:
        down = self.top - self.bottom
        return self.right - self.left if self.rotation in (90, 270) else down

    def word(self, text: str, left: float, bottom: float, right: float, top: float) -> WordBox:
        """A box in PDF units to the viewer's frame: origin top left of the page as shown, y down."""
        (u_a, v_a), (u_b, v_b) = self._point(left, bottom), self._point(right, top)
        return WordBox(text, min(u_a, u_b), min(v_a, v_b), max(u_a, u_b), max(v_a, v_b))

    def _point(self, x: float, y: float) -> tuple[float, float]:
        if self.rotation == 90:
            return y - self.bottom, x - self.left
        if self.rotation == 180:
            return self.right - x, y - self.bottom
        if self.rotation == 270:
            return self.top - y, self.right - x
        return x - self.left, self.top - y
