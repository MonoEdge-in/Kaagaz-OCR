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
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from kaagaz.db import trash
from kaagaz.db.documents import NotInTrash, delete_family_forever
from kaagaz.db.folders import (
    _FREE,
    MAX_DEPTH,
    FolderError,
    FolderNameTaken,
    FolderNotFound,
    FolderRow,
    FolderTooDeep,
    _folder,
    _lock_tree,
    _numbered,
    _write_named,
    folder_path,
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
    " held AS (SELECT down.item, count(v.id) - 1 AS folders, count(*) - count(v.id) AS unseen FROM down"
    " LEFT JOIN visible_trashed_folders v ON v.customer_id = :c AND v.id = down.id GROUP BY down.item),"
    " filed AS (SELECT down.item, count(DISTINCT d.family_id) FILTER (WHERE s.id IS NOT NULL) AS files,"
    " bool_or(s.id IS NULL) AS unseen, array_agg(DISTINCT d.uploaded_by) AS uploaders"
    " FROM down JOIN documents d"
    " ON d.customer_id = :c AND d.trashed_folder_id = down.id AND d.trash_id = down.trash_id"
    " LEFT JOIN visible_documents s ON s.customer_id = :c AND s.id = d.id"
    " GROUP BY down.item)"
    " SELECT i.id, i.parent_id, i.name, i.kind, i.deleted_at, u.display_name, h.folders,"
    " coalesce(f.files, 0), f.uploaders,"
    " EXISTS (SELECT 1 FROM folders l WHERE l.customer_id = :c AND l.id = i.parent_id),"
    " h.unseen > 0 OR coalesce(f.unseen, false),"
    # Its restore brings its delete's department back: the department is the delete's top (F1).
    " EXISTS (SELECT 1 FROM trashed_folders r WHERE r.customer_id = :c AND r.trash_id = i.trash_id"
    " AND r.parent_id IS NULL AND r.kind = 'department')"
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
    " SELECT up.start, array_agg(up.name ORDER BY up.depth DESC), bool_and(v.id IS NOT NULL) FROM up"
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

_ANY_LIVE = "SELECT EXISTS (SELECT 1 FROM folders WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[])))"
_TOP = (
    "SELECT parent_id, name, trash_id, kind, department_id, private_folder_id FROM trashed_folders"
    " WHERE customer_id = :c AND id = :f"
)
# The rows back into folders, the topmost under its new parent and name, and private when :seal
# (#594); an auto-filing key a live folder holds now is dropped (folders_autofile_key keeps one a key).
_MAKE_LIVE = (
    "INSERT INTO folders (id, customer_id, parent_id, name, created_by, created_at, kind, sort_order,"
    " autofile_key, department_only, private, department_id, private_folder_id)"
    " SELECT t.id, t.customer_id, CASE WHEN t.id = :top THEN CAST(:p AS uuid) ELSE t.parent_id END,"
    " CASE WHEN t.id = :top THEN CAST(:name AS text) ELSE t.name END, t.created_by, t.created_at, t.kind,"
    " t.sort_order, CASE WHEN EXISTS (SELECT 1 FROM folders k WHERE k.customer_id = :c"
    " AND k.autofile_key = t.autofile_key) THEN NULL ELSE t.autofile_key END, t.department_only,"
    " t.private OR (t.id = :top AND :seal), t.department_id, t.private_folder_id"
    " FROM trashed_folders t WHERE t.customer_id = :c AND t.id = ANY(CAST(:ids AS uuid[]))"
    " RETURNING id, parent_id, name, created_at, kind, sort_order"
)
_DROP = "DELETE FROM trashed_folders WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
# Every family filed in these trashed folders back into its folder, out of Trash, in one
# statement; the rows locked in family order (each family's first version, then by id).
_RESTORE_IN = (
    "UPDATE documents d SET deleted_at = NULL, deleted_by = NULL, folder_id = d.trashed_folder_id,"
    " trashed_folder_id = NULL, trash_id = NULL FROM ("
    " SELECT id FROM documents WHERE customer_id = :c AND trashed_folder_id = ANY(CAST(:ids AS uuid[]))"
    " ORDER BY family_id, (id <> family_id), id FOR NO KEY UPDATE) AS l"
    " WHERE d.customer_id = :c AND d.id = l.id RETURNING d.family_id"
)
# One family (locked already) out of Trash, into :into (its folder made live), or held for :by
# (#594; one already held keeps its holder).
_RESTORE_FAMILY = (
    "UPDATE documents SET deleted_at = NULL, deleted_by = NULL, folder_id = CAST(:into AS uuid),"
    " held_for = CASE WHEN :hold AND held_for IS NULL THEN CAST(:by AS uuid) ELSE held_for END,"
    " trashed_folder_id = NULL, trash_id = NULL WHERE customer_id = :c AND family_id = :f"
)
# The rest of a chain's files: still in Trash, now in their live folder (as a file trashed alone).
_INTO_LIVE = (
    "UPDATE documents d SET folder_id = d.trashed_folder_id, trashed_folder_id = NULL, trash_id = NULL FROM ("
    " SELECT id FROM documents WHERE customer_id = :c AND trashed_folder_id = ANY(CAST(:ids AS uuid[]))"
    " ORDER BY family_id, (id <> family_id), id FOR NO KEY UPDATE) AS l"
    " WHERE d.customer_id = :c AND d.id = l.id"
)
_FILED_IN = "SELECT trashed_folder_id FROM documents WHERE customer_id = :c AND id = :f"
_FAMILIES_IN = (
    "SELECT DISTINCT family_id FROM documents WHERE customer_id = :c"
    " AND trashed_folder_id = ANY(CAST(:ids AS uuid[])) ORDER BY family_id"
)
_EXISTS = "SELECT EXISTS (SELECT 1 FROM visible_trashed_folders WHERE customer_id = :c AND id = :f)"
_ANY_FILED = (
    "SELECT EXISTS (SELECT 1 FROM documents WHERE customer_id = :c"
    " AND trashed_folder_id = ANY(CAST(:ids AS uuid[])))"
)
# A delete's folders past TRASH_DAYS once none of its documents is left (they go first).
_PURGE = (
    "DELETE FROM trashed_folders t WHERE t.customer_id = :c AND t.deleted_at < :before"
    " AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.customer_id = :c AND d.trash_id = t.trash_id)"
)


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
    up to 100). A fixed number of statements however large: O(s log F + n log D) for the s
    folders and n documents."""
    _lock_tree(conn, customer_id)
    chain = [str(i) for i, _ in conn.execute(text(_CHAIN), {"c": customer_id, "f": folder_id})]
    if not chain:
        raise FolderNotFound()
    subtree = conn.execute(text(_SUBTREE), {"c": customer_id, "f": folder_id}).all()
    inside = [str(row[0]) for row in subtree]
    above = chain[:-1]
    height = len(above) + max(int(row[1]) for row in subtree)
    restored = _make_live(conn, customer_id, above + inside, height, admin=admin, asked=folder_id)
    families = {str(f) for f in conn.execute(text(_RESTORE_IN), {"c": customer_id, "ids": inside}).scalars()}
    _finish(conn, customer_id, above, above + inside)
    return replace(
        restored,
        folders=len(inside) - 1,
        files=len(families),
        below=tuple(inside[1:]),
        families=tuple(families),
    )


def restore_document(
    conn: Connection, customer_id: str, document_id: str, *, admin: bool, by: str
) -> tuple[trash.Placed | None, Restored | None]:
    """Bring the document's family back from Trash, by user ``by`` (``trash.restore_document``);
    one that went with a folder delete goes back into its folder, which becomes live with its
    chain of folders above it (``restore_folder``'s rules), their other files staying in Trash.
    (None, None) when there is no such document; else where it went (``trash.Placed``) and the
    Restored when folders came back. Every restore takes the tree lock before the family's, as
    every tree change: one can change who sees the file (0085). O(v log D + d) for v versions,
    depth d."""
    _lock_tree(conn, customer_id)
    if conn.execute(text(_FILED_IN), {"c": customer_id, "f": document_id}).scalar() is None:
        return trash.restore_document(conn, customer_id, document_id, by=by), None
    family_id = trash.lock_family(conn, customer_id, document_id)
    if family_id is None:
        return None, None
    folder = conn.execute(text(_FILED_IN), {"c": customer_id, "f": family_id}).scalar()
    if folder is None:  # restored meanwhile, under the lock we waited for
        return trash.restore_document(conn, customer_id, document_id, by=by), None
    chain = [str(i) for i, _ in conn.execute(text(_CHAIN), {"c": customer_id, "f": folder})]
    restored = _make_live(conn, customer_id, chain, len(chain), admin=admin) if chain else None
    # Its folder missing can't happen; then where trash.place_family says (#594), never the top level.
    placed = trash.Placed(into=str(folder)) if chain else trash.place_family(conn, customer_id, family_id)
    params = {"c": customer_id, "f": family_id, "into": placed.into, "hold": placed.held, "by": by}
    conn.execute(text(_RESTORE_FAMILY), params)
    _finish(conn, customer_id, chain, chain)
    if restored is None:
        return placed, None
    return trash.Placed(), replace(
        restored, folders=len(chain) - 1, files=1, below=tuple(chain[1:]), families=(family_id,)
    )


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
    folder. Three statements, O(s log T + n log D)."""
    top = conn.execute(text(_TOP), {"c": customer_id, "f": folder_id}).first()
    if top is None:
        return None
    inside = [str(row[0]) for row in conn.execute(text(_SUBTREE), {"c": customer_id, "f": folder_id})]
    families = conn.execute(text(_FAMILIES_IN), {"c": customer_id, "ids": inside}).scalars()
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
    if conn.execute(text(_ANY_FILED), {"c": customer_id, "ids": inside}).scalar():
        raise FolderNotEmptied()
    conn.execute(text(_DROP), {"c": customer_id, "ids": inside})
    return Deleted(str(top[2]), folders=len(inside) - 1, files=files)


def delete_family(conn: Connection, storage: BlobStorage, customer_id: str, family_id: str) -> bool:
    """One family of a folder being deleted forever, in the caller's own transaction; False if it
    is no longer in Trash or gone (restored or deleted meanwhile), and nothing is deleted."""
    try:
        return delete_family_forever(conn, storage, customer_id, family_id) is not None
    except NotInTrash:
        return False


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


def _make_live(
    conn: Connection,
    customer_id: str,
    ids: Sequence[str],
    height: int,
    *,
    admin: bool,
    asked: str | None = None,
) -> Restored:
    """Put the trashed folders ``ids`` (the topmost first, every other one under one of them) back
    into folders; the topmost into its folder when live, else where ``_out_of_folder`` says, under
    the first free name. A department (top level only, so only ever the topmost) only for an
    ``admin``. The caller holds the tree lock and moves the documents. A fixed number of
    statements."""
    params: dict[str, Any] = {"c": customer_id, "ids": list(ids)}
    if conn.execute(text(_ANY_LIVE), params).scalar():
        raise FolderLive()
    top = ids[0]
    parent, name, trash_id, kind, department, private = conn.execute(text(_TOP), {**params, "f": top}).one()
    if kind == "department" and not admin:
        raise DepartmentAdminOnly(top)
    path = [] if parent is None else folder_path(conn, customer_id, str(parent))
    relocated = seal = False
    if not path:  # #594: never the top level when it was in a department or a private folder
        relocated = parent is not None
        parent, seal = _out_of_folder(conn, customer_id, top, department, private)
        path = [] if parent is None else folder_path(conn, customer_id, parent)
    if len(path) + height > MAX_DEPTH:
        raise FolderTooDeep()
    location = tuple(row.name for row in path)
    location_ids = tuple(row.id for row in path)
    sibling = "parent_id IS NULL" if parent is None else "parent_id = CAST(:p AS uuid)"
    names = [str(name), *(_numbered(str(name), n) for n in range(2, 101))]
    free = conn.execute(text(_FREE.format(where=sibling)), {**params, "p": parent, "names": names}).scalar()
    if free is None:
        raise FolderNameTaken()
    values = {**params, "top": top, "p": parent, "name": free, "seal": seal}
    made = _write_named(conn, _MAKE_LIVE, values).all()
    row = next(_folder(each) for each in made if str(each[0]) == top)
    mine = next((_folder(each) for each in made if str(each[0]) == asked), None)
    return Restored(
        row,
        location,
        free != name,
        relocated,
        str(trash_id),
        location_ids=location_ids,
        asked=mine,
        sealed=seal,
    )


def _out_of_folder(
    conn: Connection, customer_id: str, top: str, department: Any, private: Any
) -> tuple[str | None, bool]:
    """#594: where a restored folder whose folder is gone goes (Q6), from the department and
    private folder it inherited (its own are not inherited): under that private folder while it
    is live, else under that department while it is live, else the top level; and it comes back
    private, with nobody named, when one it inherited is gone (or in Trash). One statement."""
    inherited = [None if f is None or str(f) == top else str(f) for f in (private, department)]
    kept = [f for f in inherited if f is not None]
    if not kept:
        return None, False
    live = {str(f) for f in conn.execute(text(trash.LIVE_FOLDERS), {"c": customer_id, "ids": kept}).scalars()}
    return next((f for f in kept if f in live), None), any(f not in live for f in kept)


def _finish(conn: Connection, customer_id: str, chain_only: Sequence[str], made_live: Sequence[str]) -> None:
    """After the restored files: the other files of folders that came back only as a chain stay
    in Trash in their live folder, and the folders made live leave trashed_folders."""
    if chain_only:
        conn.execute(text(_INTO_LIVE), {"c": customer_id, "ids": list(chain_only)})
    conn.execute(text(_DROP), {"c": customer_id, "ids": list(made_live)})
