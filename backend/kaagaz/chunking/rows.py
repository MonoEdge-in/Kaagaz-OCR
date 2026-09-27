"""One piece per spreadsheet row.

The text is split along the file's own layout (its rows), never every N
characters. A piece remembers every cell it was made from, in order, so any
part of it can be traced back to its exact cell.

No row is guessed to be a header here: working out which row holds the column
names would be a guess (CLAUDE.md never-do 6). Cells are labelled by their
column letter instead, for example "A: INV-1 | B: 100". (Values use a narrow,
written header rule of their own: kaagaz/extraction/spreadsheet.py.)

Time is O(n log n) in the worst case for n spans: one stable sort by
(sheet, row, column), then one pass that groups each row. Readers already
give cells in order, and Python's sort is O(n) on sorted input, so in
practice it is linear.
"""

from collections.abc import Iterable
from itertools import groupby

from kaagaz.chunking.piece import Piece, piece_label
from kaagaz.reading.spans import CellLocation, Span

__all__ = ["Piece", "column_letter", "pieces_from_rows"]


def column_letter(column: int) -> str:
    """1 -> "A", 26 -> "Z", 27 -> "AA", 16384 -> "XFD". O(log column)."""
    if column < 1:
        raise ValueError("column numbers start at 1")
    letters = []
    while column:
        column, remainder = divmod(column - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def pieces_from_rows(customer_id: str, document_id: str, spans: Iterable[Span]) -> list[Piece]:
    """One piece per (sheet, row) of the cell spans; spans of other kinds are ignored."""
    cells = [(span, span.location) for span in spans if isinstance(span.location, CellLocation)]
    cells.sort(key=lambda pair: pair[1])  # (sheet, row, column)
    pieces = []
    for _, group in groupby(cells, key=lambda pair: (pair[1].sheet, pair[1].row)):
        row = list(group)
        pieces.append(
            Piece(
                customer_id=customer_id,
                document_id=document_id,
                text=" | ".join(f"{column_letter(cell.column)}: {span.text}" for span, cell in row),
                locations=tuple(cell for _, cell in row),
                label=piece_label(span for span, _ in row),
            )
        )
    return pieces
