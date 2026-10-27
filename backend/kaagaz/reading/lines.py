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

Columns (#383, PROVISIONAL, not measured on real documents yet). A page in two
columns can have a gutter narrower than ``_COLUMN_GAP`` line heights, which would
join the two columns into one line. A gutter is found from the page itself: a gap
wider than ``_GUTTER_GAP`` line heights, with at least ``COLUMN_WIDTH`` of the page's
width of text on each side of it in its line, at the same place (the gaps overlap) in
at least ``COLUMN_LINES`` lines. Narrow text either side (a totals box: "Grand
Total" and its amount) is never a gutter. Then:

* each line is also cut at a gap that holds a gutter;
* inside a run of lines that keep to the columns (no span crosses a gutter, no more
  spans than columns, no gap above a line over ``_RUN_GAP`` line heights), the spans
  are given column by column, each column top to bottom; a line that crosses a gutter
  (a title over both columns) or holds more spans (a table row) ends the run and is
  given as it is, in its place. A page with no gutter is given line by line, as before.

Time is O(n log n) for n words (the sorts); memory is O(n).
"""

from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass

from kaagaz.reading.spans import BoxLocation, Span

_COLUMN_GAP = 3.0
_MIN_OVERLAP = 0.5
_GUTTER_GAP = 1.0  # #383: a gap wider than this many line heights may be a gutter
_RUN_GAP = 2.0  # #383: a gap above a line wider than this many line heights ends a run
COLUMN_WIDTH = 0.25  # #383: the text on each side of a gutter, as a share of the page's width
COLUMN_LINES = 3  # #383: the lines that share a gutter


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
    lines = _lines(words)
    gutters = _gutters(lines, COLUMN_WIDTH * frame.width)
    spans: list[Span] = []
    for group in _in_reading_order([_split_columns(line, gutters) for line in lines], gutters):
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


def _height(line: list[WordBox]) -> float:
    return max(w.bottom for w in line) - min(w.top for w in line)


def _split_columns(line: list[WordBox], gutters: list[float]) -> list[list[WordBox]]:
    ordered = sorted(line, key=lambda w: w.x0)
    height = _height(ordered)
    groups = [[ordered[0]]]
    right = ordered[0].x1
    for word in ordered[1:]:
        gap = word.x0 - right
        gutter = gap > _GUTTER_GAP * height and any(right <= x <= word.x0 for x in gutters)
        if gap > _COLUMN_GAP * height or gutter:
            groups.append([word])
        else:
            groups[-1].append(word)
        right = max(right, word.x1)
    return groups


def _gutters(lines: list[list[WordBox]], wide: float) -> list[float]:
    """The page's column gutters (module notes), as x positions, left to right: the
    middle of the place the gaps of at least COLUMN_LINES lines share. ``wide`` is the
    text each side needs, in page units. O(n log n)."""
    gaps: list[tuple[float, float]] = []
    for line in lines:
        ordered = sorted(line, key=lambda w: w.x0)
        height, start, end = _height(ordered), ordered[0].x0, max(w.x1 for w in ordered)
        right = ordered[0].x1
        for word in ordered[1:]:
            if word.x0 - right > _GUTTER_GAP * height and right - start >= wide and end - word.x0 >= wide:
                gaps.append((right, word.x0))
            right = max(right, word.x1)
    gutters: list[float] = []
    low = high = 0.0
    count = 0  # the lines whose gaps share [low, high]; a line's own gaps never overlap
    for left, right in sorted(gaps):
        if count and left < high:
            low, high, count = left, min(high, right), count + 1
            continue
        if count >= COLUMN_LINES:
            gutters.append((low + high) / 2)
        low, high, count = left, right, 1
    if count >= COLUMN_LINES:
        gutters.append((low + high) / 2)
    return gutters


def _in_reading_order(lines: list[list[list[WordBox]]], gutters: list[float]) -> Iterator[list[WordBox]]:
    """Each line's spans (word groups) in reading order (module notes). O(n log n)."""
    run: list[list[WordBox]] = []
    bottom = 0.0  # the run's last line's bottom
    for line in lines:
        top = min(w.top for group in line for w in group)
        keeps = bool(gutters) and len(line) <= len(gutters) + 1
        keeps = keeps and not any(_crosses(group, gutters) for group in line)
        if run and not (keeps and top - bottom <= _RUN_GAP * max(map(_height, line))):
            yield from _by_column(run, gutters)
            run = []
        if keeps:
            run.extend(line)
            bottom = max(w.bottom for group in line for w in group)
        else:
            yield from line
    yield from _by_column(run, gutters)


def _by_column(groups: list[list[WordBox]], gutters: list[float]) -> list[list[WordBox]]:
    """Column by column, left to right; a stable sort, so each column stays top to bottom."""
    return sorted(groups, key=lambda group: bisect_left(gutters, group[0].x0))


def _crosses(group: list[WordBox], gutters: list[float]) -> bool:
    start, end = group[0].x0, max(w.x1 for w in group)
    return any(start < x < end for x in gutters)


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
