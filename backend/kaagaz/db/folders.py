"""Folders: one customer's tree, at most 5 levels deep (PRD 1 F1, WP1.2).

Why here: the tree rules live in SQL next to the data. Every function takes a
connection from ``Database.customer(customer_id)`` and also filters on
``customer_id`` (two locks, docs/architecture.md).

The tree is an adjacency list (``parent_id``). A top-level folder is at
depth 1; the deepest allowed is depth 5. Folders are few per customer, so
walking the tree with a recursive CTE is cheap. Every recursive CTE here is
bounded (``depth < 6``) and filters ``customer_id`` in both terms, so even a
damaged tree cannot make a statement loop.

Concurrency: create, move and delete first take the customer's tree lock
(``_lock_tree``: one row lock on ``customers``). Tree changes of one customer
therefore run one at a time, and every statement after the lock sees the tree
as the previous change committed it (READ COMMITTED takes a new snapshot per
statement). That is what stops two opposite moves from making a cycle. The
lock is FOR NO KEY UPDATE, so it never blocks the foreign-key checks (KEY
SHARE) of ordinary inserts such as uploads. Rename is one statement and needs
no lock: the unique index ``folders_sibling_name`` settles any race.

Errors carry the API code in ``code``. After ``FolderNameTaken`` or
``FolderNotEmpty`` a statement has failed, so the caller's transaction is
unusable and must end (leave the ``with`` block). The other errors are raised
before anything is written. The caller checks the name (1 to 100 characters).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, CursorResult, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.sqlstate import FOREIGN_KEY_VIOLATION, UNIQUE_VIOLATION, sqlstate

_MAX_DEPTH = 5


@dataclass(frozen=True, slots=True)
class FolderRow:
    id: str
    parent_id: str | None
    name: str
    created_at: datetime


class FolderError(Exception):
    """A folder change that is refused. ``code`` is the API error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FolderNotFound(FolderError):
    """No such folder (or parent) for this customer."""

    def __init__(self) -> None:
        super().__init__("not_found")


class FolderNameTaken(FolderError):
    """A folder with the same parent already has this name (any letter case)."""

    def __init__(self) -> None:
        super().__init__("conflict")


class FolderCycle(FolderError):
    """The move would put a folder inside itself or one of its own folders."""

    def __init__(self) -> None:
        super().__init__("folder_cycle")


class FolderTooDeep(FolderError):
    """The folder, or a folder inside it, would end up deeper than 5 levels."""

    def __init__(self) -> None:
        super().__init__("folder_too_deep")


class FolderNotEmpty(FolderError):
    """The folder still holds a folder or a document that is not in Trash."""

    def __init__(self) -> None:
        super().__init__("folder_not_empty")


_LOCK_TREE = "SELECT 1 FROM customers WHERE id = :c FOR NO KEY UPDATE"

_LIST = "SELECT id, parent_id, name, created_at FROM folders WHERE customer_id = :c ORDER BY lower(name), id"

_INSERT = (
    "INSERT INTO folders (customer_id, parent_id, name, created_by)"
    " VALUES (:c, :p, :name, :u)"
    " RETURNING id, parent_id, name, created_at"
)

_RENAME = (
    "UPDATE folders SET name = :name WHERE customer_id = :c AND id = :f"
    " RETURNING id, parent_id, name, created_at"
)

_MOVE = (
    "UPDATE folders SET parent_id = :p WHERE customer_id = :c AND id = :f"
    " RETURNING id, parent_id, name, created_at"
)

# The folder and its ancestors; depth 1 is the folder itself, so the largest
# depth is the folder's own depth in the tree, and the root comes last.
_PATH = (
    "WITH RECURSIVE up (id, parent_id, name, created_at, depth) AS ("
    " SELECT f.id, f.parent_id, f.name, f.created_at, 1 FROM folders f"
    " WHERE f.customer_id = :c AND f.id = :f"
    " UNION ALL"
    " SELECT f.id, f.parent_id, f.name, f.created_at, up.depth + 1 FROM up"
    " JOIN folders f ON f.id = up.parent_id"
    " WHERE f.customer_id = :c AND up.depth < 6)"
    " SELECT id, parent_id, name, created_at FROM up ORDER BY depth DESC"
)

_DEPTH = (
    "WITH RECURSIVE up (id, parent_id, depth) AS ("
    " SELECT f.id, f.parent_id, 1 FROM folders f WHERE f.customer_id = :c AND f.id = :f"
    " UNION ALL"
    " SELECT f.id, f.parent_id, up.depth + 1 FROM up"
    " JOIN folders f ON f.id = up.parent_id"
    " WHERE f.customer_id = :c AND up.depth < 6)"
    " SELECT max(depth) FROM up"
)

# The folder and everything inside it; depth 1 is the folder itself.
_DESCENDANTS = (
    "WITH RECURSIVE down (id, depth) AS ("
    " SELECT f.id, 1 FROM folders f WHERE f.customer_id = :c AND f.id = :f"
    " UNION ALL"
    " SELECT f.id, down.depth + 1 FROM down"
    " JOIN folders f ON f.parent_id = down.id"
    " WHERE f.customer_id = :c AND down.depth < 6)"
    " SELECT id FROM down ORDER BY depth, id"
)

# One statement for a move under :p: the target's depth (its ancestors), whether
# the moved folder :f is one of them (a cycle), and the height of :f's subtree.
# A NULL depth or height means that folder does not exist for this customer.
_MOVE_CHECK = (
    "WITH RECURSIVE up (id, parent_id, depth) AS ("
    " SELECT f.id, f.parent_id, 1 FROM folders f WHERE f.customer_id = :c AND f.id = :p"
    " UNION ALL"
    " SELECT f.id, f.parent_id, up.depth + 1 FROM up"
    " JOIN folders f ON f.id = up.parent_id"
    " WHERE f.customer_id = :c AND up.depth < 6),"
    " down (id, depth) AS ("
    " SELECT f.id, 1 FROM folders f WHERE f.customer_id = :c AND f.id = :f"
    " UNION ALL"
    " SELECT f.id, down.depth + 1 FROM down"
    " JOIN folders f ON f.parent_id = down.id"
    " WHERE f.customer_id = :c AND down.depth < 6)"
    " SELECT (SELECT max(depth) FROM up),"
    " EXISTS (SELECT 1 FROM up WHERE up.id = :f),"
    " (SELECT max(depth) FROM down)"
)

# Trashed documents keep their folder until they are restored or purged; a
# folder holding only those can still be deleted, and they go to the top level.
# The rows are locked in family order (each family's first version, id =
# family_id, then by id), the order every family lock uses, so a folder delete
# and a restore of one of its families queue instead of deadlocking. Like every
# write of folder_id, it clears the auto-filing state (feature 28, R3), so a family
# Kaagaz filed there is no longer "filed" and its Undo is refused.
_DETACH_TRASHED = (
    "UPDATE documents d SET folder_id = NULL, autofile = NULL FROM ("
    " SELECT id FROM documents WHERE customer_id = :c AND folder_id = :f AND deleted_at IS NOT NULL"
    " ORDER BY family_id, (id <> family_id), id FOR NO KEY UPDATE) AS l"
    " WHERE d.customer_id = :c AND d.id = l.id AND d.deleted_at IS NOT NULL"
)

_DELETE = "DELETE FROM folders WHERE customer_id = :c AND id = :f RETURNING id"


def _lock_tree(conn: Connection, customer_id: str) -> None:
    """Serialise this customer's tree changes until the transaction ends. O(1)."""
    conn.execute(text(_LOCK_TREE), {"c": customer_id})


def list_folders(conn: Connection, customer_id: str) -> list[FolderRow]:
    """Every folder of this customer, flat, by name. The client builds the tree.
    O(F log F) time, O(F) space for F folders."""
    return [_folder(row) for row in conn.execute(text(_LIST), {"c": customer_id})]


def create_folder(
    conn: Connection, customer_id: str, *, name: str, parent_id: str | None, created_by: str
) -> FolderRow:
    """Add a folder at the top level (``parent_id`` None) or inside ``parent_id``.

    Raises FolderNotFound (no such parent), FolderTooDeep (the parent is at
    depth 5) or FolderNameTaken. O(d log F) time for the parent's depth d <= 5,
    O(1) space.
    """
    _lock_tree(conn, customer_id)
    if parent_id is not None:
        parent_depth: int | None = conn.execute(text(_DEPTH), {"c": customer_id, "f": parent_id}).scalar_one()
        if parent_depth is None:
            raise FolderNotFound()
        if parent_depth >= _MAX_DEPTH:
            raise FolderTooDeep()
    params = {"c": customer_id, "p": parent_id, "name": name, "u": created_by}
    return _folder(_write_named(conn, _INSERT, params).one())


def rename_folder(conn: Connection, customer_id: str, folder_id: str, *, name: str) -> FolderRow:
    """Give a folder a new name. Raises FolderNotFound or FolderNameTaken.
    One statement: O(log F) time, O(1) space."""
    row = _write_named(conn, _RENAME, {"c": customer_id, "f": folder_id, "name": name}).first()
    if row is None:
        raise FolderNotFound()
    return _folder(row)


def move_folder(conn: Connection, customer_id: str, folder_id: str, *, parent_id: str | None) -> FolderRow:
    """Move a folder, with everything inside it, to the top level (``parent_id``
    None) or inside ``parent_id``.

    Raises FolderNotFound (the folder or the target), FolderCycle (the target is
    the folder or inside it), FolderTooDeep (depth(target) + height(subtree) > 5)
    or FolderNameTaken. One check statement: O((d + s) log F) time for the
    target's depth d <= 5 and the subtree's size s, O(d + s) space.
    """
    _lock_tree(conn, customer_id)
    if parent_id is not None:
        target_depth, cycle, height = conn.execute(
            text(_MOVE_CHECK), {"c": customer_id, "f": folder_id, "p": parent_id}
        ).one()
        if target_depth is None or height is None:
            raise FolderNotFound()
        if cycle:
            raise FolderCycle()
        if int(target_depth) + int(height) > _MAX_DEPTH:
            raise FolderTooDeep()
    row = _write_named(conn, _MOVE, {"c": customer_id, "f": folder_id, "p": parent_id}).first()
    if row is None:
        raise FolderNotFound()
    return _folder(row)


def delete_folder(conn: Connection, customer_id: str, folder_id: str) -> None:
    """Delete an empty folder. Trashed documents in it move to the top level first.

    Raises FolderNotFound, or FolderNotEmpty when a folder or a live document
    is still inside (the foreign keys refuse the delete, and the detach is
    rolled back with it). O(n log D + t log t + log F) time for the n documents
    filed in it and the t trashed ones it moves (n = t when the delete succeeds),
    O(t) space.
    """
    _lock_tree(conn, customer_id)
    params = {"c": customer_id, "f": folder_id}
    conn.execute(text(_DETACH_TRASHED), params)
    try:
        deleted = conn.execute(text(_DELETE), params).first()
    except IntegrityError as error:
        if sqlstate(error) == FOREIGN_KEY_VIOLATION:
            raise FolderNotEmpty() from None
        raise
    if deleted is None:
        raise FolderNotFound()


def folder_path(conn: Connection, customer_id: str, folder_id: str) -> list[FolderRow]:
    """The folder and its ancestors, root first; empty if there is no such folder.
    O(d log F) time and O(d) space for its depth d <= 5."""
    return [_folder(row) for row in conn.execute(text(_PATH), {"c": customer_id, "f": folder_id})]


def descendant_ids(conn: Connection, customer_id: str, folder_id: str) -> list[str]:
    """The folder's id and every folder id inside it, level by level; empty if
    there is no such folder. O(s log F) time and O(s) space for the subtree size s."""
    rows: Sequence[object] = (
        conn.execute(text(_DESCENDANTS), {"c": customer_id, "f": folder_id}).scalars().all()
    )
    return [str(folder) for folder in rows]


def _write_named(conn: Connection, statement: str, params: dict[str, Any]) -> CursorResult[Any]:
    """Run an insert or update that can clash with a sibling's name."""
    try:
        return conn.execute(text(statement), params)
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION:
            raise FolderNameTaken() from None
        raise


def _folder(row: Sequence[Any]) -> FolderRow:
    return FolderRow(
        id=str(row[0]),
        parent_id=None if row[1] is None else str(row[1]),
        name=str(row[2]),
        created_at=row[3],
    )
