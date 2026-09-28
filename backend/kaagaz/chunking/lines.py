"""Pieces from text lines on pages (PDF text layers and scans).

Consecutive lines on one page are joined into a piece until it would pass
about ``MAX_CHARS`` characters. A new piece also starts at a page change and
at a blank-line gap: the space above a line is more than ``GAP_FACTOR`` times
the page's median line height, which is where paragraphs and blocks usually
end. So a piece follows the page's own layout, never a fixed cut.

Lines keep the order the reader gave them in (its reading order) within each
page. One line longer than the limit is never cut: it becomes its own piece.
Each line's box is kept, in text order, so any part of a piece can be
highlighted.

PROVISIONAL: 800 characters and 1.5 line heights are starting values, not
measured on real documents yet.

Time O(n log n) for n lines (a stable sort by page, and one median per page);
memory O(n).
"""

import statistics
from collections.abc import Iterable
from itertools import groupby

from kaagaz.chunking.piece import Piece, piece_label
from kaagaz.reading.spans import BoxLocation, Span

MAX_CHARS = 800
GAP_FACTOR = 1.5


def pieces_from_lines(
    customer_id: str, document_id: str, spans: Iterable[Span], *, max_chars: int = MAX_CHARS
) -> list[Piece]:
    """Pieces from the box spans (one per line); spans of other kinds are ignored."""
    lines = [(span, span.location) for span in spans if isinstance(span.location, BoxLocation)]
    lines.sort(key=lambda pair: pair[1].page)  # stable: reading order kept within a page
    pieces: list[Piece] = []
    for _, group in groupby(lines, key=lambda pair: pair[1].page):
        page_lines = list(group)
        gap_limit = GAP_FACTOR * statistics.median(box.y1 - box.y0 for _, box in page_lines)
        current: list[tuple[Span, BoxLocation]] = []
        length = 0
        for span, box in page_lines:
            if current:
                gap = box.y0 - current[-1][1].y1
                if gap > gap_limit or length + 1 + len(span.text) > max_chars:
                    pieces.append(_piece(customer_id, document_id, current))
                    current, length = [], 0
            length += len(span.text) + (1 if current else 0)
            current.append((span, box))
        pieces.append(_piece(customer_id, document_id, current))
    return pieces


def _piece(customer_id: str, document_id: str, lines: list[tuple[Span, BoxLocation]]) -> Piece:
    return Piece(
        customer_id=customer_id,
        document_id=document_id,
        text="\n".join(span.text for span, _ in lines),
        locations=tuple(box for _, box in lines),
        label=piece_label(span for span, _ in lines),
    )
