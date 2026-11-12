"""The files of a folder upload (#484, Tushar, 3 Nov 2026): registered, sent, and read back.

Why here: an item is the server's record of one file: its folder and batch are fixed when it
is registered (a re-plan that makes a deleted folder again moves its waiting items to the new
one, ``db/folder_uploads.py``), so a retry, a second tab or a "Continue upload" after a reload
always sends the file to the folder recorded here. Every function takes a connection from
``Database.customer(customer_id)`` and also filters on ``customer_id``; only the uploader sees
an upload.

* ``register_items``: a page of at most 1,000 files, idempotent by ``path_key`` (an item
  already there comes back as it is). The counts registered are kept on the upload row, so
  the limits are checked in O(1) a page, never by counting the items again; the bytes leave out
  a file refused for its type (by the browser, which leaves it out of the plan's bytes too) or
  its unsafe name (#516). A file Kaagaz
  can't take (empty, too large, a type the browser refused, or ``bad_name``: an unsafe name, or
  the name of another file of its folder once NFC) is registered ``failed`` with its code, so
  nothing is dropped. Item ``seq`` goes into batch ``seq // cap`` (``batch_for``), so a batch
  never holds more than MAX_FILES_PER_BATCH and ``batch_full`` can't happen.
* "Add new files only": a file registered with the browser's SHA-256 (``NewItem.sha256``) of a
  document of this organisation of the same size (``_ALREADY_IN``, on UNIQUE (customer_id,
  sha256)) is registered ``duplicate`` of it, so it is never sent. The hash is only looked up,
  never kept; no document is written or moved, and a file that is sent is hashed again by the
  server. Already-in files are not counted against the limits (``items_counted``,
  ``bytes_registered``), only against the declared count. A page that looked hashes up is
  audited ``folder.upload_check`` (counts only; only the admin's log shows it). Access: any
  user of the organisation may open any of its documents today, Trash included
  (``listing.get_document``); per-user access (NB-46) adds its rule to ``_ALREADY_IN``.
* ``finish_item`` and ``fail_item`` move a ``waiting`` item only, in one statement each, so two
  sends of one item end in one state. A send whose document already exists is ``stored`` when
  that document is this item's own (the same uploader, folder, batch and name: an earlier send
  stored it, then died before finishing), else ``duplicate`` (Kaagaz already had the file).
* ``summaries`` counts the items by state on the index ``folder_upload_items_state`` (a zip the
  worker unpacked, #522, is not listed: the zip's own document shows it); only one
  upload's ``progress`` joins documents (reading, ready, a problem), under a statement timeout.
  A stored file the reading pause holds (``documents.reading_held``, #478) is counted ``held``,
  never reading, with why (``held_reason``: the budget first, as ``pause_reason``).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Connection, text

from kaagaz.db import audit
from kaagaz.db.documents import REFUSED_ALONE
from kaagaz.db.folder_uploads import FolderUploadLimits

FAILURE_CODES = ("too_large", "unsupported_type", "empty_file", "folder_gone", "bad_name")
View = Literal["waiting", "attention", "duplicate", "all"]
RECENT_DAYS, RECENT_MAX = 7, 20
TIMEOUT_MS = 5000


@dataclass(frozen=True, slots=True)
class NewItem:
    path_key: str
    name: str  # already cleaned (NFC) by the caller
    folder_id: str
    size: int
    refused: str | None = None  # unsupported_type (the browser's), or bad_name (the caller's)
    sha256: str | None = None  # the browser's, of the file's bytes: only looked up, never kept


@dataclass(frozen=True, slots=True)
class ItemRow:
    id: str
    upload_id: str
    seq: int
    path_key: str
    name: str
    folder_id: str
    size: int
    batch_id: str
    state: str
    failure_code: str | None
    document_id: str | None
    document_status: str | None  # None also when the document is gone
    document_failure_code: str | None
    document_folder_id: str | None
    in_trash: bool


@dataclass(frozen=True, slots=True)
class Summary:
    id: str
    root_folder_id: str | None
    root_name: str
    source_name: str
    target: str
    parent_folder_id: str | None
    merge: bool
    created_at: datetime
    file_count: int
    total_bytes: int
    skipped_count: int
    registered: int
    waiting: int
    stored: int
    duplicate: int
    failed: int
    reading: int | None  # these five only for one upload (``progress``)
    ready: int | None
    problem: int | None
    held: int | None  # waiting for the reading pause (#478), not counted in reading
    held_reason: str | None  # DAILY_LIMIT or BUDGET (kaagaz.jobs.reading_pause); None with none held


class ItemsRefused(Exception):
    """``code``: not_found, bad_request, too_many_files or too_large_upload."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def batch_for(upload_id: str, seq: int, cap: int) -> str:
    """The batch of item ``seq``: one batch a ``cap`` items, the same id every time. O(1)."""
    return str(uuid.uuid5(uuid.UUID(upload_id), str(seq // cap)))


_COLUMNS = (
    "SELECT i.id, i.folder_upload_id, i.seq, i.path_key, i.name, i.folder_id, i.size_bytes, i.batch_id,"
    " i.state, i.failure_code, i.document_id, d.status, d.failure_code, d.folder_id,"
    " coalesce(d.deleted_at IS NOT NULL, false)"
    " FROM folder_upload_items i"
    " LEFT JOIN documents d ON d.customer_id = i.customer_id AND d.id = i.document_id"
)
_LOCK = text(
    "SELECT file_count, total_bytes, items_registered, items_counted, bytes_registered, root_folder_id"
    " FROM folder_uploads"
    " WHERE customer_id = :c AND id = :u AND user_id = :usr FOR UPDATE"
)
_UNKNOWN_FOLDERS = text(
    "SELECT count(*) FROM (SELECT DISTINCT w.f FROM unnest(CAST(:folders AS uuid[])) AS w (f)) AS w"
    " WHERE NOT EXISTS (SELECT 1 FROM folder_upload_folders m"
    " WHERE m.customer_id = :c AND m.folder_upload_id = :u AND m.folder_id = w.f)"
)
_KNOWN_KEYS = text(
    "SELECT path_key FROM folder_upload_items"
    " WHERE customer_id = :c AND folder_upload_id = :u AND path_key = ANY(CAST(:keys AS text[]))"
)
# "Add new files only": the document of this organisation each hash and size is, if any; a
# refused copy alone is not, so the file is sent and replaces it (#554, ``db/documents.py``).
_ALREADY_IN = text(
    "SELECT w.k, d.id FROM unnest(CAST(:keys AS text[]), CAST(:hashes AS text[]), CAST(:sizes AS bigint[]))"  # noqa: S608 - a fixed fragment
    " AS w (k, h, s) JOIN documents d ON d.customer_id = :c AND d.sha256 = w.h AND d.size_bytes = w.s"
    " AND NOT (" + REFUSED_ALONE + ")"
)
# Each row: failed with its code, else a duplicate of the document it already is, else waiting.
_INSERT = text(
    "INSERT INTO folder_upload_items (customer_id, folder_upload_id, seq, path_key, name, folder_id,"
    " size_bytes, batch_id, state, failure_code, document_id)"
    " SELECT :c, :u, w.seq, w.k, w.n, w.f, w.s, w.b,"
    " CASE WHEN w.code IS NOT NULL THEN 'failed' WHEN w.d IS NOT NULL THEN 'duplicate' ELSE 'waiting' END,"
    " w.code, CASE WHEN w.code IS NULL THEN w.d END"
    " FROM unnest(CAST(:seqs AS integer[]), CAST(:keys AS text[]), CAST(:names AS text[]),"
    " CAST(:folders AS uuid[]), CAST(:sizes AS bigint[]), CAST(:batches AS uuid[]), CAST(:codes AS text[]),"
    " CAST(:docs AS uuid[])) AS w (seq, k, n, f, s, b, code, d)"
)
_COUNTED = text(
    "UPDATE folder_uploads SET items_registered = :n, items_counted = :counted, bytes_registered = :bytes,"
    " updated_at = now() WHERE customer_id = :c AND id = :u"
)
# shortcut: no index on (folder_id, name), so this reads the upload's items, O(n) a page; add
# one to 0071 if registering a 200,000-file upload gets slow.
_SAME_NAMES = text(
    "SELECT i.folder_id, i.name FROM folder_upload_items i"
    " JOIN unnest(CAST(:folders AS uuid[]), CAST(:names AS text[])) AS w (f, n)"
    " ON i.folder_id = w.f AND i.name = w.n"
    " WHERE i.customer_id = :c AND i.folder_upload_id = :u"
)
_BY_KEYS = text(
    _COLUMNS
    + " WHERE i.customer_id = :c AND i.folder_upload_id = :u AND i.path_key = ANY(CAST(:keys AS text[]))"
)


def register_items(
    conn: Connection,
    customer_id: str,
    upload_id: str,
    *,
    user_id: str,
    items: Sequence[NewItem],
    batch_cap: int,
    largest_limit: int,
    limits: FolderUploadLimits,
) -> list[ItemRow]:
    """Register these files of this user's upload (module notes); every item, new or known, in
    the request's order. Raises ItemsRefused before anything is written: not_found (no such
    upload of this user), bad_request (a folder not in its plan, a key twice), too_many_files
    (past the plan's count, or the files not already in past the limit) or too_large_upload
    (their bytes past the plan's or the limit). At most nine statements: O(k log n) for k items
    in a page of an upload of n, O(k log D) for the hashes against D documents, and O(n) for
    the names (_SAME_NAMES)."""
    keys = [item.path_key for item in items]
    if len(set(keys)) != len(keys):
        raise ItemsRefused("bad_request")
    params: dict[str, Any] = {"c": customer_id, "u": upload_id, "usr": user_id, "keys": keys}
    upload = conn.execute(_LOCK, params).first()
    if upload is None:
        raise ItemsRefused("not_found")
    folders = [item.folder_id for item in items]
    if conn.execute(_UNKNOWN_FOLDERS, {**params, "folders": folders}).scalar_one():
        raise ItemsRefused("bad_request")
    known = {str(key) for key in conn.execute(_KNOWN_KEYS, params).scalars()}
    new = [item for item in items if item.path_key not in known]
    if new:
        _register_new(conn, params, upload, new, batch_cap, largest_limit, limits)
    found = {row.path_key: row for row in map(_item, conn.execute(_BY_KEYS, params))}
    return [found[key] for key in keys]


def _register_new(
    conn: Connection,
    params: dict[str, Any],
    upload: Sequence[Any],
    new: Sequence[NewItem],
    batch_cap: int,
    largest_limit: int,
    limits: FolderUploadLimits,
) -> None:
    """Insert the new items (failed, already in, or waiting), count them and audit the hashes
    looked up (``register_items``)."""
    file_count, total_bytes, registered, counted, counted_bytes = (int(value) for value in upload[:5])
    places = {"folders": [item.folder_id for item in new], "names": [item.name for item in new]}
    taken = {(str(f), str(n)) for f, n in conn.execute(_SAME_NAMES, {**params, **places})}
    codes = []
    for item in new:  # names are NFC already, so equal here is equal once NFC
        code = _refused(item, largest_limit)
        codes.append("bad_name" if code is None and (item.folder_id, item.name) in taken else code)
        taken.add((item.folder_id, item.name))
    looked_up = [item for item, code in zip(new, codes, strict=True) if code is None and item.sha256]
    already: dict[str, str] = {}
    if looked_up:
        hashes = {
            "keys": [item.path_key for item in looked_up],
            "hashes": [item.sha256 for item in looked_up],
            "sizes": [item.size for item in looked_up],
        }
        already = {str(k): str(d) for k, d in conn.execute(_ALREADY_IN, {**params, **hashes})}
    sent = [item for item in new if item.path_key not in already]
    count, counted = registered + len(new), counted + len(sent)
    # A file refused here by its type or name is never sent, and the browser leaves the files it
    # refused out of the plan's bytes (#516), so their bytes are not counted.
    counted_bytes += sum(item.size for item in sent if item.refused is None)
    if count > file_count or counted > limits.max_files:
        raise ItemsRefused("too_many_files")
    if counted_bytes > min(total_bytes, limits.max_bytes):
        raise ItemsRefused("too_large_upload")
    upload_id = str(params["u"])
    seqs = list(range(registered, count))
    rows = {
        **params,
        **places,
        "seqs": seqs,
        "keys": [item.path_key for item in new],
        "sizes": [item.size for item in new],
        "batches": [batch_for(upload_id, seq, batch_cap) for seq in seqs],
        "codes": codes,
        "docs": [already.get(item.path_key) for item in new],
    }
    conn.execute(_INSERT, rows)
    conn.execute(_COUNTED, {**params, "n": count, "counted": counted, "bytes": counted_bytes})
    if looked_up:
        audit.record(
            conn,
            str(params["c"]),
            actor_user_id=str(params["usr"]),
            action="folder.upload_check",
            target_type="folder",
            target_id=None if upload[5] is None else str(upload[5]),
            details={"folder_upload_id": upload_id, "checked": len(looked_up), "already_in": len(already)},
        )


def _refused(item: NewItem, largest_limit: int) -> str | None:
    """Why a file is registered failed: the browser's reason, else empty or too large; None: it waits."""
    if item.refused is not None:
        return item.refused
    if item.size == 0:
        return "empty_file"
    return "too_large" if item.size > largest_limit else None


_OWNED = text("SELECT 1 FROM folder_uploads WHERE customer_id = :c AND id = :u AND user_id = :usr")


def owned(conn: Connection, customer_id: str, upload_id: str, *, user_id: str) -> bool:
    """Is this upload this user's? O(log u)."""
    return conn.execute(_OWNED, {"c": customer_id, "u": upload_id, "usr": user_id}).first() is not None


_ITEM = text(
    _COLUMNS + " JOIN folder_uploads u ON u.customer_id = i.customer_id AND u.id = i.folder_upload_id"
    " WHERE i.customer_id = :c AND i.id = :i AND u.user_id = :usr"
)


def item(conn: Connection, customer_id: str, item_id: str, *, user_id: str) -> ItemRow | None:
    """One item of this user's uploads, with its document's state; None otherwise. O(log n)."""
    row = conn.execute(_ITEM, {"c": customer_id, "i": item_id, "usr": user_id}).first()
    return None if row is None else _item(row)


# The document is the item's own copy when an earlier send stored it (same uploader, folder,
# batch and name) and then died before it was finished: stored, not a duplicate of itself.
_OWN = text(
    "SELECT d.id FROM folder_upload_items i JOIN documents d ON d.customer_id = i.customer_id"
    " AND d.uploaded_by = :usr AND d.folder_id = i.folder_id AND d.batch_id = i.batch_id"
    " AND d.display_name = i.name WHERE i.customer_id = :c AND i.id = :i ORDER BY d.created_at, d.id LIMIT 1"
)
_FINISH = text(
    "UPDATE folder_upload_items i SET document_id = :d, updated_at = now(),"
    " state = CASE WHEN NOT :dup OR EXISTS (SELECT 1 FROM documents d WHERE d.customer_id = :c"
    " AND d.id = :d AND d.uploaded_by = :usr AND d.folder_id = i.folder_id AND d.batch_id = i.batch_id"
    " AND d.display_name = i.name) THEN 'stored' ELSE 'duplicate' END"
    " WHERE i.customer_id = :c AND i.id = :i AND i.state = 'waiting'"
)
_FAIL = text(
    "UPDATE folder_upload_items SET state = 'failed', failure_code = :code, updated_at = now()"
    " WHERE customer_id = :c AND id = :i AND state = 'waiting'"
)


def finish_item(
    conn: Connection, customer_id: str, item_id: str, *, document_id: str, duplicate: bool, user_id: str
) -> None:
    """A waiting item's file is in: ``stored``, or ``duplicate`` of another document (module
    notes). An item no longer waiting is left as it is. One statement, O(log n)."""
    params = {"c": customer_id, "i": item_id, "d": document_id, "dup": duplicate, "usr": user_id}
    conn.execute(_FINISH, params)


def own_document(conn: Connection, customer_id: str, item_id: str, *, user_id: str) -> str | None:
    """A14: the document an earlier run stored for this item (``_FINISH``'s rule) before it stopped
    short of finishing it; None for none. One statement, on the batch's documents."""
    found = conn.execute(_OWN, {"c": customer_id, "i": item_id, "usr": user_id}).scalar()
    return None if found is None else str(found)


def fail_item(conn: Connection, customer_id: str, item_id: str, code: str) -> None:
    """A waiting item failed for good with ``code`` (FAILURE_CODES). One statement, O(log n)."""
    conn.execute(_FAIL, {"c": customer_id, "i": item_id, "code": code})


_SENT = text(
    "SELECT coalesce(sum(i.size_bytes), 0) FROM folder_upload_items i"
    " JOIN folder_uploads u ON u.customer_id = i.customer_id AND u.id = i.folder_upload_id"
    " WHERE i.customer_id = :c AND i.folder_upload_id = :u AND u.user_id = :usr"
    " AND i.state IN ('stored', 'duplicate', 'failed')"
)


def sent_bytes(conn: Connection, customer_id: str, upload_id: str, *, user_id: str) -> int:
    """#516: the bytes of this user's upload no longer to be sent (stored, a duplicate, or failed);
    0 for a new upload. A re-plan ("Continue upload") checks only the rest against the storage
    limit, so the files it sent are not counted twice and a failed one, never stored, not at all.
    shortcut: a file the browser refused is failed too, though the plan's bytes left it out, so a
    re-plan can pass by its bytes; the upload service still refuses each file past the limit. Mark
    the browser's refusals apart if that matters. O(k) for its k items, on
    ``folder_upload_items_state``."""
    return int(conn.execute(_SENT, {"c": customer_id, "u": upload_id, "usr": user_id}).scalar_one())


_UPLOADS = (
    "SELECT id, root_folder_id, root_name, target, created_at, file_count, total_bytes, skipped_count,"
    " items_registered, source_name, parent_folder_id, merge"
    " FROM folder_uploads WHERE customer_id = :c AND user_id = :usr"
)
_RECENT = text(
    _UPLOADS + " AND zip_document_id IS NULL AND created_at > now() - make_interval(days => :days)"
    " ORDER BY created_at DESC LIMIT :limit"
)
_ONE = text(_UPLOADS + " AND id = :u")
_STATES = text(
    "SELECT folder_upload_id, state, count(*) FROM folder_upload_items"
    " WHERE customer_id = :c AND folder_upload_id = ANY(CAST(:ids AS uuid[]))"
    " GROUP BY folder_upload_id, state"
)
_SET_TIMEOUT = text("SELECT set_config('statement_timeout', :ms, true)")
# Held: as the release sweep's (reading_days.waiting). min(): 'budget' sorts before
# 'daily_limit', so the budget is named first, as pause_reason does.
_PROGRESS = text(
    "SELECT count(*) FILTER (WHERE d.status IN ('received', 'clean') AND d.reading_held IS NULL),"
    " count(*) FILTER (WHERE d.status = 'ready'),"
    " count(*) FILTER (WHERE d.status IN ('failed', 'rejected', 'needs_reader')),"
    " count(*) FILTER (WHERE d.status IN ('received', 'clean') AND d.reading_held IS NOT NULL),"
    " min(d.reading_held) FILTER (WHERE d.status IN ('received', 'clean'))"
    " FROM folder_upload_items i JOIN documents d ON d.customer_id = i.customer_id AND d.id = i.document_id"
    " WHERE i.customer_id = :c AND i.folder_upload_id = :u AND i.state = 'stored'"
)


def summaries(conn: Connection, customer_id: str, *, user_id: str) -> list[Summary]:
    """This user's folder uploads of the last RECENT_DAYS, newest first, at most RECENT_MAX, with
    their items counted by state (no documents read). Two statements, O(u log n + items)."""
    params = {"c": customer_id, "usr": user_id, "days": RECENT_DAYS, "limit": RECENT_MAX}
    return _summaries(conn, params, conn.execute(_RECENT, params).all())


def progress(conn: Connection, customer_id: str, upload_id: str, *, user_id: str) -> Summary | None:
    """One upload of this user, its items counted by state and its stored files by where their
    reading is (held by the reading pause apart); None for none. Three statements under
    TIMEOUT_MS, O(n log D) for n items."""
    params = {"c": customer_id, "usr": user_id, "u": upload_id}
    found = _summaries(conn, params, conn.execute(_ONE, params).all())
    if not found:
        return None
    conn.execute(_SET_TIMEOUT, {"ms": str(TIMEOUT_MS)})
    reading, ready, problem, held, reason = conn.execute(_PROGRESS, params).one()
    return replace(
        found[0],
        reading=int(reading),
        ready=int(ready),
        problem=int(problem),
        held=int(held),
        held_reason=reason,
    )


@dataclass(frozen=True, slots=True)
class Previous:
    id: str
    target: str
    parent_folder_id: str | None
    root_folder_id: str
    created_at: datetime


# Newest first on folder_uploads_owner; a root deleted since is passed over.
_PREVIOUS = text(
    "SELECT u.id, u.target, u.parent_folder_id, u.root_folder_id, u.created_at FROM folder_uploads u"
    " WHERE u.customer_id = :c AND u.user_id = :usr AND u.source_name = :name"
    " AND EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = :c AND f.id = u.root_folder_id)"
    " ORDER BY u.created_at DESC LIMIT 1"
)


def previous(conn: Connection, customer_id: str, *, user_id: str, source_name: str) -> Previous | None:
    """This user's latest upload of a folder named ``source_name`` (cleaned, as the plan keeps
    it) whose root folder is still there; None for none. One statement, O(u log F) for this
    user's u uploads."""
    params = {"c": customer_id, "usr": user_id, "name": source_name}
    row = conn.execute(_PREVIOUS, params).first()
    if row is None:
        return None
    parent = None if row[2] is None else str(row[2])
    return Previous(str(row[0]), str(row[1]), parent, str(row[3]), row[4])


def _summaries(conn: Connection, params: dict[str, Any], uploads: Sequence[Any]) -> list[Summary]:
    ids = [str(row[0]) for row in uploads]
    counts: dict[tuple[str, str], int] = {}
    if ids:
        for upload, state, n in conn.execute(_STATES, {**params, "ids": ids}):
            counts[str(upload), str(state)] = int(n)
    return [
        Summary(
            id=str(row[0]),
            root_folder_id=None if row[1] is None else str(row[1]),
            root_name=str(row[2]),
            source_name=str(row[9]),
            target=str(row[3]),
            parent_folder_id=None if row[10] is None else str(row[10]),
            merge=bool(row[11]),
            created_at=row[4],
            file_count=int(row[5]),
            total_bytes=int(row[6]),
            skipped_count=int(row[7]),
            registered=int(row[8]),
            waiting=counts.get((str(row[0]), "waiting"), 0),
            stored=counts.get((str(row[0]), "stored"), 0),
            duplicate=counts.get((str(row[0]), "duplicate"), 0),
            failed=counts.get((str(row[0]), "failed"), 0),
            reading=None,
            ready=None,
            problem=None,
            held=None,
            held_reason=None,
        )
        for row in uploads
    ]


_PAGE = {
    "waiting": " AND i.state = 'waiting'",
    "duplicate": " AND i.state = 'duplicate'",
    "attention": " AND (i.state = 'failed' OR (i.state = 'stored'"
    " AND d.status IN ('failed', 'rejected', 'needs_reader')))",
    "all": "",
}


def list_items(
    conn: Connection, customer_id: str, upload_id: str, *, view: View, after_seq: int, limit: int
) -> list[ItemRow]:
    """One page of an upload's items in ``seq`` order after ``after_seq``: those waiting, those
    needing attention (failed, or their document failed, was rejected or can't be read), the
    duplicates, or all. The caller checks the upload is the user's (``progress``). One statement
    under TIMEOUT_MS on the state index, O(limit log n) (attention: O(n) at most)."""
    conn.execute(_SET_TIMEOUT, {"ms": str(TIMEOUT_MS)})
    statement = text(
        _COLUMNS
        + " WHERE i.customer_id = :c AND i.folder_upload_id = :u AND i.seq > :after"
        + _PAGE[view]
        + " ORDER BY i.seq LIMIT :limit"
    )
    params = {"c": customer_id, "u": upload_id, "after": after_seq, "limit": limit}
    return [_item(row) for row in conn.execute(statement, params)]


def _item(row: Sequence[Any]) -> ItemRow:
    return ItemRow(
        id=str(row[0]),
        upload_id=str(row[1]),
        seq=int(row[2]),
        path_key=str(row[3]),
        name=str(row[4]),
        folder_id=str(row[5]),
        size=int(row[6]),
        batch_id=str(row[7]),
        state=str(row[8]),
        failure_code=row[9],
        document_id=None if row[10] is None else str(row[10]),
        document_status=row[11],
        document_failure_code=row[12],
        document_folder_id=None if row[13] is None else str(row[13]),
        in_trash=bool(row[14]),
    )
