"""A customer's own settings and the numbers on the admin screen.

Why here: the SQL for the admin area in one place. Every function takes a
connection from ``Database.customer(customer_id)``; the ``customers`` table's
policy lets a customer see and change only its own row.

``max_upload_mb`` NULL means the platform limit applies.

The numbers count each family once, by its current version, and leave out
Trash (``LISTED``, kaagaz/db/visibility.py); values waiting for review are
counted as the review queue shows them (``LIVE_CURRENT``). Older versions and
what is in Trash are counted on their own (``_KEPT``), so nothing is hidden.
"""

from typing import Any

from sqlalchemy import Connection, RowMapping, text


def get_settings(conn: Connection, customer_id: str) -> RowMapping | None:
    return (
        conn.execute(text("SELECT name, max_upload_mb FROM customers WHERE id = :c"), {"c": customer_id})
        .mappings()
        .first()
    )


def update_settings(conn: Connection, customer_id: str, *, max_upload_mb: int | None) -> RowMapping | None:
    return (
        conn.execute(
            text("UPDATE customers SET max_upload_mb = :mb WHERE id = :c RETURNING name, max_upload_mb"),
            {"c": customer_id, "mb": max_upload_mb},
        )
        .mappings()
        .first()
    )


_DOCUMENTS_BY_STATUS = (
    "SELECT d.status, count(*) FROM documents d"
    " WHERE d.customer_id = :c AND d.deleted_at IS NULL AND d.is_current GROUP BY d.status"
)

_COUNTS = (
    "SELECT m.questions_total, m.answered, m.not_found, m.verified_answers,"
    " m.needs_review_answers, f.feedback_up, f.feedback_down, r.open_review_items"
    " FROM (SELECT count(*) AS questions_total,"
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
