"""Trash: a document family goes to Trash together and comes back together (PRD 1 F5, WP1.2).

Why here: the family lock and the trash SQL in one place. Every function
except ``customer_ids_with_trash`` takes a connection from
``Database.customer(customer_id)`` and also filters on ``customer_id``.

The family is the unit (plan 9.3): every version of a document shares its
``family_id``, its folder, its tags and its place in Trash. ``lock_family`` is
the one lock for a family, taken first by trash, restore, permanent delete,
the version insert, ``mark_ready`` and the folder and tag writes. It locks the
family's first version (id = family_id) first, then the rest by id. That row
never changes, so two lockers of one family always queue on it instead of
deadlocking, even when a new version arrives while one of them waits.

Time is always passed in (``now``, ``before``) from the injected clock, never
read with SQL ``now()``, so tests move the clock instead of waiting.

The visibility fragments (``kaagaz.db.visibility``) are written literally in
the statements below: ``LIVE`` in ``_TRASH``, ``TRASHED`` in the others.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, text

_LOCK_FAMILY = (
    "SELECT family_id FROM documents WHERE customer_id = :c"
    " AND family_id = (SELECT family_id FROM documents WHERE customer_id = :c AND id = :d)"
    " ORDER BY (id <> family_id), id FOR NO KEY UPDATE"
)

# A family already in Trash keeps its first deleted_at, so the purge clock is
# not reset by a second delete.
_TRASH = (
    "UPDATE documents d SET deleted_at = :now, deleted_by = :by"
    " WHERE d.customer_id = :c AND d.family_id = :f AND d.deleted_at IS NULL"
)

# #537: _TRASH for every family filed in these folders at once. The rows are locked in family
# order (each family's first version, then by id, as _LOCK_FAMILY), every family together.
# #542: each keeps its folder as trashed_folder_id (its folder goes to trashed_folders) and the
# delete's trash_id, out of every folder; like every write of folder_id, auto-filing's state goes.
_TRASH_IN_FOLDERS = (
    "UPDATE documents d SET deleted_at = :now, deleted_by = :by, trashed_folder_id = d.folder_id,"
    " trash_id = CAST(:trash AS uuid), folder_id = NULL, autofile = NULL FROM ("
    " SELECT id FROM documents WHERE customer_id = :c AND folder_id = ANY(CAST(:folders AS uuid[]))"
    " ORDER BY family_id, (id <> family_id), id FOR NO KEY UPDATE) AS l"
    " WHERE d.customer_id = :c AND d.id = l.id AND d.deleted_at IS NULL RETURNING d.family_id"
)

# #542: never a family that went to Trash with its folder (trashed_folders.restore_document's).
# #594: a version in no folder goes into :into, or is held for :by (one already held keeps its
# holder); a version whose folder is live stays in it.
_RESTORE = (
    "UPDATE documents d SET deleted_at = NULL, deleted_by = NULL,"
    " folder_id = CASE WHEN d.folder_id IS NULL THEN CAST(:into AS uuid) ELSE d.folder_id END,"
    " held_for = CASE WHEN d.folder_id IS NULL AND :hold AND d.held_for IS NULL THEN CAST(:by AS uuid)"
    " ELSE d.held_for END"
    " WHERE d.customer_id = :c AND d.family_id = :f AND d.deleted_at IS NOT NULL"
    " AND d.trashed_folder_id IS NULL"
)

# #594: what a family in Trash and in no folder kept of its folder (0085 froze it there).
_KEPT_PLACE = (
    "SELECT DISTINCT department_id, private_folder_id FROM documents WHERE customer_id = :c"
    " AND family_id = :f AND deleted_at IS NOT NULL AND folder_id IS NULL"
)
# #594: which of these folders are live, FOR SHARE: the CLI's prune-folders takes no tree lock, so
# it waits for this restore instead of deleting one under it. One constant, for PR 9's owner-rights
# functions to reuse.
LIVE_FOLDERS = "SELECT id FROM folders WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[])) FOR SHARE"
# PRD 7 (#606): live is asked of the whole table (kaagaz_existing, 0091): under the database rule a
# folder the person can't see is missing from folders, and would read as gone.
_EXISTING = "SELECT id FROM kaagaz_existing('folder', CAST(:ids AS uuid[])) AS e (id)"


def live_folders(conn: Connection, customer_id: str, folder_ids: Sequence[str]) -> set[str]:
    """Which of these folders are live (``LIVE_FOLDERS``, which locks those the person can see).
    shortcut: one they can't see is not locked, so the CLI's prune-folders could delete it meanwhile,
    and then the write that puts something in it is refused by its key. Two statements."""
    params = {"c": customer_id, "ids": list(folder_ids)}
    conn.execute(text(LIVE_FOLDERS), params).all()
    return {str(f) for f in conn.execute(text(_EXISTING), params).scalars()}


_CUSTOMERS_WITH_TRASH = "SELECT t.id FROM kaagaz_customer_ids_with_trash(:before) AS t (id) ORDER BY t.id"

# One family whose trash is older than :before, picked by its first version
# (id = family_id) with SKIP LOCKED: every family locker takes that row first,
# so a family someone holds is skipped, and nobody holding another of its rows
# can be waiting on us.
_PURGE_PICK = (
    "SELECT d.family_id FROM documents d"
    " WHERE d.customer_id = :c AND d.id = d.family_id"
    " AND d.deleted_at IS NOT NULL AND d.deleted_at < :before"
    " ORDER BY d.deleted_at, d.id LIMIT 1 FOR UPDATE SKIP LOCKED"
)

# The rest of the picked family, in lock order. Only trashed rows are returned,
# so a live row can never be handed to the caller for deletion.
_PURGE_LOCK = (
    "SELECT d.id FROM documents d"
    " WHERE d.customer_id = :c AND d.family_id = :f AND d.deleted_at IS NOT NULL"
    " ORDER BY (d.id <> d.family_id), d.id FOR UPDATE"
)


def lock_family(conn: Connection, customer_id: str, document_id: str) -> str | None:
    """Lock every version of ``document_id``'s family until the transaction ends,
    first version first, and return the family_id; None if there is no such document for
    this customer. O(v log D) time and O(v) space for the family's v versions."""
    families: Sequence[object] = (
        conn.execute(text(_LOCK_FAMILY), {"c": customer_id, "d": document_id}).scalars().all()
    )
    return str(families[0]) if families else None


class InTrash(Exception):
    """The document's family is in Trash. Trash means deleted: it can't be renamed,
    moved, tagged or given a new version, only restored or deleted forever."""


_FAMILY_IN_TRASH = (
    "SELECT EXISTS (SELECT 1 FROM documents WHERE customer_id = :c AND family_id = :f"
    " AND deleted_at IS NOT NULL)"
)


def lock_live_family(conn: Connection, customer_id: str, document_id: str) -> str | None:
    """``lock_family``, then refuse a family in Trash (raises InTrash). The check is
    made under the lock, so a trash can't slip in between. None if there is no
    such document. O(v log D) time and O(v) space."""
    family_id = lock_family(conn, customer_id, document_id)
    if family_id is None:
        return None
    if conn.execute(text(_FAMILY_IN_TRASH), {"c": customer_id, "f": family_id}).scalar():
        raise InTrash(document_id)
    return family_id


def trash_document(conn: Connection, customer_id: str, document_id: str, *, by: str, now: datetime) -> bool:
    """Move the document's whole family to Trash at ``now``, by user ``by``.
    False if there is no such document. A family already in Trash is left as it
    was (still True). O(v log D) time, O(v) space."""
    family_id = lock_family(conn, customer_id, document_id)
    if family_id is None:
        return False
    conn.execute(text(_TRASH), {"c": customer_id, "f": family_id, "by": by, "now": now})
    return True


def trash_in_folders(
    conn: Connection, customer_id: str, folder_ids: Sequence[str], *, by: str, now: datetime, trash_id: str
) -> int:
    """``trash_document`` for every family filed in ``folder_ids`` (a folder deleted with its
    files, #537), in one statement instead of two a family: every version of a family shares
    its folder, so each family goes whole, and one already in Trash keeps its first time (and
    its folder). Each family sent now keeps its folder as ``trashed_folder_id`` with
    ``trash_id``, and leaves it (#542: the folder goes to ``trashed_folders``). Returns how
    many families went to Trash now. O(n log D) time and O(n) space for the n documents filed
    there.

    shortcut: the caller writes one folder.delete audit row with the count (#537's rule), no
    document.trash row a family, so a family's own timeline does not show this trash."""
    params = {"c": customer_id, "folders": list(folder_ids), "by": by, "now": now, "trash": trash_id}
    return len(set(conn.execute(text(_TRASH_IN_FOLDERS), params).scalars()))


@dataclass(frozen=True, slots=True)
class Placed:
    """Where a restored family went when its folder was gone (#594): ``into`` the folder it went
    into, or ``held`` for the restorer; neither when it went back where it was (or stayed in no
    folder, open to everyone as it was)."""

    into: str | None = None
    held: bool = False


def place_out_of_folder(
    conn: Connection, customer_id: str, department_id: str | None, private_folder_id: str | None
) -> Placed:
    """Where a file whose folder is gone goes back (#594), from the department and private folder
    it kept: its private folder while that is live, else held (the top of its department would
    show it to more people, Q1); with no private folder, the top of its department while that is
    live, else held; with neither, no folder, as it was. A folder in Trash is not live. The caller
    holds the tree lock and the family's. One statement."""
    kept = [f for f in (private_folder_id, department_id) if f is not None]
    if not kept:
        return Placed()
    return Placed(into=kept[0]) if kept[0] in live_folders(conn, customer_id, kept) else Placed(held=True)


def place_family(conn: Connection, customer_id: str, family_id: str) -> Placed:
    """``place_out_of_folder`` for a family (locked already) in Trash and in no folder; versions
    that disagree are held (fail closed). One or two statements."""
    kept = conn.execute(text(_KEPT_PLACE), {"c": customer_id, "f": family_id}).all()
    if len(kept) > 1:
        return Placed(held=True)
    if not kept:
        return Placed()
    department_id, private_folder_id = (None if v is None else str(v) for v in kept[0])
    return place_out_of_folder(conn, customer_id, department_id, private_folder_id)


def restore_document(conn: Connection, customer_id: str, document_id: str, *, by: str) -> Placed | None:
    """Bring the document's whole family back from Trash, by user ``by``, into the folder it was
    in (one in Trash with it is ``trashed_folders.restore_document``'s). #594: when that folder
    is gone (a folder delete emptied its link), ``place_family`` says where: never somewhere
    more people see it. None if there is no such document; a live family is left as it was.
    The caller holds the tree lock (0085). O(v log D) time, O(v) space."""
    family_id = lock_family(conn, customer_id, document_id)
    if family_id is None:
        return None
    placed = place_family(conn, customer_id, family_id)
    params = {"c": customer_id, "f": family_id, "into": placed.into, "hold": placed.held, "by": by}
    conn.execute(text(_RESTORE), params)
    return placed


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
    document ids, first version first; empty when there is nothing to purge.

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
