"""Folder upload (#484, Tushar, 3 Nov 2026): the limits and the plan that makes the folders.

Why here: the plan's tree rules next to their SQL. Every function takes a connection from
``Database.customer(customer_id)`` and also filters on ``customer_id`` (two locks,
docs/architecture.md).

* **Limits** (``effective_limits``): per field the organisation's own (``org_upload_limits``),
  else the platform's (``platform_ai_settings.folder_upload_*``), else the default here. The
  super admin sets them (``kaagaz_op_set_folder_upload_limits``, 0061c).
* **The plan** (``plan_folders``): the browser sends the picked folder as a list of
  ``{name, parent index}``, parents first. Under the customer's tree lock, in the caller's one
  transaction, the folders are made or merged level by level with set-based statements (two a
  level), and their ids come back in the list's order: the path to folder_id map.
  - The counts are checked against the limits before the lock; the folders it would make,
    after it (``too_many_folders`` rolls the whole plan back).
  - One plan is built at a time per organisation (``plan_busy`` at once, never a wait: a
    transaction advisory lock), and a new upload is refused while another user's upload of the
    organisation still has files waiting and was active in the last 30 minutes
    (``upload_in_progress``); a re-plan of the same upload, or the same user's new one, is not.
  - Merge (the default): a same-name folder already there (any letter case, as
    ``folders_sibling_name`` compares, and NFC on both sides, so a folder a Mac made is found)
    is used at every level. Keep separate makes the root ``Name (2)`` or the first free
    ``(n)``. "As a new department" merges only into a top-level department, never into a
    custom folder of that name.
  - A re-plan of the same upload (``id``) reuses the folder each path in the picked folder got
    before (``folder_upload_folders.path_key``: SHA-256 of the NFC names joined by "/"), in
    whatever order the list comes (another walk after a reload), even after a rename or move
    in Kaagaz; only new paths, or folders deleted since, are made or merged again, and a remade
    folder's waiting items move to it. The depth is measured again at each reused folder a new
    one goes in.
  - The uploader's folders are never pruned: ``folders.prune_empty`` deletes only folders
    audited ``folder.create`` with ``auto``, and the plan writes no such row.
  Cost: O(F) rows in at most 2H + 12 statements for F folders H levels deep (H <= 100).
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Connection, text

from kaagaz.db.folders import (
    _DEPTH,
    MAX_DEPTH,
    FolderError,
    FolderNameTaken,
    FolderNotFound,
    FolderTooDeep,
    _lock_tree,
)

GIB = 1024**3
# The server's defaults (Tushar, 3 Nov 2026) and the most the super admin may set (0061c's CHECKs).
DEFAULT_MAX_FILES, DEFAULT_MAX_GB, DEFAULT_MAX_FOLDERS = 50_000, 5, 10_000
MOST_FILES, MOST_GB, MOST_FOLDERS = 200_000, 20, 50_000  # 20 GB until a free-disk check exists


@dataclass(frozen=True, slots=True)
class FolderUploadLimits:
    max_files: int
    max_bytes: int
    max_folders: int


_LIMITS = text(
    "SELECT coalesce(o.max_files, p.folder_upload_max_files, :files),"
    " coalesce(o.max_gb, p.folder_upload_max_gb, :gb),"
    " coalesce(o.max_folders, p.folder_upload_max_folders, :folders)"
    " FROM platform_ai_settings p LEFT JOIN org_upload_limits o ON o.customer_id = :c"
)


def effective_limits(conn: Connection, customer_id: str) -> FolderUploadLimits:
    """This organisation's folder upload limits now. One statement, O(1)."""
    params = {
        "c": customer_id,
        "files": DEFAULT_MAX_FILES,
        "gb": DEFAULT_MAX_GB,
        "folders": DEFAULT_MAX_FOLDERS,
    }
    files, gb, folders = conn.execute(_LIMITS, params).one()
    return FolderUploadLimits(int(files), int(gb) * GIB, int(folders))


# --- the plan ---------------------------------------------------------------------------------
NAME_MAX = 100  # folders.name's CHECK (0002)


@dataclass(frozen=True, slots=True)
class PlannedFolder:
    name: str  # already cleaned by the caller
    parent: int | None  # the index of an EARLIER entry; None only for entry 0, the picked folder


@dataclass(frozen=True, slots=True)
class Plan:
    upload_id: str
    root_folder_id: str
    root_name: str
    folder_ids: list[str]  # aligned with the list sent: the path to folder_id map
    created: int
    merged: int
    new: bool  # the first plan of this upload


class PlanRefused(FolderError):
    """``code``: too_many_files, too_large_upload, too_many_folders, plan_changed, plan_busy or
    upload_in_progress."""


_PLAN_LOCK = text(
    "SELECT pg_try_advisory_xact_lock(hashtextextended('kaagaz.folder_plan:' || CAST(:c AS uuid)::text, 0))"
)
# Another user's upload of the organisation with a file still waiting, upload by upload on the
# state index, that was active in the last 30 minutes (production QA P1: a stopped or abandoned
# upload's waiting files blocked the organisation for ever; the browser alone knows it stopped).
_SENDING = text(
    "SELECT EXISTS (SELECT 1 FROM folder_uploads u WHERE u.customer_id = :c AND u.user_id <> :usr"
    " AND EXISTS (SELECT 1 FROM folder_upload_items i WHERE i.customer_id = :c"
    " AND i.folder_upload_id = u.id AND i.state = 'waiting')"
    " AND greatest(u.updated_at, (SELECT max(i.updated_at) FROM folder_upload_items i"
    " WHERE i.customer_id = :c AND i.folder_upload_id = u.id))"
    " > now() - interval '30 minutes')"
)


_UPLOAD = text(
    "SELECT user_id, target, parent_folder_id, source_name, folders_created FROM folder_uploads"
    " WHERE customer_id = :c AND id = :u FOR UPDATE"
)
# The folder each path of this list got before, while it still exists (a re-plan), and its name now.
_REUSE = text(
    "SELECT w.i - 1, f.id, f.name FROM unnest(CAST(:keys AS text[])) WITH ORDINALITY AS w (k, i)"
    " JOIN folder_upload_folders m ON m.customer_id = :c AND m.folder_upload_id = :u AND m.path_key = w.k"
    " JOIN folders f ON f.customer_id = :c AND f.id = m.folder_id"
)
# The planned names are NFC already; a folder's own name is made NFC here (a Mac's is NFD).
_SAME_NAME = (
    "SELECT id, name FROM folders WHERE customer_id = :c AND lower(normalize(name, NFC)) = lower(:name)"
    " AND {where}"
)
# The first candidate name no sibling has, compared as folders_sibling_name compares (lower()), NFC.
_FREE = (
    "SELECT w.n FROM unnest(CAST(:names AS text[])) WITH ORDINALITY AS w (n, i)"
    " WHERE NOT EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = :c AND {where}"
    " AND lower(normalize(f.name, NFC)) = lower(w.n)) ORDER BY w.i LIMIT 1"
)
_INSERT_ROOT = text(
    "INSERT INTO folders (customer_id, parent_id, name, created_by, kind)"
    " VALUES (:c, :p, :name, :usr, :kind) RETURNING id"
)
# One level: every new folder in one insert (a same-name one already there, NFC, or one earlier
# in this insert, is kept: NOT EXISTS, DO NOTHING), then every folder of the level found by name.
_INSERT_LEVEL = text(
    "INSERT INTO folders (customer_id, parent_id, name, created_by, kind)"
    " SELECT :c, w.p, w.n, :usr, 'custom'"
    " FROM unnest(CAST(:parents AS uuid[]), CAST(:names AS text[])) AS w (p, n)"
    " WHERE NOT EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = :c AND f.parent_id = w.p"
    " AND lower(normalize(f.name, NFC)) = lower(w.n))"
    " ON CONFLICT DO NOTHING"
)
_FIND_LEVEL = text(
    "SELECT w.i, f.id"
    " FROM unnest(CAST(:parents AS uuid[]), CAST(:names AS text[]), CAST(:idx AS integer[])) AS w (p, n, i)"
    " JOIN folders f ON f.customer_id = :c AND f.parent_id = w.p"
    " AND lower(normalize(f.name, NFC)) = lower(w.n)"
)
# The depth of each of these folders now (1: the top level), bounded as _DEPTH is.
_DEPTHS = text(
    "WITH RECURSIVE up (start, parent_id, depth) AS ("
    " SELECT f.id, f.parent_id, 1 FROM folders f"
    " WHERE f.customer_id = :c AND f.id = ANY(CAST(:ids AS uuid[]))"
    " UNION ALL"
    " SELECT up.start, f.parent_id, up.depth + 1 FROM up"
    " JOIN folders f ON f.id = up.parent_id"
    " WHERE f.customer_id = :c AND up.depth < 101)"
    " SELECT start, max(depth) FROM up GROUP BY start"
)
_SAVE_UPLOAD = text(
    "INSERT INTO folder_uploads (id, customer_id, user_id, target, parent_folder_id, root_folder_id,"
    " source_name, root_name, merge, file_count, total_bytes, skipped_count, folders_created)"
    " VALUES (:u, :c, :usr, :target, :parent, :root, :source, :root_name, :merge, :files, :bytes,"
    " :skipped, :created)"
    " ON CONFLICT (customer_id, id) DO UPDATE SET root_folder_id = EXCLUDED.root_folder_id,"
    " root_name = EXCLUDED.root_name, file_count = EXCLUDED.file_count,"
    " total_bytes = EXCLUDED.total_bytes, skipped_count = EXCLUDED.skipped_count,"
    " folders_created = folder_uploads.folders_created + EXCLUDED.folders_created, updated_at = now()"
    " WHERE folder_uploads.user_id = EXCLUDED.user_id"
)
# A path whose folder was deleted and made again: its waiting items go to the new folder
# (before _SAVE_FOLDERS writes the new id over the old one).
_MOVE_WAITING = text(
    "UPDATE folder_upload_items i SET folder_id = w.f, updated_at = now()"
    " FROM unnest(CAST(:ids AS uuid[]), CAST(:keys AS text[])) AS w (f, k)"
    " JOIN folder_upload_folders m ON m.customer_id = :c AND m.folder_upload_id = :u AND m.path_key = w.k"
    " WHERE i.customer_id = :c AND i.folder_upload_id = :u AND i.state = 'waiting'"
    " AND i.folder_id = m.folder_id AND m.folder_id <> w.f"
)
# Each path once (two names equal once NFC are one path, one folder), with this list's index.
_SAVE_FOLDERS = text(
    "INSERT INTO folder_upload_folders (customer_id, folder_upload_id, idx, path_key, folder_id)"
    " SELECT DISTINCT ON (w.k) :c, :u, w.i - 1, w.k, w.f"
    " FROM unnest(CAST(:ids AS uuid[]), CAST(:keys AS text[])) WITH ORDINALITY AS w (f, k, i)"
    " ORDER BY w.k, w.i"
    " ON CONFLICT (customer_id, folder_upload_id, path_key)"
    " DO UPDATE SET idx = EXCLUDED.idx, folder_id = EXCLUDED.folder_id"
    " WHERE (folder_upload_folders.idx, folder_upload_folders.folder_id)"
    " IS DISTINCT FROM (EXCLUDED.idx, EXCLUDED.folder_id)"
)


def plan_folders(
    conn: Connection,
    customer_id: str,
    *,
    upload_id: str,
    user_id: str,
    target: Literal["department", "folder"],
    parent_folder_id: str | None,
    merge: bool,
    folders: Sequence[PlannedFolder],
    file_count: int,
    total_bytes: int,
    skipped_count: int,
    limits: FolderUploadLimits,
) -> Plan:
    """Make or merge the picked folder's tree (module notes) and record the upload. Raises
    PlanRefused, FolderNotFound (the parent, or another user's upload of this id) or
    FolderTooDeep; after PlanRefused("too_many_folders") the caller's transaction must roll
    back. The caller checks the list: entry 0 alone has no parent, every parent is earlier.
    O(F) rows in at most 2H + 12 statements."""
    if file_count > limits.max_files:
        raise PlanRefused("too_many_files")
    if total_bytes > limits.max_bytes:
        raise PlanRefused("too_large_upload")
    if len(folders) > limits.max_folders:
        raise PlanRefused("too_many_folders")
    params: dict[str, Any] = {"c": customer_id, "u": upload_id, "usr": user_id}
    if not conn.execute(_PLAN_LOCK, params).scalar_one():
        raise PlanRefused("plan_busy")
    _lock_tree(conn, customer_id)
    before = conn.execute(_UPLOAD, params).first()
    if before is None and conn.execute(_SENDING, params).scalar_one():
        raise PlanRefused("upload_in_progress")
    paths = [folders[0].name]
    for folder in folders[1:]:
        paths.append(f"{paths[folder.parent or 0]}/{folder.name}")
    params["keys"] = [hashlib.sha256(path.encode()).hexdigest() for path in paths]
    reuse: dict[int, tuple[str, str]] = {}
    if before is not None:
        if str(before[0]) != user_id:
            raise FolderNotFound()
        old_parent = None if before[2] is None else str(before[2])
        if (before[1], old_parent, before[3]) != (target, parent_folder_id, folders[0].name):
            raise PlanRefused("plan_changed")
        reuse = {int(i): (str(f), str(name)) for i, f, name in conn.execute(_REUSE, params)}
    levels = [1] * len(folders)
    for index, folder in enumerate(folders[1:], start=1):
        levels[index] = levels[folder.parent or 0] + 1
    _check_depth(conn, customer_id, folders, reuse, parent_folder_id)

    created = 0
    if 0 in reuse:
        root, root_name = reuse[0]
    else:
        root, root_name, made = _root(conn, params, target, parent_folder_id, merge, folders[0].name)
        created += made
    ids = [root] * len(folders)  # every entry is set below, level by level
    by_level: dict[int, list[int]] = {}
    for index in range(1, len(folders)):
        by_level.setdefault(levels[index], []).append(index)
    for level in sorted(by_level):
        pending = [index for index in by_level[level] if index not in reuse]
        for index in by_level[level]:
            if index in reuse:
                ids[index] = reuse[index][0]
        if not pending:
            continue
        rows = {
            **params,
            "parents": [ids[folders[index].parent or 0] for index in pending],
            "names": [folders[index].name for index in pending],
            "idx": pending,
        }
        created += conn.execute(_INSERT_LEVEL, rows).rowcount
        for index, folder in conn.execute(_FIND_LEVEL, rows):
            ids[int(index)] = str(folder)

    if (0 if before is None else int(before[4])) + created > limits.max_folders:
        raise PlanRefused("too_many_folders")
    upload = {
        **params,
        "target": target,
        "parent": parent_folder_id,
        "root": root,
        "source": folders[0].name,
        "root_name": root_name,
        "merge": merge,
        "files": file_count,
        "bytes": total_bytes,
        "skipped": skipped_count,
        "created": created,
    }
    if not conn.execute(_SAVE_UPLOAD, upload).rowcount:  # another user's upload of this id meanwhile
        raise FolderNotFound()
    if before is not None:
        conn.execute(_MOVE_WAITING, {**params, "ids": ids})
    conn.execute(_SAVE_FOLDERS, {**params, "ids": ids})
    return Plan(upload_id, root, root_name, ids, created, len(folders) - created, before is None)


def _check_depth(
    conn: Connection,
    customer_id: str,
    folders: Sequence[PlannedFolder],
    reuse: dict[int, tuple[str, str]],
    parent_folder_id: str | None,
) -> None:
    """FolderTooDeep when a folder to make would land deeper than MAX_DEPTH: each reused folder
    a new one goes in is measured now (it may have moved since), the rest follow the list.
    FolderNotFound for a missing parent. Two statements, O(k d) for k reused parents d deep."""
    at = [1] * len(folders)  # the level each entry is at in Kaagaz
    if 0 not in reuse and parent_folder_id is not None:
        depth = conn.execute(text(_DEPTH), {"c": customer_id, "f": parent_folder_id}).scalar_one()
        if depth is None:
            raise FolderNotFound()
        at[0] = int(depth) + 1
    made = [index for index in range(len(folders)) if index not in reuse]
    parents = {reuse[parent][0]: parent for index in made if (parent := folders[index].parent) in reuse}
    measured: dict[int, int] = {}
    if parents:
        for folder, depth in conn.execute(_DEPTHS, {"c": customer_id, "ids": list(parents)}):
            measured[parents[str(folder)]] = int(depth)
    for index in range(len(folders)):
        if index in measured:
            at[index] = measured[index]
        elif index:
            at[index] = at[folders[index].parent or 0] + 1
    if max((at[index] for index in made), default=0) > MAX_DEPTH:
        raise FolderTooDeep()


def _root(
    conn: Connection, params: dict[str, Any], target: str, parent_id: str | None, merge: bool, name: str
) -> tuple[str, str, int]:
    """The picked folder's own folder (id, name, 1 when made): with merge, a same-name folder
    in that place (for a department, only a top-level department); else a new one under the
    first free name, of kind department for "As a new department". The caller holds the tree
    lock. Two or three statements."""
    sibling = "parent_id IS NULL" if parent_id is None else "parent_id = CAST(:p AS uuid)"
    named = {**params, "p": parent_id, "name": name}
    if merge:
        where = sibling + (" AND kind = 'department'" if target == "department" else "")
        same = conn.execute(text(_SAME_NAME.format(where=where)), named).first()
        if same is not None:
            return str(same[0]), str(same[1]), 0
    names = [name, *(_numbered(name, n) for n in range(2, 101))]
    free = conn.execute(text(_FREE.format(where=sibling)), {**named, "names": names}).scalar()
    if free is None:
        raise FolderNameTaken()
    kind = "department" if target == "department" else "custom"
    made = conn.execute(_INSERT_ROOT, {**named, "name": free, "kind": kind}).scalar_one()
    return str(made), str(free), 1


def _numbered(name: str, n: int) -> str:
    """``Name (n)``, the name cut so the whole fits 100 characters. O(len(name))."""
    suffix = f" ({n})"
    return name[: NAME_MAX - len(suffix)].rstrip() + suffix
