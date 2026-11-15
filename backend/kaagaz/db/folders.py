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
    department_only: bool = False  # PRD 7 (0084)
    private: bool = False


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


class AccessWouldChange(FolderError):
    """#594: what a folder deleted with ``keep`` holds would go where other people see it (Q5).
    PRD 7 PR 10c (#601): the warning; sent again with ``confirm`` it goes ahead."""

    def __init__(self) -> None:
        super().__init__("access_would_change")


class MoveChangesAccess(FolderError):
    """PRD 7 decision 18 (#601): a move that changes who can see a file or folder, by a person who
    may not make one (``Rights``)."""

    def __init__(self) -> None:
        super().__init__("move_changes_access")


class InsidePrivate(FolderError):
    """PRD 7 section 4 (#601): a private folder can't go inside another private folder."""

    def __init__(self) -> None:
        super().__init__("inside_private")


class HiddenInside(FolderError):
    """PRD 7 section 5 (#601): the folder holds a folder or a file the person can't see; only an
    Admin deletes it."""

    def __init__(self) -> None:
        super().__init__("hidden_inside")


@dataclass(frozen=True, slots=True)
class Rights:
    """Who may make a move that changes who can see something (PRD 7 decision 18): an Admin or
    Management anywhere (``wide``); a Head inside a department they head. Read under the tree lock."""

    wide: bool
    heads: frozenset[str]

    def allow(self, before: str | None, after: str | None) -> bool:
        """A move from department ``before`` to ``after`` (None: none)."""
        return self.wide or (before is not None and before == after and before in self.heads)


Place = tuple[str | None, str | None]  # (department, private folder), as stored


@dataclass(frozen=True, slots=True)
class Move:
    """A move as the access rules see it (#601): the place what it moves is in and the one it goes
    to, and the private folders it carries (each keeps its people; its Head is its department's).
    ``own``: something moves in the place itself (False: a private folder moved, everything in it
    in its own private place)."""

    before: Place
    after: Place
    private: list[str]
    own: bool = True


class DepartmentInUse(FolderError):
    """PRD 7 lead decision 6 (#601): a department with members, Department only, or a private
    folder inside keeps its kind."""

    def __init__(self) -> None:
        super().__init__("department_in_use")


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
# PRD 7 (#601): the department :f has members, is Department only, or holds a private folder.
_IN_USE = (
    "SELECT EXISTS (SELECT 1 FROM department_members m WHERE m.customer_id = :c AND m.department_id = :f)"
    " OR EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = :c AND f.department_id = :f"
    " AND (f.department_only OR f.private))"
)

# PRD 7 (#599): only the folders the person may see; one whose parent they can't see is at the top
# level for them, so no hidden folder's id reaches the tree.
_LIST = (
    "SELECT f.id, p.id AS parent_id, f.name, f.created_at, f.kind, f.sort_order, f.department_only, f.private"
    " FROM visible_folders f"
    " LEFT JOIN visible_folders p ON p.customer_id = f.customer_id AND p.id = f.parent_id"
    " WHERE f.customer_id = :c ORDER BY lower(f.name), f.id"
)

_INSERT = (
    "INSERT INTO folders (customer_id, parent_id, name, created_by, kind, sort_order)"
    " VALUES (:c, :p, :name, :u, :kind, :sort)"
    " RETURNING id, parent_id, name, created_at, kind, sort_order, department_only, private"
)

# File management F1: a folder's kind and the department order, each only when given.
_SET_KIND = (
    "UPDATE folders SET kind = CASE WHEN :set_kind THEN :kind ELSE kind END,"
    " sort_order = CASE WHEN :set_sort THEN CAST(:sort AS integer) ELSE sort_order END"
    " WHERE customer_id = :c AND id = :f RETURNING id, parent_id, name, created_at, kind, sort_order,"
    " department_only, private"
)

_RENAME = (
    "UPDATE folders SET name = :name WHERE customer_id = :c AND id = :f RETURNING "
    "id, parent_id, name, created_at, kind, sort_order, department_only, private"
)

_MOVE = (
    "UPDATE folders SET parent_id = :p WHERE customer_id = :c AND id = :f RETURNING "
    "id, parent_id, name, created_at, kind, sort_order, department_only, private"
)

# The folder and its ancestors; depth 1 is the folder itself, so the largest
# depth is the folder's own depth in the tree, and the root comes last.
_PATH = (
    "WITH RECURSIVE up (id, parent_id, name, created_at, kind, sort_order, department_only, private, depth)"
    " AS (SELECT f.id, f.parent_id, f.name, f.created_at, f.kind, f.sort_order, f.department_only,"
    " f.private, 1 FROM folders f"
    " WHERE f.customer_id = :c AND f.id = :f"
    " UNION ALL"
    " SELECT f.id, f.parent_id, f.name, f.created_at, f.kind, f.sort_order, f.department_only, f.private,"
    " up.depth + 1 FROM up JOIN folders f ON f.id = up.parent_id"
    " WHERE f.customer_id = :c AND up.depth < 101)"
    " SELECT id, parent_id, name, created_at, kind, sort_order, department_only, private FROM up"
    " ORDER BY depth DESC"
)

# PRD 7 (#606): the walk up the whole tree (kaagaz_folder_depth, 0091): under the database rule a
# folder the person can't see would end it early, and the 100-level limit would let more through.
_DEPTH = "SELECT depth FROM kaagaz_folder_depth(:f, NULL) WHERE CAST(:c AS uuid) = kaagaz_current_customer()"

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
# A NULL depth or height means that folder does not exist for this customer. PRD 7 (#606): on the
# whole tree (kaagaz_folder_depth, 0091): a folder the person can't see would end a walk early, and
# a cycle through it, or a level past 100, would go unseen.
_MOVE_CHECK = (
    "SELECT depth, cycle, height FROM kaagaz_folder_depth(:p, :f)"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)

# Trashed documents keep their folder until they are restored or purged; a
# folder holding only those can still be deleted, and they leave it (their restore
# then goes where trash.place_family says, #594).
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
# PRD 7 (#606): an Admin's dialog reads the whole organisation's totals (decision 20), through the
# owner-rights kaagaz_admin_folder_summary (0091, _SUMMARY's statement), which checks the caller.
_SUMMARY_ADMIN = (
    "SELECT * FROM kaagaz_admin_folder_summary(:f) WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
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
# #594, PRD 7 decision 18 (#601): what is in the place (:d1, :p1) sees the same people in (:d2,
# :p2): the same department and private folder, or both open (no private folder, and no department
# or one not Department only, read from the live department) with no private folder carried along
# (:inner: its Head is its department's). Every move asks it, and the keep-delete.
_SAME_PLACE = (
    "SELECT (CAST(:d1 AS uuid), CAST(:p1 AS uuid))"
    " IS NOT DISTINCT FROM (CAST(:d2 AS uuid), CAST(:p2 AS uuid))"
    " OR (CAST(:p1 AS uuid) IS NULL AND CAST(:p2 AS uuid) IS NULL AND NOT :inner"
    " AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[CAST(:d1 AS uuid), CAST(:d2 AS uuid)]) AS d (id),"
    " kaagaz_folder_place(d.id) p WHERE p.department_only))"
    " FROM (SELECT 1) one WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
# PRD 7 (#606): both read whole (kaagaz_folder_place, 0091): under the database rule a Department only
# department, or a target folder, the person can't see reads as no row, so as open or the top level.
_PLACE_OF = (
    "SELECT department_id, private_folder_id, private FROM kaagaz_folder_place(:f)"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
_PRIVATE_AMONG = (
    "SELECT id FROM folders WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[])) AND private ORDER BY id"
)
# PRD 7 section 5 (#601): the subtree of :f holds a folder, or a file (in Trash too), the person
# can't see.
# PRD 7 (#606): the whole subtree, walked on the whole tables (kaagaz_hidden_inside, 0091): under
# the database rule the table walk would find nothing hidden, and the delete would go ahead.
_HIDDEN_INSIDE = (
    "SELECT x.hidden_files > 0 OR x.hidden_folders > 0"
    " FROM kaagaz_hidden_inside(ARRAY[CAST(:f AS uuid)], NULL) x"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
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


def department_in_use(conn: Connection, customer_id: str, folder_id: str) -> bool:
    """Whether the department ``folder_id`` has members, is Department only or holds a private
    folder (``_IN_USE``): its kind may not change (PRD 7 lead decision 6). The caller holds the
    tree lock. Two index probes and its folders by department."""
    return bool(conn.execute(text(_IN_USE), {"c": customer_id, "f": folder_id}).scalar())


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


def place_of(conn: Connection, customer_id: str, folder_id: str | None) -> Place:
    """A folder's stored (department, private folder); (None, None) for the top level, or a folder
    that isn't there. One statement."""
    row = (
        None
        if folder_id is None
        else conn.execute(text(_PLACE_OF), {"c": customer_id, "f": folder_id}).first()
    )
    return (None, None) if row is None else (_id(row[0]), _id(row[1]))


def folder_move(
    conn: Connection, customer_id: str, folder_id: str, parent_id: str | None, *, contents: bool = False
) -> Move:
    """The folder's move into ``parent_id`` (None: the top level) as the access rules see it; with
    ``contents``, the move of what it holds (#594's keep-delete: the folder itself goes). Raises
    FolderNotFound. Three statements, O(s log F) for its subtree's s folders."""
    row = conn.execute(text(_PLACE_OF), {"c": customer_id, "f": folder_id}).first()
    if row is None:
        raise FolderNotFound()
    department, private_folder, private = _id(row[0]), _id(row[1]), bool(row[2])
    inside = [f for f in descendant_ids(conn, customer_id, folder_id) if not contents or f != folder_id]
    carried = [
        str(f) for f in conn.execute(text(_PRIVATE_AMONG), {"c": customer_id, "ids": inside}).scalars()
    ]
    # A private folder carries its own privacy; the place it is in has none (it can't be in one).
    before = (department, None if private and not contents else private_folder)
    after = place_of(conn, customer_id, parent_id)
    return Move(before=before, after=after, private=carried, own=contents or not private)


def changes_access(
    conn: Connection, customer_id: str, before: Place, after: Place, *, inner: bool = False
) -> bool:
    """Whether a move from ``before`` to ``after`` changes who can see what it moves (``_SAME_PLACE``;
    ``inner``: a private folder goes with it). One statement."""
    params = {
        "c": customer_id,
        "d1": before[0],
        "p1": before[1],
        "d2": after[0],
        "p2": after[1],
        "inner": inner,
    }
    return not conn.execute(text(_SAME_PLACE), params).scalar()


def check_move(conn: Connection, customer_id: str, rights: Rights, move: Move) -> bool:
    """PRD 7 section 4 and decision 18 (#601), under the tree lock, before anything is written:
    InsidePrivate for a private folder carried into another; MoveChangesAccess for a move that
    changes who can see it by a person who may not make one. Returns whether it changes access."""
    if move.private and move.after[1] is not None:
        raise InsidePrivate()
    changed = changes_access(conn, customer_id, move.before, move.after, inner=bool(move.private))
    if changed and not rights.allow(move.before[0], move.after[0]):
        raise MoveChangesAccess()
    return changed


def delete_folder(conn: Connection, customer_id: str, folder_id: str, *, seen_only: bool = False) -> None:
    """Delete an empty folder. Trashed documents in it move to the top level first.

    Raises FolderNotFound, HiddenInside (``seen_only``: a person not an Admin, and the folder holds
    a folder or a file, in Trash too, they can't see; PRD 7, #601), or FolderNotEmpty when a folder
    or a live document is still inside (the foreign keys refuse the delete, and the detach is
    rolled back with it). O(n log D + t log t + log F) time for the n documents
    filed in it and the t trashed ones it moves (n = t when the delete succeeds),
    O(t) space.
    """
    _lock_tree(conn, customer_id)
    params = {"c": customer_id, "f": folder_id}
    if seen_only and conn.execute(text(_HIDDEN_INSIDE), params).scalar():
        raise HiddenInside()
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
    conn: Connection, customer_id: str, folder_id: str, *, seen_only: bool = False, admin: bool = False
) -> FolderSummary:
    """What the folder holds at every depth (FolderSummary); with ``seen_only`` only what the
    person may see (PRD 7); with ``admin`` (an Admin's dialog) the whole organisation's, the
    files they can't see too (#606). Raises FolderNotFound (with ``seen_only`` a hidden folder
    too). One statement, O(s log F + n log D) for the s folders and n documents of its subtree."""
    statement = _SUMMARY_SEEN if seen_only else _SUMMARY_ADMIN if admin else _SUMMARY
    found = conn.execute(text(statement), {"c": customer_id, "f": folder_id}).first()
    if found is None:  # another customer's id: no row (the Admin's)
        raise FolderNotFound()
    count, files, size = found
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
    confirm: bool = False,
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
    inside it), FolderTooDeep, AccessWouldChange (with keep, ``to`` is seen by other people than
    the folder, #594; Trash inside it too; PRD 7 #601: unless ``confirm``, the Admin's answer to
    that warning), InsidePrivate (a private folder in it would go into ``to``'s), FolderUploadRunning
    (a folder upload into what is
    deleted still has files waiting and was active in the last UPLOAD_QUIET_HOURS: with keep, the
    folder itself, as its folders only move), FolderNameTaken (no free number up to 100) or
    FolderChanged (a file arrived meanwhile: a document insert takes no tree lock). Nothing is
    written before the checks; a refusal after a write leaves the caller's transaction to roll
    back. ``files`` in what it returns is, with trash, the families that went to Trash now, and
    ``trash_id`` is set. O(s log F + n log D) for the s folders and n documents of the subtree, in
    a fixed number of statements plus two a clashing name.
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
    if contents == "keep":
        moved = folder_move(conn, customer_id, folder_id, to, contents=True)
        if moved.private and moved.after[1] is not None:
            raise InsidePrivate()
        if not confirm and changes_access(
            conn, customer_id, moved.before, moved.after, inner=bool(moved.private)
        ):
            raise AccessWouldChange()
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
            # #601: folders_department_only is the other CHECK a kind change can meet.
            if (
                getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                == "folders_department_only"
            ):
                raise DepartmentInUse() from None
            raise DepartmentNotTopLevel() from None
        raise


def _numbered(name: str, n: int) -> str:
    """``Name (n)``, the name cut so the whole fits 100 characters. O(len(name))."""
    suffix = f" ({n})"
    return name[: NAME_MAX - len(suffix)].rstrip() + suffix


def _id(value: object) -> str | None:
    return None if value is None else str(value)


def _folder(row: Sequence[Any]) -> FolderRow:
    return FolderRow(
        id=str(row[0]),
        parent_id=None if row[1] is None else str(row[1]),
        name=str(row[2]),
        created_at=row[3],
        kind=str(row[4]),
        sort_order=row[5],
        department_only=bool(row[6]),
        private=bool(row[7]),
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
