"""A customer's own settings and the numbers on the admin screen.

Why here: the SQL for the admin area in one place. Every function takes a
connection from ``Database.customer(customer_id)``; the ``customers`` table's
policy lets a customer see and change only its own row.

``max_upload_mb`` NULL means the platform limit applies.
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


def stats(conn: Connection, customer_id: str) -> dict[str, Any]:
    """The admin's numbers in two queries: O(rows of this customer) counted once each."""
    params = {"c": customer_id}
    by_status = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            text("SELECT status, count(*) FROM documents WHERE customer_id = :c GROUP BY status"), params
        )
    }
    counts = (
        conn.execute(
            text(
                "SELECT m.questions_total, m.answered, m.not_found, m.verified_answers,"
                " m.needs_review_answers, f.feedback_up, f.feedback_down, v.open_review_items"
                " FROM (SELECT count(*) AS questions_total,"
                " count(*) FILTER (WHERE answer ->> 'status' = 'answered') AS answered,"
                " count(*) FILTER (WHERE answer ->> 'status' = 'not_found') AS not_found,"
                " count(*) FILTER (WHERE answer ->> 'label' = 'verified') AS verified_answers,"
                " count(*) FILTER (WHERE answer ->> 'label' = 'needs_review') AS needs_review_answers"
                " FROM messages WHERE customer_id = :c AND role = 'assistant') AS m,"
                " (SELECT count(*) FILTER (WHERE rating = 'up') AS feedback_up,"
                " count(*) FILTER (WHERE rating = 'down') AS feedback_down"
                " FROM feedback WHERE customer_id = :c) AS f,"
                " (SELECT count(*) AS open_review_items FROM extracted_values"
                " WHERE customer_id = :c AND label = 'needs_review') AS v"
            ),
            params,
        )
        .mappings()
        .one()
    )
    return {"documents_by_status": by_status, **{key: int(value) for key, value in counts.items()}}
