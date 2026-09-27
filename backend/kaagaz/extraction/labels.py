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

The second reading of a line box is the second reader's line on the same page
that overlaps it most, if their intersection over union is at least 0.5 (or,
for scans, a vision model's reading of the box, passed in by the pipeline).
When that line holds the same label, only the text after it is compared.

PROVISIONAL: what "verified" promises is Vrushit's decision (CLAUDE.md, due
9 Oct). Until then it means exactly the rules above.

Time O(v * s) in the worst case for v values and s second-reader lines on the
same page; memory O(s).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from kaagaz.extraction.labelled import value_after_label
from kaagaz.extraction.values import NEEDS_REVIEW, VERIFIED, ExtractedValue
from kaagaz.reading.spans import BoxLocation, Location, Span

LOW_CONFIDENCE = 0.90
MIN_OVERLAP = 0.5

# The rupee sign (U+20B9), and Rs / INR standing as words.
_CURRENCY = re.compile(r"\u20b9|(?<![a-z])(?:rs\.?|inr)(?![a-z])")
_SPACES = re.compile(r"\s+")


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
) -> list[ExtractedValue]:
    """Label each value. ``second`` is the second reader's spans (None: there is no
    second reader for this type); ``readings`` are extra second readings by box,
    which win over ``second`` for that box."""
    by_page: dict[int, list[tuple[Span, BoxLocation]]] = {}
    for span in second or ():
        if isinstance(span.location, BoxLocation):
            by_page.setdefault(span.location.page, []).append((span, span.location))
    labelled = []
    for value in values:
        if not isinstance(value.location, BoxLocation):
            labelled.append(replace(value, label=VERIFIED, label_reason="direct_cell"))
            continue
        reading = (readings or {}).get(value.location)
        if reading is None:
            reading = _best_line(value.location, by_page.get(value.location.page, []))
        labelled.append(_label_box(value, reading))
    return labelled


def _best_line(box: BoxLocation, lines: list[tuple[Span, BoxLocation]]) -> str | None:
    best, best_score = None, 0.0
    for span, other in lines:
        score = overlap(box, other)
        if score >= MIN_OVERLAP and score > best_score:
            best, best_score = span.text, score
    return best


def _label_box(value: ExtractedValue, reading: str | None) -> ExtractedValue:
    if reading is None:
        low = value.confidence is not None and value.confidence < LOW_CONFIDENCE
        return replace(value, label=NEEDS_REVIEW, label_reason="low_confidence" if low else "single_reader")
    after_label = value_after_label(value.field, reading)
    second = after_label if after_label is not None else reading.strip()
    agree = comparable(second) == comparable(value.value_text)
    return replace(
        value,
        second_reading=second,
        label=VERIFIED if agree else NEEDS_REVIEW,
        label_reason="readers_agree" if agree else "readers_disagree",
    )
