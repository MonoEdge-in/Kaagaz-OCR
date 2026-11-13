"""Answers know about pages that couldn't be read (PRD 5 F6).

Two rules, applied by ``AnswerService`` (kaagaz/answering/service.py):

* **Not found.** When the answer is not found and any document in the chat's scope
  has an unreadable or uncertain page (``pages.read_quality``, F1), the answer adds
  the note "Not found in the readable text. 2 pages to check: Invoice_12.pdf
  page 3, Register.jpg page 1." (``not_found_note``) and the pages themselves
  (``unreadable_pages``: document id, name, page number and quality), so the chat
  links each one. A page kept as is (F5) is still named: it stays flagged in
  answers. Unreadable pages come first, then uncertain ones, the newest document
  first; at most ``MAX_NAMED_PAGES`` are named, and the count is of all of them
  (", and 7 more").
* **Hard to read.** When a cited text holds a word the reader was unsure of, or
  could not read (a word mark, F3: a span with quality unsure or illegible, inside
  one of the citation's boxes on its page), the citation is ``hard_to_read`` and the
  answer is needs review with "Part of this text was hard to read."
  (``HARD_TO_READ_NOTE``).

``flagged_pages`` is one statement over this customer's current, ready documents
out of Trash (the ``LIVE_CURRENT`` text of kaagaz/db/visibility.py, written out
below), inside the chat's scope (the ``Scope.params()`` predicates, PRD 3 H2): O(P)
in the scope's pages, at most ``MAX_NAMED_PAGES`` rows back. ``word_marks`` is one
statement over the cited documents' first reader output: O(s) in their stored
spans, only the marks back. Neither ever logs a name, a page's text or a word.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text

from kaagaz.db.engine import Database, user_or_customer
from kaagaz.reading.spans import BoxLocation, location_from_json
from kaagaz.search.scope import EVERYTHING, Scope

MAX_NAMED_PAGES = 10
HARD_TO_READ_NOTE = "Part of this text was hard to read."
NOT_FOUND_NOTE = "Not found in the readable text."

_FLAGGED_PAGES = text(
    """
    SELECT d.id::text AS document_id, d.display_name, p.page_no, p.read_quality, count(*) OVER () AS total
    FROM pages p
    JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id
    WHERE p.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
      AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
      AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
        WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
        GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
      AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
      AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
      AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
      AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
        OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
        BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
        AND coalesce(CAST(:s_to AS date), 'infinity'))
      AND p.read_quality IN ('unreadable', 'uncertain')
    ORDER BY p.read_quality = 'unreadable' DESC, d.created_at DESC, d.id, p.page_no
    LIMIT :limit
    """
)

# The word marks (F3) of the cited documents: spans read by OCR with no kind and a
# quality of unsure or illegible (kaagaz.reading.ocr.is_word_mark), with their spot. And
# (council 16 Oct 2026) one mark over the whole of each page graded unreadable or read by
# the local reader: its text is the backup reader's (a timeout, a refusal, an outage), so a
# citation of it is always hard to read. O(s + p) in the cited documents' spans and pages.
# Word review (0030): a mark a person confirmed or edited, still in force, is no longer hard
# to read (matched on its page, spot and text as read, the parts of its word key).
_WORD_MARKS = text(
    """
    SELECT r.document_id::text AS document_id, mark -> 'region' AS region
    FROM reader_outputs r CROSS JOIN LATERAL jsonb_array_elements(r.output) AS mark
    WHERE r.customer_id = :customer_id AND r.document_id = ANY(CAST(:documents AS uuid[]))
      AND r.role = 'first' AND mark ->> 'origin' = 'ocr' AND mark ->> 'kind' IS NULL
      AND mark ->> 'quality' IN ('unsure', 'illegible')
      AND NOT EXISTS (SELECT 1 FROM word_changes w WHERE w.customer_id = r.customer_id
        AND w.document_id = r.document_id AND w.kind = 'mark' AND w.undone_at IS NULL
        AND w.action IN ('confirm', 'edit') AND w.page_no = CAST(mark -> 'region' ->> 'page' AS integer)
        AND w.region = mark -> 'region' AND w.as_read = mark ->> 'text')
    UNION ALL
    SELECT p.document_id::text,
           jsonb_build_object('kind', 'box', 'page', p.page_no, 'box', jsonb_build_array(0, 0, 1, 1))
    FROM pages p
    WHERE p.customer_id = :customer_id AND p.document_id = ANY(CAST(:documents AS uuid[]))
      AND (p.read_quality = 'unreadable'
        OR p.read_issues && CAST(ARRAY['read_locally', 'reader_refused'] AS text[]))
    """
)


@dataclass(frozen=True, slots=True)
class FlaggedPage:
    """A page of a document in scope that couldn't be read well (F1): ``read_quality``
    is unreadable or uncertain."""

    document_id: str
    document_name: str
    page_no: int
    read_quality: str

    def to_json(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_name": self.document_name,
            "page_no": self.page_no,
            "read_quality": self.read_quality,
        }


@dataclass(frozen=True, slots=True)
class WordMark:
    """Where a word the reader was unsure of, or couldn't read, sits (F3)."""

    document_id: str
    location: BoxLocation


class ReadingQuality(Protocol):
    def flagged_pages(
        self, customer_id: str, *, scope: Scope = EVERYTHING, user_id: str | None
    ) -> tuple[list[FlaggedPage], int]:
        """At most ``MAX_NAMED_PAGES`` unreadable or uncertain pages of the documents in
        ``scope``, unreadable first, and how many there are in all; ``user_id``: the asker,
        whose transaction it is (``word_marks``' too)."""
        ...

    def word_marks(
        self, customer_id: str, document_ids: Sequence[str], *, user_id: str | None
    ) -> list[WordMark]:
        """The word marks of these documents (their first reader's)."""
        ...


class PgReadingQuality:
    def __init__(self, db: Database) -> None:
        self._db = db

    def flagged_pages(
        self, customer_id: str, *, scope: Scope = EVERYTHING, user_id: str | None
    ) -> tuple[list[FlaggedPage], int]:
        params = {"customer_id": customer_id, "limit": MAX_NAMED_PAGES} | scope.params()
        with user_or_customer(self._db, customer_id, user_id) as conn:
            rows = conn.execute(_FLAGGED_PAGES, params).all()
        pages = [
            FlaggedPage(str(row.document_id), str(row.display_name), int(row.page_no), str(row.read_quality))
            for row in rows
        ]
        return pages, int(rows[0].total) if rows else 0

    def word_marks(
        self, customer_id: str, document_ids: Sequence[str], *, user_id: str | None
    ) -> list[WordMark]:
        if not document_ids:
            return []
        params = {"customer_id": customer_id, "documents": sorted(set(document_ids))}
        with user_or_customer(self._db, customer_id, user_id) as conn:
            rows = conn.execute(_WORD_MARKS, params).all()
        marks = []
        for row in rows:
            location = _box(row.region)
            if location is not None:
                marks.append(WordMark(str(row.document_id), location))
        return marks


def not_found_note(pages: Sequence[FlaggedPage], total: int) -> str | None:
    """The note of a not-found answer (F6), or None when no page in scope is flagged:
    "Not found in the readable text. 2 pages to check: Invoice_12.pdf page 3,
    Register.jpg page 1." ``total`` counts every flagged page; ``pages`` are the ones
    named, and the rest are counted as ", and 7 more". O(pages)."""
    if total <= 0 or not pages:
        return None
    named = ", ".join(f"{page.document_name} page {page.page_no}" for page in pages)
    more = total - len(pages)
    if more > 0:
        named += f", and {more} more"
    counted = "1 page to check" if total == 1 else f"{total} pages to check"
    return f"{NOT_FOUND_NOTE} {counted}: {named}."


def hard_to_read(document_id: str, regions: Iterable[dict[str, Any]], marks: Sequence[WordMark]) -> bool:
    """Whether a cited text, at ``regions`` of ``document_id``, holds a word mark: one
    whose box overlaps one of its boxes on the same page. O(r x m)."""
    boxes = [box for box in map(_box, regions) if box is not None]
    return any(
        mark.document_id == document_id and any(_overlap(mark.location, box) for box in boxes)
        for mark in marks
    )


def _box(region: Any) -> BoxLocation | None:
    """A stored region as a box, or None for a cell or a malformed region."""
    if not isinstance(region, dict):
        return None
    try:
        location = location_from_json(region)
    except (KeyError, TypeError, ValueError):
        return None
    return location if isinstance(location, BoxLocation) else None


def _overlap(a: BoxLocation, b: BoxLocation) -> bool:
    """Two boxes on the same page share some area."""
    return a.page == b.page and max(a.x0, b.x0) < min(a.x1, b.x1) and max(a.y0, b.y0) < min(a.y1, b.y1)
