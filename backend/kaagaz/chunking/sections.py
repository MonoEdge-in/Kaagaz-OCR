"""A document's map: its sections (PRD 5 CX2) and the topics that join distant pages (CX3).

Each is saved with the document's pieces, in the same replacing save: a ``Section`` is one
row of ``document_sections`` and a ``Topic`` one row of ``document_topics`` (migration 0021),
with the same field names as their columns. Both point at pieces by their ``seq`` in the
same document, never by id, so they are built before the pieces are saved.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Section:
    """CX2: one section of the document, in reading order (``seq`` from 0, no gaps).

    ``path`` is the headings above and of the section, outermost first; empty for a
    section of 5 pages in a document with no headings. Pages count from 1 and
    ``page_to >= page_from``; ``piece_seq_from`` and ``piece_seq_to`` are the seqs of its
    first and last piece, ``piece_seq_to >= piece_seq_from``. ``summary`` is the chat
    model's short summary, at most 400 characters, or None when none was written: an AI
    summary, never evidence."""

    seq: int
    path: tuple[str, ...]
    page_from: int
    page_to: int
    piece_seq_from: int
    piece_seq_to: int
    summary: str | None


@dataclass(frozen=True, slots=True)
class Topic:
    """CX3: a topic of the document as a short lower-case name, such as "leave", unique in
    the document, with its other names in any language (stored casefolded) and the seqs
    of the pieces that discuss it: never empty, and each one a piece of this document.
    Topic names are labels for finding pieces, never shown as facts."""

    topic: str
    aliases: tuple[str, ...]
    piece_seqs: tuple[int, ...]
