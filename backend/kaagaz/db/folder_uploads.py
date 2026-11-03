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
  - Merge (the default): a same-name folder already there (any letter case, as
    ``folders_sibling_name`` compares) is used at every level. Keep separate makes the root
    ``Name (2)`` or the first free ``(n)``. "As a new department" merges only into a top-level
    department, never into a custom folder of that name.
  - A re-plan of the same upload (``id``) reuses the folder each index got the first time
    (``folder_upload_folders.idx``), even after a rename or move in Kaagaz; only new indexes,
    or folders deleted since, are made or merged again. The browser sends the old list first,
    in the same order, and new folders after it.
  - The uploader's folders are never pruned: ``folders.prune_empty`` deletes only folders
    audited ``folder.create`` with ``auto``, and the plan writes no such row.
  Cost: O(F) rows in at most 2H + 8 statements for F folders H levels deep (H <= 100).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Connection, text

from kaagaz.db.folders import (
    _DEPTH,
    _MAX_DEPTH,
    FolderError,
    FolderNameTaken,
    FolderNotFound,
    FolderTooDeep,
    _lock_tree,
)

GIB = 1024**3
# The server's defaults (Tushar, 3 Nov 2026) and the most the super admin may set (0061c's CHECKs).
DEFAULT_MAX_FILES, DEFAULT_MAX_GB, DEFAULT_MAX_FOLDERS = 50_000, 5, 10_000
MOST_FILES, MOST_GB, MOST_FOLDERS = 200_000, 1024, 50_000


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
    """``code``: too_many_files, too_large_upload, too_many_folders or plan_changed."""


_UPLOAD = text(
    "SELECT user_id, target, parent_folder_id, source_name, folders_created FROM folder_uploads"
    " WHERE customer_id = :c AND id = :u FOR UPDATE"
)
# The folder each index got before, while it still exists (a re-plan), and its name now.
_REUSE = text(
    "SELECT m.idx, f.id, f.name FROM folder_upload_folders m"
    " JOIN folders f ON f.customer_id = :c AND f.id = m.folder_id"
    " WHERE m.customer_id = :c AND m.folder_upload_id = :u"
)
_SAME_NAME = "SELECT id, name FROM folders WHERE customer_id = :c AND lower(name) = lower(:name) AND {where}"
# The first candidate name no sibling has, compared as folders_sibling_name compares (lower()).
_FREE = (
    "SELECT w.n FROM unnest(CAST(:names AS text[])) WITH ORDINALITY AS w (n, i)"
    " WHERE NOT EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = :c AND {where}"
    " AND lower(f.name) = lower(w.n)) ORDER BY w.i LIMIT 1"
)
_INSERT_ROOT = text(
    "INSERT INTO folders (customer_id, parent_id, name, created_by, kind)"
    " VALUES (:c, :p, :name, :usr, :kind) RETURNING id"
)
# One level: every new folder in one insert (a same-name one already there, or one earlier in
# this insert, is kept: DO NOTHING), then every folder of the level found by name, on the
# folders_sibling_name index.
_INSERT_LEVEL = text(
    "INSERT INTO folders (customer_id, parent_id, name, created_by, kind)"
    " SELECT :c, w.p, w.n, :usr, 'custom'"
    " FROM unnest(CAST(:parents AS uuid[]), CAST(:names AS text[])) AS w (p, n)"
    " ON CONFLICT DO NOTHING"
)
_FIND_LEVEL = text(
    "SELECT w.i, f.id"
    " FROM unnest(CAST(:parents AS uuid[]), CAST(:names AS text[]), CAST(:idx AS integer[])) AS w (p, n, i)"
    " JOIN folders f ON f.customer_id = :c AND f.parent_id = w.p AND lower(f.name) = lower(w.n)"
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
)
_SAVE_FOLDERS = text(
    "INSERT INTO folder_upload_folders (customer_id, folder_upload_id, idx, folder_id)"
    " SELECT :c, :u, w.i - 1, w.f FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS w (f, i)"
    " ON CONFLICT (customer_id, folder_upload_id, idx) DO UPDATE SET folder_id = EXCLUDED.folder_id"
    " WHERE folder_upload_folders.folder_id <> EXCLUDED.folder_id"
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
    O(F) rows in at most 2H + 8 statements."""
    if file_count > limits.max_files:
        raise PlanRefused("too_many_files")
    if total_bytes > limits.max_bytes:
        raise PlanRefused("too_large_upload")
    if len(folders) > limits.max_folders:
        raise PlanRefused("too_many_folders")
    _lock_tree(conn, customer_id)
    params: dict[str, Any] = {"c": customer_id, "u": upload_id, "usr": user_id}
    before = conn.execute(_UPLOAD, params).first()
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
    # ponytail: a reused folder moved deeper in Kaagaz since is not measured again, only the root.
    start = reuse[0][0] if 0 in reuse else parent_folder_id
    base = 0
    if start is not None:
        depth = conn.execute(text(_DEPTH), {"c": customer_id, "f": start}).scalar_one()
        if depth is None:
            raise FolderNotFound()
        base = int(depth) - (1 if 0 in reuse else 0)
    if base + max(levels) > _MAX_DEPTH:
        raise FolderTooDeep()

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
    conn.execute(_SAVE_UPLOAD, upload)
    conn.execute(_SAVE_FOLDERS, {**params, "ids": ids})
    return Plan(upload_id, root, root_name, ids, created, len(folders) - created, before is None)


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
