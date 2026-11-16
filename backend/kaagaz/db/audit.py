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

# A3: the document's folder and family, or the folder itself, in the same statement. PRD 7 (#606):
# read whole (kaagaz_document_place, 0091): an Admin acting on a file they can't see (giving it,
# say) writes its folder and family too, so the Admin's log finds the row by its family.
_INSERT = text(
    "INSERT INTO audit_log (customer_id, actor_user_id, action, target_type, target_id, details,"
    " folder_id, family_id, subject_user_id)"
    " SELECT CAST(:c AS uuid), CAST(:actor AS uuid), CAST(:action AS text), CAST(:tt AS text),"
    " CAST(:tid AS text), CAST(:details AS jsonb), coalesce(d.folder_id, CAST(:folder AS uuid)), d.family_id,"
    " CAST(:subject AS uuid)"
    " FROM (SELECT 1) AS one"
    " LEFT JOIN kaagaz_document_place(CAST(:document AS uuid)) d"
    " ON CAST(:c AS uuid) = kaagaz_current_customer()"
)
# PRD 7 (#601): one row about the document :document per person in :subjects, with its own details.
_INSERT_ABOUT = text(
    "INSERT INTO audit_log (customer_id, actor_user_id, action, target_type, target_id, details,"
    " folder_id, family_id, subject_user_id)"
    " SELECT CAST(:c AS uuid), CAST(:actor AS uuid), CAST(:action AS text), 'document',"
    " CAST(:document AS text), s.details, d.folder_id, d.family_id, s.subject"
    " FROM unnest(CAST(:subjects AS uuid[]), CAST(:details AS jsonb[])) AS s (subject, details)"
    " LEFT JOIN kaagaz_document_place(CAST(:document AS uuid)) d"
    " ON CAST(:c AS uuid) = kaagaz_current_customer()"
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
    subject_user_id: str | None = None,
) -> None:
    """Write one audit row. O(log n): one indexed insert, with one key lookup for a document.
    ``subject_user_id``: the person the entry is about (PRD 7: given access, for example; WhatsApp's
    connect, #603)."""
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
            "subject": subject_user_id,
        },
    )


def record_about(
    conn: Connection,
    customer_id: str,
    *,
    actor_user_id: str,
    action: str,
    document_id: str,
    subjects: Mapping[str, Mapping[str, DetailValue]],
) -> None:
    """``record`` about one document for each person in ``subjects`` (their details), in one
    statement (PRD 7, #601: a file given to several people). O(people)."""
    if not _ACTION.match(action):
        raise ValueError("audit action must match ^[a-z_.]+$")
    conn.execute(
        _INSERT_ABOUT,
        {
            "c": customer_id,
            "actor": actor_user_id,
            "action": action,
            "document": document_id,
            "subjects": list(subjects),
            "details": [json.dumps(dict(details)) for details in subjects.values()],
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
    # PRD 7 section 14 (#600): pages the save changed, and pages left turned after it.
    "document.page_turns": ("pages", "turned"),
    "folder.create": ("parent_id",),
    "folder.move": ("parent_id",),
    "folder.kind": ("kind",),
    "folder.upload": ("files", "folders_created"),  # #484
    "version.suggested": _VERSION_DETAILS,
    "version.accepted": _VERSION_DETAILS,
    "version.rejected": _VERSION_DETAILS,
    "version.undone": _VERSION_DETAILS,
    "version.make_current": _VERSION_DETAILS,
    "document.version_separated": _VERSION_DETAILS,  # #521: made a separate file
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
    # PRD 7 (#601): the person an entry is about; only access and lock entries; only an Admin's
    # own access (details.self, an access entry).
    subject_user_id: str | None = None
    access_only: bool = False
    self_only: bool = False


Cursor = tuple[datetime, int]

# The target document's own row (any version), for its name: NULL once deleted.
_ENTRIES = (
    "SELECT a.id, a.action, a.actor_user_id, a.target_type, a.target_id, a.details, a.created_at,"
    " a.folder_id, a.family_id, a.subject_user_id, t.display_name AS document_name,"
    " t.version_no AS document_version_no,"
    " (t.id IS NOT NULL OR EXISTS (SELECT 1 FROM kaagaz_existing('document', ARRAY[CASE"
    " WHEN a.target_type = 'document' AND a.target_id ~ '^[0-9a-f-]{36}$' THEN CAST(a.target_id AS uuid)"
    " END]))) AS document_exists"
    " FROM audit_log a"
    " LEFT JOIN documents t ON t.customer_id = a.customer_id AND t.id = CASE WHEN a.target_type = 'document'"
    " AND a.target_id ~ '^[0-9a-f-]{36}$' THEN CAST(a.target_id AS uuid) END"
    " WHERE a.customer_id = :c"
)
# PRD 7 (#606): the Admin's log (shown_only false) names every file, the ones the Admin can't see too
# (decision 20): a page's rows whose join found no name take it from the owner-rights
# kaagaz_admin_document_names (0091, one call a page), which checks the caller. For an Admin only:
# Management's access activity (#601) reads the same entries and names no file it can't see.
_IS_ADMIN = text("SELECT coalesce((kaagaz_access()).admin, false)")
_ADMIN_NAMES = (
    "WITH e AS ({page}) SELECT e.id, e.action, e.actor_user_id, e.target_type, e.target_id, e.details,"
    " e.created_at, e.folder_id, e.family_id, e.subject_user_id,"
    " coalesce(e.document_name, n.display_name) AS document_name,"
    " coalesce(e.document_version_no, n.version_no) AS document_version_no,"
    " e.document_exists"
    " FROM e LEFT JOIN kaagaz_admin_document_names(ARRAY(SELECT CAST(x.target_id AS uuid) FROM e x"
    " WHERE x.target_type = 'document' AND x.document_name IS NULL AND x.target_id ~ '^[0-9a-f-]{{36}}$')) n"
    " ON e.target_type = 'document' AND e.document_name IS NULL AND n.id::text = e.target_id"
    " ORDER BY e.created_at DESC, e.id DESC"
)
_COUNT = "SELECT count(*) FROM audit_log a WHERE a.customer_id = :c"
# #484: "Add new files only"'s check of a page of files is the admin's alone. Changed on purpose
# (#648 review): so are Admins' and Management's decisions on possible versions, and (V-A) their
# links of files as versions and its Undo.
_SHOWN = (
    " AND split_part(a.action, '.', 1) = ANY(CAST(:kinds AS text[])) AND a.action NOT IN"
    " ('folder.upload_check', 'version.kept_apart', 'version.kept_apart_undone', 'version.later',"
    " 'version.linked', 'version.unlinked')"
)
# A5: rows written while in the family, and every row of its versions and of a document linked
# into it (accepted, or undone since), so a linked version's upload and suggestion show too.
# PRD 7 (#601): the linked documents come through the view, so for a family the Admin can't open
# the Admin's log under-reports them (never a leak); PR 9's owner-rights functions can widen it.
_OF_FAMILY = (
    " AND (a.family_id = CAST(:family AS uuid) OR (a.target_type = 'document' AND a.target_id IN ("
    "SELECT v.id::text FROM visible_documents v"
    " WHERE v.customer_id = :c AND v.family_id = CAST(:family AS uuid)"
    " UNION SELECT s.document_id::text FROM version_suggestions s WHERE s.customer_id = :c"
    " AND s.candidate_family_id = CAST(:family AS uuid) AND s.status IN ('accepted', 'undone'))))"
)
# PRD 7 (#595): the shown feeds (the dashboard's, a document's, a folder's) leave out each entry
# about a document the person cannot see, and each other entry in a folder they cannot see (an
# entry about a file follows the file alone, so a file given to them shows its history); one about
# a document or folder that is gone shows as before. The admin's log (shown_only false) keeps all.
# PRD 7 (#606): "gone" is asked of kaagaz_existing (0091), which reads the whole table: under the
# database rule a hidden file is missing from documents too, and would read as gone.
_TARGET = "CASE WHEN a.target_id ~ '^[0-9a-f-]{36}$' THEN CAST(a.target_id AS uuid) END"
_SEEN = (
    " AND (a.target_type <> 'document' OR EXISTS (SELECT 1 FROM visible_documents v"  # noqa: S608 - fixed fragments only
    f" WHERE v.customer_id = a.customer_id AND v.id = {_TARGET})"
    f" OR NOT EXISTS (SELECT 1 FROM kaagaz_existing('document', ARRAY[{_TARGET}])))"
    " AND (a.target_type = 'document' OR a.folder_id IS NULL"
    " OR EXISTS (SELECT 1 FROM visible_folders v WHERE v.customer_id = a.customer_id AND v.id = a.folder_id)"
    " OR NOT EXISTS (SELECT 1 FROM kaagaz_existing('folder', ARRAY[a.folder_id])))"
)
_IN_FOLDERS = " AND a.folder_id = ANY(CAST(:folders AS uuid[]))"
_BY = " AND a.actor_user_id = CAST(:actor AS uuid)"
_ON_DOCUMENT = " AND a.target_type = 'document' AND a.target_id = :document"
_ACTIONS = " AND a.action = ANY(CAST(:actions AS text[]))"
_ABOUT = " AND a.subject_user_id = CAST(:subject AS uuid)"
# PRD 7 (#601): written as audit_log_access's predicate (0090), so these reads use it.
_ACCESS = " AND (a.action LIKE 'access.%' OR a.action LIKE 'lock.%')"
_SELF = _ACCESS + " AND a.details @> '{\"self\": true}'"
_FROM = " AND a.created_at >= :start"
_TO = " AND a.created_at < :end"
_BEFORE = " AND (a.created_at, a.id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS bigint))"
_ORDER = " ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset"

_USER_NAMES = text(
    "SELECT id::text, display_name FROM users WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
)
# Each folder's path from the top, "Land & Legal / Land records" (100 levels at most), through the
# folders the person can see (PRD 7, #595): a hidden folder has no path, and a path stops below one.
_FOLDER_PATHS = text(
    "WITH RECURSIVE up (start, id, parent_id, name, depth) AS ("
    " SELECT id, id, parent_id, name, 1 FROM visible_folders"
    " WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
    " UNION ALL SELECT up.start, f.id, f.parent_id, f.name, up.depth + 1 FROM up"
    " JOIN visible_folders f ON f.customer_id = :c AND f.id = up.parent_id WHERE up.depth < 101)"
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
        (filters.subject_user_id is not None, _ABOUT, "subject", filters.subject_user_id),
        (filters.access_only, _ACCESS, "access", None),
        (filters.self_only, _SELF, "self", None),
        (filters.start is not None, _FROM, "start", filters.start),
        (filters.end is not None, _TO, "end", filters.end),
    )
    fragments = [_SEEN] if filters.shown_only else []
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
    statement = _ENTRIES + where + _ORDER
    if not filters.shown_only and conn.execute(_IS_ADMIN).scalar():  # the Admin's log: every file named
        statement = _ADMIN_NAMES.format(page=statement)
    return conn.execute(text(statement), params).mappings().all()


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
        about = row["subject_user_id"]  # PRD 7 (#601)
        users.update(filter(None, map(_uuid, (row["actor_user_id"], *owners, subject, about))))
        folders.update(
            filter(None, map(_uuid, (row["folder_id"], details.get("folder_id"), details.get("parent_id"))))
        )
    params = {"c": customer_id}
    people = conn.execute(_USER_NAMES, {**params, "ids": sorted(users)}).all() if users else []
    paths = conn.execute(_FOLDER_PATHS, {**params, "ids": sorted(folders)}).all() if folders else []
    return Names({str(i): str(n) for i, n in people}, {str(i): str(p) for i, p in paths})
