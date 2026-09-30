"""R2: is a PDF page's text layer good enough, or does the page go to OCR?

PRD 2 PR 3, with the council's ML 3 and ML 4 amendments. Pure: it looks at
the page's text (its first-reader spans joined by one space) and a few
numbers about the file (``PageSignals``, from
``kaagaz/reading/pdf_signals.py``). The reader keeps the text layer when
the answer is ``text_layer``, and otherwise draws the page for the OCR
engine (when one is set).

The page goes to OCR when any of these hold, checked in this order (the
first one found is the reason code, ML 12):

1. ``hidden_text``: at least ``HIDDEN_TEXT_MAX_SHARE`` (0.05) of its
   characters are drawn invisible (a searchable scan's hidden OCR layer, or
   text hidden on purpose). First, because this reason also decides that the
   hidden text is never kept as the fallback (``text_layer_fallback_allowed``).
2. ``legacy_font``: at least ``LEGACY_FONT_MAX_SHARE`` (0.2) of its
   characters use an old Hindi or Marathi font (Kruti Dev and others), which
   stores Devanagari on Latin codes: its text is plain ASCII that passes
   every character check.
3. ``map_errors``: at least ``MAP_ERROR_MAX_SHARE`` (0.05) of its characters
   have no Unicode mapping in their font.
4. ``few_chars``: fewer than ``min_chars`` (40) letters and digits
   (``str.isalnum``).
5. ``bad_chars``: the share of good characters is under ``min_good_ratio``
   (0.9).
6. ``scan_image``: an image covers more than ``scan_image_coverage`` (0.5)
   of the page and the text has fewer than ``SCAN_FOOTER_MAX_CHARS`` (200)
   characters: a scan with a small typed footer.
7. ``malformed_devanagari``: more than ``MALFORMED_DEVANAGARI_MAX_SHARE``
   (0.02) of its Devanagari letters and signs are malformed (a text layer in
   visual order, or a broken font map).

Good characters are ASCII letters, digits and punctuation, the Devanagari
block (U+0900 to U+097F), the rupee sign, en and em dashes, curly quotes,
the bullet and the ellipsis. The share is counted over
the characters that are not whitespace (ML 3), so spaces never dilute the
bad ones; a no-break space is whitespace, not a Latin-1 symbol. Everything
else counts against the share, including the PRD's bad characters: U+FFFD,
private use (U+E000 to U+F8FF), control characters other than tab and
newline, Latin-1 symbols (U+00A0 to U+00FF), and every character of each
``(cid:N)`` pdfminer writes for a glyph with no Unicode value (those are
ASCII, so they are taken out of the good count, and out of the letters).

PROVISIONAL: the order of the reasons, and the malformed share being over
Devanagari letters and signs (not over every character), so a mostly
English page with broken Hindi names still goes to OCR.
"""

import re
import string
import unicodedata
from dataclasses import dataclass

from kaagaz.chunking.script import malformed_devanagari
from kaagaz.reading.route import RouteDecision

# PRD R2's fixed "fewer than 200 characters"; not a setting (plan §13, 18).
SCAN_FOOTER_MAX_CHARS = 200
# The council's thresholds (ML 3, ML 4). Fixed numbers, not settings.
HIDDEN_TEXT_MAX_SHARE = 0.05  # at or over: OCR
LEGACY_FONT_MAX_SHARE = 0.2  # at or over: OCR
MAP_ERROR_MAX_SHARE = 0.05  # at or over: OCR
MALFORMED_DEVANAGARI_MAX_SHARE = 0.02  # over: OCR

TEXT_LAYER_OK = RouteDecision("text_layer", "ok")

_GOOD = frozenset(
    string.ascii_letters
    + string.digits
    + string.punctuation
    + "\u20b9\u2013\u2014\u2018\u2019\u201c\u201d\u2022\u2026"
)
_CID = re.compile(r"\(cid:\d+\)")
_CID_PUNCTUATION = 3  # "(", ":" and ")" in each (cid:N); the rest are letters and digits


@dataclass(frozen=True, slots=True)
class TextLayerRules:
    min_chars: int  # TEXT_LAYER_MIN_CHARS
    min_good_ratio: float  # TEXT_LAYER_MIN_GOOD_RATIO
    scan_image_coverage: float  # SCAN_IMAGE_COVERAGE


@dataclass(frozen=True, slots=True)
class PageSignals:
    """What the file says about a page, beyond its text (ML 3, ML 4). Each is 0 to 1."""

    coverage: float  # the share of the page under its images (their union)
    hidden_share: float  # characters drawn so they can't be seen (pdf_signals.is_hidden_char)
    legacy_font_share: float  # characters in an old Devanagari font
    map_error_share: float  # characters with no Unicode mapping
    # Where the hidden characters are, as page fractions (x0, y0, x1, y1), top-left
    # origin: the readers drop the words under them, whatever the share.
    hidden_boxes: tuple[tuple[float, float, float, float], ...] = ()


def text_layer_ok(text: str, signals: PageSignals, rules: TextLayerRules) -> RouteDecision:
    """``text_layer`` / ``ok`` when the page's text layer is kept, else ``ocr``
    with the first reason found (see the module docstring for the order).

    O(c) time for c characters of text, O(1) space beyond the text.
    """
    if signals.hidden_share >= HIDDEN_TEXT_MAX_SHARE:
        return RouteDecision("ocr", "hidden_text")
    if signals.legacy_font_share >= LEGACY_FONT_MAX_SHARE:
        return RouteDecision("ocr", "legacy_font")
    if signals.map_error_share >= MAP_ERROR_MAX_SHARE:
        return RouteDecision("ocr", "map_errors")
    counted, good, letters = _counts(text)
    if letters < rules.min_chars:
        return RouteDecision("ocr", "few_chars")
    if _ratio(good, counted) < rules.min_good_ratio:
        return RouteDecision("ocr", "bad_chars")
    if signals.coverage > rules.scan_image_coverage and len(text.strip()) < SCAN_FOOTER_MAX_CHARS:
        return RouteDecision("ocr", "scan_image")
    if malformed_devanagari_share(text) > MALFORMED_DEVANAGARI_MAX_SHARE:
        return RouteDecision("ocr", "malformed_devanagari")
    return TEXT_LAYER_OK


def text_layer_fallback_allowed(decision: RouteDecision) -> bool:
    """Whether a page sent to OCR may keep its text-layer spans when the engine
    is skipped (a blank picture) or finds nothing. Never for hidden text
    (ML 4), whose second-reader spans are dropped too. With no engine set,
    the reader keeps the text layer as before, whatever this says. O(1).
    """
    return decision.reason != "hidden_text"


def good_ratio(text: str) -> float:
    """The share of good characters among those that are not whitespace; 0.0
    for a text with none (nothing on the page is good). O(c) time, O(1) space.
    """
    counted, good, _letters = _counts(text)
    return _ratio(good, counted)


def count_malformed_devanagari(text: str) -> int:
    """The malformed Devanagari signs in ``text`` (ML 1(a)'s rule): a dependent
    vowel sign or a virama that does not directly follow a consonant or a
    nukta. Two vowel signs in a row count once, on the second. The rule lives
    in ``kaagaz.chunking.script``, shared with the OCR value checks. O(c) time,
    O(1) space.
    """
    return malformed_devanagari(text)


def malformed_devanagari_share(text: str) -> float:
    """``count_malformed_devanagari`` over the Devanagari letters and signs
    (categories L and M in U+0900 to U+097F; digits and dandas are left out);
    0.0 with none. O(c) time, O(1) space.
    """
    devanagari = sum(
        1 for char in text if "\u0900" <= char <= "\u097f" and unicodedata.category(char)[0] in "LM"
    )
    return count_malformed_devanagari(text) / devanagari if devanagari else 0.0


def _counts(text: str) -> tuple[int, int, int]:
    """(characters that are not whitespace, good ones, letters and digits),
    with every (cid:N) character moved out of the good count and its letters
    and digits out of the letters. Two passes: O(c) time, O(1) space.
    """
    counted = good = letters = 0
    for char in text:
        if _is_whitespace(char):
            continue
        counted += 1
        if char in _GOOD or "\u0900" <= char <= "\u097f":
            good += 1
        if char.isalnum():
            letters += 1
    for match in _CID.finditer(text):
        length = match.end() - match.start()
        good -= length
        letters -= length - _CID_PUNCTUATION
    return counted, good, letters


def _ratio(good: int, counted: int) -> float:
    return good / counted if counted else 0.0


def _is_whitespace(char: str) -> bool:
    """Whitespace, but a control character other than tab or newline is not:
    ``isspace()`` is true for some controls (U+001C to U+001F, U+0085), which
    R2 counts as bad."""
    return char in "\t\n" or (char.isspace() and unicodedata.category(char) != "Cc")
