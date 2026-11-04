"""The audit log: one row for every change a person makes.

Why: "who changed what, and when" must be answerable later (docs/architecture.md,
repudiation). A row holds only ids, statuses and counts in ``details``, never
document text, file names, questions or identity numbers, so the log is safe
to show to the customer's admin.

Every function takes a connection from ``Database.customer(customer_id)``, so
the write happens in the same transaction as the change it records, and row
level security applies. The explicit ``customer_id`` filter is the second lock.

File management (Phase 2 PRD 1):

* A3: a row about a document also keeps the document's folder and family at write time
  (one lookup inside the insert); a row about a folder keeps the folder. Ids only.
* A1: ``record_view`` writes ``document.view`` at most once per person per document every
  VIEW_EVERY_MINUTES. #482 (3 Nov 2026): every other way of reading a document one by one (the
  viewer's file, page images, word lists, readings, cells, the clear copy, compare) shares that
  row and its half hour, with ``{"via": ...}`` saying which; the detail writes none.
* A5: ``entries`` is the one timeline query: the document timeline (a family), the folder
  timeline (a subtree), the dashboard's last 20 and the admin log with its filters, newest
  first with a cursor. Names are read when shown (``names``), never stored.
  ``Filters.shown_only`` (security review, 19 Oct 2026): what members see holds only
  document, version and folder actions, and their details only through SHOWN_DETAILS; the
  whole log, raw details included, is the admin's (GET /api/admin/audit and its export).
"""

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, RowMapping, text

_ACTION = re.compile(r"^[a-z_.]{1,64}$")
VIEW_EVERY_MINUTES = 30

# Only these kinds of values may go into details: ids, statuses and counts.
DetailValue = str | int | bool | None

# A3: the document's folder and family, or the folder itself, in the same statement.
_INSERT = text(
    "INSERT INTO audit_log (customer_id, actor_user_id, action, target_type, target_id, details,"
    " folder_id, family_id)"
    " SELECT CAST(:c AS uuid), CAST(:actor AS uuid), CAST(:action AS text), CAST(:tt AS text),"
    " CAST(:tid AS text), CAST(:details AS jsonb), coalesce(d.folder_id, CAST(:folder AS uuid)), d.family_id"
    " FROM (SELECT 1) AS one"
    " LEFT JOIN documents d ON d.customer_id = CAST(:c AS uuid) AND d.id = CAST(:document AS uuid)"
)
# #482: views of one document by one person wait for each other here, so two at once write one row.
_VIEW_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:c || ':' || :u || ':' || :d, 0))")
# A1: on the partial index audit_log_views (0068), this person's views of it in VIEW_EVERY_MINUTES.
_VIEWED = text(
    "SELECT 1 FROM audit_log WHERE customer_id = :c AND actor_user_id = :u AND action = 'document.view'"
    " AND target_id = :d AND created_at > now() - make_interval(mins => :m) LIMIT 1"
)


def _uuid(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def record(
    conn: Connection,
    customer_id: str,
    *,
    actor_user_id: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: Mapping[str, DetailValue] | None = None,
) -> None:
    """Write one audit row. O(log n): one indexed insert, with one key lookup for a document."""
    if not _ACTION.match(action):
        raise ValueError("audit action must match ^[a-z_.]+$")
    target = None if target_id is None else _uuid(target_id)
    conn.execute(
        _INSERT,
        {
            "c": customer_id,
            "actor": actor_user_id,
            "action": action,
            "tt": target_type,
            "tid": target_id,
            "details": json.dumps(dict(details or {})),
            "document": target if target_type == "document" else None,
            "folder": target if target_type == "folder" else None,
        },
    )


def recent(conn: Connection, customer_id: str, *, action: str, target_id: str, days: int) -> bool:
    """A row of ``action`` about ``target_id`` in the last ``days`` (#507 review: a promotion's
    re-approval). On audit_log_listing: O(this customer's rows in those days), for rare callers."""
    return (
        conn.execute(
            text(
                "SELECT 1 FROM audit_log WHERE customer_id = :c AND action = :a AND target_id = :t"
                " AND created_at > now() - make_interval(days => :d) LIMIT 1"
            ),
            {"c": customer_id, "a": action, "t": target_id, "d": days},
        ).first()
        is not None
    )


def record_view(
    conn: Connection, customer_id: str, *, actor_user_id: str, document_id: str, via: str | None = None
) -> bool:
    """A1: ``document.view``, unless this person viewed it in the last VIEW_EVERY_MINUTES.
    True when a row was written. ``via``: how it was read, when not the document's detail
    (#482). Three statements, O(log n): a transaction lock on (customer, person, document),
    held to the commit, so views at the same moment write one row; then the index probe."""
    params = {"c": customer_id, "u": actor_user_id, "d": document_id, "m": VIEW_EVERY_MINUTES}
    conn.execute(_VIEW_LOCK, params)
    if conn.execute(_VIEWED, params).first() is not None:
        return False
    record(
        conn,
        customer_id,
        actor_user_id=actor_user_id,
        action="document.view",
        target_type="document",
        target_id=document_id,
        details=None if via is None else {"via": via},
    )
    return True


# --- A5: the timelines ----------------------------------------------------------------

# What a member's timeline may say about each action: the fields the A4 words need, never
# raw details. ``views.audit_entry`` adds the names these ids resolve to.
# Stage 2 writes these on version.* rows (db/versions.py); ids and counts only. folder_id and
# from_folder_id let A4 say "moved to …" when accepting moves the document.
_VERSION_DETAILS = ("version_no", "candidate_family", "outcome", "family_id", "folder_id", "from_folder_id")
SHOWN_DETAILS: dict[str, tuple[str, ...]] = {
    "document.move": ("folder_id",),
    "document.autofile": ("folder_id",),
    "document.autofile_undo": ("folder_id",),
    "document.label_change": ("from", "to"),
    "document.owner_change": ("from", "to"),
    "document.reprocess": ("page_no",),
    "document.page_accept": ("page_no",),
    "folder.create": ("parent_id",),
    "folder.move": ("parent_id",),
    "folder.kind": ("kind",),
    "folder.upload": ("files", "folders_created"),  # #484
    "version.suggested": _VERSION_DETAILS,
    "version.accepted": _VERSION_DETAILS,
    "version.rejected": _VERSION_DETAILS,
    "version.undone": _VERSION_DETAILS,
    "version.make_current": _VERSION_DETAILS,
}
SHOWN_KINDS = ("document", "version", "folder")


@dataclass(frozen=True, slots=True)
class Filters:
    """Each left at its default filters nothing; ``folder_ids`` empty matches nothing
    (another customer's folder). ``document_id``: a document whose family is gone."""

    shown_only: bool = True
    family_id: str | None = None
    folder_ids: tuple[str, ...] | None = None
    actor_user_id: str | None = None
    document_id: str | None = None
    actions: tuple[str, ...] = ()
    start: datetime | None = None
    end: datetime | None = None


Cursor = tuple[datetime, int]

# The target document's own row (any version), for its name: NULL once deleted.
_ENTRIES = (
    "SELECT a.id, a.action, a.actor_user_id, a.target_type, a.target_id, a.details, a.created_at,"
    " a.folder_id, a.family_id, t.display_name AS document_name, t.version_no AS document_version_no,"
    " t.id IS NOT NULL AS document_exists"
    " FROM audit_log a"
    " LEFT JOIN documents t ON t.customer_id = a.customer_id AND t.id = CASE WHEN a.target_type = 'document'"
    " AND a.target_id ~ '^[0-9a-f-]{36}$' THEN CAST(a.target_id AS uuid) END"
    " WHERE a.customer_id = :c"
)
_COUNT = "SELECT count(*) FROM audit_log a WHERE a.customer_id = :c"
_SHOWN = " AND split_part(a.action, '.', 1) = ANY(CAST(:kinds AS text[]))"
# A5: rows written while in the family, and every row of its versions and of a document linked
# into it (accepted, or undone since), so a linked version's upload and suggestion show too.
_OF_FAMILY = (
    " AND (a.family_id = CAST(:family AS uuid) OR (a.target_type = 'document' AND a.target_id IN ("
    "SELECT v.id::text FROM documents v WHERE v.customer_id = :c AND v.family_id = CAST(:family AS uuid)"
    " UNION SELECT s.document_id::text FROM version_suggestions s WHERE s.customer_id = :c"
    " AND s.candidate_family_id = CAST(:family AS uuid) AND s.status IN ('accepted', 'undone'))))"
)
_IN_FOLDERS = " AND a.folder_id = ANY(CAST(:folders AS uuid[]))"
_BY = " AND a.actor_user_id = CAST(:actor AS uuid)"
_ON_DOCUMENT = " AND a.target_type = 'document' AND a.target_id = :document"
_ACTIONS = " AND a.action = ANY(CAST(:actions AS text[]))"
_FROM = " AND a.created_at >= :start"
_TO = " AND a.created_at < :end"
_BEFORE = " AND (a.created_at, a.id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS bigint))"
_ORDER = " ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset"

_USER_NAMES = text(
    "SELECT id::text, display_name FROM users WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
)
# Each folder's path from the top, "Land & Legal / Land records" (100 levels at most).
_FOLDER_PATHS = text(
    "WITH RECURSIVE up (start, id, parent_id, name, depth) AS ("
    " SELECT id, id, parent_id, name, 1 FROM folders"
    " WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
    " UNION ALL SELECT up.start, f.id, f.parent_id, f.name, up.depth + 1 FROM up"
    " JOIN folders f ON f.customer_id = :c AND f.id = up.parent_id WHERE up.depth < 101)"
    " SELECT start::text, string_agg(name, ' / ' ORDER BY depth DESC) FROM up GROUP BY start"
)


def _where(filters: Filters, params: dict[str, Any]) -> str:
    chosen = (
        (filters.shown_only, _SHOWN, "kinds", list(SHOWN_KINDS)),
        (filters.family_id is not None, _OF_FAMILY, "family", filters.family_id),
        (filters.folder_ids is not None, _IN_FOLDERS, "folders", list(filters.folder_ids or ())),
        (filters.actor_user_id is not None, _BY, "actor", filters.actor_user_id),
        (filters.document_id is not None, _ON_DOCUMENT, "document", filters.document_id),
        (bool(filters.actions), _ACTIONS, "actions", list(filters.actions)),
        (filters.start is not None, _FROM, "start", filters.start),
        (filters.end is not None, _TO, "end", filters.end),
    )
    fragments = []
    for wanted, fragment, name, value in chosen:
        if wanted:
            fragments.append(fragment)
            params[name] = value
    return "".join(fragments)


def entries(
    conn: Connection,
    customer_id: str,
    filters: Filters,
    *,
    limit: int,
    cursor: Cursor | None = None,
    offset: int = 0,
) -> Sequence[RowMapping]:
    """A5: at most ``limit`` rows, newest first, older than ``cursor`` (the last row's
    created_at and id) when given. One query on audit_log_family, _folder, _actor or
    _listing (the filter's): O(limit + offset) rows read past the filters."""
    if filters.folder_ids == ():
        return []
    params: dict[str, Any] = {"c": customer_id, "limit": limit, "offset": offset}
    where = _where(filters, params)
    if cursor is not None:
        where += _BEFORE
        params["before_at"], params["before_id"] = cursor
    return conn.execute(text(_ENTRIES + where + _ORDER), params).mappings().all()


def count(conn: Connection, customer_id: str, filters: Filters) -> int:
    """How many rows ``filters`` keeps. One count: O(n) for the n rows kept."""
    if filters.folder_ids == ():
        return 0
    params: dict[str, Any] = {"c": customer_id}
    return int(conn.execute(text(_COUNT + _where(filters, params)), params).scalar_one())


@dataclass(frozen=True, slots=True)
class Names:
    """Names read when shown (A5): people by id, folders' paths by id."""

    users: Mapping[str, str]
    folders: Mapping[str, str]


NO_NAMES = Names({}, {})


def names(conn: Connection, customer_id: str, rows: Sequence[RowMapping]) -> Names:
    """The actors, the owners in owner changes, the people account events are about (issue
    375) and every folder ``rows`` name. Two queries whatever the page size: O(r) ids, each
    path O(depth)."""
    users: set[str] = set()
    folders: set[str] = set()
    for row in rows:
        details = row["details"] if isinstance(row["details"], dict) else {}
        owners = (details.get("from"), details.get("to")) if row["action"] == "document.owner_change" else ()
        subject = row["target_id"] if row["target_type"] == "user" else None
        users.update(filter(None, map(_uuid, (row["actor_user_id"], *owners, subject))))
        folders.update(
            filter(None, map(_uuid, (row["folder_id"], details.get("folder_id"), details.get("parent_id"))))
        )
    params = {"c": customer_id}
    people = conn.execute(_USER_NAMES, {**params, "ids": sorted(users)}).all() if users else []
    paths = conn.execute(_FOLDER_PATHS, {**params, "ids": sorted(folders)}).all() if folders else []
    return Names({str(i): str(n) for i, n in people}, {str(i): str(p) for i, p in paths})
