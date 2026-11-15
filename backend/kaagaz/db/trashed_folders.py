"""Folders in Trash (#542, Tushar, 7 Nov 2026): a folder deleted with everything inside it keeps
its structure in ``trashed_folders`` (0081), and Restore puts it back where it was, like the
Windows Recycle Bin.

Why here: the trashed tree's SQL in one place. Every function takes a connection from
``Database.customer(customer_id)`` and also filters on ``customer_id``.

The live ``folders`` table never holds a trashed folder, so no folder query changes. A delete's
folders share a ``trash_id``, and its documents keep their folder as
``documents.trashed_folder_id`` with the same ``trash_id`` (``folders.delete_folder_with_contents``).
Every walk here stays inside one ``trash_id``, so each delete is its own forest: a folder whose
parent is not in Trash with it is one item of Trash (the folder deleted, or one left when a file
inside was restored with its chain). Every recursive walk is bounded (``depth < 101``).

Restore runs in one transaction under the tree lock (``folders._lock_tree``), which create, move
and delete take too, so a new folder of the same name is either there before it (and the
restored one is renamed) or made after it (and refused as today). The rows go back with the same
ids, parents, names, kinds, order, maker and time, and the auto-filing key unless a live folder
took it meanwhile. The topmost goes back into its folder when that is live, else (#594) under the
private folder or department it was in while live, else to the top level, private with nobody
named when one of those is gone; under the first name no sibling has ("Name (2)",
``folders._FREE``). Its documents go back into
their folders, every version, in one statement however many (locked in family order, as every
family lock). Restoring one file from inside makes only its chain of folders live: their other
files stay in Trash, now in a live folder, as a file trashed on its own is.

Delete forever deletes each family for good in its own transaction, as the purge does (the
caller, with ``families_in`` and ``drop_folder``), so a storage failure part way leaves the rest
whole and in Trash, and no tree lock is held across storage calls; the folders go last, only
once none of their documents is left. The purge removes a delete's folders past TRASH_DAYS once
none of its documents is left (``purge_folders``), so no document ever names a trashed folder
that is gone.

Only an admin brings a department back (F1: only an admin creates or changes one), by either
restore.

Who can see what (PRD 7, #599): Trash shows each person only the folders they may see
(``visible_trashed_folders``, 0087) and counts only the files they may see. A folder they can see
inside one they can't is an item of its own for them. Restore and delete forever act on the whole
delete as before, after the route found the folder visible; who uploaded each version is read
over every file (the restore rule), and ``hidden_inside`` says whether anything in it is hidden.
#606: under the database rule those whole-delete reads and writes are 0092's owner-rights
functions (``kaagaz_restore_folder``, ``kaagaz_restore_with_folders``, ``kaagaz_trash_uploaders``,
``kaagaz_trash_families``, ``kaagaz_purge_family``), which check the caller and take the same locks.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from kaagaz.db import trash
from kaagaz.db.folders import (
    FolderError,
    FolderNameTaken,
    FolderNotFound,
    FolderRow,
    FolderTooDeep,
    _folder,
    _lock_tree,
    folder_path,
    prune_empty,
)
from kaagaz.ingestion.ports import BlobStorage


class FolderLive(FolderError):
    """A folder to restore is already in the live tree (it can't happen through the app)."""

    def __init__(self) -> None:
        super().__init__("folder_live")


class DepartmentAdminOnly(FolderError):
    """The restore would bring a department back, and only an admin may (F1). ``folder_id``: that
    department, so the caller can tell whether the person may see its name (PRD 7)."""

    def __init__(self, folder_id: str) -> None:
        super().__init__("forbidden")
        self.folder_id = folder_id


class FolderNotEmptied(FolderError):
    """Delete forever left a file in the folder (one restored, or failed, meanwhile)."""

    def __init__(self) -> None:
        super().__init__("folder_not_emptied")


@dataclass(frozen=True, slots=True)
class TrashedFolder:
    """One folder in Trash, with what it holds at every depth (files: families)."""

    id: str
    name: str
    kind: str
    deleted_at: datetime
    deleted_by_name: str | None
    folders: int
    files: int
    uploaders: tuple[str | None, ...]  # who uploaded each version of its files, for Restore's rule
    was_in: (
        tuple[str, ...] | None
    )  # the live folder it goes back into, root first; () the top level; None gone (or hidden)
    hidden_inside: bool = False  # PRD 7: it holds a folder or a file the person can't see
    needs_admin: bool = False  # its restore brings a department back (only an admin's, F1)


@dataclass(frozen=True, slots=True)
class Restored:
    """What a restore made live: the topmost folder, where it went and what came with it."""

    folder: FolderRow
    location: tuple[str, ...] | None  # its folder's path, root first; () the top level; None hidden
    renamed: bool  # a live sibling had its name: it is "Name (n)" now
    relocated: bool  # its folder was gone: it is at ``location`` now (#594: not always the top level)
    trash_id: str
    folders: int = 0  # the folders inside it that came back too
    files: int = 0  # the families restored
    location_ids: tuple[str, ...] = ()  # the folders of ``location``, for ``as_seen``
    below: tuple[str, ...] = ()  # the folders counted in ``folders``
    families: tuple[str, ...] = ()  # the families counted in ``files``
    asked: FolderRow | None = None  # PRD 7: the folder asked for, when ``folder`` is above it
    sealed: bool = False  # #594: a department or private folder it was in is gone: it came back private


@dataclass(frozen=True, slots=True)
class Deleted:
    """What a delete forever removed: the folders inside the one deleted, and the families."""

    trash_id: str
    folders: int
    files: int


# The folder and every folder of its delete inside it; depth 1 is the folder itself.
_SUBTREE = (
    "WITH RECURSIVE down (id, trash_id, depth) AS ("
    " SELECT t.id, t.trash_id, 1 FROM trashed_folders t WHERE t.customer_id = :c AND t.id = :f"
    " UNION ALL"
    " SELECT t.id, t.trash_id, down.depth + 1 FROM down"
    " JOIN trashed_folders t ON t.parent_id = down.id AND t.trash_id = down.trash_id"
    " WHERE t.customer_id = :c AND down.depth < 101)"
    " SELECT id, depth FROM down ORDER BY depth, id"
)
# The folder and the folders of its delete above it, the topmost first.
_CHAIN = (
    "WITH RECURSIVE up (id, parent_id, trash_id, name, depth) AS ("
    " SELECT t.id, t.parent_id, t.trash_id, t.name, 1 FROM trashed_folders t"
    " WHERE t.customer_id = :c AND t.id = :f"
    " UNION ALL"
    " SELECT t.id, t.parent_id, t.trash_id, t.name, up.depth + 1 FROM up"
    " JOIN trashed_folders t ON t.id = up.parent_id AND t.trash_id = up.trash_id"
    " WHERE t.customer_id = :c AND up.depth < 101)"
    " SELECT id, name FROM up ORDER BY depth DESC"
)

# The items, then what each holds: its folders and its delete's documents filed in them.
# PRD 7 (#599): only the folders the person may see; one whose folder of the same delete they
# can't see is an item of its own.
_TOP_ITEMS = (
    "WITH RECURSIVE item AS (SELECT t.* FROM visible_trashed_folders t WHERE t.customer_id = :c"
    " AND NOT EXISTS (SELECT 1 FROM visible_trashed_folders p WHERE p.customer_id = :c AND p.id = t.parent_id"
    " AND p.trash_id = t.trash_id)),"
)
_CHILD_ITEMS = (
    "WITH RECURSIVE item AS (SELECT t.* FROM visible_trashed_folders t WHERE t.customer_id = :c"
    " AND t.parent_id = :f"
    " AND t.trash_id = (SELECT x.trash_id FROM trashed_folders x WHERE x.customer_id = :c AND x.id = :f)),"
)
_ONE_ITEM = (
    "WITH RECURSIVE item AS (SELECT t.* FROM visible_trashed_folders t"
    " WHERE t.customer_id = :c AND t.id = :f),"
)
_HELD = (
    " down (item, id, trash_id, depth) AS ("
    " SELECT id, id, trash_id, 1 FROM item"
    " UNION ALL SELECT down.item, t.id, t.trash_id, down.depth + 1 FROM down"
    " JOIN trashed_folders t ON t.parent_id = down.id AND t.trash_id = down.trash_id"
    " WHERE t.customer_id = :c AND down.depth < 101),"
    " held AS (SELECT down.item, count(v.id) - 1 AS folders FROM down"
    " LEFT JOIN visible_trashed_folders v ON v.customer_id = :c AND v.id = down.id GROUP BY down.item),"
    " filed AS (SELECT down.item, count(DISTINCT d.family_id) FILTER (WHERE s.id IS NOT NULL) AS files"
    " FROM down JOIN documents d"
    " ON d.customer_id = :c AND d.trashed_folder_id = down.id AND d.trash_id = down.trash_id"
    " LEFT JOIN visible_documents s ON s.customer_id = :c AND s.id = d.id"
    " GROUP BY down.item)"
    " SELECT i.id, i.parent_id, i.name, i.kind, i.deleted_at, u.display_name, h.folders,"
    # PRD 7 (#606): who uploaded every file, the restore rule, from the whole tables
    # (kaagaz_trash_uploaders, 0092): under the database rule a hidden file's uploader would be missed.
    " coalesce(f.files, 0), kaagaz_trash_uploaders(i.id),"
    " EXISTS (SELECT 1 FROM folders l WHERE l.customer_id = :c AND l.id = i.parent_id),"
    # PRD 7 (#606): what is hidden inside, from the whole tables (kaagaz_hidden_inside, 0091): the
    # walk above, under the database rule, would find no hidden folder or file to count.
    " (SELECT x.hidden_files > 0 OR x.hidden_folders > 0 FROM kaagaz_hidden_inside(ARRAY[i.id]) x),"
    # Its restore brings its delete's department back: the department is the delete's top (F1).
    # PRD 7 (#606): read on the whole table (kaagaz_trash_top, 0091): a department the person can't
    # see is still the one its restore would bring back.
    " coalesce((SELECT x.kind = 'department' FROM kaagaz_trash_top(i.id) x), false)"
    " FROM item i JOIN held h ON h.item = i.id LEFT JOIN filed f ON f.item = i.id"
    " LEFT JOIN users u ON u.customer_id = :c AND u.id = i.deleted_by"
    " ORDER BY i.deleted_at DESC, lower(i.name), i.id"
)
# Each live folder's path, root first (100 levels at most), and whether the person may see every
# folder on it (PRD 7: a path is shown whole or not at all).
_PATHS = (
    "WITH RECURSIVE up (start, id, parent_id, name, depth) AS ("
    " SELECT id, id, parent_id, name, 1 FROM folders"
    " WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
    " UNION ALL SELECT up.start, f.id, f.parent_id, f.name, up.depth + 1 FROM up"
    " JOIN folders f ON f.customer_id = :c AND f.id = up.parent_id WHERE up.depth < 101)"
    # PRD 7 (#606): and it reaches the top: under the database rule the walk stops below a hidden one.
    " SELECT up.start, array_agg(up.name ORDER BY up.depth DESC),"
    " bool_and(v.id IS NOT NULL) AND bool_or(up.parent_id IS NULL) FROM up"
    " LEFT JOIN visible_folders v ON v.customer_id = :c AND v.id = up.id GROUP BY up.start"
)
# PRD 7 (#599): which of these trashed folders the person may see.
_SEEN = text(
    "SELECT id FROM visible_trashed_folders WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
)
# PRD 7 (#599): a restore as the person may see it: its folder, its place, what came back with it.
_SEEN_RESTORE = text(
    "SELECT EXISTS (SELECT 1 FROM visible_folders WHERE customer_id = :c AND id = :top),"
    " EXISTS (SELECT 1 FROM visible_folders WHERE customer_id = :c AND id = :asked),"
    " (SELECT count(*) FROM visible_folders WHERE customer_id = :c AND id = ANY(CAST(:path AS uuid[]))),"
    " (SELECT count(*) FROM visible_folders WHERE customer_id = :c AND id = ANY(CAST(:below AS uuid[]))),"
    " (SELECT count(DISTINCT family_id) FROM visible_documents"
    " WHERE customer_id = :c AND family_id = ANY(CAST(:families AS uuid[])))"
)

_TOP = (
    "SELECT parent_id, name, trash_id, kind, department_id, private_folder_id FROM trashed_folders"
    " WHERE customer_id = :c AND id = :f"
)
_DROP = "DELETE FROM trashed_folders WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
# PRD 7 (#606): a restore, whole, by the owner-rights functions of 0092 (this module's rules, #542
# and #594): under the database rule its walks would stop at a folder the person can't see, and its
# writes skip the files they can't see. One row for the topmost folder made live and one for the
# folder asked for; one row saying why (``refused``) when nothing was done; none for one the person
# can't see, as for one that isn't there.
_RESTORE_FOLDER = "SELECT * FROM kaagaz_restore_folder(:f) WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
_RESTORE_WITH_FOLDERS = (
    "SELECT * FROM kaagaz_restore_with_folders(:d) WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
_FILED_IN = "SELECT trashed_folder_id FROM documents WHERE customer_id = :c AND id = :f"
# PRD 7 (#606): every family inside, seen or not (kaagaz_trash_families, 0092): an Admin's delete
# forever deletes the files they can't see too; anyone else's holds nothing hidden (F6).
_FAMILIES_IN = (
    "SELECT f.id FROM kaagaz_trash_families(:f) AS f (id) WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
# PRD 7 (#606): one family deleted for good, seen or not (kaagaz_purge_family, 0092): every version
# when every one is in Trash, else none; each one's id and folder.
_PURGE_FAMILY = (
    "SELECT document_id, folder_id FROM kaagaz_purge_family(:f)"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
_REFUSED: dict[str, Callable[[], FolderError]] = {
    "folder_live": FolderLive,
    "folder_too_deep": FolderTooDeep,
    "folder_names_taken": FolderNameTaken,
}
_EXISTS = "SELECT EXISTS (SELECT 1 FROM visible_trashed_folders WHERE customer_id = :c AND id = :f)"
# PRD 7 (#606): any file inside, seen or not, the whole subtree walked from the whole tables
# (kaagaz_hidden_inside, 0091): a hidden file left in it must stop the delete.
_ANY_FILED = (
    "SELECT x.any_files FROM kaagaz_hidden_inside(ARRAY[CAST(:f AS uuid)]) x"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
# A delete's folders past TRASH_DAYS once none of its documents is left (they go first).
_PURGE = (
    "DELETE FROM trashed_folders t WHERE t.customer_id = :c AND t.deleted_at < :before"
    " AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.customer_id = :c AND d.trash_id = t.trash_id)"
)


# PRD 7 (#606): the top of a delete, read on the whole table (kaagaz_trash_top, 0091).
_DELETE_TOP = "SELECT id, kind FROM kaagaz_trash_top(:f) WHERE CAST(:c AS uuid) = kaagaz_current_customer()"


def _admin_for_the_top(conn: Connection, customer_id: str, folder_id: str, *, admin: bool) -> None:
    """A restore brings back every folder of its delete above it, so one whose top is a department
    is an Admin's (F1), whether the person can see that department or not: under the database rule
    a walk on the table stops below one they can't see. Raises DepartmentAdminOnly. One statement."""
    top = conn.execute(text(_DELETE_TOP), {"c": customer_id, "f": folder_id}).first()
    if top is not None and top.kind == "department" and not admin:
        raise DepartmentAdminOnly(str(top.id))


def audit_details(restored: Restored) -> dict[str, str | int | bool | None]:
    """``folder.restore``'s details: ids and counts only, never a name; ``to`` (None: the top
    level) when its folder was gone, and ``sealed`` when it came back private (#594)."""
    details: dict[str, str | int | bool | None] = {
        "trash_id": restored.trash_id,
        "folders": restored.folders,
        "files": restored.files,
    }
    if restored.relocated:
        details["to"] = restored.location_ids[-1] if restored.location_ids else None
    if restored.sealed:
        details["sealed"] = True
    return details


def list_items(conn: Connection, customer_id: str) -> list[TrashedFolder]:
    """Every item of Trash that is a folder, newest first. Two statements: the walk down from
    every item and its documents, each grouped once (O(T log T + n log D) for the T trashed
    folders and the n documents filed in them), and the live parents' paths (O(d) each)."""
    return _items(conn, customer_id, _TOP_ITEMS, {})


def one(conn: Connection, customer_id: str, folder_id: str) -> TrashedFolder:
    """One trashed folder with what it holds. Raises FolderNotFound. Two statements."""
    found = _items(conn, customer_id, _ONE_ITEM, {"f": folder_id})
    if not found:
        raise FolderNotFound()
    return found[0]


def contents(
    conn: Connection, customer_id: str, folder_id: str
) -> tuple[TrashedFolder, list[tuple[str, str]], list[TrashedFolder]]:
    """A trashed folder as Trash opens it: itself, its path within Trash ((id, name), the item
    first) and the folders directly in it. Raises FolderNotFound. O(subtree) statements' rows.
    PRD 7: the path starts below the last folder on it the person can't see (their item)."""
    found = one(conn, customer_id, folder_id)
    chain = [(str(i), str(n)) for i, n in conn.execute(text(_CHAIN), {"c": customer_id, "f": folder_id})]
    seen = {str(row[0]) for row in conn.execute(_SEEN, {"c": customer_id, "ids": [i for i, _ in chain]})}
    path = chain[max((n + 1 for n, (i, _) in enumerate(chain) if i not in seen), default=0) :]
    return found, path, _items(conn, customer_id, _CHILD_ITEMS, {"f": folder_id})


def seen(conn: Connection, customer_id: str, folder_ids: Sequence[str]) -> set[str]:
    """Which of these trashed folders the person may see (PRD 7). One statement."""
    return {str(row[0]) for row in conn.execute(_SEEN, {"c": customer_id, "ids": list(folder_ids)})}


def exists(conn: Connection, customer_id: str, folder_id: str) -> bool:
    """Is ``folder_id`` a folder in this customer's Trash the person may see? One key lookup."""
    return bool(conn.execute(text(_EXISTS), {"c": customer_id, "f": folder_id}).scalar())


def restore_folder(conn: Connection, customer_id: str, folder_id: str, *, admin: bool) -> Restored:
    """Make the trashed folder live again, with every folder and file of its delete inside it,
    and the folders of its delete above it (with only it in them). Raises FolderNotFound,
    FolderLive (an id is live already), DepartmentAdminOnly (a department, and not ``admin``),
    FolderTooDeep (its live folder moved too deep meanwhile) or FolderNameTaken (no free number
    up to 100). PRD 7 (#606): the whole delete, seen or not (``kaagaz_restore_folder``, 0092).
    Three statements however large: O(s log F + n log D) for the s folders and n documents."""
    _lock_tree(conn, customer_id)
    _admin_for_the_top(conn, customer_id, folder_id, admin=admin)
    rows = conn.execute(text(_RESTORE_FOLDER), {"c": customer_id, "f": folder_id}).all()
    restored = _restored(conn, customer_id, rows)
    if restored is None:
        raise FolderNotFound()
    return restored


def restore_document(
    conn: Connection, customer_id: str, document_id: str, *, admin: bool, by: str
) -> tuple[trash.Placed | None, Restored | None]:
    """Bring the document's family back from Trash, by user ``by`` (``trash.restore_document``);
    one that went with a folder delete goes back into its folder, which becomes live with its
    chain of folders above it (``restore_folder``'s rules), their other files staying in Trash.
    (None, None) when there is no such document; else where it went (``trash.Placed``) and the
    Restored when folders came back. Every restore takes the tree lock before the family's, as
    every tree change: one can change who sees the file (0085). PRD 7 (#606): the chain whole, seen
    or not (``kaagaz_restore_with_folders``, 0092). O(v log D + d) for v versions, depth d."""
    _lock_tree(conn, customer_id)
    folder = conn.execute(text(_FILED_IN), {"c": customer_id, "f": document_id}).scalar()
    if folder is None:
        return trash.restore_document(conn, customer_id, document_id, by=by), None
    _admin_for_the_top(conn, customer_id, str(folder), admin=admin)
    rows = conn.execute(text(_RESTORE_WITH_FOLDERS), {"c": customer_id, "d": document_id}).all()
    restored = _restored(conn, customer_id, rows)
    return (None, None) if restored is None else (trash.Placed(), restored)


def as_seen(conn: Connection, customer_id: str, restored: Restored) -> Restored | None:
    """PRD 7 (#599): a restore as the person may see it, for its reply. The topmost folder that came
    back with its place, when they may see it and every folder on its place; else the folder asked
    for (``asked``), with no place and nothing said of a rename or a move, which belong to the one
    above; None when they may see neither. Only the folders and files they can see are counted.
    One statement."""
    asked = restored.asked or restored.folder
    params = {
        "c": customer_id,
        "top": restored.folder.id,
        "asked": asked.id,
        "path": list(restored.location_ids),
        "below": list(restored.below),
        "families": list(restored.families),
    }
    top, mine, path, folders, files = conn.execute(_SEEN_RESTORE, params).one()
    seen = replace(restored, folders=int(folders), files=int(files))
    if top:
        return replace(seen, location=restored.location if path == len(restored.location_ids) else None)
    if not mine:
        return None
    return replace(seen, folder=asked, location=None, renamed=False, relocated=False, sealed=False)


def families_in(conn: Connection, customer_id: str, folder_id: str) -> tuple[str, list[str]] | None:
    """The trash_id of the trashed folder and the families of its delete in it at every depth, for
    delete forever to delete one per transaction (``delete_family``); None if there is no such
    folder. Two statements, O(s log T + n log D)."""
    top = conn.execute(text(_TOP), {"c": customer_id, "f": folder_id}).first()
    if top is None:
        return None
    families = conn.execute(text(_FAMILIES_IN), {"c": customer_id, "f": folder_id}).scalars()
    return str(top[2]), [str(f) for f in families]


def drop_folder(conn: Connection, customer_id: str, folder_id: str, *, files: int) -> Deleted:
    """Delete forever's last step, once its families are gone: the trashed folder and every one
    of its delete inside it, under the tree lock. Raises FolderNotFound (gone meanwhile) or
    FolderNotEmptied (a document still names one of them: none is deleted). Four statements."""
    _lock_tree(conn, customer_id)
    top = conn.execute(text(_TOP), {"c": customer_id, "f": folder_id}).first()
    if top is None:
        raise FolderNotFound()
    inside = [str(row[0]) for row in conn.execute(text(_SUBTREE), {"c": customer_id, "f": folder_id})]
    if conn.execute(text(_ANY_FILED), {"c": customer_id, "f": folder_id}).scalar():
        raise FolderNotEmptied()
    conn.execute(text(_DROP), {"c": customer_id, "ids": inside})
    return Deleted(str(top[2]), folders=len(inside) - 1, files=files)


def delete_family(conn: Connection, storage: BlobStorage, customer_id: str, family_id: str) -> bool:
    """One family of a folder being deleted forever, in the caller's own transaction: every
    version's row (``kaagaz_purge_family``, seen or not, PRD 7), the folder Kaagaz made that it
    leaves empty, then its stored files, so a storage failure rolls the rows back (``delete_forever``'s
    order). False if it is no longer in Trash or gone (restored or deleted meanwhile), and nothing
    is deleted. One statement, then one a folder and O(k) storage calls a version."""
    gone = conn.execute(text(_PURGE_FAMILY), {"c": customer_id, "f": family_id}).all()
    prune_empty(conn, customer_id, [None if row.folder_id is None else str(row.folder_id) for row in gone])
    for row in gone:
        storage.delete(customer_id, str(row.document_id))
    return bool(gone)


def purge_folders(conn: Connection, customer_id: str, *, before: datetime) -> int:
    """Delete the folders of each delete that went to Trash before ``before`` and has no document
    left in Trash (the purge removes the documents first). Under the tree lock, so a restore
    never meets a half purged delete. Returns how many. One statement, O(T) for T trashed folders."""
    _lock_tree(conn, customer_id)
    return int(conn.execute(text(_PURGE), {"c": customer_id, "before": before}).rowcount or 0)


def _items(conn: Connection, customer_id: str, head: str, params: dict[str, Any]) -> list[TrashedFolder]:
    rows = conn.execute(text(head + _HELD), {"c": customer_id, **params}).all()
    live_parents = [str(row[1]) for row in rows if row[9]]
    paths: dict[str, tuple[str, ...]] = {}
    if live_parents:
        found = conn.execute(text(_PATHS), {"c": customer_id, "ids": live_parents})
        paths = {str(start): tuple(names) for start, names, seen in found if seen}
    return [
        TrashedFolder(
            id=str(row[0]),
            name=str(row[2]),
            kind=str(row[3]),
            deleted_at=row[4],
            deleted_by_name=row[5],
            folders=int(row[6]),
            files=int(row[7]),
            uploaders=tuple(None if u is None else str(u) for u in row[8] or ()),
            # shortcut: a parent in Trash from another delete reads as gone; it restores to the top level.
            was_in=() if row[1] is None else (paths.get(str(row[1])) if row[9] else None),
            hidden_inside=bool(row[10]),
            needs_admin=bool(row[11]),
        )
        for row in rows
    ]


def _restored(conn: Connection, customer_id: str, rows: Sequence[Any]) -> Restored | None:
    """A restore function's rows as a Restored (None: no such folder or file for the person); its
    place, root first, from ``folder_path`` (a folder the person can't see ends that walk: then no
    place is named, ``as_seen``). Raises the FolderError a refusal names. O(d) for its depth d."""
    if not rows:
        return None
    if rows[0].refused is not None:
        raise _REFUSED[rows[0].refused]()
    top = next(row for row in rows if row.is_top)
    asked = next((row for row in rows if row.is_asked), None)
    parent = None if top.parent_id is None else str(top.parent_id)
    path = [] if parent is None else folder_path(conn, customer_id, parent)
    location = tuple(row.name for row in path)
    location_ids = tuple(row.id for row in path)
    if parent is not None and (not path or path[0].parent_id is not None):
        # #606: the walk stopped below a folder the person can't see: its id first, so the reply
        # (as_seen) names no place, as it names none with any folder on it hidden.
        location = ()
        location_ids = (str(path[0].parent_id if path else parent), *location_ids)
    return Restored(
        _made(top),
        location,
        bool(top.renamed),
        bool(top.relocated),
        str(top.trash_id),
        folders=len(top.below),
        files=len(top.families),
        location_ids=location_ids,
        below=tuple(str(f) for f in top.below),
        families=tuple(str(f) for f in top.families),
        asked=None if asked is None else _made(asked),
        sealed=bool(top.sealed),
    )


def _made(row: Any) -> FolderRow:
    """A folder a restore made live; a blank name for one the person can't see (never shown)."""
    return _folder(
        (
            row.id,
            row.parent_id,
            row.name or "",
            row.created_at,
            row.kind,
            row.sort_order,
            row.department_only,
            row.private,
        )
    )
