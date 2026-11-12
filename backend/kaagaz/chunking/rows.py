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

__all__ = ["TEXT_MAX_CHARS", "column_letter", "pieces_from_rows", "pieces_from_text_lines"]

TEXT_MAX_CHARS = 800  # a text file's piece, as long as a PDF's (kaagaz/chunking/lines.py MAX_CHARS)


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


def pieces_from_text_lines(customer_id: str, document_id: str, spans: Iterable[Span]) -> list[Piece]:
    """Pieces of a text file read as lines (#522, ``kaagaz/reading/text_reader.py``: each line
    a cell in column 1 of its page): a paragraph, the lines up to a blank one, is a piece, and
    a new piece starts at a new page or when one more line would take it past TEXT_MAX_CHARS
    (a PDF piece's MAX_CHARS). Its lines are joined by line breaks, with no column letter.
    A line longer than the limit is a piece of its own. O(n log n) for n spans, O(n) sorted."""
    cells = sorted(
        ((span, span.location) for span in spans if isinstance(span.location, CellLocation)),
        key=lambda pair: pair[1],
    )
    pieces: list[Piece] = []
    paragraph: list[tuple[Span, CellLocation]] = []

    def close() -> None:
        if paragraph:
            pieces.append(
                Piece(
                    customer_id=customer_id,
                    document_id=document_id,
                    text="\n".join(span.text for span, _ in paragraph),
                    locations=tuple(cell for _, cell in paragraph),
                    label=piece_label(span for span, _ in paragraph),
                )
            )
            paragraph.clear()

    size = 0
    for span, cell in cells:
        if paragraph:
            last = paragraph[-1][1]
            if (
                cell.sheet != last.sheet
                or cell.row != last.row + 1
                or size + 1 + len(span.text) > TEXT_MAX_CHARS
            ):
                close()
        size = len(span.text) if not paragraph else size + 1 + len(span.text)
        paragraph.append((span, cell))
    close()
    return pieces
