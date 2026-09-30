"""Numbers that only a document card holds (PRD 2 D3; decided 30 Sep 2026).

A card value is found by the model and located on the page, but no second
reader has confirmed it, so it is always ``needs_review`` and the fixed
queries never add it up. When a question asks for a figure that only card
values hold, the answer gives no number: it says the figure isn't checked yet,
asks for it to be confirmed in Review first, and points to the documents where
it appears, so the reader can open the whole document instead of getting a
wrong number.

``documents_with`` is one query over this customer's current, ready documents
out of Trash (the ``LIVE_CURRENT`` text of kaagaz/db/visibility.py, written out
below): O(v) for the v card values of those fields, at most ``MAX_DOCUMENTS``
documents back.
"""

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import text

from kaagaz.db.engine import Database

MAX_DOCUMENTS = 5

_CARD_DOCUMENTS = text(
    """
    SELECT d.id, d.display_name
    FROM extracted_values v
    JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
    WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
      AND v.record_key = 'card' AND v.label = 'needs_review'
      AND v.field = ANY(CAST(:fields AS text[]))
    GROUP BY d.id, d.display_name, d.created_at
    ORDER BY d.created_at DESC, d.id
    LIMIT :limit
    """
)


class UncheckedValues(Protocol):
    def documents_with(self, customer_id: str, fields: Sequence[str]) -> list[dict[str, str]]:
        """``{"id", "display_name"}`` of the documents whose card holds an unchecked
        value of one of ``fields``, newest first."""
        ...


class PgUncheckedValues:
    def __init__(self, db: Database) -> None:
        self._db = db

    def documents_with(self, customer_id: str, fields: Sequence[str]) -> list[dict[str, str]]:
        if not fields:
            return []
        params = {"customer_id": customer_id, "fields": list(fields), "limit": MAX_DOCUMENTS}
        with self._db.customer(customer_id) as conn:
            rows = conn.execute(_CARD_DOCUMENTS, params).all()
        return [{"id": str(row.id), "display_name": str(row.display_name)} for row in rows]


def unchecked_sentence(concept: str) -> str:
    """The one sentence of an ``unchecked`` answer, for a metric concept such as "profit"."""
    return (
        f"A {concept} figure appears in these documents, but it hasn't been checked yet, so I won't"
        " give a number from it. Ask a reviewer to confirm it in Review, then ask again. Until then,"
        " open the document to read it yourself."
    )
