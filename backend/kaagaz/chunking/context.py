"""The context header of a piece (PRD 2 C4).

One short line that says where a piece sits in its document. PRD 3 embeds it
with the piece's text and gives it to the answer model as context; it is never
shown as cited text. For example:

    Document: Sept report.pdf | Type: other | Date: 2026-09-30 |
    Section: Monthly results > Profit | Pages 15-16 | Before: …were $48,500.

* ``Section`` is the piece's title, then its heading. It is at most
  ``SECTION_MAX`` characters with its label (council A7), because a layout
  reader can call a whole paragraph a heading.
* ``Before`` is the last line of the piece before it, at most ``BEFORE_MAX``
  characters: a longer one becomes "…" and its last ``BEFORE_MAX - 1``.
* A part with no value is left out (PROVISIONAL, plan section 13, conflict
  13): before the document card (PR 5) there is no type or date.
* A sheet piece names its sheet in place of the section and the pages
  (PROVISIONAL reading of C5's "the header names the sheet"). A sheet is named
  by its number: its name is typed by the customer and is never stored.
* Line breaks and runs of spaces inside a part become one space. Control,
  bidi and format characters are dropped, and a "|" becomes "¦", so the
  document's own text can never add a part (council).

``with_context`` fills every piece's header, in document order, and its
``text_latin`` (L3) when the piece is Devanagari (L2).

Note for PRD 3 (council A9): the header holds document text (the file name,
the headings and a line of the text), so it must be fenced like any other
document text before it reaches a model.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from kaagaz.answering.sanitize import _CONTROL
from kaagaz.chunking.lines import BEFORE_MAX
from kaagaz.chunking.piece import Piece
from kaagaz.chunking.script import is_devanagari, latin_spelling
from kaagaz.reading.spans import CellLocation

SECTION_MAX = 120  # council A7: the whole "Section: …" part
_ELLIPSIS = "…"


def context_header(
    piece: Piece,
    *,
    document_name: str,
    doc_type: str | None,
    document_date: date | None,
    before: str | None,
    sheet: int | None,
) -> str:
    """The header for ``piece``; ``before`` is the previous piece's last line. O(length)."""
    document = document_part(document_name)
    parts = [document] if document else []
    if doc_type:
        parts.append(f"Type: {_one_line(doc_type)}")
    if document_date is not None:
        parts.append(f"Date: {document_date.isoformat()}")
    if sheet is not None:
        parts.append(f"Sheet {sheet}")
    else:
        section = _one_line(" > ".join(piece.section))
        if section:
            part = f"Section: {section}"
            parts.append(part if len(part) <= SECTION_MAX else part[: SECTION_MAX - 1] + _ELLIPSIS)
        first, last = piece.page_from, piece.page_to
        parts.append(f"Page {first}" if first == last else f"Pages {first}-{last}")
    text = _one_line(before or "")
    if text:
        parts.append(f"Before: {text if len(text) <= BEFORE_MAX else _ELLIPSIS + text[1 - BEFORE_MAX :]}")
    return " | ".join(parts)


def document_part(document_name: str) -> str:
    """The header's first part, "Document: {name}", or "" for an empty name. A rename
    rewrites it in every stored header (kaagaz.db.listing.rename_document)."""
    name = _one_line(document_name)
    return f"Document: {name}" if name else ""


def with_context(
    pieces: Sequence[Piece], *, document_name: str, doc_type: str | None, document_date: date | None
) -> list[Piece]:
    """``pieces`` in the same order, each with its context header and, when it is
    Devanagari (L2), its Latin spelling (L3). The type and date are None until the
    document card (PR 5) knows them. O(n) in the characters of the pieces."""
    done = []
    before: str | None = None
    for piece in pieces:
        sheet = piece.page_from if isinstance(piece.locations[0], CellLocation) else None
        header = context_header(
            piece,
            document_name=document_name,
            doc_type=doc_type,
            document_date=document_date,
            before=before,
            sheet=sheet,
        )
        latin = latin_spelling(piece.text) if is_devanagari(piece.text) else None
        done.append(replace(piece, context_header=header, text_latin=latin))
        before = piece.text.rsplit("\n", 1)[-1]
    return done


def _one_line(text: str) -> str:
    """One line of plain text for a header part (council): no control, bidi or
    format characters (category Cf), and a "|" becomes "¦", so document text
    can never forge a part. Runs of whitespace become one space. O(length)."""
    text = _CONTROL.sub("", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return " ".join(text.replace("|", "¦").split())
