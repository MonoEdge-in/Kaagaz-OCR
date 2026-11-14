"""Folders: one customer's tree, at most 100 levels deep (PRD 1 F1, WP1.2; #484).

Why here: the tree rules live in SQL next to the data. Every function takes a
connection from ``Database.customer(customer_id)`` and also filters on
``customer_id`` (two locks, docs/architecture.md).

The tree is an adjacency list (``parent_id``). A top-level folder is at
depth 1; the deepest allowed is depth 100 (Tushar, 3 Nov 2026: any depth, like
Google Drive, whose own limit is 100; it was 5). A customer may hold tens of
thousands of folders, 100 levels deep; every walk is on an index (up: the
primary key, down: ``folders_sibling_name``'s prefix) and bounded, O(depth) up
and O(subtree) down. Every recursive CTE here is bounded (``depth < 101``) and
filters ``customer_id`` in both terms, so even a damaged tree cannot make a
statement loop.

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

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Connection, CursorResult, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db import audit, trash
from kaagaz.db.sqlstate import CHECK_VIOLATION, FOREIGN_KEY_VIOLATION, UNIQUE_VIOLATION, sqlstate

MAX_DEPTH = 100  # Tushar, 3 Nov 2026 (#484): was 5
NAME_MAX = 100  # folders.name's CHECK (0002)
# #537: a folder upload with nothing sent or registered this long is not running (a closed tab).
# shortcut: a guess; an upload left open that long and continued after the delete sends its
# files into folders that are gone, which fail folder_gone (or a re-plan makes them again).
UPLOAD_QUIET_HOURS = 1


@dataclass(frozen=True, slots=True)
class FolderRow:
    id: str
    parent_id: str | None
    name: str
    created_at: datetime
    kind: str = "custom"  # File management F1 (0041)
    sort_order: int | None = None


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
    """The folder, or a folder inside it, would end up deeper than 100 levels."""

    def __init__(self) -> None:
        super().__init__("folder_too_deep")


class FolderNotEmpty(FolderError):
    """The folder still holds a folder or a document that is not in Trash."""

    def __init__(self) -> None:
        super().__init__("folder_not_empty")


class FolderUploadRunning(FolderError):
    """A folder upload into the folder or one inside it still has files waiting (#537)."""

    def __init__(self) -> None:
        super().__init__("folder_upload_running")


class FolderChanged(FolderError):
    """A file or folder arrived in the folder while it was being deleted with its contents (#537);
    nothing was deleted."""

    def __init__(self) -> None:
        super().__init__("folder_changed")


class DestinationNotFound(FolderNotFound):
    """The folder chosen to take what is inside is not there for this customer (#537)."""


@dataclass(frozen=True, slots=True)
class FolderSummary:
    """What a folder holds, at every depth (#537)."""

    files: int  # live document families (a file with three versions is one)
    folders: int  # the folders inside it
    bytes: int  # every live version's size
    trash_id: str | None = None  # #542: the delete that sent it to Trash, with contents=trash


class DepartmentNotTopLevel(FolderError):
    """A department is a top-level folder only (0041's CHECK)."""

    def __init__(self) -> None:
        super().__init__("department_top_level")


_LOCK_TREE = "SELECT 1 FROM customers WHERE id = :c FOR NO KEY UPDATE"

# PRD 7 (#599): only the folders the person may see; one whose parent they can't see is at the top
# level for them, so no hidden folder's id reaches the tree.
_LIST = (
    "SELECT f.id, p.id AS parent_id, f.name, f.created_at, f.kind, f.sort_order FROM visible_folders f"
    " LEFT JOIN visible_folders p ON p.customer_id = f.customer_id AND p.id = f.parent_id"
    " WHERE f.customer_id = :c ORDER BY lower(f.name), f.id"
)

_INSERT = (
    "INSERT INTO folders (customer_id, parent_id, name, created_by, kind, sort_order)"
    " VALUES (:c, :p, :name, :u, :kind, :sort)"
    " RETURNING id, parent_id, name, created_at, kind, sort_order"
)

# File management F1: a folder's kind and the department order, each only when given.
_SET_KIND = (
    "UPDATE folders SET kind = CASE WHEN :set_kind THEN :kind ELSE kind END,"
    " sort_order = CASE WHEN :set_sort THEN CAST(:sort AS integer) ELSE sort_order END"
    " WHERE customer_id = :c AND id = :f RETURNING id, parent_id, name, created_at, kind, sort_order"
)

_RENAME = (
    "UPDATE folders SET name = :name WHERE customer_id = :c AND id = :f RETURNING "
    "id, parent_id, name, created_at, kind, sort_order"
)

_MOVE = (
    "UPDATE folders SET parent_id = :p WHERE customer_id = :c AND id = :f RETURNING "
    "id, parent_id, name, created_at, kind, sort_order"
)

# The folder and its ancestors; depth 1 is the folder itself, so the largest
# depth is the folder's own depth in the tree, and the root comes last.
_PATH = (
    "WITH RECURSIVE up (id, parent_id, name, created_at, kind, sort_order, depth) AS ("
    " SELECT f.id, f.parent_id, f.name, f.created_at, f.kind, f.sort_order, 1 FROM folders f"
    " WHERE f.customer_id = :c AND f.id = :f"
    " UNION ALL"
    " SELECT f.id, f.parent_id, f.name, f.created_at, f.kind, f.sort_order, up.depth + 1 FROM up"
    " JOIN folders f ON f.id = up.parent_id"
    " WHERE f.customer_id = :c AND up.depth < 101)"
    " SELECT id, parent_id, name, created_at, kind, sort_order FROM up ORDER BY depth DESC"
)

_DEPTH = (
    "WITH RECURSIVE up (id, parent_id, depth) AS ("
    " SELECT f.id, f.parent_id, 1 FROM folders f WHERE f.customer_id = :c AND f.id = :f"
    " UNION ALL"
    " SELECT f.id, f.parent_id, up.depth + 1 FROM up"
    " JOIN folders f ON f.id = up.parent_id"
    " WHERE f.customer_id = :c AND up.depth < 101)"
    " SELECT max(depth) FROM up"
)

# The folder and everything inside it; depth 1 is the folder itself.
_DESCENDANTS = (
    "WITH RECURSIVE down (id, depth) AS ("
    " SELECT f.id, 1 FROM folders f WHERE f.customer_id = :c AND f.id = :f"
    " UNION ALL"
    " SELECT f.id, down.depth + 1 FROM down"
    " JOIN folders f ON f.parent_id = down.id"
    " WHERE f.customer_id = :c AND down.depth < 101)"
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
    " WHERE f.customer_id = :c AND up.depth < 101),"
    " down (id, depth) AS ("
    " SELECT f.id, 1 FROM folders f WHERE f.customer_id = :c AND f.id = :f"
    " UNION ALL"
    " SELECT f.id, down.depth + 1 FROM down"
    " JOIN folders f ON f.parent_id = down.id"
    " WHERE f.customer_id = :c AND down.depth < 101)"
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
    " SELECT id FROM documents WHERE customer_id = :c AND folder_id = ANY(CAST(:ids AS uuid[]))"
    " AND deleted_at IS NOT NULL"
    " ORDER BY family_id, (id <> family_id), id FOR NO KEY UPDATE) AS l"
    " WHERE d.customer_id = :c AND d.id = l.id AND d.deleted_at IS NOT NULL"
)

_DELETE = "DELETE FROM folders WHERE customer_id = :c AND id = :f RETURNING id, parent_id"
_PARENT = "SELECT parent_id FROM folders WHERE customer_id = :c AND id = :f"

# --- a folder deleted with what is inside it (#537) ---
# The subtree's size: the folders in it (the folder itself too), its live files (families) and
# their bytes (every live version), on documents_folder.
_SUMMARY = (
    "WITH RECURSIVE down (id, depth) AS ("
    " SELECT f.id, 1 FROM folders f WHERE f.customer_id = :c AND f.id = :f"
    " UNION ALL"
    " SELECT f.id, down.depth + 1 FROM down"
    " JOIN folders f ON f.parent_id = down.id"
    " WHERE f.customer_id = :c AND down.depth < 101),"
    " live AS (SELECT d.family_id, d.size_bytes FROM documents d WHERE d.customer_id = :c"
    " AND d.deleted_at IS NULL AND d.folder_id IN (SELECT id FROM down))"
    " SELECT (SELECT count(*) FROM down), (SELECT count(DISTINCT family_id) FROM live),"
    " (SELECT coalesce(sum(size_bytes), 0) FROM live)"
)
# PRD 7 (#599): the same, of what the person may see: a hidden folder is not there (0 folders), and
# its files and the hidden ones inside a visible folder are not counted. An Admin's delete dialog
# reads the true totals (_SUMMARY, PRD 7 section 5).
_SUMMARY_SEEN = _SUMMARY.replace(" folders f", " visible_folders f").replace(
    " documents d", " visible_documents d"
)
# A folder upload (a zip the worker unpacks too) whose plan holds one of :ids, that still has a
# file waiting, and that planned, registered or sent something since :since: the uploads with
# one waiting first, on folder_upload_items_state, then their plan on folder_upload_folders_folder.
_UPLOAD_RUNNING = (
    "SELECT EXISTS (SELECT 1 FROM folder_uploads u WHERE u.customer_id = :c"
    " AND EXISTS (SELECT 1 FROM folder_upload_items i WHERE i.customer_id = :c"
    " AND i.folder_upload_id = u.id AND i.state = 'waiting')"
    " AND (u.updated_at > :since OR EXISTS (SELECT 1 FROM folder_upload_items r WHERE r.customer_id = :c"
    " AND r.folder_upload_id = u.id AND r.updated_at > :since))"
    " AND EXISTS (SELECT 1 FROM folder_upload_folders m WHERE m.customer_id = :c"
    " AND m.folder_upload_id = u.id AND m.folder_id = ANY(CAST(:ids AS uuid[]))))"
)
# The deleted folder steps out of its siblings' names first, so a folder inside it may take its
# name where they meet. Its id is its name only inside this transaction (36 characters).
_NAME_AWAY = "UPDATE folders SET name = id::text WHERE customer_id = :c AND id = :f"
# Every document directly in :f, Trash included, into :p; locked in family order as
# _DETACH_TRASHED, and the auto-filing state cleared as every write of folder_id does.
_MOVE_DOCUMENTS = (
    "UPDATE documents d SET folder_id = CAST(:p AS uuid), autofile = NULL FROM ("
    " SELECT id FROM documents WHERE customer_id = :c AND folder_id = :f"
    " ORDER BY family_id, (id <> family_id), id FOR NO KEY UPDATE) AS l"
    " WHERE d.customer_id = :c AND d.id = l.id"
)
# A13: the trashed folders that were directly in :f now restore into :p (0082's column grant).
_REPARENT_TRASHED = (
    "UPDATE trashed_folders SET parent_id = CAST(:p AS uuid) WHERE customer_id = :c AND parent_id = :f"
)
# The folders directly in :f whose name nothing at :p has (as _FREE compares), in one statement.
_MOVE_FREE = (
    "UPDATE folders ch SET parent_id = CAST(:p AS uuid) WHERE ch.customer_id = :c AND ch.parent_id = :f"
    " AND NOT EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = :c AND {where}"
    " AND lower(normalize(f.name, NFC)) = lower(normalize(ch.name, NFC)))"
)
_LEFT = "SELECT id, name FROM folders WHERE customer_id = :c AND parent_id = :f ORDER BY lower(name), id"
_MOVE_RENAMED = (
    "UPDATE folders SET parent_id = CAST(:p AS uuid), name = :name WHERE customer_id = :c AND id = :id"
)
_DELETE_ALL = "DELETE FROM folders WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
# #542: the subtree into Trash as it is, every column folders has, under one trash_id; :f is the root.
_TRASH_TREE = (
    "INSERT INTO trashed_folders (id, customer_id, parent_id, name, kind, sort_order, autofile_key,"
    " created_by, created_at, trash_id, root, deleted_at, deleted_by, department_only, private,"
    " department_id, private_folder_id)"
    " SELECT f.id, f.customer_id, f.parent_id, f.name, f.kind, f.sort_order, f.autofile_key, f.created_by,"
    " f.created_at, CAST(:trash AS uuid), f.id = :f, :now, :by, f.department_only, f.private,"
    " f.department_id, f.private_folder_id"
    " FROM folders f WHERE f.customer_id = :c AND f.id = ANY(CAST(:ids AS uuid[]))"
)
# The first candidate name no sibling has, compared as folders_sibling_name compares (lower()), NFC.
_FREE = (
    "SELECT w.n FROM unnest(CAST(:names AS text[])) WITH ORDINALITY AS w (n, i)"
    " WHERE NOT EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = :c AND {where}"
    " AND lower(normalize(f.name, NFC)) = lower(normalize(w.n, NFC))) ORDER BY w.i LIMIT 1"
)


def _lock_tree(conn: Connection, customer_id: str) -> None:
    """Serialise this customer's tree changes until the transaction ends. O(1)."""
    conn.execute(text(_LOCK_TREE), {"c": customer_id})


def list_folders(conn: Connection, customer_id: str) -> list[FolderRow]:
    """Every folder of this customer, flat, by name. The client builds the tree.
    O(F log F) time, O(F) space for F folders."""
    return [_folder(row) for row in conn.execute(text(_LIST), {"c": customer_id})]


def create_folder(
    conn: Connection,
    customer_id: str,
    *,
    name: str,
    parent_id: str | None,
    created_by: str,
    kind: str = "custom",
    sort_order: int | None = None,
) -> FolderRow:
    """Add a folder at the top level (``parent_id`` None) or inside ``parent_id``.

    Raises FolderNotFound (no such parent), FolderTooDeep (the parent is at
    depth 100), DepartmentNotTopLevel or FolderNameTaken. O(d log F) time for the
    parent's depth d <= 100, O(1) space.
    """
    if kind == "department" and parent_id is not None:
        raise DepartmentNotTopLevel()
    _lock_tree(conn, customer_id)
    if parent_id is not None:
        parent_depth: int | None = conn.execute(text(_DEPTH), {"c": customer_id, "f": parent_id}).scalar_one()
        if parent_depth is None:
            raise FolderNotFound()
        if parent_depth >= MAX_DEPTH:
            raise FolderTooDeep()
    params = {
        "c": customer_id,
        "p": parent_id,
        "name": name,
        "u": created_by,
        "kind": kind,
        "sort": sort_order,
    }
    return _folder(_write_named(conn, _INSERT, params).one())


def set_kind(
    conn: Connection,
    customer_id: str,
    folder_id: str,
    *,
    kind: str | None,
    sort_order: int | None,
    order: bool,
) -> FolderRow:
    """Set the folder's kind (None: keep it) and, with ``order``, its ``sort_order``.
    Raises FolderNotFound or DepartmentNotTopLevel. One statement, O(log F)."""
    params = {
        "c": customer_id,
        "f": folder_id,
        "set_kind": kind is not None,
        "kind": kind,
        "set_sort": order,
        "sort": sort_order,
    }
    row = _write_named(conn, _SET_KIND, params).first()
    if row is None:
        raise FolderNotFound()
    return _folder(row)


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
    the folder or inside it), FolderTooDeep (depth(target) + height(subtree) > 100)
    or FolderNameTaken. One check statement: O((d + s) log F) time for the
    target's depth d <= 100 and the subtree's size s, O(d + s) space.
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
        if int(target_depth) + int(height) > MAX_DEPTH:
            raise FolderTooDeep()
    before = conn.execute(text(_PARENT), {"c": customer_id, "f": folder_id}).scalar()
    row = _write_named(conn, _MOVE, {"c": customer_id, "f": folder_id, "p": parent_id}).first()
    if row is None:
        raise FolderNotFound()
    if before is not None and str(before) != parent_id:
        prune_empty(conn, customer_id, [str(before)])
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
    conn.execute(text(_DETACH_TRASHED), {**params, "ids": [folder_id]})
    try:
        deleted = conn.execute(text(_DELETE), params).first()
    except IntegrityError as error:
        if sqlstate(error) == FOREIGN_KEY_VIOLATION:
            raise FolderNotEmpty() from None
        raise
    if deleted is None:
        raise FolderNotFound()
    prune_empty(conn, customer_id, [None if deleted[1] is None else str(deleted[1])])


def folder_summary(
    conn: Connection, customer_id: str, folder_id: str, *, seen_only: bool = False
) -> FolderSummary:
    """What the folder holds at every depth (FolderSummary); with ``seen_only`` only what the
    person may see (PRD 7). Raises FolderNotFound (with ``seen_only`` a hidden folder too).
    One statement, O(s log F + n log D) for the s folders and n documents of its subtree."""
    statement = _SUMMARY_SEEN if seen_only else _SUMMARY
    count, files, size = conn.execute(text(statement), {"c": customer_id, "f": folder_id}).one()
    if not count:
        raise FolderNotFound()
    return FolderSummary(files=int(files), folders=int(count) - 1, bytes=int(size))


def delete_folder_with_contents(
    conn: Connection,
    customer_id: str,
    folder_id: str,
    *,
    contents: Literal["keep", "trash"],
    to: str | None,
    by: str,
    now: datetime,
) -> FolderSummary:
    """Delete a folder that may hold files and folders (#537); returns what it held.

    ``keep``: the documents directly in it (Trash included) and its folders, with all inside
    them, move into ``to`` (None: the top level), and a folder of it in Trash is restored there
    (A13), checked as ``move_folder`` checks a move; a
    folder of the same name there is never merged: the moved one becomes "Name (2)", or the
    first free number. ``trash``: every family in the folder and below it goes to Trash
    (``trash.trash_in_folders``: all versions, restorable, purged after TRASH_DAYS), and the
    folder and every folder inside it go to ``trashed_folders`` under one new ``trash_id``
    (#542: restored as they were by ``kaagaz.db.trashed_folders``); a family already in Trash
    goes to the top level, as a deleted folder's trashed documents always have. A document still
    being read goes too, as with a single delete.

    Raises FolderNotFound, DestinationNotFound (``to``), FolderCycle (``to`` is the folder or
    inside it), FolderTooDeep, FolderUploadRunning (a folder upload into what is deleted still has
    files waiting and was active in the last UPLOAD_QUIET_HOURS: with keep, the folder itself, as
    its folders only move), FolderNameTaken (no free number up to 100) or FolderChanged (a file
    arrived meanwhile: a document insert takes no tree lock). Nothing is written before
    the checks; a refusal after a write leaves the caller's transaction to roll back. ``files``
    in what it returns is, with trash, the families that went to Trash now, and ``trash_id`` is set.
    O(s log F + n log D)
    for the s folders and n documents of the subtree, in a fixed number of statements plus two a
    clashing name.
    """
    _lock_tree(conn, customer_id)
    held = folder_summary(conn, customer_id, folder_id)
    params: dict[str, Any] = {"c": customer_id, "f": folder_id, "p": to}
    if contents == "keep" and to is not None:
        target_depth, cycle, height = conn.execute(text(_MOVE_CHECK), params).one()
        if target_depth is None:
            raise DestinationNotFound()
        if cycle:
            raise FolderCycle()
        if int(target_depth) + int(height) - 1 > MAX_DEPTH:  # its folders land just below ``to``
            raise FolderTooDeep()
    gone = [folder_id] if contents == "keep" else descendant_ids(conn, customer_id, folder_id)
    since = now - timedelta(hours=UPLOAD_QUIET_HOURS)
    if conn.execute(text(_UPLOAD_RUNNING), {**params, "ids": gone, "since": since}).scalar_one():
        raise FolderUploadRunning()
    parent = conn.execute(text(_PARENT), params).scalar()
    if contents == "keep":
        _move_contents(conn, params)
    else:
        trash_id = str(uuid.uuid4())
        files = trash.trash_in_folders(conn, customer_id, gone, by=by, now=now, trash_id=trash_id)
        held = replace(held, files=files, trash_id=trash_id)
        conn.execute(text(_DETACH_TRASHED), {**params, "ids": gone})
        conn.execute(text(_TRASH_TREE), {**params, "ids": gone, "trash": trash_id, "now": now, "by": by})
    try:
        conn.execute(text(_DELETE_ALL), {**params, "ids": gone})
    except IntegrityError as error:
        if sqlstate(error) == FOREIGN_KEY_VIOLATION:
            raise FolderChanged() from None
        raise
    prune_empty(conn, customer_id, [None if parent is None else str(parent)])
    return held


def _move_contents(conn: Connection, params: dict[str, Any]) -> None:
    """Everything directly in folder ``f`` into ``p`` (``delete_folder_with_contents``): the
    documents, and the folders whose names are free there, in one statement each; then each
    clashing folder under its first free "Name (n)". The caller holds the tree lock."""
    sibling = "parent_id IS NULL" if params["p"] is None else "parent_id = CAST(:p AS uuid)"
    conn.execute(text(_NAME_AWAY), params)
    conn.execute(text(_MOVE_DOCUMENTS), params)
    conn.execute(text(_REPARENT_TRASHED), params)
    _write_named(conn, _MOVE_FREE.format(where=sibling), params)
    for folder, name in conn.execute(text(_LEFT), params).all():
        names = [_numbered(str(name), n) for n in range(2, 101)]
        free = conn.execute(text(_FREE.format(where=sibling)), {**params, "names": names}).scalar()
        if free is None:
            raise FolderNameTaken()
        _write_named(conn, _MOVE_RENAMED, {**params, "id": folder, "name": free})


def folder_path(conn: Connection, customer_id: str, folder_id: str) -> list[FolderRow]:
    """The folder and its ancestors, root first; empty if there is no such folder.
    O(d log F) time and O(d) space for its depth d <= 100."""
    return [_folder(row) for row in conn.execute(text(_PATH), {"c": customer_id, "f": folder_id})]


def descendant_ids(conn: Connection, customer_id: str, folder_id: str) -> list[str]:
    """The folder's id and every folder id inside it, level by level; empty if
    there is no such folder. O(s log F) time and O(s) space for the subtree size s."""
    rows: Sequence[object] = (
        conn.execute(text(_DESCENDANTS), {"c": customer_id, "f": folder_id}).scalars().all()
    )
    return [str(folder) for folder in rows]


def _write_named(conn: Connection, statement: str, params: dict[str, Any]) -> CursorResult[Any]:
    """Run an insert or update that can clash with a sibling's name, or put a department
    below the top level (0041's CHECK)."""
    try:
        return conn.execute(text(statement), params)
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION:
            raise FolderNameTaken() from None
        if sqlstate(error) == CHECK_VIOLATION:
            raise DepartmentNotTopLevel() from None
        raise


def _numbered(name: str, n: int) -> str:
    """``Name (n)``, the name cut so the whole fits 100 characters. O(len(name))."""
    suffix = f" ({n})"
    return name[: NAME_MAX - len(suffix)].rstrip() + suffix


def _folder(row: Sequence[Any]) -> FolderRow:
    return FolderRow(
        id=str(row[0]),
        parent_id=None if row[1] is None else str(row[1]),
        name=str(row[2]),
        created_at=row[3],
        kind=str(row[4]),
        sort_order=row[5],
    )


# --- empty folders Kaagaz made (Vrushit UAT 3, 27 Oct 2026) ---------------------------------
# A folder auto-filing made (its folder.create audit row says auto, on audit_log_folder) goes once
# nothing is in it: no folder, no document (Trash included: Restore needs it), no accepted
# version's Undo that would put a document back in it, and no folder whose key names it (a type
# folder's key holds its department's id, a year folder's its category's: R10 keeps using a moved
# one). #542: nor while a folder in Trash would go back into it, or its key names it. A folder a
# person made is never deleted.
_PRUNE = (
    "DELETE FROM folders f WHERE f.customer_id = :c AND f.id = :f"
    " AND NOT EXISTS (SELECT 1 FROM folders ch WHERE ch.customer_id = :c AND ch.parent_id = f.id)"
    " AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.customer_id = :c AND d.folder_id = f.id)"
    " AND NOT EXISTS (SELECT 1 FROM version_suggestions s"
    " WHERE s.customer_id = :c AND s.previous_folder_id = f.id)"
    " AND NOT EXISTS (SELECT 1 FROM folders k WHERE k.customer_id = :c"
    " AND k.autofile_key LIKE '%:' || f.id::text)"
    " AND NOT EXISTS (SELECT 1 FROM trashed_folders t WHERE t.customer_id = :c"
    " AND (t.parent_id = f.id OR t.autofile_key LIKE '%:' || f.id::text))"
    " AND EXISTS (SELECT 1 FROM audit_log a WHERE a.customer_id = :c AND a.folder_id = f.id"
    " AND a.action = 'folder.create' AND a.details ->> 'auto' = 'true')"
    " RETURNING f.parent_id"
)
# The empty folders Kaagaz made today, deepest first (the cleanup command).
_EMPTY_MADE = (
    "SELECT f.id FROM folders f WHERE f.customer_id = :c"
    " AND NOT EXISTS (SELECT 1 FROM folders ch WHERE ch.customer_id = :c AND ch.parent_id = f.id)"
    " AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.customer_id = :c AND d.folder_id = f.id)"
    " AND NOT EXISTS (SELECT 1 FROM version_suggestions s"
    " WHERE s.customer_id = :c AND s.previous_folder_id = f.id)"
    " AND NOT EXISTS (SELECT 1 FROM folders k WHERE k.customer_id = :c"
    " AND k.autofile_key LIKE '%:' || f.id::text)"
    " AND NOT EXISTS (SELECT 1 FROM trashed_folders t WHERE t.customer_id = :c"
    " AND (t.parent_id = f.id OR t.autofile_key LIKE '%:' || f.id::text))"
    " AND EXISTS (SELECT 1 FROM audit_log a WHERE a.customer_id = :c AND a.folder_id = f.id"
    " AND a.action = 'folder.create' AND a.details ->> 'auto' = 'true')"
    " ORDER BY f.id"
)


def prune_empty(conn: Connection, customer_id: str, folder_ids: Sequence[str | None]) -> list[str]:
    """Delete each of ``folder_ids`` that Kaagaz made and that is now empty (``_PRUNE``), then
    its parent the same way, up the tree; the ids deleted, each audited with no actor. Called
    after a write that may have emptied them, in the same transaction. No tree lock (a move
    holds a family lock, and the tree lock comes first everywhere else): a file or folder put
    in one meanwhile makes its foreign key refuse the delete, and that folder simply stays.
    One statement a folder, O(depth) a folder given."""
    deleted: list[str] = []
    for start in dict.fromkeys(f for f in folder_ids if f is not None):
        folder: str | None = start
        while folder is not None:
            try:
                with conn.begin_nested():
                    row = conn.execute(text(_PRUNE), {"c": customer_id, "f": folder}).first()
            except IntegrityError as error:
                if sqlstate(error) != FOREIGN_KEY_VIOLATION:
                    raise
                break  # filled meanwhile: kept
            if row is None:
                break  # not Kaagaz's, not empty, or gone
            deleted.append(folder)
            audit.record(
                conn,
                customer_id,
                actor_user_id=None,
                action="folder.delete",
                target_type="folder",
                target_id=folder,
                details={"auto": True},
            )
            folder = None if row[0] is None else str(row[0])
    return deleted


def empty_made_folders(conn: Connection, customer_id: str) -> list[str]:
    """The folders Kaagaz made that ``prune_empty`` would delete now (no folder, no document, no
    Undo waiting to fill them); their parents may follow. O(F) for F folders."""
    return [str(row[0]) for row in conn.execute(text(_EMPTY_MADE), {"c": customer_id})]
