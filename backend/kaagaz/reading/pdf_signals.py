"""What a PDF page's file says about its text layer: the R2 signals.

PRD 2 PR 3, with the council's ML 3 and ML 4. ``page_signals`` reads one
page, open in pdfplumber and in pypdfium2 at the same time (as the reader has
it), and gives the ``PageSignals`` that ``text_quality.text_layer_ok`` needs:

* ``coverage``: the share of the crop box under the page's images, their union
  clipped to it, so a scan cut into strips counts as a whole (pdfplumber
  ``page.images``, whose ``x0``, ``x1``, ``top`` and ``bottom`` are in the crop
  box's own top-left coordinates, nested form XObjects included).
* From one pass over PDFium's text page, for the characters the file holds
  (PDFium's made-up spaces and line breaks, and whitespace, are left out):

  * ``hidden_share``: characters that can't be seen (``is_hidden_char``): text
    render mode 3 (invisible) or 7 (clip only), through ``FPDFText_GetTextObject``
    then ``FPDFTextObj_GetTextRenderMode``; a fill with no alpha or near white
    (``FPDFText_GetFillColor``); or a font size under 1 pt
    (``FPDFText_GetFontSize``) (the council's PR 3 review, high 1). Their loose
    boxes, merged along each run, become ``hidden_boxes`` as page fractions, and
    both PDF readers drop the words under them, whatever the share.
  * ``legacy_font_share``: in a font whose ``/BaseFont`` name, without a
    subset tag (``ABCDEF+``) and casefolded, matches ``LEGACY_FONT_PATTERN``:
    ``FPDFTextObj_GetFont`` then ``FPDFFont_GetBaseFontName``.
  * ``map_error_share``: ``FPDFText_HasUnicodeMapError`` is 1.

VERIFIED on pypdfium2 5.13 (PDFium 153.0.7999.0) and pdfplumber 0.11.10:

* The render mode is reported per text object, 3 and 7 as written. PDFium's
  made-up characters have no text object (NULL).
* ``FPDFText_HasUnicodeMapError`` is 1 for each glyph of a Type0 font with
  no ToUnicode (pdfplumber writes those as ``(cid:N)``), 0 for mapped glyphs,
  and -1 for an index out of range.
* ``FPDFFont_GetBaseFontName`` is the ``/BaseFont`` name as written, subset
  tag kept and ``#20`` decoded, with or without a font descriptor.
  pdfplumber's ``fontname`` is the descriptor's ``/FontName`` instead, and
  ``unknown`` when a font has no descriptor, so the font names come from
  PDFium here, not pdfplumber (ML 3's VERIFY).
* Both pdfplumber and PDFium extract invisible text, so both PDF readers
  would agree on a hidden layer: that is why it goes to OCR.
"""

import ctypes
import itertools
import math
import re
from dataclasses import dataclass
from typing import Any

import pypdfium2.raw as pdfium_c

from kaagaz.reading.lines import PageFrame, WordBox
from kaagaz.reading.text_quality import PageSignals

# ML 3, verbatim: old Hindi and Marathi fonts that store Devanagari on Latin codes.
LEGACY_FONT_PATTERN = re.compile(
    r"kruti|devlys|chanakya|shivaji|shree.?dev|aps.?dv|akruti|walkman|dv.?tt|kiran|yogesh|priya|susha"
)
_SUBSET_TAG = re.compile(r"[A-Z]{6}\+")
_HIDDEN_MODES = frozenset({pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE, pdfium_c.FPDF_TEXTRENDERMODE_CLIP})
# PROVISIONAL (council PR 3 review, high 1): a fill at or above this on every
# channel (0 to 255) is near white, and a size under MIN_FONT_POINTS is too small
# to read, so both are hidden.
NEAR_WHITE = 240
MIN_FONT_POINTS = 1.0
# At most this many images are measured for the coverage, the largest first, so
# a crafted page of thousands of images costs O(k^2) with k this, not more.
_MAX_COVERAGE_IMAGES = 500

# A real font name is short. A longer one is not read (and so not called
# legacy), so a crafted name cannot make us copy megabytes per font.
_MAX_FONT_NAME_BYTES = 256


def is_legacy_font(name: str) -> bool:
    """Whether a font name, without its subset tag and casefolded, is an old
    Devanagari font (ML 3). The tag is six random capitals, which could spell
    a family name ("AKIRAN+"), so it is dropped first. O(len(name)).
    """
    bare = name[7:] if _SUBSET_TAG.match(name) else name
    return LEGACY_FONT_PATTERN.search(bare.casefold()) is not None


def image_coverage(page: Any) -> float:
    """The share of a pdfplumber page's crop box under its images: their union,
    each clipped to the crop box first; 0.0 with no image. An image with a
    coordinate that is not finite is left out. Exact, by slabs between the
    images' top and bottom edges: O(k^2 log k) for the k (at most
    _MAX_COVERAGE_IMAGES) largest images, O(k) space.
    """
    left, top, right, bottom = (float(value) for value in page.cropbox)
    area = (right - left) * (bottom - top)
    if not (math.isfinite(area) and area > 0):
        return 0.0
    boxes: list[tuple[float, float, float, float]] = []
    for image in page.images:
        x0, y0, x1, y1 = (float(image[key]) for key in ("x0", "top", "x1", "bottom"))
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            continue
        clipped = (max(x0, left), max(y0, top), min(x1, right), min(y1, bottom))
        if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
            boxes.append(clipped)
    boxes.sort(key=lambda box: (box[2] - box[0]) * (box[3] - box[1]), reverse=True)
    return min(1.0, _union_area(boxes[:_MAX_COVERAGE_IMAGES]) / area)


def _union_area(boxes: list[tuple[float, float, float, float]]) -> float:
    edges = sorted({y for box in boxes for y in (box[1], box[3])})
    covered = 0.0
    for upper, lower in itertools.pairwise(edges):
        spans = sorted((box[0], box[2]) for box in boxes if box[1] <= upper and box[3] >= lower)
        width, reach = 0.0, -math.inf
        for start, end in spans:
            if end > reach:
                width += end - max(start, reach)
                reach = end
        covered += width * (lower - upper)
    return covered


@dataclass(frozen=True, slots=True)
class ViewerFrame:
    """A page's crop box in PDF units and its rotation (clockwise, as shown): turns
    PDFium's boxes (origin bottom left, before rotation) into the viewer's frame
    (the crop box, rotation applied, origin top left), as pdfplumber gives them."""

    left: float
    bottom: float
    right: float
    top: float
    rotation: int

    @classmethod
    def of(cls, page: Any) -> "ViewerFrame":
        left, bottom, right, top = (float(value) for value in page.get_cropbox())
        return cls(left, bottom, right, top, int(page.get_rotation()) % 360)

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

    def fractions(
        self, left: float, bottom: float, right: float, top: float
    ) -> tuple[float, float, float, float]:
        """A box in PDF units as fractions of the page as shown (x0, y0, x1, y1)."""
        box = self.word("", left, bottom, right, top)
        width, height = max(self.width, 1e-9), max(self.height, 1e-9)
        return box.x0 / width, box.top / height, box.x1 / width, box.bottom / height

    def _point(self, x: float, y: float) -> tuple[float, float]:
        if self.rotation == 90:
            return y - self.bottom, x - self.left
        if self.rotation == 180:
            return self.right - x, y - self.bottom
        if self.rotation == 270:
            return self.top - y, self.right - x
        return x - self.left, self.top - y


def is_hidden_char(textpage: Any, index: int) -> bool:
    """Whether the character at ``index`` can't be seen: render mode 3 or 7, a fill
    with no alpha or near white, or a font size under MIN_FONT_POINTS. O(1)."""
    text_object = pdfium_c.FPDFText_GetTextObject(textpage, index)
    if ctypes.cast(text_object, ctypes.c_void_p).value is not None and (
        int(pdfium_c.FPDFTextObj_GetTextRenderMode(text_object)) in _HIDDEN_MODES
    ):
        return True
    if float(pdfium_c.FPDFText_GetFontSize(textpage, index)) < MIN_FONT_POINTS:
        return True
    red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
    filled = bool(pdfium_c.FPDFText_GetFillColor(textpage, index, red, green, blue, alpha))
    return filled and (alpha.value == 0 or min(red.value, green.value, blue.value) >= NEAR_WHITE)


def words_left_visible(
    words: list[WordBox], frame: PageFrame, hidden: tuple[tuple[float, float, float, float], ...]
) -> list[WordBox]:
    """``words`` (in the units of ``frame``, the visible page) without those whose
    centre lies in a hidden box (page fractions). The boxes are put in 100 bands by height, so
    each word is checked against the boxes of its own band: O(words + boxes x
    bands spanned) time and space."""
    if not hidden or frame.width <= 0 or frame.height <= 0:
        return words
    bands: dict[int, list[tuple[float, float, float, float]]] = {}
    for box in hidden:
        for band in range(max(0, int(box[1] * 100)), min(100, int(box[3] * 100)) + 1):
            bands.setdefault(band, []).append(box)
    kept = []
    for word in words:
        x = ((word.x0 + word.x1) / 2 - frame.left) / frame.width
        y = ((word.top + word.bottom) / 2 - frame.top) / frame.height
        inside = any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in bands.get(int(y * 100), ()))
        if not inside:
            kept.append(word)
    return kept


def page_signals(plumber_page: Any, pdfium_page: Any) -> PageSignals:
    """The R2 signals of one page, open in pdfplumber and in pypdfium2.

    O(images + characters) time. Space: one PDFium text page (closed before
    returning), plus one entry per distinct font.
    """
    hidden, legacy, map_errors, boxes = _character_shares(pdfium_page)
    return PageSignals(
        coverage=image_coverage(plumber_page),
        hidden_share=hidden,
        legacy_font_share=legacy,
        map_error_share=map_errors,
        hidden_boxes=boxes,
    )


def _character_shares(page: Any) -> tuple[float, float, float, tuple[tuple[float, float, float, float], ...]]:
    """(hidden, legacy font, map error) shares of the page's own characters, and
    the hidden characters' boxes as page fractions, one per run of them (a run
    ends at a line PDFium made up, or a character that can be seen)."""
    counted = hidden = legacy = map_errors = 0
    fonts: dict[int, bool] = {}  # font handle -> legacy?
    last_object: int | None = None
    object_legacy = False
    frame = ViewerFrame.of(page)
    boxes: list[tuple[float, float, float, float]] = []
    run: list[float] | None = None  # the hidden run being built: left, bottom, right, top in PDF units
    textpage = page.get_textpage()
    try:
        for index in range(textpage.count_chars()):
            if pdfium_c.FPDFText_IsGenerated(textpage, index) == 1:
                _flush(run, boxes, frame)
                run = None
                continue
            code = int(pdfium_c.FPDFText_GetUnicode(textpage, index))
            if 0 < code < 0x110000 and chr(code).isspace():
                continue
            counted += 1
            if pdfium_c.FPDFText_HasUnicodeMapError(textpage, index) == 1:
                map_errors += 1
            if is_hidden_char(textpage, index):
                hidden += 1
                left, bottom, right, top = textpage.get_charbox(index, loose=True)
                if run is None:
                    run = [left, bottom, right, top]
                else:
                    run = [min(run[0], left), min(run[1], bottom), max(run[2], right), max(run[3], top)]
            else:
                _flush(run, boxes, frame)
                run = None
            text_object = pdfium_c.FPDFText_GetTextObject(textpage, index)
            address = ctypes.cast(text_object, ctypes.c_void_p).value
            if address is None:
                continue
            if address != last_object:  # characters of one object come together
                last_object = address
                object_legacy = _font_is_legacy(pdfium_c.FPDFTextObj_GetFont(text_object), fonts)
            legacy += object_legacy
        _flush(run, boxes, frame)
    finally:
        textpage.close()
    if not counted:
        return 0.0, 0.0, 0.0, ()
    return hidden / counted, legacy / counted, map_errors / counted, tuple(boxes)


def _flush(
    run: list[float] | None, boxes: list[tuple[float, float, float, float]], frame: ViewerFrame
) -> None:
    """Add the hidden run's box, if there is one, as page fractions."""
    if run is not None:
        boxes.append(frame.fractions(*run))


def _font_is_legacy(font: Any, fonts: dict[int, bool]) -> bool:
    address = ctypes.cast(font, ctypes.c_void_p).value
    if address is None:
        return False
    if address not in fonts:
        fonts[address] = is_legacy_font(_base_font_name(font))
    return fonts[address]


def _base_font_name(font: Any) -> str:
    """The font's /BaseFont name, or "" if PDFium has none or it is too long."""
    size = int(pdfium_c.FPDFFont_GetBaseFontName(font, None, 0))  # bytes, with the final NUL
    if not 0 < size <= _MAX_FONT_NAME_BYTES:
        return ""
    buffer = ctypes.create_string_buffer(size)
    if int(pdfium_c.FPDFFont_GetBaseFontName(font, buffer, size)) != size:
        return ""
    return buffer.value.decode("utf-8", "replace")
