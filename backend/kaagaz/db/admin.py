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
what is in Trash are counted on their own (``older_versions``, ``in_trash``), so nothing is
hidden.
The failed jobs list (PRD 3 Z1) shows current versions out of Trash too (``LISTED``).

Each member's numbers (#386) are what that person did in a window: files they uploaded (every
version, those in Trash too; a file deleted for good is forgotten), the pages of those files
that were read (``pages.route`` set, each page once, every route), their questions (replies in
their own chats, a clarifying question back not counted, as ``questions_total``) and the values
they verified (their ``corrections`` rows: every verify writes one). Last active is when their
session was last used or they last signed in (``users``), whatever the window.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, RowMapping, text


def get_settings(conn: Connection, customer_id: str) -> RowMapping | None:
    return (
        conn.execute(
            text(
                "SELECT name, max_upload_mb, autofile_granted, autofile_on, auto_approve_connections"
                " FROM customers WHERE id = :c"
            ),
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
                " RETURNING name, max_upload_mb, autofile_granted, autofile_on, auto_approve_connections"
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


def set_auto_approve(conn: Connection, customer_id: str, *, on: bool) -> None:
    """Switch "Approve new sign-in connections automatically" on or off (#457 phase 1). O(1)."""
    conn.execute(
        text("UPDATE customers SET auto_approve_connections = :on WHERE id = :c"),
        {"c": customer_id, "on": on},
    )


# PRD 7 (#606): an Admin's numbers are the whole organisation's (decision 20), so each is read by
# an owner-rights function that checks the caller is an Admin (or the worker role) and holds
# today's statement (0091): the database rule would leave out the files the Admin can't see. Each
# also names ``:c``, so another customer's id reads nothing, as row level security did.
# kaagaz_admin_stats: the documents by status (current versions out of Trash), the questions,
# feedback and review items, and every version on purpose, what those leave out:
# ``older_versions`` is every version out of Trash that isn't current (the older ones, and a newer
# one still being read or failed, which the list shows beside the current version).
_ONLY = " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
_STATS = "SELECT * FROM kaagaz_admin_stats()" + _ONLY  # noqa: S608 - a fixed fragment


def stats(conn: Connection, customer_id: str) -> dict[str, Any]:
    """The admin's numbers in one call (``kaagaz_admin_stats``): O(rows of this customer) counted
    once each."""
    row = conn.execute(text(_STATS), {"c": customer_id}).mappings().one()
    return {
        "documents_by_status": {str(k): int(v) for k, v in row["documents_by_status"].items()},
        **{key: int(value) for key, value in row.items() if key != "documents_by_status"},
    }


# #386: every user of this customer with their numbers in [lo, hi); NULL is no bound (all time).
# Three grouped reads joined to the users, never one query per member.
_LO_HI = "CAST(:lo AS timestamptz), CAST(:hi AS timestamptz)"
_MEMBERS = f"SELECT * FROM kaagaz_admin_member_numbers({_LO_HI})" + _ONLY  # noqa: S608 - fixed fragments


def member_numbers(
    conn: Connection, customer_id: str, *, start: datetime | None, end: datetime | None
) -> Sequence[RowMapping]:
    """#386: each user of this customer with what they did in [start, end) (None: no bound),
    in one call (``kaagaz_admin_member_numbers``). O(users + this customer's documents, pages,
    replies and corrections in the window), each grouped once."""
    return conn.execute(text(_MEMBERS), {"c": customer_id, "lo": start, "hi": end}).mappings().all()


# PRD 3 Z1: each family's current version that failed or can't be read, out of Trash,
# newest first, with the start of its last run (the index processing_runs_document).
_FAILED = "SELECT * FROM kaagaz_admin_failed(:limit, :offset)" + _ONLY  # noqa: S608 - a fixed fragment


def failed_documents(
    conn: Connection, customer_id: str, *, limit: int, offset: int
) -> tuple[Sequence[RowMapping], int]:
    """The failed jobs page and its total: one call (``kaagaz_admin_failed``), plus one index
    probe per row for its last run. ponytail: O(D) over the customer's D documents, as no index
    leads on status; a partial index on the failed states if customers grow large."""
    params = {"c": customer_id, "limit": limit, "offset": offset}
    rows = conn.execute(text(_FAILED), params).mappings().all()
    total = int(rows[0]["total"]) if rows else 0  # an empty page is one row with the total alone
    return [row for row in rows if row["id"] is not None], total
