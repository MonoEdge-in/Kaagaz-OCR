"""Numbers that only a document card holds (PRD 2 D3; decided 30 Sep 2026).

A card value is found by the model and located on the page, but no second
reader has confirmed it, so it is always ``needs_review`` and the fixed
queries never add it up. When a question asks for a figure that only card
values hold, the answer gives no number: it says the figure is in these
documents and points to them, so the reader can open the whole document instead
of getting a wrong number. No Review or needs-review wording (Tushar, 16 Oct
2026: Ask stays calm; Review is its own feature).

``documents_with`` is one query over this customer's current, ready documents
out of Trash (the ``LIVE_CURRENT`` text of kaagaz/db/visibility.py, written out
below), inside the chat's scope (the ``Scope.params()`` predicates, PRD 3 H2):
O(v) for the v card values of those fields, at most ``MAX_DOCUMENTS`` documents back.
"""

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import text

from kaagaz.db.engine import Database, user_or_customer
from kaagaz.search.scope import EVERYTHING, Scope

MAX_DOCUMENTS = 5

_CARD_DOCUMENTS = text(
    """
    SELECT d.id, d.display_name
    FROM extracted_values v
    JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
    WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
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
      AND v.record_key = 'card' AND v.label = 'needs_review'
      AND v.field = ANY(CAST(:fields AS text[]))
    GROUP BY d.id, d.display_name, d.created_at
    ORDER BY d.created_at DESC, d.id
    LIMIT :limit
    """
)


class UncheckedValues(Protocol):
    def documents_with(
        self, customer_id: str, fields: Sequence[str], *, scope: Scope = EVERYTHING, user_id: str | None
    ) -> list[dict[str, str]]:
        """``{"id", "display_name"}`` of the documents whose card holds an unchecked
        value of one of ``fields``, newest first; ``user_id``: the asker, whose transaction it is."""
        ...


class PgUncheckedValues:
    def __init__(self, db: Database) -> None:
        self._db = db

    def documents_with(
        self,
        customer_id: str,
        fields: Sequence[str],
        *,
        scope: Scope = EVERYTHING,
        user_id: str | None,
    ) -> list[dict[str, str]]:
        if not fields:
            return []
        params = {"customer_id": customer_id, "fields": list(fields), "limit": MAX_DOCUMENTS}
        params |= scope.params()
        with user_or_customer(self._db, customer_id, user_id) as conn:
            rows = conn.execute(_CARD_DOCUMENTS, params).all()
        return [{"id": str(row.id), "display_name": str(row.display_name)} for row in rows]


def unchecked_sentence(concept: str) -> str:
    """The one sentence of an ``unchecked`` answer, for a metric concept such as "profit"."""
    return f"A {concept} figure is in these documents. Open them to see it."
