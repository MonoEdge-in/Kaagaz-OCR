"""The audit log: one row for every change a person makes.

Why: "who changed what, and when" must be answerable later (docs/system-design/05,
repudiation). A row holds only ids, statuses and counts in ``details``, never
document text, file names, questions or identity numbers, so the log is safe
to show to the customer's admin.

Every function takes a connection from ``Database.customer(customer_id)``, so
the write happens in the same transaction as the change it records, and row
level security applies. The explicit ``customer_id`` filter is the second lock.
"""

import json
import re
from collections.abc import Mapping, Sequence

from sqlalchemy import Connection, RowMapping, text

_ACTION = re.compile(r"^[a-z_.]{1,64}$")

# Only these kinds of values may go into details: ids, statuses and counts.
DetailValue = str | int | bool | None


def record(
    conn: Connection,
    customer_id: str,
    *,
    actor_user_id: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: Mapping[str, DetailValue] | None = None,
) -> None:
    """Write one audit row. O(log n): one indexed insert."""
    if not _ACTION.match(action):
        raise ValueError("audit action must match ^[a-z_.]+$")
    conn.execute(
        text(
            "INSERT INTO audit_log (customer_id, actor_user_id, action, target_type, target_id, details)"
            " VALUES (:c, :actor, :action, :tt, :tid, CAST(:details AS jsonb))"
        ),
        {
            "c": customer_id,
            "actor": actor_user_id,
            "action": action,
            "tt": target_type,
            "tid": target_id,
            "details": json.dumps(dict(details or {})),
        },
    )


def list_entries(
    conn: Connection, customer_id: str, *, limit: int, offset: int
) -> tuple[Sequence[RowMapping], int]:
    """This customer's audit rows, newest first, with the actor's name.

    Two indexed queries (the page and the count): O(limit + offset) rows read.
    """
    rows = (
        conn.execute(
            text(
                "SELECT a.id, a.action, u.display_name AS actor_name, a.target_type, a.target_id,"
                " a.details, a.created_at"
                " FROM audit_log a"
                " LEFT JOIN users u ON u.customer_id = a.customer_id AND u.id = a.actor_user_id"
                " WHERE a.customer_id = :c"
                " ORDER BY a.created_at DESC, a.id DESC"
                " LIMIT :limit OFFSET :offset"
            ),
            {"c": customer_id, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    total: int = conn.execute(
        text("SELECT count(*) FROM audit_log WHERE customer_id = :c"), {"c": customer_id}
    ).scalar_one()
    return rows, int(total)
