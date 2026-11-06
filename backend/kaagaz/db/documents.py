"""The documents table behind the upload and scan ports.

Implements ``DocumentRepository`` (kaagaz.ingestion.ports) and ``StatusStore``
(kaagaz.scanning.gate). Every method opens its own transaction as the given
customer, so row level security and the explicit ``customer_id`` filter both
apply (two locks, docs/architecture.md).

Status writes never move a document out of ``rejected``: the UPDATE says so,
and a trigger refuses it as well (the gate's adapter contract).

A record filed in a folder the customer does not have is refused by the
foreign key ``documents_folder_fk`` (it names the customer too, so another
customer's folder fails the same way), and ``add_if_absent`` raises
UnknownFolder.

Each call is one indexed statement: O(log n) in the number of documents.
``mark_ready`` and ``add_version`` are the exceptions: each takes the family
lock first (below). ``PgDocuments.mark_ready`` runs its transaction again when
PostgreSQL breaks a deadlock (AR7).

``delete_forever`` is today's full delete of one document, shared by the trash
purge (kaagaz/jobs/purge.py) and ``delete_family_forever`` (the permanent delete).

``mark_for_reprocess`` is "Read again" (PRD 2 X1): under the family lock, a
ready, failed or unreadable document out of Trash, or one still in progress
RETRY_STUCK_HOURS after it was asked for (issue 407), goes back to ``received`` with
its tries reset, within the customer's hourly limit and one document's cooldown
(security A8). The caller audits it and queues the job in the same transaction.

``mark_for_upgrade`` is the upgrade sweep's read again (PRD 5 Q7, V9): a ready
document with a page the backup reader read while the main one was down
(``read_locally``) goes back to ``received`` as ``mark_for_reprocess`` sets it, under
the family lock, but outside the users' limits (the sweep has its own, 20 an hour),
audited as ``document.read_upgrade`` with no actor. ``oldest_read_locally`` finds a
customer's oldest such document that the sweep has not sent in the last
UPGRADE_RETRY_AFTER_HOURS, so a document the main reader fails on again can't starve
the rest.

Read one page again, harder (PRD 5 F5): a queue job carries only a document id, so
``request_page_read_again`` marks the page (``pages.read_again_requested_at``, 0021)
in the same transaction as ``mark_for_reprocess``, and the worker asks which pages
are marked (``pages_to_read_again``). The save that replaces the pages clears the
marks (kaagaz.db.pipeline_store); a document that fails for good (``set_failed``,
``mark_exhausted``) clears them in the same transaction as its failure, under the
family lock. A request is refused while the document is being read, and accepted
once its failure has committed, so no request can land between the failure and the
clearing and lose its mark. "Keep as is" (F5) is ``accept_page``.

``documents.batch_id`` (PRD 5 Q1, 0021) is the drop a new upload came in:
``add_if_absent`` writes the record's ``batch_id`` (None: in none). A new version is
in no batch.

Model choice (council 11 Oct 2026, MS5, SP1; 0023): ``documents.read_requested_by`` is who
the next reading's scan spend is billed to. An upload or a new version sets it to the
uploader; ``mark_for_reprocess`` to who asked (``requested_by``); ``mark_for_upgrade`` to
NULL (the system). ``documents.read_model`` is the model the reading asked for:
``mark_for_reprocess`` replaces it only when given one (a page read again keeps the
document's model, FINAL 4), and the upgrade sweep keeps it. The worker checks it again
(``kaagaz.db.models.resolve``).

Auto-filing (feature 28, council 15 Oct 2026; 0025): ``add_if_absent`` marks a new upload
``autofile = 'pending'`` in its own insert when it goes in no folder and the customer's
switch is on; a new version never is, and a duplicate makes no row. ``file_document`` is
``kaagaz.db.autofile.file_document`` in its own transaction after ``mark_ready``.
"""

import dataclasses
import json
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db import audit, autofile, customers, folders, storage_limits, versions
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.runs import close_running, finish_run, is_run_open
from kaagaz.db.sqlstate import FOREIGN_KEY_VIOLATION, retry_on_deadlock, sqlstate
from kaagaz.db.trash import lock_family
from kaagaz.ingestion.ports import BlobStorage, DocumentRecord, StorageRoom, UnknownFolder
from kaagaz.jobs.worker import Superseded
from kaagaz.reading.ocr import Region
from kaagaz.scanning.gate import DocumentStatus

if TYPE_CHECKING:
    from kaagaz.pipeline import FinishedRun, Progress  # the processor's records; pipeline builds this store

_log = logging.getLogger(__name__)

# PRD 5 Q6: the progress of a document waiting for the reader to come back.
WAITING_FOR_READER = {"step": "waiting"}

_SELECT = (
    "SELECT customer_id, id, sha256, size_bytes, detected_type, display_name, uploaded_by,"
    " folder_id, family_id, version_no, ocr_choice"
    " FROM documents WHERE customer_id = :c"
)


# The highest ready version_no is current (PRD 1 F6, PROVISIONAL plan 7.3): is a
# newer version of this document's family ready already?
_READY_STATE = (
    "SELECT d.status, EXISTS (SELECT 1 FROM documents newer WHERE newer.customer_id = :c"
    " AND newer.family_id = d.family_id AND newer.status = 'ready'"
    " AND newer.version_no > d.version_no) AS newer_ready"
    " FROM documents d WHERE d.customer_id = :c AND d.id = :d"
)

# Versions V10 (verify): a run ready before this one means a read again, which never changes
# which version is current (a version made current by hand stays so). The run fence's own run
# is still running here, so only an earlier one counts.
_READ_BEFORE = (
    "SELECT EXISTS (SELECT 1 FROM processing_runs r WHERE r.customer_id = :c AND r.document_id = :d"
    " AND r.status = 'ready' AND r.id IS DISTINCT FROM CAST(:fence AS uuid))"
)

# The others first, then this one: the unique index documents_current allows
# one current row per family at every moment.
_OTHERS_NOT_CURRENT = (
    "UPDATE documents SET is_current = false"
    " WHERE customer_id = :c AND family_id = :f AND id <> :d AND is_current"
)

# Ready first (16 Oct 2026): the progress is kept while the run goes on understanding the
# ready document (``open_run``), else cleared: a ready document shows none.
_READY = (
    "UPDATE documents SET status = 'ready', failure_code = NULL, page_count = :pages,"
    " is_current = CASE WHEN CAST(:again AS boolean) THEN is_current ELSE :current END,"
    " progress = CASE WHEN CAST(:keep AS boolean) THEN progress ELSE NULL END"
    " WHERE customer_id = :c AND id = :d"
)


def mark_ready(
    conn: Connection,
    customer_id: str,
    document_id: str,
    page_count: int,
    *,
    run: "FinishedRun | None" = None,
    open_run: str | None = None,
) -> None:
    """The document is read: set it ready with its page count and, unless a newer
    version of its family is ready already, make it the family's one current version.
    A read again (a run of it was ready before, ``_READ_BEFORE``) keeps ``is_current`` as it
    is, so it never changes which version is current (File management V10).
    With ``run``, finish that run in the same transaction, so a ready document and
    its finished run commit together. With ``open_run`` instead (Ready first, 16 Oct
    2026), the run fence on that run id, which stays open while the run goes on
    understanding the ready document, and the progress it wrote last is kept; otherwise
    the progress is cleared. A document set to be read again meanwhile (``received``)
    raises Superseded with either, so a run never takes a read again back to ready.

    ``conn`` comes from ``Database.customer(customer_id)``. The family lock
    (``trash.lock_family``) comes before any write, so two versions finishing at
    once queue, and trash, restore and the purge never deadlock with it. A
    missing document, or a rejected one (final), is left as it is, and ``run``
    is still finished: failed with ``document_missing``, or rejected (AR11,
    PROVISIONAL). Raises Superseded, and writes nothing, when ``run`` is no longer
    open (the run fence, read under the family lock).
    3 to 7 statements; O(v log D + r) time and O(v) space for the family's v versions and the
    document's r runs.
    """
    family_id = lock_family(conn, customer_id, document_id)
    if family_id is None:
        if run is not None:
            closed = dataclasses.replace(run.totals, status="failed", error_code="document_missing")
            finish_run(conn, customer_id, dataclasses.replace(run, totals=closed))
        return
    fence = run.run_id if run is not None else open_run
    if fence is not None and not is_run_open(conn, customer_id, document_id, fence):
        raise Superseded(fence)
    params = {"c": customer_id, "d": document_id, "f": family_id}
    state = conn.execute(text(_READY_STATE), params).one()
    if fence is not None and state.status == DocumentStatus.RECEIVED.value:
        raise Superseded(fence)
    if state.status == DocumentStatus.REJECTED.value:
        if run is not None:
            closed = dataclasses.replace(run.totals, status="rejected")
            finish_run(conn, customer_id, dataclasses.replace(run, totals=closed))
        return
    again = bool(conn.execute(text(_READ_BEFORE), {**params, "fence": fence}).scalar_one())
    current = not state.newer_ready
    if current and not again:
        conn.execute(text(_OTHERS_NOT_CURRENT), params)
    ready = {"pages": page_count, "current": current, "again": again, "keep": open_run is not None}
    conn.execute(text(_READY), {**params, **ready})
    if run is not None:
        finish_run(conn, customer_id, run)


def delete_forever(conn: Connection, storage: BlobStorage, customer_id: str, document_id: str) -> None:
    """Delete one document for good: its row, with its pages, pieces, values and
    the rest (cascade), then its stored files.

    ``conn`` comes from ``Database.customer(customer_id)``. The files go inside
    that transaction: if storage cannot be reached, BackendUnavailable rolls the
    row back and a retry works. One statement, plus O(k) storage calls for the
    document's k stored files.
    """
    folder = conn.execute(
        text("DELETE FROM documents WHERE customer_id = :c AND id = :d RETURNING folder_id"),
        {"c": customer_id, "d": document_id},
    ).scalar()
    # The folder Kaagaz made that its last document leaves goes too (Vrushit UAT 3).
    folders.prune_empty(conn, customer_id, [None if folder is None else str(folder)])
    storage.delete(customer_id, document_id)


# --- read again (PRD 2 X1) ----------------------------------------------------

REPROCESSABLE = ("ready", "failed", "needs_reader")
# Security A8 (PROVISIONAL): one document is read again at most once in this time.
REPROCESS_COOLDOWN_MINUTES = 10

# Issue 407: a document still in progress this many hours after its reading was asked for
# may be read again (Retry), whatever holds it: a key problem, a lost job, a slot that never
# frees. Asked for: its upload, or its last read again (the audit rows "read again" already
# counts, on the index audit_log_family), whichever is later. Never ``updated_at``: every try,
# step and hand-back moves it, so a document that keeps waiting would never get old enough.
# Never one held by a reading pause (#478 review, 3 Nov 2026): it waits on purpose, and the
# release sweep queues it; once queued, it is asked for again then (``document.reading_released``,
# QA P2, 4 Nov 2026).
RETRY_STUCK_HOURS = 6
STUCK = (
    "CASE WHEN d.status IN ('received', 'clean') AND d.reading_held IS NULL THEN GREATEST(d.created_at,"  # noqa: S608 - a fixed number
    " (SELECT max(a.created_at) FROM audit_log a WHERE a.customer_id = d.customer_id"
    " AND a.family_id = d.family_id AND a.target_id = d.id::text"
    " AND a.action IN ('document.reprocess', 'document.read_upgrade', 'document.reading_released')))"
    f" < now() - make_interval(hours => {RETRY_STUCK_HOURS}) ELSE false END"
)


class NotReprocessable(Exception):
    """The document is being read, was rejected, or is in Trash."""


class EnsembleRunning(NotReprocessable):
    """An Ensemble run of the document is queued or running (scan review UX rules, 16 Oct
    2026): it is read again once the run ends or is stopped."""


class ReprocessLimited(Exception):
    """Too many "read again" requests. ``reason`` is ``hourly`` (the customer's
    REPROCESS_PER_HOUR) or ``cooldown`` (this document, within the cooldown)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_REPROCESS_STATE = (
    f"SELECT d.status, d.deleted_at IS NOT NULL AS in_trash, {STUCK} AS stuck"  # noqa: S608 - a fixed fragment, STUCK
    " FROM documents d WHERE d.customer_id = :c AND d.id = :d"
)
# A queued or running Ensemble run of the document (0030's ensemble_runs_one_active index).
_ENSEMBLE_RUNNING = (
    "SELECT EXISTS (SELECT 1 FROM ensemble_runs WHERE customer_id = :c AND document_id = :d"
    " AND status IN ('queued', 'running'))"
)

# One customer's "read again" requests take their counts one after the other
# (council PR 3 review, medium 4), so two at once can't both pass the hourly limit.
_REPROCESS_LOCK = (
    "SELECT pg_advisory_xact_lock(hashtextextended('kaagaz.reprocess:' || CAST(:c AS uuid)::text, 0))"
)

# The audit rows are the record of each "read again", so they are what is counted.
# Their times come from the database's clock, so the windows are measured on it too.
# Both use the index audit_log_listing (customer_id, created_at DESC).
_REPROCESSED_THIS_HOUR = (
    "SELECT count(*) FROM audit_log WHERE customer_id = :c AND action = 'document.reprocess'"
    " AND created_at > now() - interval '1 hour'"
)
_REPROCESSED_LATELY = (
    "SELECT EXISTS (SELECT 1 FROM audit_log WHERE customer_id = :c AND action = 'document.reprocess'"
    " AND target_id = :target AND created_at > now() - make_interval(mins => :minutes))"
)

# Features 26 and 27 (0026): the reading asked for. A mode given replaces read_also too, in
# the same statement, so the ai_documents_read_mode_models check always sees a pair it
# allows; a model or mode left out (NULL) keeps the document's (as listing.set_reading).
# A read again is a new reading, so the reading pause asks again (#520): the day an earlier
# reading that failed was let in (reading_admitted) is cleared, never a pass past the pause.
_REPROCESS = (
    "UPDATE documents d SET status = 'received', failure_code = NULL, attempts = 0, max_attempts = NULL,"  # noqa: S608 - a fixed fragment, STUCK
    " progress = NULL, read_requested_by = CAST(:by AS uuid), reading_admitted = NULL,"
    " read_model = COALESCE(CAST(:m AS text), read_model),"
    " read_mode = COALESCE(CAST(:mode AS text), read_mode),"
    " read_also = CASE WHEN CAST(:mode AS text) IS NULL THEN read_also ELSE CAST(:also AS text[]) END"
    " WHERE d.customer_id = :c AND d.id = :d"
    f" AND (d.status IN ('ready', 'failed', 'needs_reader') OR {STUCK}) AND d.deleted_at IS NULL"
)


def mark_for_reprocess(
    conn: Connection,
    customer_id: str,
    document_id: str,
    *,
    per_hour: int,
    requested_by: str | None = None,
    read_model: str | None = None,
    read_mode: str | None = None,
    read_also: Sequence[str] = (),
) -> str | None:
    """Set the document to be read again (X1), and return the status it had; None
    when this customer has no such document. ``requested_by`` is who asked: the reading's
    scan spend is billed to them (SP1; None: the system). ``read_model``, when given,
    replaces the model the document asked for (MS5; the caller checked it is allowed);
    None keeps it. ``read_mode``, when given, replaces its mode and Ensemble's other
    readers (``read_also``); None keeps both (features 26 and 27).

    ``conn`` comes from ``Database.customer(customer_id)``. The family lock comes
    first, and is waited for, never skipped: a family being moved to Trash is
    waited for, then seen. Then the customer's reprocess lock (a transaction
    advisory lock), so the hourly count is exact. Raises NotReprocessable unless the status is ready,
    failed or needs_reader, or it is still in progress RETRY_STUCK_HOURS after it was asked for
    (``STUCK``, issue 407; a job still on it is fenced off by the new run, kaagaz/db/runs.py), and
    it is out of Trash (EnsembleRunning while an Ensemble run of
    it is queued or running), and ReprocessLimited when the
    customer already started ``per_hour`` in the last hour, or this document one
    in the last REPROCESS_COOLDOWN_MINUTES. Nothing is changed when it raises.
    ``is_current`` is kept, so an older version never takes its place.
    6 statements; O(v log D) for the family's v versions, plus the audit rows of
    the last hour.
    """
    if lock_family(conn, customer_id, document_id) is None:
        return None
    params: dict[str, Any] = {"c": customer_id, "d": document_id}
    state = conn.execute(text(_REPROCESS_STATE), params).one()
    if state.in_trash or (state.status not in REPROCESSABLE and not state.stuck):
        raise NotReprocessable(document_id)
    if conn.execute(text(_ENSEMBLE_RUNNING), params).scalar_one():
        raise EnsembleRunning(document_id)
    conn.execute(text(_REPROCESS_LOCK), params)  # held to the end of the transaction
    if int(conn.execute(text(_REPROCESSED_THIS_HOUR), params).scalar_one()) >= per_hour:
        raise ReprocessLimited("hourly")
    lately = {**params, "target": document_id, "minutes": REPROCESS_COOLDOWN_MINUTES}
    if conn.execute(text(_REPROCESSED_LATELY), lately).scalar_one():
        raise ReprocessLimited("cooldown")
    reading = {"m": read_model, "mode": read_mode, "also": list(read_also)}
    conn.execute(text(_REPROCESS), {**params, "by": requested_by, **reading})
    return str(state.status)


# --- the upgrade sweep's read again (PRD 5 Q7) -----------------------------------

# A document the sweep sent to be read again within this many hours is passed over,
# even if a page is still read locally (the main reader failed on it again), so one
# document can't take every pass and starve the rest. 6 hours: about 120 passes at
# 20 an hour, enough for the rest of a large outage's backlog to be tried first.
UPGRADE_RETRY_AFTER_HOURS = 6

# A ready document out of Trash with a page read by the backup reader, oldest first,
# not sent by the sweep in the last UPGRADE_RETRY_AFTER_HOURS. The pages side uses
# pages_read_locally (0022), the documents side their primary key, the audit side
# audit_log_listing (customer_id, created_at DESC).
_OLDEST_READ_LOCALLY = (
    "SELECT d.id, d.created_at FROM documents d"
    " WHERE d.customer_id = :c AND d.status = 'ready' AND d.deleted_at IS NULL"
    " AND d.id IN (SELECT p.document_id FROM pages p"
    " WHERE p.customer_id = :c AND 'read_locally' = ANY (p.read_issues))"
    " AND NOT EXISTS (SELECT 1 FROM ensemble_runs r WHERE r.customer_id = :c AND r.document_id = d.id"
    " AND r.status IN ('queued', 'running'))"
    " AND NOT EXISTS (SELECT 1 FROM audit_log a WHERE a.customer_id = :c"
    " AND a.action = 'document.read_upgrade' AND a.target_id = d.id::text"
    " AND a.created_at > now() - make_interval(hours => :hours))"
    " ORDER BY d.created_at, d.id LIMIT 1"
)


def oldest_read_locally(conn: Connection, customer_id: str) -> tuple[str, datetime] | None:
    """This customer's oldest ready document out of Trash with a page read locally
    (``read_locally``, PRD 5 Q7), as (id, uploaded at); None when there is none. One the
    sweep sent in the last UPGRADE_RETRY_AFTER_HOURS is passed over (no starving).
    ``conn`` comes from ``Database.customer(customer_id)``. One statement: O(L log D)
    for the customer's L locally read pages, plus its audit rows of that window."""
    params = {"c": customer_id, "hours": UPGRADE_RETRY_AFTER_HOURS}
    row = conn.execute(text(_OLDEST_READ_LOCALLY), params).first()
    return None if row is None else (str(row[0]), row[1])


def mark_for_upgrade(conn: Connection, customer_id: str, document_id: str) -> bool:
    """Set a document read locally to be read again by the main reader (PRD 5 Q7):
    True when it was set and audited as ``document.read_upgrade`` (no actor, the
    sweep's own); False when this customer has no such document, or it is being read,
    rejected, in Trash or in an Ensemble run, and then nothing changes.

    ``conn`` comes from ``Database.customer(customer_id)``. The family lock comes first,
    as in ``mark_for_reprocess``, then the same change (``_REPROCESS``), with none of
    the users' limits: REPROCESS_PER_HOUR and the cooldown count ``document.reprocess``
    rows only. Its scan spend is billed to no user (``read_requested_by`` NULL, SP1), and
    the document keeps the model it asked for. The caller queues the job (the main lane)
    in the same transaction.
    3 statements; O(v log D) for the family's v versions."""
    if lock_family(conn, customer_id, document_id) is None:
        return False
    if conn.execute(text(_ENSEMBLE_RUNNING), {"c": customer_id, "d": document_id}).scalar_one():
        return False
    # Every bind of _REPROCESS (a missing one made the sweep fail on its first document).
    params = {"c": customer_id, "d": document_id, "by": None, "m": None, "mode": None, "also": None}
    if conn.execute(text(_REPROCESS + " RETURNING id"), params).first() is None:
        return False
    audit.record(
        conn,
        customer_id,
        actor_user_id=None,
        action="document.read_upgrade",
        target_type="document",
        target_id=document_id,
    )
    return True


# --- the stuck-document sweep (stuck reading, 16 Oct 2026, kaagaz/jobs/revive.py) ---------

# In progress, out of Trash, not held by a reading pause (#478), and nothing written on it for
# :stale seconds, on the database's clock (the touch trigger sets updated_at at every change:
# each try, each progress step).
# Each statement below repeats it, so a change is made in all three.
_STUCK_FIND = (
    "SELECT id, attempts FROM documents"
    " WHERE customer_id = :c AND status IN ('received', 'clean') AND deleted_at IS NULL"
    " AND reading_held IS NULL AND updated_at < now() - make_interval(secs => :stale)"
    " ORDER BY updated_at, id LIMIT :n"
)
_STUCK_REQUEUE = (
    "UPDATE documents SET attempts = attempts + 1, max_attempts = :max, progress = NULL"
    " WHERE id = :d AND customer_id = :c AND status IN ('received', 'clean') AND deleted_at IS NULL"
    " AND reading_held IS NULL AND updated_at < now() - make_interval(secs => :stale)"
    " AND attempts < :max RETURNING attempts"
)
_STUCK_FAIL = (
    "UPDATE documents SET status = 'failed', failure_code = :code"
    " WHERE id = :d AND customer_id = :c AND status IN ('received', 'clean') AND deleted_at IS NULL"
    " AND reading_held IS NULL AND updated_at < now() - make_interval(secs => :stale)"
    " AND attempts >= :max RETURNING id"
)
STUCK_NO_JOB = "stuck_no_job"


def stuck_documents(
    conn: Connection, customer_id: str, *, stale_seconds: int, limit: int
) -> list[tuple[str, int]]:
    """This customer's stuck documents (``_STUCK_FIND``) as (id, tries so far), longest stuck first,
    at most ``limit``. ``conn`` comes from ``Database.customer(customer_id)``. One statement."""
    rows = conn.execute(
        text(_STUCK_FIND),
        {"c": customer_id, "stale": stale_seconds, "n": limit},
    )
    return [(str(row[0]), int(row[1])) for row in rows]


def requeue_stuck(
    conn: Connection, customer_id: str, document_id: str, *, stale_seconds: int, max_attempts: int
) -> int | None:
    """Count one more try on a stuck document with a try left, and clear its stale progress
    ("Waiting to try again" then shows); that try's number, or None when it is no longer
    stuck or has no try left, and then nothing changes. The caller queues its job, carrying
    the tries before this one, in the same transaction. One conditional statement."""
    row = conn.execute(
        text(_STUCK_REQUEUE),
        {"c": customer_id, "d": document_id, "stale": stale_seconds, "max": max_attempts},
    ).first()
    return None if row is None else int(row[0])


def fail_stuck(
    conn: Connection,
    customer_id: str,
    document_id: str,
    *,
    stale_seconds: int,
    max_attempts: int,
    now: datetime,
) -> bool:
    """Mark a stuck document with no try left failed with ``stuck_no_job``, ending its runs
    still running and clearing its read-again marks, as ``mark_exhausted`` does; False when it
    is no longer stuck or has a try left, and then nothing changes. 1 to 3 statements."""
    marked = conn.execute(
        text(_STUCK_FAIL),
        {
            "c": customer_id,
            "d": document_id,
            "stale": stale_seconds,
            "max": max_attempts,
            "code": STUCK_NO_JOB,
        },
    ).first()
    if marked is None:
        return False
    close_running(conn, customer_id, document_id, status="failed", error_code=STUCK_NO_JOB, now=now)
    conn.execute(text(_CLEAR_READ_AGAIN), {"c": customer_id, "d": document_id})
    return True


# --- read one page again, and Keep as is (PRD 5 F5) ----------------------------

# Only a page of a document out of Trash is marked or accepted.
_REQUEST_PAGE = (
    "UPDATE pages p SET read_again_requested_at = :now,"
    " read_again_regions = CAST(:regions AS jsonb) FROM documents d"
    " WHERE p.customer_id = :c AND p.document_id = :d AND p.page_no = :n"
    " AND d.customer_id = p.customer_id AND d.id = p.document_id AND d.deleted_at IS NULL"
    " RETURNING p.page_no"
)
_ACCEPT_PAGE = (
    "UPDATE pages p SET accepted_by = :u, accepted_at = :now FROM documents d"
    " WHERE p.customer_id = :c AND p.document_id = :d AND p.page_no = :n"
    " AND d.customer_id = p.customer_id AND d.id = p.document_id AND d.deleted_at IS NULL"
    " RETURNING p.page_no"
)
_PAGES_TO_READ_AGAIN = (
    "SELECT page_no FROM pages WHERE customer_id = :c AND document_id = :d"
    " AND read_again_requested_at IS NOT NULL ORDER BY page_no"
)
_CLEAR_READ_AGAIN = (
    "UPDATE pages SET read_again_requested_at = NULL, read_again_regions = NULL"
    " WHERE customer_id = :c AND document_id = :d AND read_again_requested_at IS NOT NULL"
)

# #385: the regions asked for on the pages to read again, by page.
_REGIONS_TO_READ_AGAIN = (
    "SELECT page_no, read_again_regions FROM pages WHERE customer_id = :c AND document_id = :d"
    " AND read_again_requested_at IS NOT NULL AND read_again_regions IS NOT NULL ORDER BY page_no"
)


def request_page_read_again(
    conn: Connection,
    customer_id: str,
    document_id: str,
    page_no: int,
    *,
    now: datetime,
    regions: Sequence[Region] | None = None,
) -> bool:
    """Mark page ``page_no`` to be read again, harder, by the document's next run (F5),
    and return True; False when this customer's document, out of Trash, has no such
    page, and then nothing changes. ``regions`` (#385, page fractions x0, y0, x1, y1): only
    those regions of the page are read again; None, the whole page. A later request for
    the page replaces an earlier one.

    ``conn`` comes from ``Database.customer(customer_id)``, in the transaction in which
    ``mark_for_reprocess`` (which takes the family lock first, and counts the request
    against REPROCESS_PER_HOUR) sets the document to be read again, so the mark and the
    job commit together. One statement on the pages key: O(log P)."""
    shapes = None if regions is None else json.dumps([list(region) for region in regions])
    params = {"c": customer_id, "d": document_id, "n": page_no, "now": now, "regions": shapes}
    return conn.execute(text(_REQUEST_PAGE), params).first() is not None


def accept_page(
    conn: Connection, customer_id: str, document_id: str, page_no: int, *, user_id: str, now: datetime
) -> bool:
    """Keep as is (F5): ``user_id`` accepts page ``page_no`` as it was read, at ``now``;
    True, or False when this customer's document, out of Trash, has no such page.
    The page leaves the attention list but keeps its reading result, so it stays
    flagged on the page and in answers; the save carries the acceptance forward
    unless the page is read again (kaagaz.db.pipeline_store).

    ``conn`` comes from ``Database.customer(customer_id)``; the family lock
    (``trash.lock_family``) comes first, so it never races a save. 2 statements:
    O(v log D) for the family's v versions."""
    if lock_family(conn, customer_id, document_id) is None:
        return False
    params = {"c": customer_id, "d": document_id, "n": page_no, "u": user_id, "now": now}
    return conn.execute(text(_ACCEPT_PAGE), params).first() is not None


class NotInTrash(Exception):
    """A version of the family is not in Trash, so nothing may be deleted forever."""


# Every version of one family, and whether it is in Trash.
_FAMILY_VERSIONS = (
    "SELECT d.id, d.deleted_at IS NOT NULL AS in_trash FROM documents d"
    " WHERE d.customer_id = :c AND d.family_id = :f ORDER BY d.id"
)


def delete_family_forever(
    conn: Connection, storage: BlobStorage, customer_id: str, document_id: str
) -> int | None:
    """Delete every version of ``document_id``'s family for good, rows and files, by
    ``delete_forever``, and return how many versions went; None if there is no such
    document. Raises NotInTrash, and deletes nothing, unless every version is in Trash.

    ``conn`` comes from ``Database.customer(customer_id)``. The family lock
    (``trash.lock_family``) comes first. One transaction for the family: if storage
    cannot be reached, every row comes back. 2 statements plus one per version, and
    O(k) storage calls per version; O(v) space for the family's v versions.
    """
    family_id = lock_family(conn, customer_id, document_id)
    if family_id is None:
        return None
    versions = conn.execute(text(_FAMILY_VERSIONS), {"c": customer_id, "f": family_id}).all()
    if not all(version.in_trash for version in versions):
        raise NotInTrash(document_id)
    for version in versions:
        delete_forever(conn, storage, customer_id, str(version.id))
    return len(versions)


def _record(row: Sequence[Any]) -> DocumentRecord:
    return DocumentRecord(
        customer_id=str(row[0]),
        document_id=str(row[1]),
        sha256=str(row[2]),
        size=int(str(row[3])),
        detected_type=str(row[4]),
        display_name=str(row[5]),
        uploaded_by=None if row[6] is None else str(row[6]),
        folder_id=None if row[7] is None else str(row[7]),
        family_id=str(row[8]),
        version_no=int(str(row[9])),
        ocr_choice=None if row[10] is None else str(row[10]),
    )


def folder_missing(error: IntegrityError) -> bool:
    """True when ``error`` is ``documents_folder_fk`` refusing a folder id (23503)."""
    diag = getattr(error.orig, "diag", None)
    return (
        sqlstate(error) == FOREIGN_KEY_VIOLATION
        and getattr(diag, "constraint_name", None) == "documents_folder_fk"
    )


# --- new versions (PRD 1 F6) -------------------------------------------------

# The family's folder and next number, read through the document the version
# is for, unless its family is in Trash (the whole family moves together).
_NEXT_VERSION = (
    "SELECT d.folder_id, (SELECT max(v.version_no) FROM documents v"
    " WHERE v.customer_id = :c AND v.family_id = :f) + 1 AS version_no"
    " FROM documents d WHERE d.customer_id = :c AND d.id = :of AND d.deleted_at IS NULL"
)

# File management O1, O2: a new version takes the family's owner and label.
_INSERT_VERSION = (
    "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes, detected_type,"
    " uploaded_by, read_requested_by, folder_id, family_id, version_no, is_current, ocr_choice,"
    " owner_id, sensitivity, sensitivity_set_by_person)"
    " VALUES (:d, :c, :name, :h, :size, :type, :by, :by, :folder, :f, :version, false, :ocr,"
    " (SELECT o.owner_id FROM documents o WHERE o.customer_id = :c AND o.id = :of),"
    " (SELECT o.sensitivity FROM documents o WHERE o.customer_id = :c AND o.id = :of),"
    " (SELECT o.sensitivity_set_by_person FROM documents o WHERE o.customer_id = :c AND o.id = :of))"
    " ON CONFLICT (customer_id, sha256) DO NOTHING RETURNING id"
)

# Every version holds the family's tag set (tags.set_document_tags), so the
# document the version is for has the whole set.
_COPY_TAGS = (
    "INSERT INTO document_tags (customer_id, document_id, tag_id)"
    " SELECT dt.customer_id, :d, dt.tag_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.document_id = :of"
)


def add_version(
    conn: Connection, record: DocumentRecord, *, of_document_id: str
) -> tuple[DocumentRecord, bool] | None:
    """``DocumentRepository.add_version_if_absent`` on a connection from
    ``Database.customer(record.customer_id)``.

    The family lock (``trash.lock_family``) comes first, so a second new version
    waits, then sees this one and takes the next number (the unique index
    ``documents_family_version`` refuses a repeat anyway). 4 statements for a
    new version (lock, family, insert, tags); O(v log D + t log N) time and
    O(v + t) space for the family's v versions and t tags.
    """
    customer_id = record.customer_id
    family_id = lock_family(conn, customer_id, of_document_id)
    if family_id is None:
        return None
    params: dict[str, Any] = {"c": customer_id, "f": family_id, "of": of_document_id}
    family = conn.execute(text(_NEXT_VERSION), params).first()
    if family is None:  # the family is in Trash
        return None
    stored = dataclasses.replace(
        record,
        folder_id=None if family[0] is None else str(family[0]),
        family_id=family_id,
        version_no=int(family[1]),
    )
    params.update(
        d=stored.document_id,
        name=stored.display_name,
        h=stored.sha256,
        size=stored.size,
        type=stored.detected_type,
        by=stored.uploaded_by,
        folder=stored.folder_id,
        version=stored.version_no,
        ocr=stored.ocr_choice,
    )
    if conn.execute(text(_INSERT_VERSION), params).first() is None:  # this customer has these bytes
        row = conn.execute(text(_SELECT + " AND sha256 = :h"), params).one()
        return _record(row), False
    conn.execute(text(_COPY_TAGS), params)
    return stored, True


class PgDocuments:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- DocumentRepository ---------------------------------------------------

    def get(self, customer_id: str, document_id: str) -> DocumentRecord | None:
        with unavailable(), self._db.customer(customer_id) as conn:
            row = conn.execute(
                text(_SELECT + " AND id = :d"),
                {"c": customer_id, "d": document_id},
            ).first()
        return None if row is None else _record(row)

    def customer_active(self, customer_id: str) -> bool:
        """Is this customer active (0014)? The worker asks at the start of each job;
        a database that is down is BackendUnavailable, so the job is tried again. O(1)."""
        with unavailable(), self._db.customer(customer_id) as conn:
            return customers.is_active(conn, customer_id)

    def find_by_fingerprint(self, customer_id: str, sha256: str) -> DocumentRecord | None:
        with unavailable(), self._db.customer(customer_id) as conn:
            row = conn.execute(
                text(_SELECT + " AND sha256 = :h"),
                {"c": customer_id, "h": sha256},
            ).first()
        return None if row is None else _record(row)

    def add_if_absent(self, record: DocumentRecord) -> tuple[DocumentRecord, bool]:
        """Atomic per (customer, fingerprint): the unique index decides the race.
        Raises UnknownFolder when the record's folder is not this customer's."""
        with unavailable(), self._db.customer(record.customer_id) as conn:
            params = {
                "d": record.document_id,
                "c": record.customer_id,
                "name": record.display_name,
                "h": record.sha256,
                "size": record.size,
                "type": record.detected_type,
                "by": record.uploaded_by,
                "folder": record.folder_id,
                "family": record.family_id or record.document_id,
                "version": record.version_no,
                "ocr": record.ocr_choice,
                "batch": record.batch_id,
                "keep": record.keep_place,
            }
            try:
                inserted = conn.execute(
                    text(
                        "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes,"  # noqa: S608 - a fixed fragment, APPLIES
                        " detected_type, uploaded_by, read_requested_by, folder_id, family_id, version_no,"
                        " ocr_choice, batch_id, owner_id, autofile) VALUES (:d, :c, :name, :h, :size, :type,"
                        " :by, :by, :folder, :family, :version, :ocr, :batch, :by,"
                        # File management F2: no folder, or a department (filed inside it, F3);
                        # never a folder upload's file (#484, keep_place).
                        " CASE WHEN CAST(:keep AS boolean) THEN NULL ELSE"
                        " (SELECT 'pending' FROM customers WHERE id = :c"
                        " AND (CAST(:folder AS uuid) IS NULL OR EXISTS (SELECT 1 FROM folders"
                        " WHERE customer_id = :c AND id = CAST(:folder AS uuid) AND kind = 'department'))"
                        " AND " + autofile.APPLIES + ") END)"
                        " ON CONFLICT (customer_id, sha256) DO NOTHING RETURNING id"
                    ),
                    params,
                ).first()
            except IntegrityError as error:
                if folder_missing(error):
                    raise UnknownFolder(record.document_id) from None
                raise
            if inserted is not None:
                return record, True
            row = conn.execute(
                text(_SELECT + " AND sha256 = :h"),
                {"c": record.customer_id, "h": record.sha256},
            ).one()
        return _record(row), False

    def add_version_if_absent(
        self, record: DocumentRecord, *, of_document_id: str
    ) -> tuple[DocumentRecord, bool] | None:
        """``add_version`` in its own transaction."""
        with unavailable(), self._db.customer(record.customer_id) as conn:
            return add_version(conn, record, of_document_id=of_document_id)

    def remove(self, record: DocumentRecord) -> None:
        with unavailable(), self._db.customer(record.customer_id) as conn:
            conn.execute(
                text("DELETE FROM documents WHERE customer_id = :c AND id = :d AND sha256 = :h"),
                {"c": record.customer_id, "d": record.document_id, "h": record.sha256},
            )

    def storage_room(self, customer_id: str) -> StorageRoom:
        """The storage limit and the bytes used (#516, ``storage_limits.storage_room``)."""
        with unavailable(), self._db.customer(customer_id) as conn:
            return storage_limits.storage_room(conn, customer_id)

    # --- StatusStore ----------------------------------------------------------

    def get_status(self, customer_id: str, document_id: str) -> DocumentStatus | None:
        with unavailable(), self._db.customer(customer_id) as conn:
            status = conn.execute(
                text("SELECT status FROM documents WHERE customer_id = :c AND id = :d"),
                {"c": customer_id, "d": document_id},
            ).scalar()
        if status is None or status == DocumentStatus.RECEIVED.value:
            # "received" means not scanned yet, which the gate treats as None.
            return None
        return DocumentStatus(status)

    def set_status(
        self, customer_id: str, document_id: str, status: DocumentStatus, *, run_id: str | None = None
    ) -> None:
        """With ``run_id``, the run fence, as ``set_failed``: under the family lock,
        raise Superseded and change nothing unless that run is still open."""
        with unavailable(), self._db.customer(customer_id) as conn:
            if run_id is not None:
                lock_family(conn, customer_id, document_id)
                if not is_run_open(conn, customer_id, document_id, run_id):
                    raise Superseded(run_id)
            conn.execute(
                text(
                    "UPDATE documents SET status = :s, failure_code = NULL"
                    " WHERE customer_id = :c AND id = :d AND status <> 'rejected'"
                ),
                {"s": status.value, "c": customer_id, "d": document_id},
            )

    # --- used by the pipeline -----------------------------------------------

    def set_failed(self, customer_id: str, document_id: str, code: str, *, run_id: str | None = None) -> None:
        """Mark a document failed for good with a short code. Rejected stays rejected,
        but keeps its code so the file list can say why. With ``run_id``, raise
        Superseded and change nothing unless that run is still open (the run fence,
        read under the family lock), so a stale delivery (a newer run has started,
        after a "read again", say) never fails the document.

        A document failed for good keeps no read-again mark (F5): they are cleared in
        this transaction, so a page asked to be read again after it commits (the
        document is failed, so it may be read again) keeps its mark for the next run."""
        with unavailable(), self._db.customer(customer_id) as conn:
            if run_id is not None:
                lock_family(conn, customer_id, document_id)
                if not is_run_open(conn, customer_id, document_id, run_id):
                    raise Superseded(run_id)
            conn.execute(
                text(
                    "UPDATE documents"
                    " SET status = CASE WHEN status = 'rejected' THEN 'rejected' ELSE 'failed' END,"
                    " failure_code = :code"
                    " WHERE customer_id = :c AND id = :d"
                ),
                {"code": code, "c": customer_id, "d": document_id},
            )
            conn.execute(text(_CLEAR_READ_AGAIN), {"c": customer_id, "d": document_id})

    def mark_ready(
        self,
        customer_id: str,
        document_id: str,
        page_count: int,
        *,
        run: "FinishedRun | None" = None,
        open_run: str | None = None,
    ) -> None:
        """``mark_ready`` in its own transaction: the pipeline's last step (Ready first: also
        the one before the document is understood, with ``open_run``)."""

        def transaction() -> None:
            with self._db.customer(customer_id) as conn:
                mark_ready(conn, customer_id, document_id, page_count, run=run, open_run=open_run)

        with unavailable():
            retry_on_deadlock(transaction, customer_id=customer_id, document_id=document_id)

    def file_document(self, customer_id: str, document_id: str) -> None:
        """Auto-filing (feature 28) in its own transaction, run again on a deadlock, right
        after ``mark_ready``; then version spotting (``versions.spot``) in another. What was
        decided is logged as a code with the ids only."""

        def transaction() -> str | None:
            with self._db.customer(customer_id) as conn:
                return autofile.file_document(conn, customer_id, document_id)

        def spotting() -> str | None:
            with self._db.customer(customer_id) as conn:
                return versions.spot(conn, customer_id, document_id)

        ids = {"customer_id": customer_id, "document_id": document_id}
        try:
            with unavailable():
                code = retry_on_deadlock(transaction, **ids)
        except Exception as failed:  # noqa: BLE001 - a filing bug must never skip spotting
            code = None
            _log.warning("autofile_failed", extra={**ids, "code": type(failed).__name__})
        if code is not None:
            _log.info("autofile", extra={**ids, "code": code})
        # Versions V1 to V5, after filing: an automatic link checks the folder filing chose.
        with unavailable():
            code = retry_on_deadlock(spotting, **ids)
        if code is not None:
            _log.info("version_spotting", extra={**ids, "code": code})

    def set_progress(
        self, customer_id: str, document_id: str, progress: "Progress", *, run_id: str | None = None
    ) -> None:
        """Show how far the reading has got (X3), on a document still in progress
        (received or clean) only. With ``run_id``, the run fence, as ``set_failed``:
        under the family lock, raise Superseded and change nothing unless that run is
        still open, so a stale delivery never overwrites the newer run's progress. Its
        own short transaction, one indexed statement (three with the fence); never
        called while this process holds another transaction."""
        with unavailable(), self._db.customer(customer_id) as conn:
            if run_id is not None:
                lock_family(conn, customer_id, document_id)
                if not is_run_open(conn, customer_id, document_id, run_id):
                    raise Superseded(run_id)
            conn.execute(
                text(
                    "UPDATE documents SET progress = CAST(:p AS jsonb)"
                    " WHERE customer_id = :c AND id = :d AND status IN ('received', 'clean')"
                ),
                {"p": json.dumps(progress.to_json()), "c": customer_id, "d": document_id},
            )

    def clear_progress(
        self, customer_id: str, document_id: str, *, run_id: str | None = None, waiting: str | None = None
    ) -> None:
        """No progress shown, on a document still in progress (received or clean) only:
        a try a backend stopped leaves no stale "Reading page 1" while the job waits to
        come back. ``waiting="reader"`` (PRD 5 Q6: the reader is down or has a key
        problem) writes ``{"step": "waiting"}`` instead, so the list can say "Waiting for
        the reader to come back". With ``run_id``, the run fence: under the family lock,
        change nothing unless that run is still open, so a stale delivery never clears
        the newer run's progress. Its own short transaction, as ``set_progress``."""
        if waiting not in (None, "reader"):
            raise ValueError("waiting must be None or 'reader'")
        progress = None if waiting is None else json.dumps(WAITING_FOR_READER)
        with unavailable(), self._db.customer(customer_id) as conn:
            if run_id is not None:
                lock_family(conn, customer_id, document_id)
                if not is_run_open(conn, customer_id, document_id, run_id):
                    return
            conn.execute(
                text(
                    "UPDATE documents SET progress = CAST(:p AS jsonb)"
                    " WHERE customer_id = :c AND id = :d AND status IN ('received', 'clean')"
                ),
                {"p": progress, "c": customer_id, "d": document_id},
            )

    def pages_to_read_again(self, customer_id: str, document_id: str) -> tuple[int, ...]:
        """The pages a user asked to be read again, harder (F5), in page order: the run
        that reads the document reads them so. Empty when none (or no such document).
        Its own short transaction, one statement on the pages key: O(log P + p)."""
        with unavailable(), self._db.customer(customer_id) as conn:
            rows = conn.execute(text(_PAGES_TO_READ_AGAIN), {"c": customer_id, "d": document_id})
            return tuple(int(row[0]) for row in rows)

    def regions_to_read_again(self, customer_id: str, document_id: str) -> dict[int, tuple[Region, ...]]:
        """The regions asked for (#385) on the pages to read again, by page; a page asked
        for whole is not in it. Its own short transaction, one statement on the pages key:
        O(log P + p)."""
        with unavailable(), self._db.customer(customer_id) as conn:
            rows = conn.execute(text(_REGIONS_TO_READ_AGAIN), {"c": customer_id, "d": document_id})
            return {
                int(page_no): tuple(
                    (float(x0), float(y0), float(x1), float(y1)) for x0, y0, x1, y1 in regions
                )
                for page_no, regions in rows
            }

    def set_page_count(self, customer_id: str, document_id: str, page_count: int) -> None:
        with unavailable(), self._db.customer(customer_id) as conn:
            conn.execute(
                text("UPDATE documents SET page_count = :n WHERE customer_id = :c AND id = :d"),
                {"n": page_count, "c": customer_id, "d": document_id},
            )

    # --- used by the worker ---------------------------------------------------

    def record_attempt(self, customer_id: str, document_id: str, attempt: int, max_attempts: int) -> None:
        """Write the worker's try number on a document still in progress (received
        or clean), before the try runs, so the file list can say "Try 2 of 5".
        A finished or rejected document is left alone. Only the document's own try or the
        next one is written (issue 407): a job from before a read again, its tries counted
        from before the reset, is still delivered, and its numbers must not take the count
        over, or its last delivery (``mark_exhausted``) would fail the new reading early. A
        try whose write was lost (the database was down, a blip at admission, or a worker that
        stopped between receiving the job and this write) leaves the count behind; the job's
        later tries are then not written and its last delivery fails nothing, and the stuck
        sweep counts on from it (``requeue_stuck``), so it is settled later but always settled.
        One indexed statement."""
        with unavailable(), self._db.customer(customer_id) as conn:
            conn.execute(
                text(
                    "UPDATE documents SET attempts = :a, max_attempts = :m"
                    " WHERE customer_id = :c AND id = :d AND status IN ('received', 'clean')"
                    " AND :a BETWEEN attempts AND attempts + 1"
                ),
                {"a": attempt, "m": max_attempts, "c": customer_id, "d": document_id},
            )

    def still_waiting(self, customer_id: str, document_id: str) -> None:
        """A job handed back while the platform reads its most documents at once (package S,
        0053): a write on its document still in progress (the touch trigger moves updated_at),
        at most every 10 minutes, well inside the stuck sweep's QUEUE_LEASE_SECONDS x
        JOB_MAX_ATTEMPTS, so the sweep never takes a waiting document for one no job holds
        (kaagaz/jobs/revive.py). Nothing else changes. One indexed statement."""
        with unavailable(), self._db.customer(customer_id) as conn:
            conn.execute(
                text(
                    "UPDATE documents SET attempts = attempts"
                    " WHERE customer_id = :c AND id = :d AND status IN ('received', 'clean')"
                    " AND updated_at < now() - interval '10 minutes'"
                ),
                {"c": customer_id, "d": document_id},
            )

    def mark_exhausted(self, customer_id: str, document_id: str, *, now: datetime, max_attempts: int) -> None:
        """The queue's last delivery of this document's job came: mark it failed
        with ``retries_exhausted``, so it does not show "Reading…" for good.

        Only a document still in progress (received or clean) changes, in one
        conditional statement: a rejected one keeps its reason, and a ready,
        needs_reader or failed one keeps its state and code, and so does one whose
        recorded try is under ``max_attempts``: after "read again" the tries start
        again, so an old job's last delivery must not fail the new one. When it changes,
        its runs still ``running`` end failed with ``retries_exhausted`` at
        ``now`` (AR11), and its read-again marks are cleared (F5, as ``set_failed``),
        in the same transaction.
        """
        with unavailable(), self._db.customer(customer_id) as conn:
            marked = conn.execute(
                text(
                    "UPDATE documents SET status = 'failed', failure_code = 'retries_exhausted'"
                    " WHERE customer_id = :c AND id = :d AND status IN ('received', 'clean')"
                    " AND attempts >= :max RETURNING id"
                ),
                {"c": customer_id, "d": document_id, "max": max_attempts},
            ).first()
            if marked is not None:
                close_running(
                    conn, customer_id, document_id, status="failed", error_code="retries_exhausted", now=now
                )
                conn.execute(text(_CLEAR_READ_AGAIN), {"c": customer_id, "d": document_id})
