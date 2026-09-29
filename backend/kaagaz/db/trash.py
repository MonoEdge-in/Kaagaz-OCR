"""Trash: a document family goes to Trash together and comes back together (PRD 1 F5, WP1.2).

Why here: the family lock and the trash SQL in one place. Every function
except ``customer_ids_with_trash`` takes a connection from
``Database.customer(customer_id)`` and also filters on ``customer_id``.

The family is the unit (plan 9.3): every version of a document shares its
``family_id``, its folder, its tags and its place in Trash. ``lock_family`` is
the one lock for a family, taken first by trash, restore, permanent delete,
the version insert, ``mark_ready`` and the folder and tag writes. It locks the
rows in id order, so two lockers of one family always queue instead of
deadlocking.

Time is always passed in (``now``, ``before``) from the injected clock, never
read with SQL ``now()``, so tests move the clock instead of waiting.

The visibility fragments (``kaagaz.db.visibility``) are written literally in
the statements below: ``LIVE`` in ``_TRASH``, ``TRASHED`` in the others.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, text

_LOCK_FAMILY = (
    "SELECT family_id FROM documents WHERE customer_id = :c"
    " AND family_id = (SELECT family_id FROM documents WHERE customer_id = :c AND id = :d)"
    " ORDER BY id FOR NO KEY UPDATE"
)

# A family already in Trash keeps its first deleted_at, so the purge clock is
# not reset by a second delete.
_TRASH = (
    "UPDATE documents d SET deleted_at = :now, deleted_by = :by"
    " WHERE d.customer_id = :c AND d.family_id = :f AND d.deleted_at IS NULL"
)

_RESTORE = (
    "UPDATE documents d SET deleted_at = NULL, deleted_by = NULL"
    " WHERE d.customer_id = :c AND d.family_id = :f AND d.deleted_at IS NOT NULL"
)

_CUSTOMERS_WITH_TRASH = "SELECT t.id FROM kaagaz_customer_ids_with_trash(:before) AS t (id) ORDER BY t.id"

# One family whose trash is older than :before. Only the family's first row in
# lock order (the smallest id) can be picked, and it is taken with SKIP LOCKED:
# every family locker takes that row first, so a family someone holds is
# skipped, and nobody holding another of its rows can be waiting on us.
_PURGE_PICK = (
    "SELECT d.family_id FROM documents d"
    " WHERE d.customer_id = :c AND d.deleted_at IS NOT NULL AND d.deleted_at < :before"
    " AND NOT EXISTS (SELECT 1 FROM documents o"
    " WHERE o.customer_id = :c AND o.family_id = d.family_id AND o.id < d.id)"
    " ORDER BY d.deleted_at, d.id LIMIT 1 FOR UPDATE OF d SKIP LOCKED"
)

# The rest of the picked family, in lock order. Only trashed rows are returned,
# so a live row can never be handed to the caller for deletion.
_PURGE_LOCK = (
    "SELECT d.id FROM documents d"
    " WHERE d.customer_id = :c AND d.family_id = :f AND d.deleted_at IS NOT NULL"
    " ORDER BY d.id FOR UPDATE"
)


def lock_family(conn: Connection, customer_id: str, document_id: str) -> str | None:
    """Lock every version of ``document_id``'s family until the transaction ends,
    in id order, and return the family_id; None if there is no such document for
    this customer. O(v log D) time and O(v) space for the family's v versions."""
    families: Sequence[object] = (
        conn.execute(text(_LOCK_FAMILY), {"c": customer_id, "d": document_id}).scalars().all()
    )
    return str(families[0]) if families else None


def trash_document(conn: Connection, customer_id: str, document_id: str, *, by: str, now: datetime) -> bool:
    """Move the document's whole family to Trash at ``now``, by user ``by``.
    False if there is no such document. A family already in Trash is left as it
    was (still True). O(v log D) time, O(v) space."""
    family_id = lock_family(conn, customer_id, document_id)
    if family_id is None:
        return False
    conn.execute(text(_TRASH), {"c": customer_id, "f": family_id, "by": by, "now": now})
    return True


def restore_document(conn: Connection, customer_id: str, document_id: str) -> bool:
    """Bring the document's whole family back from Trash, into the folder it was
    in (a deleted folder has already moved it to the top level). False if there
    is no such document; a live family is left as it was (still True).
    O(v log D) time, O(v) space."""
    family_id = lock_family(conn, customer_id, document_id)
    if family_id is None:
        return False
    conn.execute(text(_RESTORE), {"c": customer_id, "f": family_id})
    return True


def customer_ids_with_trash(conn: Connection, *, before: datetime) -> list[str]:
    """The customers with anything that went to Trash before ``before``, for the purge.

    ``conn`` comes from ``Database.system()``: row level security hides every
    customer there, so this calls the SECURITY DEFINER function
    ``kaagaz_customer_ids_with_trash``, which returns ids only. O(T) time over
    all T trashed rows, O(C) space for the C customers returned.
    """
    rows: Sequence[object] = conn.execute(text(_CUSTOMERS_WITH_TRASH), {"before": before}).scalars().all()
    return [str(customer) for customer in rows]


def purge_next_family(conn: Connection, customer_id: str, *, before: datetime) -> list[str]:
    """Lock the family longest in Trash (trashed before ``before``) and return its
    document ids, smallest first; empty when there is nothing to purge.

    A family another transaction holds is skipped, never waited for. The caller
    deletes the storage and the rows of exactly these ids in this same
    transaction: one family per transaction. O(t + v log D) time for the t
    trashed rows looked at before an unlocked family, O(v) space.
    """
    family_id = conn.execute(text(_PURGE_PICK), {"c": customer_id, "before": before}).scalar()
    if family_id is None:
        return []
    rows: Sequence[object] = (
        conn.execute(text(_PURGE_LOCK), {"c": customer_id, "f": family_id}).scalars().all()
    )
    return [str(document) for document in rows]
