"""Label every value verified or needs review, with the reason.

The reasons (the ``label_reason`` column):

* ``direct_cell``: a spreadsheet cell, read directly. Verified.
* ``readers_agree``: a second, independent reading of the same spot says the
  same after normalising both (case, spaces, currency signs). Verified.
* ``readers_disagree``: the second reading differs. Needs review, and the
  second reading is kept so the reviewer can pick it.
* ``low_confidence``: no second reading, and the scan engine was less than
  90% sure. Needs review.
* ``single_reader``: no second reading at all. Needs review.
* ``ocr_script`` (PRD 2 ML amendment 1): read by OCR, and a letter is neither
  ASCII nor Devanagari, the Devanagari is malformed, or it has Devanagari
  letters at all (PROVISIONAL for PRD 2: OCR of Hindi and Marathi is not
  trusted yet). Digits and the danda are not letters. Needs review.
* ``model_chosen`` (Lead's resolution 1, security amendment A1(b)): a value
  of the document card, whose field the model chose. Needs review, always.
* ``unsure_text`` (PRD 5 V4): the value's text includes a word the reading
  model listed as unsure, or [?]. Needs review, even if the checker agrees.
* ``box_unanchored`` (PRD 5 G5): the value's line could not be pinned to a box
  RapidOCR's text finder found, so its box is the model's own. Needs review.
* ``local_reader`` (PRD 5 Q7): the page was read by RapidOCR while the main
  reader was down. Needs review, and the page is read again later.

The second reading of a line box is the second reader's line on the same page
that overlaps it most, if their intersection over union is at least 0.5 (or,
for scans, a vision model's reading of the box, passed in by the pipeline).
When that line holds the same label, only the text after it is compared.

Two labellers (ML amendment 2):

* ``label_values``, for the values the rules found (cells and ``labelled.py``):
  the rules above. For a value read by OCR, ML amendment 1 adds: ``ocr_script``
  first; a box that holds more than one value is ``single_reader`` (its reading
  is not this value's own); and the reading must equal the value after the D2
  normalising too (``quotes.normalise_quote``: NFKC, Devanagari digits), never
  just contain it. An OCR value's only second reading is the vision reading of
  its box, never a second-reader line (council fix 1: a hidden text layer under
  a scan could agree with anything). The pipeline asks the vision model only
  about a value's own box (ML 1(c)), so an OCR value with no reading is
  ``single_reader`` (``low_confidence`` when the engine was unsure).
* ``label_quoted``, for the values the model located by quoting (the card,
  and PR 6's invoice values), with security amendment A2's stricter rules:

  1. a card value is ``model_chosen``;
  2. a value whose quote covers more than one span (two cells, two lines, two
     pages) or only part of a cell, was found only by the loose pass, or fits a
     later spot too, is ``model_chosen``: no reading of its spot can confirm it
     (council fixes 5 and 6, from ``ExtractedValue.quote``);
  3. a cell is ``direct_cell``;
  4. an OCR value is labelled as ``label_values`` labels one (``ocr_script``, a
     shared box, then equality with its vision reading only);
  5. a box that holds more than one located value (the table's box given to
     each cell, a line or paragraph holding several values) is ``single_reader``;
  6. a vision reading must equal the value (after its label), never contain it;
  7. otherwise the value agrees when its comparable text is a whole-token run
     inside the second reader's line (``quotes.find_quote``), and
     ``second_reading`` is the matched part of that line, or the whole line when
     it is not there.

"More than one value" counts every located value in the box (council fix 2):
"Subtotal 500 Total 500" holds two. Only the same (record, field, text) twice
is one value located twice. ``also_located`` adds the values the other
labeller gets (the rule values next to the quoted ones), so both are counted.

PROVISIONAL: what "verified" promises is Vrushit's decision (CLAUDE.md, due
9 Oct). Until then it means exactly the rules above.

Time O(v * s) in the worst case for v values and s second-reader lines on the
same page, plus O(v) for the boxes shared, and O(line length) for each
containment check; memory O(s + v).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from kaagaz.chunking.script import has_devanagari_letters, ocr_script_suspect
from kaagaz.extraction.labelled import value_after_label
from kaagaz.extraction.quotes import find_quote, normalise_quote, reader_text
from kaagaz.extraction.values import CARD_RECORD, NEEDS_REVIEW, VERIFIED, ExtractedValue
from kaagaz.reading.spans import BoxLocation, CellLocation, Location, Span

LOW_CONFIDENCE = 0.90
MIN_OVERLAP = 0.5
# PRD 5 reasons (the module notes): unsure words (V4), a box no found box confirmed (G5),
# and a page read by the backup reader during an outage (Q7).
UNSURE_TEXT = "unsure_text"
BOX_UNANCHORED = "box_unanchored"
LOCAL_READER = "local_reader"

# The rupee sign (U+20B9), and Rs / INR standing as words.
_CURRENCY = re.compile(r"₹|(?<![a-z])(?:rs\.?|inr)(?![a-z])")
_SPACES = re.compile(r"\s+")

_Lines = dict[int, list[tuple[Span, BoxLocation]]]


def overlap(a: BoxLocation, b: BoxLocation) -> float:
    """Intersection over union of two boxes on one page (0 when apart)."""
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.y1, b.y1) - max(a.y0, b.y0)
    if width <= 0 or height <= 0:
        return 0.0
    shared = width * height
    union = (a.x1 - a.x0) * (a.y1 - a.y0) + (b.x1 - b.x0) * (b.y1 - b.y0) - shared
    return shared / union


def comparable(text: str) -> str:
    """Casefolded, currency signs removed, spaces collapsed."""
    return _SPACES.sub(" ", _CURRENCY.sub("", text.casefold())).strip()


def label_values(
    values: Iterable[ExtractedValue],
    second: Sequence[Span] | None,
    readings: Mapping[Location, str] | None = None,
    *,
    also_located: Iterable[ExtractedValue] = (),
) -> list[ExtractedValue]:
    """Label each value the rules found. ``second`` is the second reader's spans (None:
    there is no second reader for this type); ``readings`` are extra second readings by
    box, which win over ``second`` for that box; ``also_located`` are the document's
    other located values, counted for the shared boxes only."""
    values = list(values)
    lines = _lines_by_page(second)
    shared = _shared_boxes([*values, *also_located])
    labelled = []
    for value in values:
        box = value.location
        if value.record_key == CARD_RECORD:  # never verified, on any path (council fix 4)
            labelled.append(_needs_review(value, "model_chosen", None))
        elif not isinstance(box, BoxLocation):
            labelled.append(replace(value, label=VERIFIED, label_reason="direct_cell"))
        elif _is_ocr(value):
            labelled.append(_label_ocr(value, box, shared, readings))
        else:
            labelled.append(_label_box(value, _reading(box, lines, readings)))
    return labelled


def label_quoted(
    values: Iterable[ExtractedValue],
    second: Sequence[Span] | None,
    readings: Mapping[Location, str] | None = None,
    *,
    also_located: Iterable[ExtractedValue] = (),
) -> list[ExtractedValue]:
    """Label each value the model located by quoting (the module docstring, rules 1 to 7).
    ``also_located``: as in ``label_values``."""
    values = list(values)
    lines = _lines_by_page(second)
    shared = _shared_boxes([*values, *also_located])
    labelled = []
    for value in values:
        box = value.location
        if value.record_key == CARD_RECORD or spot_unsure(value):
            labelled.append(_needs_review(value, "model_chosen", None))
        elif not isinstance(box, BoxLocation):
            labelled.append(replace(value, label=VERIFIED, label_reason="direct_cell"))
        elif _is_ocr(value):
            labelled.append(_label_ocr(value, box, shared, readings))
        elif box in shared:
            labelled.append(_needs_review(value, "single_reader", None))
        elif (readings or {}).get(box) is not None:
            labelled.append(_compare(value, (readings or {}).get(box)))
        else:
            labelled.append(_contained(value, _best_line(box, lines.get(box.page, []))))
    return labelled


def _label_ocr(
    value: ExtractedValue, box: BoxLocation, shared: set[Location], readings: Mapping[Location, str] | None
) -> ExtractedValue:
    """ML amendment 1 for a value read by OCR: its only second reading is the vision
    reading of its box (council fix 1)."""
    vision = (readings or {}).get(box)
    if _script_unsound(value.value_text):
        return _needs_review(value, "ocr_script", _after_label(value, vision))
    if box in shared:
        return _needs_review(value, "single_reader", None)
    return _compare(value, vision)


def _lines_by_page(second: Sequence[Span] | None) -> _Lines:
    by_page: _Lines = {}
    for span in second or ():
        if isinstance(span.location, BoxLocation):
            by_page.setdefault(span.location.page, []).append((span, span.location))
    return by_page


def _shared_boxes(values: Sequence[ExtractedValue]) -> set[Location]:
    """The boxes that hold more than one located value; the same (record, field, text)
    twice counts once. O(v)."""
    located: dict[Location, set[tuple[str, str, str]]] = {}
    for value in values:
        if isinstance(value.location, BoxLocation):
            located.setdefault(value.location, set()).add((value.record_key, value.field, value.value_text))
    return {box for box, seen in located.items() if len(seen) > 1}


def _reading(box: BoxLocation, lines: _Lines, readings: Mapping[Location, str] | None) -> str | None:
    """The reading by box when there is one, else the best second-reader line's text."""
    reading = (readings or {}).get(box)
    if reading is not None:
        return reading
    line = _best_line(box, lines.get(box.page, []))
    return None if line is None else line.text


def _best_line(box: BoxLocation, lines: list[tuple[Span, BoxLocation]]) -> Span | None:
    best, best_score = None, 0.0
    for span, other in lines:
        score = overlap(box, other)
        if score >= MIN_OVERLAP and score > best_score:
            best, best_score = span, score
    return best


def spot_unsure(value: ExtractedValue) -> bool:
    """Rule 2 of ``label_quoted``: the quote is not one span, is only part of a cell, was
    found only by the loose pass, or fits a later spot too."""
    quote = value.quote
    if quote is None:
        return False
    if quote.loose or quote.ambiguous or quote.spans > 1 or quote.pages > 1:
        return True
    return isinstance(value.location, CellLocation) and not quote.whole


def _is_ocr(value: ExtractedValue) -> bool:
    return value.origin == "ocr"


def _script_unsound(text: str) -> bool:
    """ML amendment 1 (a) and (b): an OCR reading that is not trusted."""
    return ocr_script_suspect(text) or has_devanagari_letters(text)


def _after_label(value: ExtractedValue, reading: str | None) -> str | None:
    """The part of ``reading`` compared with the value: the text after the value's own
    label when the reading holds it, else the whole reading."""
    if reading is None:
        return None
    after = value_after_label(value.field, reading)
    return after if after is not None else reading.strip()


def _same(a: str, b: str) -> bool:
    """Equal after ``comparable`` and the D2 normalising (ML amendment 1(d))."""
    return normalise_quote(comparable(a)) == normalise_quote(comparable(b))


def _needs_review(value: ExtractedValue, reason: str, second: str | None) -> ExtractedValue:
    return replace(value, second_reading=second, label=NEEDS_REVIEW, label_reason=reason)


def _no_reading(value: ExtractedValue) -> ExtractedValue:
    low = value.confidence is not None and value.confidence < LOW_CONFIDENCE
    return replace(value, label=NEEDS_REVIEW, label_reason="low_confidence" if low else "single_reader")


def _agreed(value: ExtractedValue, second: str, agree: bool) -> ExtractedValue:
    return replace(
        value,
        second_reading=second,
        label=VERIFIED if agree else NEEDS_REVIEW,
        label_reason="readers_agree" if agree else "readers_disagree",
    )


def _label_box(value: ExtractedValue, reading: str | None) -> ExtractedValue:
    """Today's rule for a value the rules found: equal after ``comparable``."""
    second = _after_label(value, reading)
    if second is None:
        return _no_reading(value)
    return _agreed(value, second, comparable(second) == comparable(value.value_text))


def _compare(value: ExtractedValue, reading: str | None) -> ExtractedValue:
    """Equality with the D2 normalising too: for OCR values and vision readings."""
    second = _after_label(value, reading)
    if second is None:
        return _no_reading(value)
    return _agreed(value, second, _same(second, value.value_text))


def _contained(value: ExtractedValue, line: Span | None) -> ExtractedValue:
    """A second-reader line agrees when the value is a whole-token run inside it."""
    if line is None:
        return _no_reading(value)
    found = find_quote(reader_text([line]), comparable(value.value_text))
    if found is None:
        return _agreed(value, line.text.strip(), False)
    return _agreed(value, found.text, True)
