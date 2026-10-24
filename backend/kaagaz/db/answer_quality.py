"""The company admin's Answer quality page (PRD 6 F2): answers marked Not useful after the
notice, and Useful and Not useful counts by week.

Every function takes a connection from ``Database.customer(customer_id)`` and filters on
``customer_id`` too. Only Not useful rows given after the notice (``notice_shown``,
decision 7) are listed or may be marked handled; a Useful row, an older one, or another
company's id matches nothing.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, RowMapping, text

# Not useful after the notice, newest first, with who asked, the question (the user
# message written with the answer, so the same created_at) and the answer. The window
# count gives the total of the filter in the same statement. feedback_not_useful (0050)
# serves the filter; the question is one lookup on messages_conversation per row.
_NOT_USEFUL = text(
    "SELECT f.id, f.message_id, f.reason, f.reason_codes, f.created_at, f.handled_at,"
    " u.display_name AS asked_by, q.text AS question, a.answer, count(*) OVER () AS total"
    " FROM feedback f"
    " JOIN messages a ON a.customer_id = f.customer_id AND a.id = f.message_id"
    " JOIN users u ON u.customer_id = f.customer_id AND u.id = f.user_id"
    " LEFT JOIN LATERAL (SELECT m.text FROM messages m WHERE m.customer_id = a.customer_id"
    " AND m.conversation_id = a.conversation_id AND m.role = 'user' AND m.created_at = a.created_at"
    " ORDER BY m.id LIMIT 1) q ON true"
    " WHERE f.customer_id = :c AND f.rating = 'down' AND f.notice_shown AND f.created_at >= :since"
    " AND (f.handled_at IS NOT NULL) = :handled"
    " ORDER BY f.created_at DESC, f.id DESC LIMIT :n OFFSET :o"
)


def not_useful(
    conn: Connection, customer_id: str, *, since: datetime, handled: bool, limit: int, offset: int
) -> tuple[Sequence[RowMapping], int]:
    """One page of the Not useful answers given since ``since``, open or handled, and how
    many there are (0 past the last page). One statement."""
    rows = (
        conn.execute(
            _NOT_USEFUL,
            {"c": customer_id, "since": since, "handled": handled, "n": limit, "o": offset},
        )
        .mappings()
        .all()
    )
    return rows, int(rows[0]["total"]) if rows else 0


def weekly_counts(
    conn: Connection, customer_id: str, *, since: datetime, zone: str
) -> dict[str, tuple[int, int]]:
    """Monday of each week (in ``zone``, ISO text) with ratings since ``since``: (Useful,
    Not useful). Not useful counts only those given after the notice, as the list (decision
    7); Useful carries no reason and no notice, so every one counts."""
    rows = conn.execute(
        text(
            "SELECT date_trunc('week', f.created_at AT TIME ZONE :zone)::date AS week,"
            " count(*) FILTER (WHERE f.rating = 'up') AS useful,"
            " count(*) FILTER (WHERE f.rating = 'down' AND f.notice_shown) AS not_useful"
            " FROM feedback f WHERE f.customer_id = :c AND f.created_at >= :since GROUP BY 1"
        ),
        {"c": customer_id, "since": since, "zone": zone},
    ).all()
    return {row.week.isoformat(): (int(row.useful), int(row.not_useful)) for row in rows}


def set_handled(
    conn: Connection, customer_id: str, feedback_id: str, admin_id: str, *, handled: bool, seen: datetime
) -> RowMapping | None:
    """Mark one Not useful answer (given after the notice) handled by this admin, or open
    again, only while it is still the rating the admin saw (``seen``, its ``created_at``: a new
    rating keeps the id). Handled again keeps its first time. None when nothing changed."""
    return (
        conn.execute(
            text(
                "UPDATE feedback SET"
                " handled_at = CASE WHEN :h THEN coalesce(handled_at, now()) END,"
                " handled_by = CASE WHEN NOT :h THEN NULL WHEN handled_at IS NULL THEN CAST(:u AS uuid)"
                " ELSE handled_by END"
                " WHERE customer_id = :c AND id = :id AND rating = 'down' AND notice_shown"
                " AND created_at = :seen"
                " RETURNING id, message_id, handled_at"
            ),
            {"c": customer_id, "id": feedback_id, "u": admin_id, "h": handled, "seen": seen},
        )
        .mappings()
        .first()
    )


def rated_again(conn: Connection, customer_id: str, feedback_id: str, seen: datetime) -> bool:
    """True when this rating exists but was given again since ``seen``."""
    found = conn.execute(
        text("SELECT 1 FROM feedback WHERE customer_id = :c AND id = :id AND created_at <> :seen"),
        {"c": customer_id, "id": feedback_id, "seen": seen},
    ).first()
    return found is not None
