"""A page's layout (#383) and its tables as rows and cells (#382), from the main reader's spans.

**Blocks.** Each page's box spans (lines and table cells; never word marks, which the
caller leaves out) are grouped into blocks, each with its kind (``BLOCK_TYPES``) and box
(the union of its spans' boxes), in reading order (the order of each block's first span):

* the cells of one table are one block of kind "table";
* the lines the reader put in one block (``Span.block``, the Gemini reader's) are one;
* any other line joins the line just before it when both are text of the same type on
  the same page, it starts below that line by at most ``BLOCK_GAP`` times the taller one's
  height (and not above it by more than half of it), and the two overlap side to side by at
  least half the narrower one. Else it starts a new block. ``kaagaz.reading.lines`` gives
  the lines of two columns column by column, so two columns are two blocks, never one.

A line's type comes from its kind: title or heading is "title", stamp "stamp", signature
"signature", note, header or footer "note" (text at the side or in a margin), anything else
"text". A reader's block takes the type most of its lines have (the first of a tie).

**Tables.** ``Layout.cells`` holds every table cell, by page, table, row and column: each a
span with its text, header flag, ``col_span`` and box, so it can be stored as rows and cells
and pointed at. ``header_names`` names a table's columns from its header cells, a merged
header naming every column it covers: "Amount" over two columns with "CGST" and "SGST" under
it names them "Amount CGST" and "Amount SGST".

Time O(S log S) for S spans (one stable sort by page, one by cell); memory O(S).
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from kaagaz.reading.spans import BoxLocation, Span

BLOCK_TYPES = ("title", "text", "table", "stamp", "signature", "note")
BLOCK_GAP = 1.0  # a line joins the one above when the space between is at most this many heights
_TYPE_OF_KIND = {
    "title": "title",
    "heading": "title",
    "table_cell": "table",
    "stamp": "stamp",
    "signature": "signature",
    "note": "note",
    "header": "note",
    "footer": "note",
}


@dataclass(frozen=True, slots=True)
class Block:
    """One block of a page: its kind (one of BLOCK_TYPES), its box, and for a table its id."""

    page: int
    kind: str
    box: BoxLocation
    table_id: str | None = None


@dataclass(frozen=True, slots=True)
class Layout:
    """A document's blocks, page by page in reading order, and its table cells."""

    blocks: tuple[Block, ...] = ()
    cells: tuple[Span, ...] = ()


def page_layout(spans: Iterable[Span]) -> Layout:
    """The blocks and table cells of ``spans`` (the module notes). Spans that are not on a
    page (a sheet's cells) are left out."""
    lines = sorted(
        (span for span in spans if isinstance(span.location, BoxLocation)),
        key=lambda span: span.location.page,
    )
    groups: dict[tuple[object, ...], list[Span]] = {}
    previous: tuple[Span, tuple[object, ...]] | None = None  # the last free line and its block
    for index, span in enumerate(lines):
        page = span.location.page
        if span.table_id is not None:
            key: tuple[object, ...] = ("table", page, span.table_id)
        elif span.block is not None:
            key = ("block", page, span.block)
        elif previous is not None and _joins(previous[0], span):
            key = previous[1]
        else:
            key = ("line", page, index)
        groups.setdefault(key, []).append(span)
        previous = (span, key) if key[0] == "line" else None
    blocks = tuple(_block(key, members) for key, members in groups.items())
    cells = tuple(sorted((s for s in lines if s.table_id is not None), key=_cell_order))
    return Layout(blocks, cells)


def header_names(cells: Iterable[Span]) -> dict[int, str]:
    """Each column's name (from 0) from a table's header cells, given in (row, col) order:
    their texts down the column joined by a space; a merged cell names every column it
    covers (``col_span``). O(cells x span)."""
    names: dict[int, str] = {}
    for cell in cells:
        start = cell.col or 0
        for col in range(start, start + (cell.col_span or 1)):
            names[col] = f"{names[col]} {cell.text}" if col in names else cell.text
    return names


def _type(span: Span) -> str:
    return _TYPE_OF_KIND.get(span.kind or "", "text")


def _joins(above: Span, below: Span) -> bool:
    """``below`` continues ``above``'s block (the module notes)."""
    a, b = above.location, below.location
    if not (isinstance(a, BoxLocation) and isinstance(b, BoxLocation)) or a.page != b.page:
        return False
    if _type(above) != _type(below):
        return False
    height = max(a.y1 - a.y0, b.y1 - b.y0)
    gap = b.y0 - a.y1
    overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    narrower = min(a.x1 - a.x0, b.x1 - b.x0)
    return -height / 2 <= gap <= BLOCK_GAP * height and overlap >= narrower / 2


def _block(key: tuple[object, ...], members: list[Span]) -> Block:
    boxes = [span.location for span in members if isinstance(span.location, BoxLocation)]
    box = BoxLocation(
        boxes[0].page,
        min(box.x0 for box in boxes),
        min(box.y0 for box in boxes),
        max(box.x1 for box in boxes),
        max(box.y1 for box in boxes),
    )
    if key[0] == "table":
        return Block(box.page, "table", box, str(key[2]))
    return Block(box.page, Counter(_type(span) for span in members).most_common(1)[0][0], box)


def _cell_order(span: Span) -> tuple[int, str, int, int]:
    return span.location.page, span.table_id or "", span.row or 0, span.col or 0
