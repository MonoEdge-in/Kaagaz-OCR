"""Pieces from the lines and table cells on pages (PDF text layers and scans).

PRD 2 C1: the lines are joined in the document's reading order, across pages.
A new piece starts only:

* at a title or heading span, which then leads the new piece;
* at the start of a table, and after its end;
* at a blank-line gap within one page: the space above a line is more than
  ``GAP_FACTOR`` times the page's median line height;
* when one more line would take the piece past ``MAX_CHARS``.

A page break alone never starts a piece, so a sentence that runs from page 15
onto page 16 stays in one piece. The gap is measured only between two lines
on the same page. One line longer than the limit is never cut: it becomes its
own piece.

PROVISIONAL: a heading is never left alone in a piece. While a piece holds
only titles and headings, the next line joins it even across a gap (headings
usually have space below them), and a heading under a title joins the title.

C2: running headers and footers (``running_spans``) are left out of pieces.
They stay in the reader output and in values.

C3: each piece keeps its section, the last title and then the last heading,
carried across pages until a new one replaces them.

C5: a table from a layout reader (cells that carry ``table_id``, ``row`` and
``col``) makes its own pieces, of at most ``TABLE_MAX_ROWS`` rows or
``MAX_CHARS``. Each row is written "Header: value | Header: value", like a
sheet row (``rows.py``). The names come from the table's header rows (rows
where every cell is a header). A table with no header row, found right after
a table on the previous page with the same number of columns, takes that
table's names: it is the same table running on over the page. Otherwise the
columns are named by letter. A piece's parts are its data cells; a header
name is a label, like a column letter. A PDF text layer has no table cells,
so its tables stay lines (plan section 13, conflict 14).

C6: the list is in document order, so ``pieces.seq`` is too.

Each line and cell keeps its box, in text order, so any part of a piece can be
highlighted.

PROVISIONAL: 800 characters and 1.5 line heights are starting values, not
measured on real documents yet.

Time O(S log S) for S spans: a stable sort by page (O(S) when the reader gives
them in page order, as it does), one median per page, one sort per table by
(row, column), then one pass. Memory O(S).
"""

import re
import statistics
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import groupby

from kaagaz.chunking.piece import Piece, piece_label
from kaagaz.chunking.rows import column_letter
from kaagaz.reading.spans import BoxLocation, Span

MAX_CHARS = 800
GAP_FACTOR = 1.5
BAND = 0.08  # C2: the top and bottom 8% of a page
REPEAT_PAGES = 3  # C2: a line repeated in a band on this many pages is running
TABLE_MAX_ROWS = 20  # C5
BEFORE_MAX = 120  # C4: the "Before" part of a context header (kaagaz/chunking/context.py)

_RUNNING_KINDS = frozenset({"header", "footer"})
_DIGITS = re.compile(r"\d+")

Line = tuple[Span, BoxLocation]


def running_spans(spans: Sequence[Span]) -> set[int]:
    """The indexes of the running headers and footers in ``spans`` (C2).

    A span of kind ``header`` or ``footer`` is one, wherever it sits. So is a
    line in the top or bottom ``BAND`` of its page whose text is found in the
    same band on ``REPEAT_PAGES`` or more pages. The text is compared case
    folded, with each run of digits as "#" and spaces collapsed, so "Page 15
    of 20" and "Page 16 of 20" match. Table cells are never counted this way,
    so a table's header row at the top of each page is kept.

    Time O(S) for S spans; memory O(B) for the B lines in the bands.
    """
    running: set[int] = set()
    found: dict[tuple[bool, str], tuple[set[int], list[int]]] = {}  # (top?, text) -> (pages, indexes)
    for index, span in enumerate(spans):
        box = span.location
        if span.kind in _RUNNING_KINDS:
            running.add(index)
        elif (
            isinstance(box, BoxLocation) and span.table_id is None and (box.y1 <= BAND or box.y0 >= 1 - BAND)
        ):
            key = (box.y1 <= BAND, " ".join(_DIGITS.sub("#", span.text.casefold()).split()))
            pages, indexes = found.setdefault(key, (set(), []))
            pages.add(box.page)
            indexes.append(index)
    for pages, indexes in found.values():
        if len(pages) >= REPEAT_PAGES:
            running.update(indexes)
    return running


def pieces_from_lines(
    customer_id: str, document_id: str, spans: Iterable[Span], *, max_chars: int = MAX_CHARS
) -> list[Piece]:
    """Pieces from the box spans (lines, blocks and table cells), by the rules
    above. A sheet's cell spans are left to ``rows.py``."""
    spans = list(spans)
    running = running_spans(spans)
    lines = [
        (span, span.location)
        for index, span in enumerate(spans)
        if index not in running and isinstance(span.location, BoxLocation)
    ]
    lines.sort(key=lambda line: line[1].page)  # stable: reading order kept within a page
    builder = _Builder(customer_id, document_id, max_chars, _gap_limits(lines))
    for unit in _units(lines):
        if isinstance(unit, list):
            builder.table(unit)
        else:
            builder.line(*unit)
    builder.close()
    return builder.pieces


def _table_key(line: Line) -> tuple[int, str] | None:
    """(page, table) for a table cell; None for a text line."""
    span, box = line
    if span.table_id is None or span.row is None or span.col is None:
        return None
    return box.page, span.table_id


def _row_col(span: Span) -> tuple[int, int]:
    return span.row or 0, span.col or 0  # both are set on every cell (_table_key)


def _gap_limits(lines: list[Line]) -> dict[int, float]:
    """Per page, the gap that starts a new piece: GAP_FACTOR x the median height
    of its text lines. O(S log S)."""
    heights: dict[int, list[float]] = {}
    for line in lines:
        if _table_key(line) is None:
            heights.setdefault(line[1].page, []).append(line[1].y1 - line[1].y0)
    return {page: GAP_FACTOR * statistics.median(values) for page, values in heights.items()}


def _units(lines: list[Line]) -> Iterator[Line | list[Line]]:
    """Each text line on its own, and each table (its cells one after another on one page) as a list."""
    for key, group in groupby(lines, key=_table_key):
        if key is None:
            yield from group
        else:
            yield list(group)


@dataclass(frozen=True, slots=True)
class _Names:
    """A table's column names, kept so a table on the next page can take them."""

    page: int
    columns: int
    names: dict[int, str]  # column (from 0) -> name
    spans: tuple[Span, ...]  # the header cells the names came from


class _Builder:
    """Builds the pieces in one pass over the lines and tables, in document order."""

    def __init__(
        self, customer_id: str, document_id: str, max_chars: int, gap_limits: dict[int, float]
    ) -> None:
        self.pieces: list[Piece] = []
        self._ids = (customer_id, document_id)
        self._max_chars = max_chars
        self._gap_limits = gap_limits
        self._lines: list[Line] = []  # the text piece being built
        self._length = 0
        self._leading = True  # the piece so far holds only titles and headings
        self._title: str | None = None
        self._section: tuple[str, ...] = ()
        self._table: _Names | None = None  # the unit just before, when it was a table

    def line(self, span: Span, box: BoxLocation) -> None:
        heading = span.kind in ("title", "heading")
        if self._lines:
            previous = self._lines[-1][1]
            gap = previous.page == box.page and box.y0 - previous.y1 > self._gap_limits[box.page]
            too_long = self._length + 1 + len(span.text) > self._max_chars
            if too_long or (not self._leading and (heading or gap)):
                self.close()
        if span.kind == "title":
            self._title, self._section = span.text, (span.text,)
        elif heading:
            self._section = (span.text,) if self._title is None else (self._title, span.text)
        self._length += len(span.text) + (1 if self._lines else 0)
        self._lines.append((span, box))
        self._leading = self._leading and heading
        self._table = None

    def close(self) -> None:
        """Finish the text piece being built, if there is one."""
        if self._lines:
            spans = [span for span, _ in self._lines]
            self._add("\n".join(span.text for span in spans), self._lines, spans)
        self._lines, self._length, self._leading = [], 0, True

    def table(self, cells: list[Line]) -> None:
        """One table's pieces. O(T log T) for its T cells."""
        self.close()
        page = cells[0][1].page
        rows = [
            list(row)
            for _, row in groupby(sorted(cells, key=lambda c: _row_col(c[0])), key=lambda c: c[0].row)
        ]
        headers = [row for row in rows if all(span.header for span, _ in row)]
        data = [row for row in rows if not all(span.header for span, _ in row)]
        found = self._names(page, 1 + max(_row_col(span)[1] for span, _ in cells), headers)
        # A table of only header rows still keeps its text, under column letters.
        names = found if data else _Names(page, found.columns, {}, ())
        chunk: list[tuple[str, list[Line]]] = []
        length = 0
        for row in data or headers:
            text = " | ".join(f"{_column_name(names, span)}: {span.text}" for span, _ in row)
            if chunk and (len(chunk) == TABLE_MAX_ROWS or length + 1 + len(text) > self._max_chars):
                self._table_piece(chunk, names)
                chunk, length = [], 0
            length += len(text) + (1 if chunk else 0)
            chunk.append((text, row))
        self._table_piece(chunk, names)
        self._table = found

    def _names(self, page: int, columns: int, headers: list[list[Line]]) -> _Names:
        """The header rows' names, one per column; else the names of a table just
        before on the previous page with as many columns; else none."""
        if headers:
            names: dict[int, str] = {}
            for row in headers:
                for span, _ in row:
                    col = _row_col(span)[1]
                    names[col] = f"{names[col]} {span.text}" if col in names else span.text
            return _Names(page, columns, names, tuple(span for row in headers for span, _ in row))
        previous = self._table
        if previous is not None and previous.page == page - 1 and previous.columns == columns:
            return _Names(page, columns, previous.names, previous.spans)
        return _Names(page, columns, {}, ())

    def _table_piece(self, chunk: list[tuple[str, list[Line]]], names: _Names) -> None:
        located = [line for _, row in chunk for line in row]
        # The label counts the header cells too: their text is in the piece.
        self._add("\n".join(text for text, _ in chunk), located, [*(s for s, _ in located), *names.spans])

    def _add(self, text: str, located: list[Line], labelled: list[Span]) -> None:
        customer_id, document_id = self._ids
        locations = tuple(box for _, box in located)
        label = piece_label(labelled)
        self.pieces.append(Piece(customer_id, document_id, text, locations, label, section=self._section))


def _column_name(names: _Names, span: Span) -> str:
    col = _row_col(span)[1]
    return names.names.get(col) or column_letter(col + 1)
