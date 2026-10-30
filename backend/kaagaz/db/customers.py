"""A customer's status (0014, super admin PR 1, council 6 Oct 2026, PROVISIONAL):
active, disabled or deleting.

Where it is checked, so a customer that is not active gets no sign-in, no
session and no model calls:

* sign-in and every request: ``users.session_user`` and ``users.begin_attempt``
  (in their own queries, no extra round trip);
* the worker, at the start of each job (``DocumentProcessor``, through
  ``PgDocuments.customer_active``): the job fails with ``customer_disabled``;
* the two vector sweeps (``search.indexing.embed_missing`` and
  ``search.story_vectors.embed_missing_stories``) and ``cli reembed`` skip it.

The hourly Trash purge still runs for it: it deletes only, calls no model, and
a disabled customer's Trash is removed on time all the same.

* ``is_active`` and ``is_pending`` take a connection from ``Database.customer`` or
  ``Database.user``: the ``customers`` policy shows only that customer's row.
* ``set_status`` takes an OWNER connection (the CLI's login role with no SET
  ROLE, ``cli._owner``): ``kaagaz_app`` may update ``max_upload_mb`` only (0014),
  so no customer-side code can change a status, whatever its bug.

Each call is one or two statements on the primary key: O(1).
"""

from datetime import datetime

from sqlalchemy import Connection, text

ACTIVE = "active"
DISABLED = "disabled"
DELETING = "deleting"
PENDING = "pending"  # a sign-up waiting for an operator's Approve (0061)

_IS_ACTIVE = text("SELECT EXISTS (SELECT 1 FROM customers WHERE id = :c AND status = 'active')")
_IS_PENDING = text("SELECT EXISTS (SELECT 1 FROM customers WHERE id = :c AND status = 'pending')")
_LOCK = text("SELECT status FROM customers WHERE id = :c FOR UPDATE")
# disabled_at is set exactly when the status is not active (0014's CHECK).
_SET = text(
    "UPDATE customers SET status = :s,"
    " disabled_at = CASE WHEN :s = 'active' THEN NULL ELSE CAST(:now AS timestamptz) END"
    " WHERE id = :c"
)
# Disabling also ends every session of its users for good (as "Sign out everywhere"
# does), so enabling it again never brings an old cookie back.
_END_SESSIONS = text("UPDATE users SET session_version = session_version + 1 WHERE customer_id = :c")
# Enabling it again clears the sign-in failures counted while it was off: a right
# password is refused then, so a user who kept trying would stay locked out after.
_CLEAR_LOCKS = text("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE customer_id = :c")


def is_active(conn: Connection, customer_id: str) -> bool:
    """True when this customer exists and is active. False for a customer that is
    disabled, being deleted or gone. O(1): one primary-key lookup."""
    return bool(conn.execute(_IS_ACTIVE, {"c": customer_id}).scalar())


def is_pending(conn: Connection, customer_id: str) -> bool:
    """True while this customer is a sign-up waiting for the operator's Approve (0061). O(1)."""
    return bool(conn.execute(_IS_PENDING, {"c": customer_id}).scalar())


def set_status(conn: Connection, customer_id: str, status: str, *, now: datetime) -> str | None:
    """Make an active customer disabled, or a disabled one active, on an owner
    connection. Returns the status it had before: None when there is no such
    customer, and then nothing changes; nothing changes either when it already
    had ``status``, or when it is being deleted (cancelling a deletion is PR 4's), or while it
    is a pending sign-up (only the operator's Approve makes it active, 0061; disabling it
    first would route round that, 0063).
    The row is locked first, so two runs at once change it once. O(1), and
    disabling also raises every user's session version and enabling clears their sign-in
    locks: O(u) for its u users."""
    if status not in (ACTIVE, DISABLED):
        raise ValueError("status must be active or disabled")
    before = conn.execute(_LOCK, {"c": customer_id}).scalar()
    if before is None:
        return None
    if before not in (status, DELETING, PENDING):
        conn.execute(_SET, {"c": customer_id, "s": status, "now": now})
        conn.execute(_END_SESSIONS if status == DISABLED else _CLEAR_LOCKS, {"c": customer_id})
    return str(before)
