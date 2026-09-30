"""What a PDF page's file says about its text layer: the R2 signals.

PRD 2 PR 3, with the council's ML 3 and ML 4. ``page_signals`` reads one
page, open in pdfplumber and in pypdfium2 at the same time (as the reader has
it), and gives the ``PageSignals`` that ``text_quality.text_layer_ok`` needs:

* ``coverage``: the largest image's share of the crop box, clipped to it
  (pdfplumber ``page.images``, whose ``x0``, ``x1``, ``top`` and ``bottom`` are
  in the crop box's own top-left coordinates, nested form XObjects included).
* From one pass over PDFium's text page, for the characters the file holds
  (PDFium's made-up spaces and line breaks, and whitespace, are left out):

  * ``hidden_share``: drawn with text render mode 3 (invisible) or 7 (clip
    only): ``FPDFText_GetTextObject`` then ``FPDFTextObj_GetTextRenderMode``.
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
import math
import re
from typing import Any

import pypdfium2.raw as pdfium_c

from kaagaz.reading.text_quality import PageSignals

# ML 3, verbatim: old Hindi and Marathi fonts that store Devanagari on Latin codes.
LEGACY_FONT_PATTERN = re.compile(
    r"kruti|devlys|chanakya|shivaji|shree.?dev|aps.?dv|akruti|walkman|dv.?tt|kiran|yogesh|priya|susha"
)
_SUBSET_TAG = re.compile(r"[A-Z]{6}\+")
_HIDDEN_MODES = frozenset({pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE, pdfium_c.FPDF_TEXTRENDERMODE_CLIP})
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
    """The largest image's share of a pdfplumber page's crop box, each image
    clipped to the crop box first; 0.0 with no image. An image with a
    coordinate that is not finite is left out. O(images) time, O(1) space.
    """
    left, top, right, bottom = (float(value) for value in page.cropbox)
    area = (right - left) * (bottom - top)
    if not (math.isfinite(area) and area > 0):
        return 0.0
    largest = 0.0
    for image in page.images:
        x0, y0, x1, y1 = (float(image[key]) for key in ("x0", "top", "x1", "bottom"))
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            continue
        width = min(x1, right) - max(x0, left)
        height = min(y1, bottom) - max(y0, top)
        if width > 0 and height > 0:
            largest = max(largest, width * height)
    return min(1.0, largest / area)


def page_signals(plumber_page: Any, pdfium_page: Any) -> PageSignals:
    """The R2 signals of one page, open in pdfplumber and in pypdfium2.

    O(images + characters) time. Space: one PDFium text page (closed before
    returning), plus one entry per distinct font.
    """
    hidden, legacy, map_errors = _character_shares(pdfium_page)
    return PageSignals(
        coverage=image_coverage(plumber_page),
        hidden_share=hidden,
        legacy_font_share=legacy,
        map_error_share=map_errors,
    )


def _character_shares(page: Any) -> tuple[float, float, float]:
    """(hidden, legacy font, map error) shares of the page's own characters."""
    counted = hidden = legacy = map_errors = 0
    fonts: dict[int, bool] = {}  # font handle -> legacy?
    last_object: int | None = None
    object_hidden = object_legacy = False
    textpage = page.get_textpage()
    try:
        for index in range(textpage.count_chars()):
            if pdfium_c.FPDFText_IsGenerated(textpage, index) == 1:
                continue
            code = int(pdfium_c.FPDFText_GetUnicode(textpage, index))
            if 0 < code < 0x110000 and chr(code).isspace():
                continue
            counted += 1
            if pdfium_c.FPDFText_HasUnicodeMapError(textpage, index) == 1:
                map_errors += 1
            text_object = pdfium_c.FPDFText_GetTextObject(textpage, index)
            address = ctypes.cast(text_object, ctypes.c_void_p).value
            if address is None:
                continue
            if address != last_object:  # characters of one object come together
                last_object = address
                object_hidden = int(pdfium_c.FPDFTextObj_GetTextRenderMode(text_object)) in _HIDDEN_MODES
                object_legacy = _font_is_legacy(pdfium_c.FPDFTextObj_GetFont(text_object), fonts)
            hidden += object_hidden
            legacy += object_legacy
    finally:
        textpage.close()
    if not counted:
        return 0.0, 0.0, 0.0
    return hidden / counted, legacy / counted, map_errors / counted


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
