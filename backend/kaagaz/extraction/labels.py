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
* ``local_reader`` (PRD 5 Q7, G6): the reading model handed the page to RapidOCR,
  while it was down or after it declined the page (the page's issues say
  read_locally or reader_refused, never its ``read_by``: a page read with no key is
  not one). Needs review, and the page is read again later.

PRD 5's checker (V1 to V4): on a page the reading model read, the pipeline crops
each value's box and RapidOCR reads it (``checked``, the boxes it read). V2's
agreement is ``_compare`` below (NFKC, collapsed spaces, Devanagari digits to 0 to
9, Latin case ignored). V3: a Devanagari value of such a page is verified only by
exact agreement with that reading; when RapidOCR read nothing, it stays
``ocr_script``. ``label_reading_marks`` then applies what the reader said about the
value's line, after both labellers: a page read locally is ``local_reader``; a value
with an unsure word or ``[?]`` is ``unsure_text`` (V4: even when the checker
agrees); a line no found box confirmed is ``box_unanchored`` (G5), in that order. A
card value (``model_chosen``) keeps its reason. The checker's reading stays as the
second reading, so the review card shows both.

Ensemble (F27, Tushar 15 Oct 2026, ``settle_votes``): when more than one model read a
scan, each value read by OCR that needs review is decided by the readers' majority (2 of
3), and what each read is saved with it (``votes``) so the user sees why. The majority's
text is then labelled by the rules above: Verified still needs the checker to agree. With
no majority the value stays Needs review.

The second reading of a line box is the second reader's line on the same page
that overlaps it most, if their intersection over union is at least 0.5 (or,
for scans, the checker's reading of the box (PRD 5 V1), passed in by the pipeline).
When that line holds the same label, only the text after it is compared.

Two labellers (ML amendment 2):

* ``label_values``, for the values the rules found (cells and ``labelled.py``):
  the rules above. For a value read by OCR, ML amendment 1 adds: ``ocr_script``
  first; a box that holds more than one value is ``single_reader`` (its reading
  is not this value's own), unless PRD 5's checker read it and agrees with the
  value (section 12: two lines on one found box); and the reading must equal the value after the D2
  normalising too (``quotes.normalise_quote``: NFKC, Devanagari digits), never
  just contain it. An OCR value's only second reading is the checker's reading
  of its box (PRD 5 V1), never a second-reader line (council fix 1: a hidden text
  layer under a scan could agree with anything). The pipeline asks the checker only
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
     shared box, then equality with the checker's reading only);
  5. a box that holds more than one located value (the table's box given to
     each cell, a line or paragraph holding several values) is ``single_reader``;
  6. the checker's reading must equal the value (after its label), never contain it;
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
import string
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from kaagaz.chunking.script import has_devanagari_letters, ocr_script_suspect
from kaagaz.extraction.labelled import value_after_label
from kaagaz.extraction.quotes import find_quote, normalise_quote, reader_text
from kaagaz.extraction.values import CARD_RECORD, NEEDS_REVIEW, VERIFIED, ExtractedValue
from kaagaz.reading.ocr import is_word_mark
from kaagaz.reading.page_quality import ILLEGIBLE_MARK
from kaagaz.reading.spans import BoxLocation, CellLocation, Location, Span

LOW_CONFIDENCE = 0.90
MIN_OVERLAP = 0.5
# PRD 5 reasons (the module notes): unsure words (V4), a box no found box confirmed (G5),
# and a page read by the backup reader during an outage (Q7).
UNSURE_TEXT = "unsure_text"
BOX_UNANCHORED = "box_unanchored"
LOCAL_READER = "local_reader"
# Ensemble (F27, Tushar 15 Oct 2026): another reader's lines at a value's spot are those with
# at least this share of their own box inside the value's box, so a line it split in two
# still counts, and a much longer line it joined does not.
VOTE_INSIDE = 0.5
# Another reader's text per page for the vote: (the page's main model, [(model, spans or
# None when that model failed on the page)]).
PageVotes = tuple[str, Sequence[tuple[str, Sequence[Span] | None]]]

# The rupee sign (U+20B9), and Rs / INR standing as words.
_CURRENCY = re.compile(r"₹|(?<![a-z])(?:rs\.?|inr)(?![a-z])")
_SPACES = re.compile(r"\s+")
# V4: punctuation at a word's ends, Latin and Devanagari (danda, double danda), so an unsure
# "1,180.00." still names the value "1,180.00".
_WORD_EDGES = string.punctuation + "\u0964\u0965"

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
    checked: Collection[Location] = (),
) -> list[ExtractedValue]:
    """Label each value the rules found. ``second`` is the second reader's spans (None:
    there is no second reader for this type); ``readings`` are extra second readings by
    box, which win over ``second`` for that box; ``also_located`` are the document's
    other located values, counted for the shared boxes only; ``checked`` are the boxes
    PRD 5's checker read (V3)."""
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
            labelled.append(_label_ocr(value, box, shared, readings, checked))
        else:
            labelled.append(_label_box(value, _reading(box, lines, readings)))
    return labelled


def label_quoted(
    values: Iterable[ExtractedValue],
    second: Sequence[Span] | None,
    readings: Mapping[Location, str] | None = None,
    *,
    also_located: Iterable[ExtractedValue] = (),
    checked: Collection[Location] = (),
) -> list[ExtractedValue]:
    """Label each value the model located by quoting (the module docstring, rules 1 to 7).
    ``also_located`` and ``checked``: as in ``label_values``."""
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
            labelled.append(_label_ocr(value, box, shared, readings, checked))
        elif box in shared:
            labelled.append(_needs_review(value, "single_reader", None))
        elif (readings or {}).get(box) is not None:
            labelled.append(_compare(value, (readings or {}).get(box)))
        else:
            labelled.append(_contained(value, _best_line(box, lines.get(box.page, []))))
    return labelled


def _label_ocr(
    value: ExtractedValue,
    box: BoxLocation,
    shared: set[Location],
    readings: Mapping[Location, str] | None,
    checked: Collection[Location],
) -> ExtractedValue:
    """ML amendment 1 for a value read by OCR: its only second reading is the checker's
    reading of its box (council fix 1, PRD 5 V1). V3: a value whose only
    untrusted letters are Devanagari is compared with the checker's reading of its own
    box (exact agreement, V2); with no such reading it stays ``ocr_script``.

    A box that holds more than one value (``shared``; PRD 5 section 12: two lines on one
    found box) is ``single_reader``, or ``ocr_script``, unless the checker read it and
    agrees with this value (``_checker_agrees``); its reading holds the other value too,
    so a difference is no disagreement."""
    reading = (readings or {}).get(box)
    checker = reading if box in checked else None
    if _script_unsound(value.value_text):
        devanagari_only = not ocr_script_suspect(value.value_text)
        if checker is not None and devanagari_only and (box not in shared or _checker_agrees(value, checker)):
            return _compare(value, checker)
        return _needs_review(value, "ocr_script", _after_label(value, reading))
    if box in shared:
        if checker is not None and _checker_agrees(value, checker):
            return _compare(value, checker)
        return _needs_review(value, "single_reader", None)
    return _compare(value, reading)


def _checker_agrees(value: ExtractedValue, reading: str) -> bool:
    """The checker's reading of the box equals the value (``_compare``'s rule)."""
    return _compare(value, reading).label == VERIFIED


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
    """Equality with the D2 normalising too: for OCR values and the checker's readings."""
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


@dataclass(frozen=True, slots=True)
class LineMarks:
    """What the main reader said about one line (PRD 5): the words it was not sure of
    (G3, V4), and whether no box RapidOCR's text finder found confirmed its spot (G5)."""

    unsure: tuple[str, ...] = ()
    unpinned: bool = False


def label_reading_marks(
    values: Iterable[ExtractedValue],
    lines: Mapping[BoxLocation, LineMarks],
    local_pages: Collection[int],
    *,
    voted: bool = False,
) -> list[ExtractedValue]:
    """PRD 5's reasons for values read by OCR (the module notes), after ``label_values``
    and ``label_quoted``: ``local_reader`` on a page in ``local_pages`` (Q7: the pages
    the reading model handed to its local reader, by their issues), then
    ``unsure_text`` when the value has ``[?]`` or a word of its line's unsure words (V4),
    then ``box_unanchored`` when its line is unpinned (G5). ``lines`` are the marks by the
    line's box, the value's own location. A card value and every value with none of these
    keep their label. ``voted``: the readers' majority settled the main reader's unsure
    words (``settle_votes``), so they are no reason; ``[?]`` still is. O(v x u) for v
    values and u unsure words of a line."""
    labelled = []
    for value in values:
        box = value.location
        if not isinstance(box, BoxLocation) or not _is_ocr(value) or value.label_reason == "model_chosen":
            labelled.append(value)
            continue
        marks = lines.get(box, LineMarks())
        reason: str | None = None
        if box.page in local_pages:
            reason = LOCAL_READER
        elif _has_unsure_word(value.value_text, () if voted else marks.unsure):
            reason = UNSURE_TEXT
        elif marks.unpinned:
            reason = BOX_UNANCHORED
        labelled.append(value if reason is None else _needs_review(value, reason, value.second_reading))
    return labelled


def _has_unsure_word(text: str, unsure: Sequence[str]) -> bool:
    """V4: ``[?]`` in ``text``, or a word of it among the unsure words, compared without
    case, currency signs or the punctuation at a word's ends."""
    if ILLEGIBLE_MARK in text:
        return True
    words = _words(text)
    return any(_words(word) & words for word in unsure)


def _words(text: str) -> set[str]:
    return {word for word in (part.strip(_WORD_EDGES) for part in comparable(text).split()) if word}


def reading_at(value: ExtractedValue, box: BoxLocation, spans: Sequence[Span]) -> str | None:
    """What another reader read at ``value``'s spot ``box`` (Ensemble, C6): its lines with
    at least VOTE_INSIDE of their box inside ``box``, joined in reading order. The value's
    own text when it is a whole-token run inside them (``find_quote``, never the loose
    pass), else the part after the value's label (as the checker's reading is compared).
    None when no line of it is there, or it marked a word there unsure or unreadable: a
    reader that is not sure has no vote. O(s) plus the search."""
    lines = []
    for span in spans:
        other = span.location
        if not isinstance(other, BoxLocation) or other.page != box.page or _inside(other, box) < VOTE_INSIDE:
            continue
        if is_word_mark(span):
            return None
        lines.append(span)
    if not lines:
        return None
    found = find_quote(reader_text(lines), comparable(value.value_text))
    if found is not None and not found.loose:
        return value.value_text
    return _after_label(value, " ".join(span.text for span in lines)) or None


def vote(value: ExtractedValue, box: BoxLocation, page: PageVotes) -> dict[str, Any]:
    """Ensemble's majority vote on one value (Tushar, 15 Oct 2026). The readers are the
    page's main model, with the value's own text, and each other model that read the page,
    with its ``reading_at`` the value's spot; one that failed on the page is not counted.
    Texts are compared after ``comparable`` and the D2 normalising, the checker's rule.
    The vote's value is the text more than half the readers read (2 of 3, or 2 of 2), the
    main reader's spelling when it is one of them; None with no majority (a reader that
    read nothing there counts against every text). Plain JSON for
    ``extracted_values.votes``: {"value", "agreed" (how many read the most read text),
    "readers", "reads": [{"model", "text"}], the main reader first}. O(readers)."""
    lead, others = page
    reads: list[tuple[str, str | None]] = [(lead, value.value_text)]
    reads += [(model, reading_at(value, box, spans)) for model, spans in others if spans is not None]
    counts = Counter(_vote_key(text) for _model, text in reads if text is not None)
    key, agreed = counts.most_common(1)[0] if counts else (None, 0)
    winner = None
    if agreed * 2 > len(reads):
        winner = next(text for _model, text in reads if text is not None and _vote_key(text) == key)
    return {
        "value": winner,
        "agreed": agreed,
        "readers": len(reads),
        "reads": [{"model": model, "text": text} for model, text in reads],
    }


def settle_votes(
    values: Iterable[ExtractedValue],
    pages: Mapping[int, PageVotes],
    readings: Mapping[Location, str] | None,
    *,
    checked: Collection[Location],
    lines: Mapping[BoxLocation, LineMarks],
    local_pages: Collection[int],
) -> list[ExtractedValue]:
    """Ensemble's rule (Tushar, 15 Oct 2026), after ``label_reading_marks``: each value read
    by OCR that needs review, on a page the other readers read (``pages``), is decided by
    the readers' majority (``vote``), and the vote is kept with it (``votes``). A card
    value or a quote whose spot is unsure (``model_chosen``) and a value of a page the
    local reader read are not voted on. With a majority the value is the majority's text
    and is labelled again by today's rules (``_label_ocr``: Verified only when the checker
    read its box and agrees with that text; ``readings`` and ``checked`` are the
    checker's), then by the reading marks, but the main reader's unsure words: the vote
    settled them. With none it keeps its label: Needs review, never a guess. The raw text
    stays the main reader's. O(v x s) for v values and s spans of a page."""
    values = list(values)
    shared = _shared_boxes(values)
    settled = []
    for value in values:
        box = value.location
        page = pages.get(box.page) if isinstance(box, BoxLocation) else None
        if (
            page is None
            or not isinstance(box, BoxLocation)
            or value.label != NEEDS_REVIEW
            or not _is_ocr(value)
            or value.label_reason in ("model_chosen", LOCAL_READER)
        ):
            settled.append(value)
            continue
        votes = vote(value, box, page)
        if votes["value"] is None:
            settled.append(replace(value, votes=votes))
            continue
        relabelled = _label_ocr(
            replace(value, value_text=votes["value"], votes=votes), box, shared, readings, checked
        )
        settled.extend(label_reading_marks([relabelled], lines, local_pages, voted=True))
    return settled


def _inside(line: BoxLocation, box: BoxLocation) -> float:
    """The share of ``line``'s box that is inside ``box`` (0 when apart or with no area)."""
    width = min(line.x1, box.x1) - max(line.x0, box.x0)
    height = min(line.y1, box.y1) - max(line.y0, box.y0)
    area = (line.x1 - line.x0) * (line.y1 - line.y0)
    if width <= 0 or height <= 0 or area <= 0:
        return 0.0
    return width * height / area


def _vote_key(text: str) -> str:
    return normalise_quote(comparable(text))
