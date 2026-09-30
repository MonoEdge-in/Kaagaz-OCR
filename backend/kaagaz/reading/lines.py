"""Turn word boxes into line spans, the same way for every page reader.

The PDF text reader (pdfplumber), the second PDF reader (pypdfium2) and the
scan reader (OCR) each find words with boxes in their own way. All three hand
them here, so a "line" means the same thing for each of them: the two PDF
readers are compared line by line later (IoU of the boxes), and that
comparison should only ever disagree about the text read, never about where a
line was cut.

The rule (PROVISIONAL, #3):

* A word joins the line being built when their vertical extents overlap by at
  least half the smaller height. Words are taken in (top, left) order.
* Within a line, a horizontal gap wider than ``_COLUMN_GAP`` times the line's
  height starts a new span, so two columns on one baseline ("Invoice No: 7"
  far left, "Date: 1/2/26" far right) stay two spans instead of one run-on.
* A span's box is the union of its words' boxes, as fractions of the visible
  page, clamped to 0..1. A span with nothing left inside the page is dropped:
  it cannot be pointed at on the page picture (CLAUDE.md never-do 5).
* A span's confidence is the lowest of its words' (None if none has one).
* Every span carries the ``origin`` its reader gives: how it was read.

Time is O(n log n) for n words (one sort); memory is O(n).
"""

from dataclasses import dataclass

from kaagaz.reading.spans import BoxLocation, Span

_COLUMN_GAP = 3.0
_MIN_OVERLAP = 0.5


@dataclass(frozen=True, slots=True)
class WordBox:
    """One word in page units (points or pixels), origin at the page's top left."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class PageFrame:
    """The visible page in the same units as the words: its top left corner and size."""

    left: float
    top: float
    width: float
    height: float


def line_spans(words: list[WordBox], page_no: int, frame: PageFrame, *, origin: str) -> list[Span]:
    if frame.width <= 0 or frame.height <= 0:
        return []
    spans: list[Span] = []
    for line in _lines(words):
        for group in _split_columns(line):
            span = _span(group, page_no, frame, origin)
            if span is not None:
                spans.append(span)
    return spans


def _lines(words: list[WordBox]) -> list[list[WordBox]]:
    lines: list[list[WordBox]] = []
    line_top = line_bottom = 0.0
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if not word.text.strip():
            continue
        if lines and _overlaps(line_top, line_bottom, word.top, word.bottom):
            lines[-1].append(word)
            line_top, line_bottom = min(line_top, word.top), max(line_bottom, word.bottom)
        else:
            lines.append([word])
            line_top, line_bottom = word.top, word.bottom
    return lines


def _overlaps(top_a: float, bottom_a: float, top_b: float, bottom_b: float) -> bool:
    smaller = min(bottom_a - top_a, bottom_b - top_b)
    shared = min(bottom_a, bottom_b) - max(top_a, top_b)
    if smaller <= 0:
        return shared >= 0  # a zero-height word joins a line it touches
    return shared >= _MIN_OVERLAP * smaller


def _split_columns(line: list[WordBox]) -> list[list[WordBox]]:
    ordered = sorted(line, key=lambda w: w.x0)
    height = max(w.bottom for w in ordered) - min(w.top for w in ordered)
    groups = [[ordered[0]]]
    right = ordered[0].x1
    for word in ordered[1:]:
        if word.x0 - right > _COLUMN_GAP * height:
            groups.append([word])
        else:
            groups[-1].append(word)
        right = max(right, word.x1)
    return groups


def _span(group: list[WordBox], page_no: int, frame: PageFrame, origin: str) -> Span | None:
    raw = " ".join(word.text for word in group)
    text = " ".join(raw.split())
    box = BoxLocation(
        page=page_no,
        x0=_fraction(min(w.x0 for w in group), frame.left, frame.width),
        y0=_fraction(min(w.top for w in group), frame.top, frame.height),
        x1=_fraction(max(w.x1 for w in group), frame.left, frame.width),
        y1=_fraction(max(w.bottom for w in group), frame.top, frame.height),
    )
    if not text or not box.is_valid():
        return None
    confidences = [w.confidence for w in group if w.confidence is not None]
    confidence = min(confidences) if confidences else None
    return Span(text=text, raw=raw, location=box, confidence=confidence, origin=origin)


def _fraction(value: float, start: float, size: float) -> float:
    return min(1.0, max(0.0, (value - start) / size))
