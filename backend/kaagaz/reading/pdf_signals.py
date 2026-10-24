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

  * ``hidden_share``: characters that can't be seen (``hidden_chars``, which the
    second reader uses too): text render mode 3 (invisible) or 7 (clip only),
    through ``FPDFText_GetTextObject`` then ``FPDFTextObj_GetTextRenderMode``; a
    colour with no alpha (``FPDFText_GetFillColor``, or ``GetStrokeColor`` for
    stroked text); a font size under 1 pt (``FPDFText_GetFontSize``) (the
    council's PR 3 review, high 1); a box off the crop box or too small; or a
    light colour that does not stand out where the page draws it (council
    17 Oct 2026: white text on a dark band is seen, white on white is not).
    Their loose boxes, merged along each run, become ``hidden_boxes`` as page
    fractions, and both PDF readers drop the words under them, whatever the share.
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
import unicodedata
from dataclasses import dataclass
from typing import Any, cast

import pypdfium2.raw as pdfium_c

from kaagaz.preparing.pages import render_pdf_page
from kaagaz.reading.lines import PageFrame, WordBox
from kaagaz.reading.spans import ReadError
from kaagaz.reading.text_quality import PageSignals

# ML 3, verbatim: old Hindi and Marathi fonts that store Devanagari on Latin codes.
LEGACY_FONT_PATTERN = re.compile(
    r"kruti|devlys|chanakya|shivaji|shree.?dev|aps.?dv|akruti|walkman|dv.?tt|kiran|yogesh|priya|susha"
)
_SUBSET_TAG = re.compile(r"[A-Z]{6}\+")
_HIDDEN_MODES = frozenset({pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE, pdfium_c.FPDF_TEXTRENDERMODE_CLIP})
_STROKE_ONLY = frozenset({pdfium_c.FPDF_TEXTRENDERMODE_STROKE, pdfium_c.FPDF_TEXTRENDERMODE_STROKE_CLIP})
_STROKED = _STROKE_ONLY | {
    pdfium_c.FPDF_TEXTRENDERMODE_FILL_STROKE,
    pdfium_c.FPDF_TEXTRENDERMODE_FILL_STROKE_CLIP,
}
_NO_WIDTH = frozenset({"Mn", "Mc", "Cf", "Cs"})  # combining marks, joiners, surrogate halves
_HIDDEN = (-1, -1, -1)  # _look's answer for a character that can't be seen
_LINEAR = tuple(c / 255 / 12.92 if c <= 10 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in range(256))
# PROVISIONAL (council PR 3 review, high 1): a size under MIN_FONT_POINTS is too
# small to read, so it is hidden.
MIN_FONT_POINTS = 1.0
# PROVISIONAL (council 17 Oct 2026, white text), not measured on real files yet:
# a loose box (the font's height, the advance's width) smaller than this, in points, is hidden.
MIN_BOX_HEIGHT = 2.5
MIN_BOX_WIDTH = 0.3
# A colour whose WCAG contrast with white is under this is light: seen only on a
# darker background, which is checked on the drawn page. Also the contrast a
# light glyph's background needs.
LIGHT_CONTRAST = 3.0
MAX_LIGHT_GLYPHS = 5000  # more light characters on a page: all hidden, nothing drawn
RENDER_DPI = 150  # the page is drawn at this, or lower to stay near RENDER_PIXELS
RENDER_PIXELS = 4_000_000  # about 12 MB as RGB
HALO = 0.15  # of the font size, added round the tight box to find the background
INK_DISTANCE = 48  # a pixel this close (on every channel, 0 to 255) to the glyph's colour is its ink
INK_SHARE = 0.05  # of the box's pixels must be ink
CONTRAST_SHARE = 0.20  # of the box's pixels must have LIGHT_CONTRAST with the glyph's colour
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


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2 relative luminance of an sRGB colour (0 to 255 per channel): 0.0 black, 1.0 white."""
    red, green, blue = rgb
    return 0.2126 * _LINEAR[red] + 0.7152 * _LINEAR[green] + 0.0722 * _LINEAR[blue]


def contrast_ratio(one: float, two: float) -> float:
    """WCAG 2 contrast ratio of two relative luminances: 1.0 (the same) to 21.0 (black on white)."""
    return (max(one, two) + 0.05) / (min(one, two) + 0.05)


def hidden_chars(page: Any, textpage: Any) -> frozenset[int]:
    """The indices of ``textpage``'s own characters (not PDFium's made-up ones,
    nor whitespace) that can't be seen on ``page``. The one decision both PDF
    readers use, so they always agree on what is hidden.

    Each character is, by ``_look``: hidden (render mode 3 or 7, no alpha, a font
    size under MIN_FONT_POINTS, its loose box's centre outside the crop box, or a
    loose box under MIN_BOX_HEIGHT high or MIN_BOX_WIDTH wide); visible (its
    colour has a contrast of LIGHT_CONTRAST or more with white, as before); or
    light. Light characters can be seen only on a darker background, so the page
    is drawn once and each one is checked where it is drawn (``_unseen``).

    O(characters + the pixels of the light characters' boxes) time; space
    O(drawn pixels), at most about RENDER_PIXELS, drawn only for a light character.
    """
    crop = tuple(float(value) for value in page.get_cropbox())
    hidden: set[int] = set()
    light: list[tuple[int, tuple[int, int, int]]] = []
    for index in range(textpage.count_chars()):
        code = int(pdfium_c.FPDFText_GetUnicode(textpage, index))
        if (
            not 0 < code < 0x110000
            or chr(code).isspace()
            or pdfium_c.FPDFText_IsGenerated(textpage, index) == 1
        ):
            continue
        look = _look(textpage, index, chr(code), crop)
        if look == _HIDDEN:
            hidden.add(index)
        elif look is not None:
            light.append((index, look))
    if light:
        hidden.update(_unseen(page, textpage, light))
    return frozenset(hidden)


def _look(textpage: Any, index: int, char: str, crop: tuple[float, ...]) -> tuple[int, int, int] | None:
    """``_HIDDEN``, None for visible, or a light character's colour (r, g, b). O(1)."""
    text_object = pdfium_c.FPDFText_GetTextObject(textpage, index)
    mode = pdfium_c.FPDF_TEXTRENDERMODE_FILL
    if ctypes.cast(text_object, ctypes.c_void_p).value is not None:
        mode = int(pdfium_c.FPDFTextObj_GetTextRenderMode(text_object))
    if mode in _HIDDEN_MODES or float(pdfium_c.FPDFText_GetFontSize(textpage, index)) < MIN_FONT_POINTS:
        return _HIDDEN
    left, bottom, right, top = textpage.get_charbox(index, loose=True)
    if not (crop[0] <= (left + right) / 2 <= crop[2] and crop[1] <= (bottom + top) / 2 <= crop[3]):
        return _HIDDEN
    # A combining mark, a joiner or half a surrogate pair has no width of its own.
    if unicodedata.category(char) not in _NO_WIDTH and (
        top - bottom < MIN_BOX_HEIGHT or right - left < MIN_BOX_WIDTH
    ):
        return _HIDDEN
    getters = []
    if mode not in _STROKE_ONLY:
        getters.append(pdfium_c.FPDFText_GetFillColor)
    if mode in _STROKED:
        getters.append(pdfium_c.FPDFText_GetStrokeColor)
    colours = []
    for getter in getters:
        red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
        if getter(textpage, index, red, green, blue, alpha):
            colours.append(((red.value, green.value, blue.value), alpha.value))
    if not colours:
        return None  # a pattern or shading: not read, so visible, as before
    if all(alpha == 0 for _, alpha in colours):
        return _HIDDEN
    # Fill or stroke, each as it shows on white paper; a see-through one is always checked on the page.
    shown = [
        cast(tuple[int, int, int], tuple(255 - (255 - c) * alpha // 255 for c in rgb))  # three in, three out
        for rgb, alpha in colours
        if alpha
    ]
    darkest = min(shown, key=relative_luminance)
    opaque = all(alpha == 255 for _, alpha in colours if alpha)
    return None if opaque and contrast_ratio(relative_luminance(darkest), 1.0) >= LIGHT_CONTRAST else darkest


def _unseen(page: Any, textpage: Any, light: list[tuple[int, tuple[int, int, int]]]) -> set[int]:
    """The light characters that can't be seen where the page draws them. Above
    MAX_LIGHT_GLYPHS of them, or if the page cannot be drawn, all of them (the
    rule before light text was checked)."""
    every = {index for index, _ in light}
    if len(light) > MAX_LIGHT_GLYPHS:
        return every
    width, height = page.get_size()
    dpi = min(RENDER_DPI, int(72 * math.sqrt(RENDER_PIXELS / max(width * height, 1e-9))))
    try:
        image = render_pdf_page(page, dpi=max(1, dpi), max_image_pixels=2 * RENDER_PIXELS)
    except ReadError:
        return every
    try:
        frame = ViewerFrame.of(page)
        scale = (image.width / max(frame.width, 1e-9), image.height / max(frame.height, 1e-9))
        unseen = set()
        for index, colour in light:
            tight = textpage.get_charbox(index)
            halo = float(pdfium_c.FPDFText_GetFontSize(textpage, index)) * HALO
            grown = (tight[0] - halo, tight[1] - halo, tight[2] + halo, tight[3] + halo)
            if not _shows(image, frame.word("", *tight), frame.word("", *grown), scale, colour):
                unseen.add(index)
        return unseen
    finally:
        image.close()


def _shows(
    image: Any, tight: WordBox, grown: WordBox, scale: tuple[float, float], colour: tuple[int, int, int]
) -> bool:
    """Whether a glyph of ``colour`` is seen in ``image``: INK_SHARE of the pixels
    of its ``tight`` box are close to its colour (it reached the page: not
    covered, clipped or see-through), and CONTRAST_SHARE of those of its
    ``grown`` box (the tight box with a margin, so a thin "I" has background
    too) stand out from it. O(the boxes' pixels), by their distinct colours."""
    own = relative_luminance(colour)
    ink = _share(
        image,
        tight,
        scale,
        lambda px: max(abs(a - b) for a, b in zip(px, colour, strict=True)) <= INK_DISTANCE,
    )
    seen = _share(
        image, grown, scale, lambda px: contrast_ratio(relative_luminance(px), own) >= LIGHT_CONTRAST
    )
    return ink >= INK_SHARE and seen >= CONTRAST_SHARE


def _share(image: Any, box: WordBox, scale: tuple[float, float], test: Any) -> float:
    """The share of ``box``'s pixels (at least one, rounded outwards) that pass ``test``."""
    x0, x1 = max(0, math.floor(box.x0 * scale[0])), min(image.width, math.ceil(box.x1 * scale[0]))
    y0, y1 = max(0, math.floor(box.top * scale[1])), min(image.height, math.ceil(box.bottom * scale[1]))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    total: int = (x1 - x0) * (y1 - y0)
    counted: list[tuple[int, Any]] = image.crop((x0, y0, x1, y1)).getcolors(total)  # (count, pixel)
    return sum(count for count, pixel in counted if test(pixel)) / total


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
        unseen = hidden_chars(page, textpage)
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
            if index in unseen:
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
