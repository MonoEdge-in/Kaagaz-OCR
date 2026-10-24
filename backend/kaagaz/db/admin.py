"""A customer's own settings and the numbers on the admin screen.

Why here: the SQL for the admin area in one place. Every function takes a
connection from ``Database.customer(customer_id)``; the ``customers`` table's
policy lets a customer see and change only its own row.

``max_upload_mb`` NULL means the platform limit applies.

Auto-filing (feature 28, 0025): the admin may switch ``autofile_on`` only while the
platform operator granted it (``autofile_granted``, which the app can't write). The UPDATE
says so itself, so a switch-on without the grant changes no row; the CHECK
``customers_autofile_check`` refuses it as well.

The numbers count each family once, by its current version, and leave out
Trash (``LISTED``, kaagaz/db/visibility.py); values waiting for review are
counted as the review queue shows them (``LIVE_CURRENT``). Older versions and
what is in Trash are counted on their own (``_KEPT``), so nothing is hidden.
The failed jobs list (PRD 3 Z1) shows current versions out of Trash too (``LISTED``).
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Connection, RowMapping, text


def get_settings(conn: Connection, customer_id: str) -> RowMapping | None:
    return (
        conn.execute(
            text("SELECT name, max_upload_mb, autofile_granted, autofile_on FROM customers WHERE id = :c"),
            {"c": customer_id},
        )
        .mappings()
        .first()
    )


def update_settings(conn: Connection, customer_id: str, *, max_upload_mb: int | None) -> RowMapping | None:
    return (
        conn.execute(
            text(
                "UPDATE customers SET max_upload_mb = :mb WHERE id = :c"
                " RETURNING name, max_upload_mb, autofile_granted, autofile_on"
            ),
            {"c": customer_id, "mb": max_upload_mb},
        )
        .mappings()
        .first()
    )


def set_autofile(conn: Connection, customer_id: str, *, on: bool) -> bool:
    """Switch auto-filing on or off. False, and nothing changed: on without the grant. O(1)."""
    return (
        conn.execute(
            text(
                "UPDATE customers SET autofile_on = :on WHERE id = :c AND (autofile_granted OR NOT :on)"
                " RETURNING id"
            ),
            {"c": customer_id, "on": on},
        ).first()
        is not None
    )


_DOCUMENTS_BY_STATUS = (
    "SELECT d.status, count(*) FROM documents d"
    " WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current GROUP BY d.status"
)

_COUNTS = (
    "SELECT m.questions_total, m.answered, m.not_found, m.verified_answers,"
    " m.needs_review_answers, f.feedback_up, f.feedback_down, r.open_review_items"
    " FROM (SELECT count(*) FILTER (WHERE answer ->> 'status' IS DISTINCT FROM 'clarify') AS questions_total,"
    " count(*) FILTER (WHERE answer ->> 'status' = 'answered') AS answered,"
    " count(*) FILTER (WHERE answer ->> 'status' = 'not_found') AS not_found,"
    " count(*) FILTER (WHERE answer ->> 'label' = 'verified') AS verified_answers,"
    " count(*) FILTER (WHERE answer ->> 'label' = 'needs_review') AS needs_review_answers"
    " FROM messages WHERE customer_id = :c AND role = 'assistant') AS m,"
    " (SELECT count(*) FILTER (WHERE rating = 'up') AS feedback_up,"
    " count(*) FILTER (WHERE rating = 'down') AS feedback_down"
    " FROM feedback WHERE customer_id = :c) AS f,"
    " (SELECT count(*) AS open_review_items FROM extracted_values v"
    " JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id"
    " WHERE v.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND v.label = 'needs_review') AS r"
)

# Every version on purpose: what the two numbers above leave out. ``older_versions``
# is every version out of Trash that isn't current: the older ones, and a newer one
# still being read or failed (the list shows that one beside the current version).
_KEPT = (
    "SELECT count(*) FILTER (WHERE d.deleted_at IS NULL AND NOT d.is_current) AS older_versions,"
    " count(DISTINCT d.family_id) FILTER (WHERE d.deleted_at IS NOT NULL) AS in_trash"
    " FROM documents d WHERE d.customer_id = :c"
)


def stats(conn: Connection, customer_id: str) -> dict[str, Any]:
    """The admin's numbers in three queries: O(rows of this customer) counted once each."""
    params = {"c": customer_id}
    by_status = {str(row[0]): int(row[1]) for row in conn.execute(text(_DOCUMENTS_BY_STATUS), params)}
    counts = conn.execute(text(_COUNTS), params).mappings().one()
    kept = conn.execute(text(_KEPT), params).mappings().one()
    return {
        "documents_by_status": by_status,
        **{key: int(value) for key, value in counts.items()},
        **{key: int(value) for key, value in kept.items()},
    }


# PRD 3 Z1: each family's current version that failed or can't be read, out of Trash,
# newest first, with the start of its last run (the index processing_runs_document).
_FAILED = (
    "SELECT d.id, d.display_name, d.detected_type, d.status, d.failure_code, d.page_count,"
    " d.attempts, d.max_attempts, r.started_at AS last_run_at"
    " FROM documents d"
    " LEFT JOIN LATERAL (SELECT pr.started_at FROM processing_runs pr"
    " WHERE pr.customer_id = :c AND pr.document_id = d.id ORDER BY pr.started_at DESC LIMIT 1) r ON true"
    " WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current"
    " AND d.status IN ('failed', 'needs_reader')"
    " ORDER BY d.created_at DESC, d.id DESC LIMIT :limit OFFSET :offset"
)

_FAILED_COUNT = (
    "SELECT count(*) FROM documents d"
    " WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current"
    " AND d.status IN ('failed', 'needs_reader')"
)


def failed_documents(
    conn: Connection, customer_id: str, *, limit: int, offset: int
) -> tuple[Sequence[RowMapping], int]:
    """The failed jobs page and its total: two queries, plus one index probe per row
    for its last run. ponytail: O(D) over the customer's D documents, as no index
    leads on status; a partial index on the failed states if customers grow large."""
    params = {"c": customer_id, "limit": limit, "offset": offset}
    rows = conn.execute(text(_FAILED), params).mappings().all()
    total: int = conn.execute(text(_FAILED_COUNT), params).scalar_one()
    return rows, int(total)
