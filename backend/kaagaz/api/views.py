"""Database rows into the JSON of ``frontend/lib/types.ts``, field for field.

Why one place: the frontend's types are the contract. Each function here is
named after the type it builds, so a reviewer can hold the two side by side.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Connection, RowMapping

from kaagaz.answering.clarify import without_resume
from kaagaz.answering.hard_to_read import FlaggedPage, not_found_note
from kaagaz.api.auth import CurrentUser, needs_google_link
from kaagaz.db import audit, listing, word_review
from kaagaz.db import ensemble as ensemble_sql
from kaagaz.db.folder_upload_items import ItemRow
from kaagaz.db.folders import FolderRow
from kaagaz.db.listing import CLEAR_COPY_LINES, CLEAR_COPY_TEXT, MAX_READING_LINES, MAX_READING_TEXT
from kaagaz.db.tags import TagRow
from kaagaz.db.trashed_folders import TrashedFolder
from kaagaz.db.zip_unpacks import Unpack
from kaagaz.eta import time_left
from kaagaz.ingestion.unzip import KEPT_ZIP, REASONS, SHORT_REASONS, ZIP_FAILURES
from kaagaz.jobs.reading_pause import BUDGET, CODES, DAILY_LIMIT
from kaagaz.jobs.worker import KEY_PROBLEMS
from kaagaz.reading.page_quality import ILLEGIBLE_WORDS, READERS_DISAGREE, UNPINNED, fix, says
from kaagaz.scanning.gate import CUSTOMER_MESSAGES

_STATUS_MESSAGES = {
    "received": "Waiting to start.",  # in the queue: the virus check runs later, in the worker
    "clean": "Reading the document…",
    "ready": "Ready. You can ask about it.",
    "needs_reader": "Stored safely. This kind of file can't be read yet.",
    "locked": "Locked. It can only be viewed.",  # PRD 7 (#605)
}
_IN_PROGRESS = ("received", "clean")
# PRD 7 (#605): a locked version whose page pictures were not made (storage down, a stuck job, its
# tries used up): Retry only prepares it again, never reads it. ``cannot_draw`` (kaagaz.pipeline
# CANNOT_DRAW): a page of it can't be drawn at all.
LOCKED_NOT_PREPARED = "Locked. Its page pictures couldn't be made yet. Retry to try again."
LOCKED_CANNOT_DRAW = (
    "Locked. Its pages can't be shown as pictures, so it can't be viewed. Unlock it to read it."
)
# All formats (#522): a file that can never be read as it is, in words that say what to do.
FILE_FAILURES = {
    "corrupt_file": "This file is damaged, so it can't be opened. Save it again and upload that copy.",
    "unsupported_type": (
        "Kaagaz can't read this kind of file. Save it as a PDF, a photo, a spreadsheet or a text file"
        " and upload that."
    ),
    "not_utf8": (
        "This text file isn't saved as UTF-8, so it can't be read. Save it as UTF-8 and upload it again."
    ),
    "too_deep": (
        "This JSON file is nested more than 100 levels deep, so it can't be read. Flatten it and upload it"
        " again."
    ),
}
# All formats (#522 PR 2): what an Office file LibreOffice could not open is called, by its kind.
_OFFICE_FILES = {
    "docx": "Word file",
    "dotx": "Word file",
    "doc": "Word file",
    "pptx": "PowerPoint file",
    "ppt": "PowerPoint file",
    "xls": "Excel file",
}
_SPREADSHEETS = ("xlsx", "xls")
_MONEY_KEY = re.compile(r"usd|price|budget|cost", re.IGNORECASE)

# How far the worker has got (PRD 2 X3), by step. PROVISIONAL wording (plan 3.3).
# "done" counts the pages finished, so the page being worked on is the next one.
_STEP_MESSAGES = {
    "scanning": "Checking for viruses…",
    "understanding": "Understanding the document…",
    "indexing": "Making it searchable…",
}
_PAGE_STEPS = {"preparing": "Preparing", "reading": "Reading"}
# A try has run (attempts) and its progress was cleared: a backend stopped it (pipeline).
_WAITING_AGAIN = "Waiting to try again."
# PRD 5 Q6: the worker cleared the progress to {"step": "waiting"} because the reader is
# down (pipeline _clear_progress); or the files before it in the uploader's own batch.
READER_WAITING = "Reading is paused for now. It will continue on its own."
_WAITING_STEP = "waiting"
# Issue 407: the wait's run ended with a key problem (pipeline: a wrong key, a spent cap, a
# model ID that names no model; #473: Mistral's key or credit, or Mistral not set up here). No
# wait fixes that, so the words never say it goes on by itself. PROVISIONAL wording.
READER_STOPPED = (
    "Reading is stopped until the Kaagaz team fixes the reading service. It starts again after that."
)
_KEY_WAITS = frozenset(f"{name}_unavailable" for name in KEY_PROBLEMS)  # #473: Mistral's too
# #478 (Tushar, 3 Nov 2026): the organisation's reading pause held it; never a sum of money.
# Review, 3 Nov 2026: the super admin is told, not the organisation's admin. QA C4 (4 Nov 2026):
# never "tomorrow morning" (a weekend, a raised limit), and the budget is the "monthly AI limit".
PAUSED_FOR_TODAY = (
    "Reading is paused. Your organisation has reached its daily page limit. This file will be read"
    " on its own when the limit resets, or sooner if it is raised."
)
PAUSED_BUDGET = (
    "Reading is paused. Your organisation has reached its monthly AI limit. It starts again when the"
    " limit is raised or the new month begins."
)
_PAUSE_WORDS = {CODES[DAILY_LIMIT]: PAUSED_FOR_TODAY, CODES[BUDGET]: PAUSED_BUDGET}


def held_message(reason: str | None) -> str | None:
    """#484 with #478: the words for files the reading pause holds (``documents.reading_held``,
    DAILY_LIMIT or BUDGET), as a held document's own; None with none held."""
    return None if reason is None else _PAUSE_WORDS[CODES[reason]]


def files_ahead_message(count: int) -> str:
    """ "Waiting: 12 files ahead in your batch" (Q6)."""
    return f"Waiting: {count} {'file' if count == 1 else 'files'} ahead in your batch"


def iso(value: datetime) -> str:
    return value.isoformat()


def status_message(
    status: str,
    failure_code: str | None,
    *,
    detected_type: str,
    max_pages: int,
    page_count: int | None = None,
    attempts: int = 0,
    max_attempts: int | None = None,
    progress: Mapping[str, Any] | None = None,
    batch_ahead: int = 0,
    pages_unreadable: int = 0,
    waiting_code: str | None = None,
    locked: bool = False,
) -> str:
    """``max_pages`` is the MAX_PAGES setting, named when a file had too many pages
    (a PDF's pages, or a TIFF's frames: ``detected_type`` says which to call it),
    with its real ``page_count`` when the worker stored it. ``attempts`` and
    ``max_attempts`` are the worker's tries: shown from the second try on, and
    named when they ran out (``retries_exhausted``). ``progress`` is the worker's
    ``{step, done, total}`` (PRD 2 X3), used only while the document is being
    checked or read, so an old one on a finished document is ignored. In progress
    with no progress after a try: "Waiting to try again." (council of 5 Oct 2026).
    PRD 5 Q6: a progress of ``{"step": "waiting"}`` says the reader is down; with no
    progress, ``batch_ahead`` files of the uploader's own batch still before it say
    how many are ahead. A ready document with ``pages_unreadable`` pages still flagged
    says so (council 16 Oct 2026: such a document is ready, not failed, since e01b937d).
    ``waiting_code`` is the last run's code while it waits: a key problem is READER_STOPPED
    (issue 407), a reading pause (#478) PAUSED_FOR_TODAY or PAUSED_BUDGET. ``locked`` (PRD 7,
    #605): a locked version that failed is never "read it again": its own words."""
    if status == "failed" and locked:
        return LOCKED_CANNOT_DRAW if failure_code == "cannot_draw" else LOCKED_NOT_PREPARED
    if status == "rejected":
        return CUSTOMER_MESSAGES.get(failure_code or "", "This file was not processed.")
    if status == "failed" and failure_code == "too_many_pages":
        what = "This PDF" if detected_type == "pdf" else "This file"
        if page_count is None:  # failed before the count was stored
            return f"{what} has more than {max_pages} pages. The limit is {max_pages}. Split it into parts."
        split = "Split it into parts and upload each one."
        return f"{what} has {page_count} pages. The limit is {max_pages}. {split}"
    if status == "failed" and failure_code == "office_unreadable" and detected_type in _OFFICE_FILES:
        # #522 PR 2: a password, damage, or a conversion that ran out of time.
        what = _OFFICE_FILES[detected_type]
        return f"This {what} could not be opened. If it has a password, remove it and upload again."
    if status == "failed" and failure_code == "password_protected":  # an encrypted .docx, .xlsx or .pptx
        return "This file has a password, so it can't be opened. Remove the password and upload it again."
    if status == "failed" and failure_code == "too_large":  # over its kind's own limit, told after the scan
        if detected_type in _SPREADSHEETS:
            what = "This spreadsheet is larger than spreadsheets can be."
        else:
            what = "This file is larger than files of its kind can be."
        return f"{what} Split it into smaller files and upload them."
    if status == "failed" and failure_code == "retries_exhausted":
        tries = f"{max_attempts} tries" if max_attempts else "several tries"
        return f"The scan stopped after {tries}. Read it again, or ask your admin to look at it."
    if status == "failed" and failure_code == "unreadable":
        # PRD 5 F4: every non-blank page was unreadable; a document with one readable page is ready.
        return "None of this file could be read. Upload a sharper scan."
    if status == "failed" and failure_code == "ocr_failed":
        # The scan reader failed on a page of this file (kaagaz/reading/ocr.py): not an outage.
        return "A page of this file needs another read. Read it again, or upload a clearer scan."
    if status == "failed" and failure_code == "too_large":
        # #522 PR 3: an Office file that came in under a zip's limit, past its own kind's.
        return (
            "This file is larger than Kaagaz takes for its kind."
            " Split it or save it smaller and upload it again."
        )
    if status == "failed" and failure_code == "customer_disabled":
        # The worker came to it while the organisation was turned off (0014): nothing was read.
        return "This file was not read because your organisation was turned off. Read it again."
    if status == "failed" and failure_code in FILE_FAILURES:
        return FILE_FAILURES[failure_code]  # #522: a read again can't help; another copy can
    if status == "failed":
        return "This file needs another read. Read it again, or try another copy of it."
    if status == "ready" and pages_unreadable > 0:
        pages = "1 page" if pages_unreadable == 1 else f"{pages_unreadable} pages"
        return f"Ready. {pages} to check."
    message = _STATUS_MESSAGES.get(status, "")
    if status in _IN_PROGRESS and progress is not None and progress.get("step") == _WAITING_STEP:
        message = _PAUSE_WORDS.get(waiting_code or "") or (
            READER_STOPPED if waiting_code in _KEY_WAITS else READER_WAITING
        )
    elif status in _IN_PROGRESS and progress is not None:
        message = progress_message(progress) or message
    elif status in _IN_PROGRESS and batch_ahead > 0:
        message = files_ahead_message(batch_ahead)
    elif status in _IN_PROGRESS and attempts >= 1:
        message = _WAITING_AGAIN
    if status in _IN_PROGRESS and attempts >= 2 and max_attempts:
        message += f" Try {min(attempts, max_attempts)} of {max_attempts}."
    return message


def progress_message(progress: Mapping[str, Any]) -> str | None:
    """ "Reading page 3 of 40…" and the like, or None for a step it does not know.
    A sheet is read as a whole (total 0), so it has no page number. O(1)."""
    step, done, total = progress.get("step"), progress.get("done"), progress.get("total")
    if step in _STEP_MESSAGES:
        return _STEP_MESSAGES[step]
    if step == "rereading" and isinstance(total, int) and total > 0:  # Dynamic's Careful (F26)
        return f"Reading {total} {'page' if total == 1 else 'pages'} again carefully…"
    if step not in _PAGE_STEPS or not isinstance(done, int) or not isinstance(total, int):
        return None
    if total <= 0:
        return "Reading the document…"
    return f"{_PAGE_STEPS[step]} page {max(1, min(done + 1, total))} of {total}…"


def in_progress(status: str, progress: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """``DocumentProgress``: the stored progress while the document is being
    checked or read, else None (an old one left on a finished document, or a wait for
    the reader, Q6, which has no step count)."""
    if status not in _IN_PROGRESS or progress is None or progress.get("step") == _WAITING_STEP:
        return None
    return {"step": progress["step"], "done": int(progress["done"]), "total": int(progress["total"])}


# Ready first (16 Oct 2026): a ready document is still being understood (its card, map and
# filing) while the worker's last progress before Ready, with its ``ready_at``, is on it; the
# run's end clears it. One left by a worker that died is stale after this long.
UNDERSTANDING_STALE = timedelta(minutes=15)


def understanding(row: Mapping[Any, Any], *, now: datetime) -> bool:
    """Ready, and its understanding still going on (``UNDERSTANDING_STALE``). O(1)."""
    progress = row["progress"]
    if row["status"] != "ready" or not isinstance(progress, Mapping):
        return False
    try:
        ready_at = datetime.fromisoformat(str(progress["ready_at"]))
    except (KeyError, ValueError):
        return False
    return ready_at.tzinfo is not None and now - ready_at < UNDERSTANDING_STALE


def document_time_left(row: Mapping[Any, Any], *, now: datetime) -> dict[str, Any] | None:
    """``TimeLeft`` (PRD 4 #13): how long until ready, while the document is being read
    or understood and is out of Trash; else None. Never on a newer version. O(1)."""
    if row["status"] not in _IN_PROGRESS or row["progress"] is None or row["deleted_at"] is not None:
        return None
    return time_left(row["progress"], now=now)


def can_delete(row: RowMapping, user: CurrentUser) -> bool:
    """May ``user`` move this document's family to Trash, or bring it back? Both act
    on every version, so: an admin, or the one user who uploaded every version.
    ``family_uploaders`` comes from db/listing.py's _COLUMNS."""
    return uploaded_every_version(row["family_uploaders"] or [], user)


def uploaded_every_version(uploaders: Sequence[object], user: CurrentUser) -> bool:
    """``can_delete``'s rule over these versions' uploaders: an admin, or the one user who
    uploaded every one of them (#542: every file of a folder in Trash too)."""
    if user.role == "admin":
        return True
    return bool(uploaders) and all(
        uploader is not None and str(uploader) == user.user_id for uploader in uploaders
    )


def newer_version(newer: Mapping[str, Any] | None, *, max_pages: int) -> dict[str, Any] | None:
    """``NewerVersion``: the family's newest version after this one, while it is
    still being read or failed (a ready one would be current)."""
    if newer is None:
        return None
    return {
        "id": str(newer["id"]),
        "version_no": int(newer["version_no"]),
        "status": newer["status"],
        "failure_code": newer["failure_code"],
        "status_message": status_message(
            newer["status"],
            newer["failure_code"],
            detected_type=newer["detected_type"],
            max_pages=max_pages,
            page_count=newer["page_count"],
            attempts=int(newer["attempts"] or 0),
            max_attempts=newer["max_attempts"],
            progress=newer.get("progress"),
            waiting_code=newer.get("waiting_code"),
        ),
    }


def document_summary(
    row: RowMapping,
    *,
    max_pages: int,
    tags: Sequence[TagRow],
    user: CurrentUser,
    now: datetime,
    unpack: Unpack | None = None,
) -> dict[str, Any]:
    """``DocumentSummary``. ``max_pages`` is passed on to ``status_message``;
    ``tags`` are the document's own, from one ``tags_for_documents`` call per page;
    ``user`` decides ``can_delete``; ``now`` is the request's one clock reading. A zip
    (#522 PR 3) also has ``unpack``, its unpacking (``zip_unpacks.unpacks``; null when it
    wasn't unpacked, or the caller didn't look), whose words are its status message."""
    summary = {
        "id": str(row["id"]),
        "display_name": row["display_name"],
        "detected_type": row["detected_type"],
        "size_bytes": int(row["size_bytes"]),
        "status": row["status"],
        "failure_code": row["failure_code"],
        "status_message": status_message(
            row["status"],
            row["failure_code"],
            detected_type=row["detected_type"],
            max_pages=max_pages,
            page_count=row["page_count"],
            attempts=int(row["attempts"]),
            max_attempts=row["max_attempts"],
            progress=row["progress"],
            batch_ahead=int(row["batch_ahead"]),
            pages_unreadable=int(row["pages_unreadable"]),
            waiting_code=row["waiting_code"],
            locked=bool(row["locked"]),
        ),
        "progress": in_progress(row["status"], row["progress"]),
        "stuck": bool(row["stuck"]),  # issue 407: may be read again (db/documents.py STUCK)
        "locked": bool(row["locked"]),  # PRD 7 (#605): opened only as page pictures, never read
        "time_left": document_time_left(row, now=now),
        "understanding": understanding(row, now=now),  # ready, its card and map still coming
        "doc_type": row["doc_type"],  # PRD 2 D3: from the document card; null without one
        "languages": list(row["languages"] or []),
        "attempts": int(row["attempts"]),
        "max_attempts": row["max_attempts"],
        "can_delete": can_delete(row, user),
        "newer_version": newer_version(row["newer"], max_pages=max_pages),
        "page_count": row["page_count"],
        "created_at": iso(row["created_at"]),
        "uploaded_by_name": row["uploaded_by_name"],
        "folder_id": None if row["folder_id"] is None else str(row["folder_id"]),
        # PRD 7 (#601): in a folder the person can't see (folder_id null): "Shared with you".
        "shared_with_you": bool(row["in_a_folder"]) and row["folder_id"] is None,
        "tags": [tag(each) for each in tags],
        "deleted_at": None if row["deleted_at"] is None else iso(row["deleted_at"]),
        "version_no": int(row["version_no"]),
        "family_id": str(row["family_id"]),
        # PRD 5 F4: pages unreadable or uncertain still on the attention list (a page kept as
        # is has left it), and the values to review (db/listing.py's _COLUMNS).
        "pages_unreadable": int(row["pages_unreadable"]),
        "pages_uncertain": int(row["pages_uncertain"]),
        "values_to_review": int(row["values_to_review"]),
        "words_unsure": int(row["words_unsure"]),  # PRD 5 U2: on the same pages
        "autofile": row["autofile"],  # feature 28: filed, not_filed or null (db/listing.py's _COLUMNS)
        # File management (Q3, O1 to O3, F5): db/listing.py's _FILE_COLUMNS.
        "owner": None if row["owner_id"] is None else {"id": str(row["owner_id"]), "name": row["owner_name"]},
        "sensitivity": row["sensitivity"],
        "versions": int(row["versions"]),
        "version_note": row["version_note"],
        "document_date": None if row["document_date"] is None else row["document_date"].isoformat(),
        "updated_at": None if row["last_updated_at"] is None else iso(row["last_updated_at"]),
        "updated_by": row["last_updated_by_name"],
        "department": row["department"],
        "suggested_department": row["suggested_department"],  # PRD 9 F-01: {id, name} or null
        "pending_suggestion": row["pending_suggestion"],
    }
    if row["detected_type"] == "zip":
        summary["unpack"] = unpack_view(unpack)
        summary["status_message"] = (
            zip_message(row["status"], row["failure_code"], unpack) or summary["status_message"]
        )
    return summary


def unpack_view(unpack: Unpack | None) -> dict[str, Any] | None:
    """``ZipUnpack``: where a zip was unpacked (#522 PR 3), its files counted (unpacked, already
    in Kaagaz, skipped, still waiting) and the first skipped ones, each with why in plain words."""
    if unpack is None:
        return None
    return {
        "folder_upload_id": unpack.upload_id,
        "folder_id": unpack.root_folder_id,
        # PRD 7 (#601): no name for a folder gone or one the person can't see.
        "folder_name": None if unpack.root_folder_id is None else unpack.root_name,
        "files": unpack.file_count,
        "unpacked": unpack.stored,
        "already_in": unpack.duplicate,
        "skipped": unpack.failed,
        "waiting": unpack.waiting,
        "skipped_files": [
            {"name": name, "code": code, "reason": REASONS.get(code, "It could not be unpacked.")}
            for name, code in unpack.skipped
        ],
    }


def zip_message(status: str, failure_code: str | None, unpack: Unpack | None) -> str | None:
    """A zip document's words (#522 PR 3): unpacking k of n files, the result line ("Unpacked:
    120 files into folder "X", 3 skipped (...)", then that the zip still counts toward storage,
    #555), where it stopped, why it wasn't unpacked, or that it was kept as a file; None for the
    usual words (waiting, its virus check)."""
    if unpack is None:
        if status == "needs_reader":
            return "Kept as a file, not unpacked: Kaagaz unpacks a zip only when it is uploaded on its own."
        return ZIP_FAILURES.get(failure_code or "") if status == "failed" else None
    folder = "a folder" if unpack.root_folder_id is None else f'folder "{unpack.root_name}"'
    if status in _IN_PROGRESS:
        done = unpack.stored + unpack.duplicate + unpack.failed
        return f"Unpacking: {done} of {_files(unpack.file_count)} into {folder}…"
    line = f"{_files(unpack.stored)} into {folder}"
    if unpack.duplicate:
        line += f", {unpack.duplicate} already in Kaagaz"
    if unpack.failed:
        why = sorted(unpack.reasons.items(), key=lambda item: (-item[1], item[0]))
        line += (
            f", {unpack.failed} skipped ("
            + ", ".join(f"{count} {SHORT_REASONS.get(code, code)}" for code, count in why)
            + ")"
        )
    if status == "failed":
        stopped = ZIP_FAILURES.get(failure_code or "") or "Unpack it again, or try another copy of the zip."
        return f"Unpacking stopped after {line}. {stopped}"
    return f"Unpacked: {line}. {KEPT_ZIP}"


def _files(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


_FAILED = ("failed", "rejected", "needs_reader")  # as the operator's health counts them (0020)


def batch_view(
    batch_id: str,
    rows: Sequence[RowMapping],
    *,
    max_pages: int,
    tags: Mapping[str, Sequence[TagRow]],
    user: CurrentUser,
    now: datetime,
    max_active: int,
    unpacks: Mapping[str, Unpack] | None = None,
) -> dict[str, Any]:
    """``BatchView`` (PRD 5 Q1, U1): the uploader's own files of one drop, in upload
    order (``listing.batch_rows``), each a ``DocumentSummary`` with its first unreadable
    page (``first_unreadable``, U1's "Couldn't read page n") and its count of values
    (``values_found``), and the drop's counts:

    * failed: failed, rejected or can't be read yet (needs_reader);
    * waiting: in progress with no step yet (queued) or waiting for the reader (Q6);
    * reading: in progress with a step (scanning, preparing, reading, understanding);
    * attention: ready, with a page or a value that needs attention (U2's rule);
    * ready: ready with none;
    * locked: locked and pictured (PRD 7, #605): finished, never read.

    The bar is pages read over total pages: a file whose page count is not known yet
    counts as 1 page; a finished file (ready or failed) is read whole; one past reading
    (understanding, indexing) too; one being read counts its pages done. ``time_left`` is
    the slowest file being read's, plus the pages still waiting at its page rate shared
    over ``max_active`` (MAX_ACTIVE_PER_CUSTOMER, Q5); null while no file has an estimate.
    ponytail: one rate for the whole drop; per-file estimates if drops mix scans and text.
    Only this user's files, so no other customer's load reaches it. O(files)."""
    counts = {
        "total": len(rows),
        "ready": 0,
        "reading": 0,
        "waiting": 0,
        "attention": 0,
        "failed": 0,
        "locked": 0,
    }
    pages_read = pages_total = pages_waiting = 0
    estimates = []
    for row in rows:
        status, progress = row["status"], row["progress"]
        total = int(row["page_count"] or 0) or 1
        done = total
        if status in _FAILED:
            counts["failed"] += 1
        elif status == "ready":
            flagged = (
                int(row["pages_unreadable"]) + int(row["pages_uncertain"]) + int(row["values_to_review"])
            )
            counts["attention" if flagged else "ready"] += 1
        elif status == "locked":
            counts["locked"] += 1  # finished: read whole
        elif progress is None or progress.get("step") == _WAITING_STEP:
            counts["waiting"] += 1
            done = 0
            pages_waiting += total
        else:
            counts["reading"] += 1
            step, count = progress.get("step"), progress.get("done")
            if step in ("scanning", "preparing"):
                done = 0
            elif step == "reading":
                done = min(total, count) if isinstance(count, int) else 0
        pages_read, pages_total = pages_read + done, pages_total + total
        left = document_time_left(row, now=now)
        if left is not None:
            estimates.append(left)
    return {
        "id": batch_id,
        "documents": [
            {
                **document_summary(
                    row,
                    max_pages=max_pages,
                    tags=tags[str(row["id"])],
                    user=user,
                    now=now,
                    unpack=(unpacks or {}).get(str(row["id"])),
                ),
                "first_unreadable": first_unreadable(row["first_unreadable"]),
                "values_found": int(row["values_found"]),  # U1: "All values verified" needs some
            }
            for row in rows
        ],
        "counts": counts,
        "pages_read": pages_read,
        "pages_total": pages_total,
        "time_left": _batch_time_left(estimates, pages_waiting, pages_total - pages_read, max_active),
    }


def _batch_time_left(
    estimates: Sequence[dict[str, Any]], pages_waiting: int, pages_left: int, max_active: int
) -> dict[str, Any] | None:
    if not estimates:
        return None
    slowest = max(estimates, key=lambda left: float(left["seconds"]))
    queued = pages_waiting * float(slowest["item_seconds"]) / max(1, max_active)
    return {**slowest, "seconds": round(float(slowest["seconds"]) + queued, 1), "items_left": pages_left}


def document_version(row: RowMapping) -> dict[str, Any]:
    """``DocumentVersion``: one version in the detail's ``versions`` (``listing.family_versions``),
    with V12's name, note, uploader and how it joined: ``uploaded``, ``linked`` (a person
    accepted Kaagaz's suggestion) or ``auto`` (linked on its own)."""
    uploader = row["uploaded_by"]
    return {
        "id": str(row["id"]),
        "version_no": int(row["version_no"]),
        "status": row["status"],
        "is_current": bool(row["is_current"]),
        "created_at": iso(row["created_at"]),
        "display_name": row["display_name"],
        "version_note": row["version_note"],
        "uploaded_by": None if uploader is None else {"id": str(uploader), "name": row["uploaded_by_name"]},
        "joined": {None: "uploaded", "suggested": "linked", "auto": "auto"}[row["linked"]],
    }


def document_folder(path: Sequence[FolderRow]) -> dict[str, Any] | None:
    """``DocumentFolder``: the document's folder and the folders from the top down
    to it, itself last (``folders.folder_path``). None at the top level."""
    if not path:
        return None
    here = path[-1]
    return {"id": here.id, "name": here.name, "path": [{"id": each.id, "name": each.name} for each in path]}


def shown_folder(conn: Connection, customer_id: str, row: FolderRow) -> dict[str, Any]:
    """``Folder`` in a write's reply (PRD 7, #599): as ``GET /folders`` shows it to the person, so a
    parent they can't see is null. One statement when it has a parent."""
    hidden = row.parent_id is not None and not listing.folders_seen(conn, customer_id, [row.parent_id])
    return folder(replace(row, parent_id=None) if hidden else row)


def folder(row: FolderRow) -> dict[str, Any]:
    """``Folder``."""
    return {
        "id": row.id,
        "name": row.name,
        "parent_id": row.parent_id,
        "created_at": iso(row.created_at),
        "kind": row.kind,  # File management F1
        "sort_order": row.sort_order,
        "department_only": row.department_only,  # PRD 7 (#601): the icons
        "private": row.private,
    }


def trashed_folder(row: TrashedFolder, user: CurrentUser) -> dict[str, Any]:
    """``TrashedFolder`` (#542): a folder in Trash with what it holds at every depth, the live
    folder it goes back into (``was_in``, root first: [] the top level, null gone), and whether
    this user may restore it (``can_delete``'s rule over every version of every file in it; and
    an admin's only, when its restore brings a department back, F1)."""
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "was_in": None if row.was_in is None else list(row.was_in),
        "files": row.files,
        "folders": row.folders,
        "deleted_at": iso(row.deleted_at),
        "deleted_by_name": row.deleted_by_name,
        "can_restore": uploaded_every_version(row.uploaders, user)
        and (user.role == "admin" or not row.needs_admin),
    }


def seen_items(conn: Connection, customer_id: str, rows: Sequence[ItemRow]) -> list[ItemRow]:
    """PRD 7 (#599): the items as the person may see them: an item whose document they can't see
    (a copy of a hidden file) names no document, only that it is hidden. One statement."""
    ids = [row.document_id for row in rows if row.document_id is not None]
    seen = listing.documents_seen(conn, customer_id, ids) if ids else set()
    # #601: a file the person sees in a folder they don't (given to them) names no folder.
    places = [row.document_folder_id for row in rows if row.document_id in seen and row.document_folder_id]
    places_seen = listing.folders_seen(conn, customer_id, places) if places else set()
    return [
        (row if row.document_folder_id in places_seen else replace(row, document_folder_id=None))
        if row.document_id is None or row.document_id in seen
        else replace(
            row,
            document_id=None,
            document_status=None,
            document_failure_code=None,
            document_folder_id=None,
            in_trash=False,
            hidden=True,
        )
        for row in rows
    ]


def folder_upload_item(row: ItemRow) -> dict[str, Any]:
    """``FolderUploadItem`` (#484): the item, and its document's state (null with none, or one
    deleted for good). PRD 7: ``hidden`` for a copy of a file the person can't see."""
    document = None
    if row.document_status is not None:
        document = {
            "status": row.document_status,
            "failure_code": row.document_failure_code,
            "folder_id": row.document_folder_id,
            "in_trash": row.in_trash,
        }
    return {
        "id": row.id,
        "seq": row.seq,
        "path_key": row.path_key,
        "name": row.name,
        "folder_id": row.folder_id,
        "size": row.size,
        "state": row.state,
        "failure_code": row.failure_code,
        "document_id": row.document_id,
        "document": document,
        "hidden": row.hidden,
    }


def tag(row: TagRow) -> dict[str, Any]:
    """``Tag``."""
    return {"id": row.id, "name": row.name}


def page_summary(row: RowMapping) -> dict[str, Any]:
    """``PageSummary``, from a ``listing`` page row, with its problems (PRD 5 U4) and the
    catalogue name of the model that read it (model choice: the viewer's "Read by {name}";
    null when no catalogue model read it: rapidocr, text_layer, cells)."""
    return {**page_fields(row), "read_by_name": row["read_by_name"], "problems": page_problems(row)}


def page_fields(row: RowMapping) -> dict[str, Any]:
    """``PageSummary`` without ``problems`` (the review queue's page). PRD 5 adds who read
    the page (RT2), its reading result (F1), "Keep as is" and a read again asked for (F5),
    named as the pages columns are (0021)."""
    accepted_by, accepted_at = row["accepted_by"], row["accepted_at"]
    asked_at = row["read_again_requested_at"]
    return {
        "page_no": int(row["page_no"]),
        "width": float(row["width"]),
        "height": float(row["height"]),
        "unit": row["unit"],
        "has_image": bool(row["has_image"]),
        "route": row["route"],  # PRD 2 R1; null when the page was not read
        "effective_dpi": row["effective_dpi"],  # R4
        "low_resolution": bool(row["low_resolution"]),
        # RT2: the model ID, rapidocr, text_layer or cells; null for a blank page or one read before PRD 5.
        "read_by": row["read_by"],
        "read_quality": row["read_quality"],  # F1: good, uncertain or unreadable
        "read_issues": list(row["read_issues"] or []),  # F1: codes from the issue table
        "unsure_words": int(row["unsure_words"]),
        "illegible_words": int(row["illegible_words"]),
        "unpinned_lines": int(row["unpinned_lines"]),
        # F5 "Keep as is": who and when, both or neither; the page stays flagged.
        "accepted_by": None if accepted_by is None else str(accepted_by),
        "accepted_at": None if accepted_at is None else iso(accepted_at),
        "read_again_requested_at": None if asked_at is None else iso(asked_at),  # F5, until it is read
    }


def first_unreadable(page: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """``UnreadablePage`` (PRD 5 U1): a batch file's first unreadable page still on the
    attention list (``listing``'s ``first_unreadable``), as ``{page_no, says, fix}`` from
    its first problem, for the tray's "Couldn't read page n"; ``says`` and ``fix`` are
    null when no problem can be said. None when it has no such page. O(issues)."""
    if page is None:
        return None
    problems = page_problems(cast(RowMapping, {**page, "values_disputed": 0}))  # no disputed count here
    said = problems[0] if problems else {"says": None, "fix": None}
    return {"page_no": int(page["page_no"]), "says": said["says"], "fix": said["fix"]}


def page_problems(row: RowMapping) -> list[dict[str, Any]]:
    """``PageProblem`` for each issue in ``read_issues``, in the F1 table's order (as
    stored): what happened (``page_quality.says``, with its count) and the fix. An issue
    whose count is 0 now (every disputed value since given another reason) is left out: a
    sentence never shows a made-up number. O(issues)."""
    counts = {
        ILLEGIBLE_WORDS: int(row["illegible_words"]),
        UNPINNED: int(row["unpinned_lines"]),
        READERS_DISAGREE: int(row["values_disputed"]),
    }
    problems = []
    for code in row["read_issues"] or []:
        if code in counts and counts[code] == 0:
            continue
        problems.append({"code": code, "says": says(code, count=counts.get(code)), "fix": fix(code)})
    return problems


def extracted_value(row: RowMapping) -> dict[str, Any]:
    """``ExtractedValue``."""
    confidence = row["confidence"]
    return {
        "id": str(row["id"]),
        "document_id": str(row["document_id"]),
        "record_key": row["record_key"],
        "field": row["field"],
        "value": row["value_text"],
        "raw": row["value_raw"],
        "second_reading": row["second_reading"],
        "page_no": int(row["page_no"]),
        "region": row["region"],
        "label": row["label"],
        "label_reason": row["label_reason"],
        "confidence": None if confidence is None else float(confidence),
        # PRD 2 D3: the value's label spot, which can be on another page; null when it has none.
        "label_page_no": row["label_page_no"],
        "label_region": row["label_region"],
        "votes": value_votes(row.get("votes")),
    }


def value_votes(votes: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """``ValueVotes`` (Ensemble, F27): the readers' vote saved with a value, for the user in
    plain words: what each reader read there, the main reader first, how many read the
    decided text and of how many; never a model's id or name (members never see them on a
    value). None when one reader read it."""
    if votes is None:
        return None
    reads = [
        {"main": index == 0, "text": read.get("text")} for index, read in enumerate(votes.get("reads", []))
    ]
    return {
        "value": votes.get("value"),
        "agreed": votes.get("agreed"),
        "readers": votes.get("readers"),
        "reads": reads,
    }


def _decimal(value: Decimal | None) -> str | None:
    """A numeric column as a string, so no float rounding happens (PROVISIONAL, plan 3.8)."""
    return None if value is None else str(value)


def invoice(row: RowMapping, lines: Sequence[RowMapping]) -> dict[str, Any]:
    """The detail's invoice (PRD 2 E5): the row, then its lines in line_no order."""
    day = row["invoice_date"]
    return {
        "invoice_number": row["invoice_number"],
        "invoice_date": None if day is None else day.isoformat(),
        "seller_name": row["seller_name"],
        "seller_gstin": row["seller_gstin"],
        "buyer_name": row["buyer_name"],
        "buyer_gstin": row["buyer_gstin"],
        "subtotal": _decimal(row["subtotal"]),
        "tax_total": _decimal(row["tax_total"]),
        "total": _decimal(row["total"]),
        "currency": row["currency"],
        "label": row["label"],
        "check_status": row["check_status"],
        "check_reasons": list(row["check_reasons"]),
        "lines": [
            {
                "line_no": line["line_no"],
                "description": line["description"],
                "hsn": line["hsn"],
                "quantity": _decimal(line["quantity"]),
                "unit": line["unit"],
                "rate": _decimal(line["rate"]),
                "amount": _decimal(line["amount"]),
                "label": line["label"],
            }
            for line in lines
        ],
    }


def document_link(row: RowMapping) -> dict[str, Any]:
    """``DocumentLink``: a related document and the identifier both share (PRD 3 Y2),
    as links.normalise_identifier stored it."""
    return {
        "id": str(row["id"]),
        "display_name": row["display_name"],
        "doc_type": row["doc_type"],
        "identifier": {"kind": row["identifier_kind"], "value": row["identifier_value"]},
    }


def page_readings(
    page_no: int, rows: Sequence[RowMapping], marks: Sequence[RowMapping] = ()
) -> dict[str, Any]:
    """``PageReadings``: each reader's lines on one page, the first reader first, and
    the page's reading marks (PRD 5 U3, ``listing.page_marks``).
    ``text`` is the stored ``raw``, exactly as read. A reader keeps its lines while
    they fit MAX_READING_LINES and MAX_READING_TEXT; past either, or after a line cut
    in SQL, it stops and says ``truncated``. Never logged: it is document content."""
    readers: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        if not readers or readers[-1]["role"] != row["role"]:
            readers.append(
                {
                    "role": row["role"],
                    "reader": row["reader"],
                    "reader_version": row["reader_version"],
                    "truncated": False,
                    "lines": [],
                }
            )
            used = 0
        reader = readers[-1]
        if row["ord"] is None or reader["truncated"]:
            continue
        line = row["text"]
        if len(reader["lines"]) == MAX_READING_LINES or used + len(line) > MAX_READING_TEXT:
            reader["truncated"] = True
            continue
        used += len(line)
        reader["lines"].append({**_line(row), "box_source": row["box_source"]})  # PRD 9 F-16
        reader["truncated"] = bool(row["cut"])
    shown = [{"quality": mark["quality"], "region": mark["region"], "text": mark["text"]} for mark in marks]
    return {"page_no": page_no, "readers": readers, "marks": shown}


def clear_copy(
    page_numbers: Sequence[int],
    rows: Sequence[RowMapping],
    marks: Sequence[RowMapping],
    live: Sequence[RowMapping],
) -> dict[str, Any]:
    """``ClearCopy`` (Read clearly, 19 Oct 2026): every page with its lines and marks, from
    ``listing.clear_copy``, each line as ``clear_line`` with ``live`` (the changes in force,
    oldest first). The lines stop at CLEAR_COPY_LINES or CLEAR_COPY_TEXT for the whole
    document, or after a line cut in SQL; then ``truncated``. O(lines + marks)."""
    pages: dict[int, dict[str, Any]] = {n: {"page_no": n, "lines": [], "marks": []} for n in page_numbers}
    truncated, used, count = False, 0, 0
    for row in rows:
        if count == CLEAR_COPY_LINES or used + len(row["text"]) > CLEAR_COPY_TEXT:
            truncated = True
            break
        used, count = used + len(row["text"]), count + 1
        truncated = truncated or bool(row["cut"])
        if row["page_no"] in pages:
            pages[row["page_no"]]["lines"].append(clear_line(row, live))
    for mark in marks:
        if mark["page_no"] in pages:
            pages[mark["page_no"]]["marks"].append(
                {"quality": mark["quality"], "region": mark["region"], "text": mark["text"]}
            )
    return {"pages": list(pages.values()), "truncated": truncated}


def clear_line(row: RowMapping, live: Sequence[RowMapping]) -> dict[str, Any]:
    """A clean copy's ``ReadLine`` (#527): with its ``key`` and the ``correction`` in force (who
    and when, and ``before_text``, the line as first shown); ``text`` is the line's text now
    when a change in force touches it (``word_review.line_text``). O(changes)."""
    change = word_review.line_correction(row, live)
    text_now = word_review.line_text(row, live)
    line = {**_line(row), "key": word_review.line_key(row), "correction": None}
    if text_now is not None:
        line["text"] = text_now
    if change is not None:
        line["correction"] = {
            "before_text": row["text"],
            "by": change["user_name"] or "",
            "at": iso(change["created_at"]),
            "change_id": str(change["id"]),
        }
    return line


def _line(row: RowMapping) -> dict[str, Any]:
    """``ReadLine``: one line as read (``text`` is the stored ``raw``), with its table place."""
    return {
        "text": row["text"],
        "confidence": row["confidence"],
        "origin": row["origin"],
        "kind": row["kind"],
        "region": row["region"],
        "table": _table(row),  # Read clearly (19 Oct 2026)
    }


def _table(row: RowMapping) -> dict[str, Any] | None:
    """A table cell's place, as stored (PRD 2 P4): its table, row and column from 0, and
    whether it is a header; None for any other line."""
    if row["table_id"] is None:
        return None
    return {
        "id": row["table_id"],
        "row": row["table_row"],
        "col": row["table_col"],
        "header": bool(row["table_header"]),
    }


def review_item(row: RowMapping) -> dict[str, Any]:
    """``ReviewItem``: the value, its document and its page, and the reasons its
    document's invoice check failed (PRD 3 Y5; [] when it passed or has no invoice)."""
    return {
        "value": extracted_value(row),
        "document_id": str(row["document_id"]),
        "document_name": row["document_name"],
        "detected_type": row["detected_type"],
        "page": page_fields(row),
        "check_reasons": list(row["check_reasons"]),
        "page_turn": int(row["page_turn"]),  # PRD 7 section 14 (#600): saved for everyone, 0 none
    }


def failed_document(row: RowMapping, *, max_pages: int) -> dict[str, Any]:
    """``FailedDocument`` (PRD 3 Z1): a document that failed or can't be read, with the
    message the document screen shows for it and when it was last tried."""
    run = row["last_run_at"]
    return {
        "id": str(row["id"]),
        "display_name": row["display_name"],
        "status": row["status"],
        "failure_code": row["failure_code"],
        "message": status_message(
            row["status"],
            row["failure_code"],
            detected_type=row["detected_type"],
            max_pages=max_pages,
            page_count=row["page_count"],
            attempts=int(row["attempts"]),
            max_attempts=row["max_attempts"],
        ),
        "last_run_at": None if run is None else iso(run),
    }


def message(
    row: RowMapping, feedback: str | None = None, pin: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """``Message``. ``pin``: the source this user put on top of the answer (``SourcePin``)."""
    return {
        "id": str(row["id"]),
        "role": row["role"],
        "text": row["text"],
        "created_at": iso(row["created_at"]),
        "answer": None if row["answer"] is None else without_resume(row["answer"]),
        "scope": row["scope"],  # a question's ScopeBody (PRD 3 H2), None when it had none
        "feedback": feedback,
        "pin": None
        if pin is None
        else {
            "document_id": str(pin["document_id"]),
            "page_no": int(pin["page_no"]),
            "ref_id": pin.get("ref_id"),
        },
    }


# The words that stand in for a sentence whose every source has been deleted.
GONE_SENTENCE = "This part of the answer came from a document that has since been deleted."
# PRD 7 (#602, questions 1 and 3): the one line of a saved answer the person may no longer see whole.
NO_ACCESS = "no_access"
NO_ACCESS_LINE = (
    "You no longer have access to a document behind this answer. Ask again to get an answer from the"
    " files you can see."
)
# PRD 7 (#605, p2:80): a saved answer that used a document now locked, shown to a person who may
# open it, is this one line, never its words (one who may not gets NO_ACCESS_LINE, as for any
# hidden source: the view leaves the document out of ``document_states``).
LOCKED_LINE = "This answer used a document that is now locked. Open it directly to read it."
# p2:79: the fixed line for a person who may open a locked file, added when shown, never saved.
LOCKED_NOTE = "Ask doesn't read locked files. To check one, open it directly."


def cited_document_ids(answer: Mapping[str, Any] | None) -> list[str]:
    """Every document a saved answer points at: its citations, closest and related
    documents, the documents of the pages that couldn't be read it names (PRD 5 F6), and
    those it lists (PRD 6 A, ``documents``)."""
    if not answer:
        return []
    ids = [str(each["document_id"]) for each in answer.get("citations", []) if each.get("document_id")]
    ids += [str(each["id"]) for each in answer.get("closest_documents", []) if each.get("id")]
    ids += [
        str(each["document_id"]) for each in answer.get("related_documents", []) if each.get("document_id")
    ]
    ids += [
        str(each["document_id"]) for each in answer.get("unreadable_pages", []) if each.get("document_id")
    ]
    ids += [
        str(each["document_id"])
        for each in (answer.get("mentions") or {}).get("documents", [])
        if each.get("document_id")
    ]
    ids += [str(each["document_id"]) for each in answer.get("documents") or [] if each.get("document_id")]
    ids += [str(each) for choice in _choices(answer) for each in choice.get("document_ids", [])]  # PRD 6 B1
    return ids


def _choices(answer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    clarify = answer.get("clarify")
    return list(clarify.get("choices", [])) if isinstance(clarify, Mapping) else []


def no_access(answer: Mapping[str, Any] | None, states: Mapping[str, str], scope: Sequence[str] = ()) -> bool:
    """PRD 7 (#602, question 1): whether ``answer`` names a document missing from ``states``
    (db/chat.py ``document_states``, read through the view): one the person can no longer see,
    or deleted forever. Such an answer is never shown, quoted or fed to a prompt again. A
    not-found answer names no document, so its question's ``scope`` ids count as its own: the
    words it understood may have come from them."""
    named = cited_document_ids(answer)
    if answer and answer.get("status") == "not_found":
        named += [str(each) for each in scope]
    return any(document not in states for document in named)


def no_access_answer() -> dict[str, Any]:
    """The whole answer replaced (question 3): status ``no_access``, NO_ACCESS_LINE, and nothing
    else of it, no list, word, question as understood, clarify, note or opening."""
    return {
        "status": NO_ACCESS,
        "label": None,
        "sentences": [{"text": NO_ACCESS_LINE, "citations": []}],
        "citations": [],
        "closest_documents": [],
        "route": "search",
        "normalized_question": "",
        "model_used": False,
    }


def locked_answer() -> dict[str, Any]:
    """PRD 7 (#605): a saved answer that used a document now locked, replaced whole: status
    ``no_access``, reason ``locked``, LOCKED_LINE, and nothing else of it."""
    return {
        "status": NO_ACCESS,
        "reason": "locked",
        "label": None,
        "sentences": [{"text": LOCKED_LINE, "citations": []}],
        "citations": [],
        "closest_documents": [],
        "route": "search",
        "normalized_question": "",
        "model_used": False,
    }


def shown_message(
    row: RowMapping,
    states: Mapping[str, str],
    feedback: str | None = None,
    pin: Mapping[str, Any] | None = None,
    scope: Sequence[str] = (),
) -> dict[str, Any]:
    """``message`` with its answer as it may be shown now (``answer_with_source_states``; ``scope``,
    its question's document ids); one replaced whole loses its text (the joined sentences) and
    its pick too."""
    shown = message(row, feedback, pin)
    if shown["answer"]:
        shown["answer"] = answer_with_source_states(shown["answer"], states, scope)
        if shown["answer"]["status"] == NO_ACCESS:
            locked = shown["answer"].get("reason") == "locked"  # PRD 7 (#605)
            shown["text"], shown["pin"] = LOCKED_LINE if locked else NO_ACCESS_LINE, None
    return shown


def answer_with_source_states(
    answer: Mapping[str, Any], states: Mapping[str, str], scope: Sequence[str] = ()
) -> dict[str, Any]:
    """A saved answer as it may be shown now (``states`` from db/chat.py
    ``document_states``, through the view).

    * PRD 7 (#602): one naming a document missing from ``states`` (the person can no longer see
      it, or it was deleted forever; ``no_access``, with its question's ``scope``) is replaced
      whole (``no_access_answer``). Changed on purpose: a document deleted forever was a
      sentence's GONE_SENTENCE before. So every document it names is in ``states``.
    * PRD 7 (#605): one naming a document the person may see but that is now ``locked`` is
      replaced whole too (``locked_answer``: LOCKED_LINE).
    * Each citation gets ``source_state``. One from a document in Trash loses its snippet and
      its highlight regions: Trash means deleted, so its words and its place on the page must
      not show any more.
    * A sentence whose every citation is gone is replaced by ``GONE_SENTENCE``.
    * Closest, related, "Also mentions" and listed (PRD 6 A) documents that are gone are left out.
    * PRD 5 F6: a page that couldn't be read, of a document that is gone, is left out
      of ``unreadable_pages``, its count and the note, so a deleted document's name
      never shows again (the note is written again from the pages left).
    A citation of an older version keeps its text and link, marked
    ``older_version``: it was true of that version. The stored answer is never
    changed, so a restore brings everything back. O(citations + sentences + pages).
    """
    if no_access(answer, states, scope):
        return no_access_answer()
    if any(states.get(document) == "locked" for document in cited_document_ids(answer)):
        return locked_answer()

    def kept(document: Any) -> bool:
        return states.get(str(document)) not in (None, "in_trash")

    citations = []
    gone: set[int] = set()
    for each in answer.get("citations", []):
        state = states.get(str(each.get("document_id")), "in_trash")
        citation = {**each, "source_state": state}
        if state == "in_trash":
            citation["snippet"] = None
            citation["regions"] = citation["label_regions"] = []
            gone.add(each.get("n"))
        citations.append(citation)
    sentences = []
    for sentence in answer.get("sentences", []):
        cited = sentence.get("citations", [])
        if cited and all(number in gone for number in cited):
            sentence = {**sentence, "text": GONE_SENTENCE}
        sentences.append(sentence)
    closest = [each for each in answer.get("closest_documents", []) if kept(each.get("id"))]
    related = [each for each in answer.get("related_documents", []) if kept(each.get("document_id"))]
    shown = {
        **without_resume(answer),
        "citations": citations,
        "sentences": sentences,
        "closest_documents": closest,
    }
    if "related_documents" in answer:
        shown["related_documents"] = related
    if "documents" in answer:
        shown["documents"] = [each for each in answer.get("documents") or [] if kept(each.get("document_id"))]
    if answer.get("clarify"):  # PRD 6 B1: a choice whose every document is gone is no choice
        alive = {document for document, state in states.items() if state != "in_trash"}
        documents = [set(map(str, c.get("document_ids", []))) for c in _choices(answer)]
        live = [c for c, d in zip(_choices(answer), documents, strict=True) if not d or d & alive]
        shown["clarify"] = {**answer["clarify"], "choices": live}
    if answer.get("mentions"):
        # "Also mentions": a gone document leaves the list and the count.
        listed = answer["mentions"].get("documents", [])
        live = [each for each in listed if kept(each.get("document_id"))]
        total = int(answer["mentions"].get("total") or 0) - (len(listed) - len(live))
        shown["mentions"] = {**answer["mentions"], "documents": live, "total": total}
    pages = answer.get("unreadable_pages") or []
    left = [page for page in pages if kept(page.get("document_id"))]
    if len(left) != len(pages):
        total = int(answer.get("unreadable_page_count") or 0) - (len(pages) - len(left)) if left else 0
        named = [
            FlaggedPage(
                str(page["document_id"]),
                str(page["document_name"]),
                int(page["page_no"]),
                str(page["read_quality"]),
            )
            for page in left
        ]
        shown.update(
            unreadable_pages=left, unreadable_page_count=total, reading_note=not_found_note(named, total)
        )
    return shown


def conversation_summary(row: RowMapping) -> dict[str, Any]:
    """``ConversationSummary``; ``channel``: ``whatsapp`` for one asked on WhatsApp (#603), else None."""
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "created_at": iso(row["created_at"]),
        "channel": row["channel"],
    }


def me_json(
    user: CurrentUser,
    *,
    email_on: bool,
    providers: Sequence[str],
    recent_connections: Sequence[dict[str, str]] = (),
    access: tuple[bool, Sequence[str], bool, bool],
    whatsapp: bool = False,
) -> dict[str, Any]:
    """``Me``: who is signed in, their customer, and the session's scope ("app" for a
    member or reviewer, "admin" for an admin; the screen shows Admin only for "admin").
    ``email_confirmed`` false shows the "Confirm your email" banner (#457), so it is true
    while email is off: nothing could be sent to confirm it. ``recent_connections``: the
    "Google connected on 30 Oct." banner's ``{provider, linked_at}`` (#457 phase 1; only
    /auth/me and /auth/session read them, every other reply leaves the list empty).
    ``providers``: those switched on (``oidc.enabled_providers``), so Account shows "Ways to
    sign in" only while one is. ``google_link_required`` (#507): the limited session, whose
    password lets the user only connect Google; ``google_unchecked`` (#507 review): in it, their
    connected Google account is from before its domain was checked, so they sign in with Google
    once instead. ``access`` (PRD 7, #601): the person's Management and the departments they head,
    so the screens show a Head's menus, and whether they are in a live department (``in_department``,
    PR 10c: "Ask your admin for access" when not), and whether the organisation has ever kept
    something to some people (``walled``, PR 11c: that message only once it has). ``whatsapp``
    (#603): only while WHATSAPP_ENABLED is on, so Account shows WhatsApp."""
    return {
        "access": {
            "management": access[0],
            "heads": list(access[1]),
            "in_department": access[2],
            "walled": access[3],
        },
        "user": {
            "id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "must_change_password": user.must_change_password,
            "totp_enabled": user.totp_enabled,
            "two_step_turned_off": user.two_step_turned_off,  # by the admin; sign-in is the password
            # The organisation requires it for admins (0068, #481): Account offers no Turn off.
            "two_step_required": user.two_step_required and user.role == "admin",
            "can_delete_forever": user.delete_forever_allowed and user.role != "admin",  # the grant
            "email_confirmed": user.email_confirmed or not email_on,
            "google_link_required": needs_google_link(user, "google" in providers),
            "google_unchecked": needs_google_link(user, "google" in providers) and user.google_unchecked,
        },
        "customer": {"id": user.customer_id, "name": user.customer_name},
        "session": {"scope": user.scope},
        "recent_connections": list(recent_connections),
        "providers": list(providers),
        **({"whatsapp": True} if whatsapp else {}),
    }


def admin_user(
    row: RowMapping,
    *,
    now: datetime,
    signs_in_with: Sequence[str] | None = None,
    google_needed: bool | None = None,
    google_unchecked: bool = False,
) -> dict[str, Any]:
    """``AdminUser``. ``locked_until`` is null once the lock has passed, so the
    screen never shows a lock that no longer holds. ``signs_in_with`` (#457 phase 1:
    "password" and each approved provider) only where the reply knows it: the list, and the
    replies that removed connections; the screen keeps what it had otherwise. ``google_needed``
    (#507: their role uses Google only and they have no connection that passes) likewise, with
    ``google_unchecked`` (#507 review: their connection's domain is not checked yet)."""
    locked_until: datetime | None = row["locked_until"]
    return {
        **({} if signs_in_with is None else {"signs_in_with": list(signs_in_with)}),
        **(
            {}
            if google_needed is None
            else {"google_needed": google_needed, "google_unchecked": google_unchecked}
        ),
        "id": str(row["id"]),
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
        "disabled": bool(row["disabled"]),
        "created_at": iso(row["created_at"]),
        "locked_until": iso(locked_until) if locked_until is not None and locked_until > now else None,
        "totp_enabled": row["totp_enabled_at"] is not None,
        "two_step_turned_off": row["totp_off_at"] is not None and not row["two_step_required"],
        "must_change_password": bool(row["must_change_password"]),
        "can_delete_forever": row["delete_forever_allowed_at"] is not None and row["role"] != "admin",
        "management": bool(row["management"]),  # PRD 7 (#601)
    }


DELETED_DOCUMENT = "a deleted document"
HIDDEN_FILE = "a file you can't see"  # PRD 7 (#601): the access activity's mask
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
CURSOR = r"^\d{1,16}-\d{1,18}$"  # the last row's created_at in microseconds, then its id


def timeline(
    conn: Connection,
    customer_id: str,
    filters: audit.Filters,
    *,
    limit: int,
    cursor: str | None = None,
    offset: int = 0,
    full: bool = False,
    unseen_masked: bool = False,
) -> dict[str, Any]:
    """A5's ``{items, next_cursor}``: one page of ``audit.entries`` with its names (three
    queries); ``next_cursor`` is null on the last page. ``cursor`` matches CURSOR.
    ``unseen_masked`` (PRD 7, #601: a non-Admin reading entries the view doesn't filter, the
    access activity): an entry about a file the person can't see names no file, version, family
    or folder, and keeps only ``details.self`` (one more query)."""
    after = None
    if cursor is not None:
        moment, row_id = cursor.split("-")
        after = (_EPOCH + int(moment) * _MICROSECOND, int(row_id))
    rows = audit.entries(conn, customer_id, filters, limit=limit + 1, cursor=after, offset=offset)
    page = rows[:limit]
    found = audit.names(conn, customer_id, page)
    last = page[-1] if len(rows) > limit else None
    items = [audit_entry(row, found, full=full) for row in page]
    if unseen_masked:
        files = {
            e["document"]["id"] for e in items if e["document"] is not None and not e["document"]["deleted"]
        }
        seen = listing.documents_seen(conn, customer_id, sorted(files)) if files else set()
        for entry in items:
            if entry["document"] is not None and entry["document"]["id"] in files - seen:
                details = entry["details"] if isinstance(entry["details"], dict) else {}
                entry.update(
                    target_id=None,
                    family_id=None,
                    folder=None,
                    document={"id": None, "name": HIDDEN_FILE, "version_no": None, "deleted": False},
                    details={key: details[key] for key in ("self",) if key in details},
                )
    return {
        "items": items,
        "next_cursor": None
        if last is None
        else f"{(last['created_at'] - _EPOCH) // _MICROSECOND}-{int(last['id'])}",
    }


def audit_entry(row: RowMapping, names: audit.Names = audit.NO_NAMES, *, full: bool) -> dict[str, Any]:
    """``AuditEntry`` (File management A5): names read now, a deleted document by
    DELETED_DOCUMENT. ``full`` (the admin's log): every detail but money; otherwise only
    ``audit.SHOWN_DETAILS``. The ids in details come with their names: ``folder_path``,
    ``parent_path``, and ``from_name`` and ``to_name`` for an owner change. An account event
    (a ``user`` target) names the person it is about: ``subject_name`` (issue 375); PRD 7 (#601):
    so does an entry about a person (``subject_id``, an access change)."""
    raw = row["details"] if isinstance(row["details"], dict) else {}
    # Older org.models_set rows hold the operator's budget_usd: never shown to the org.
    if full:
        details = without_money(raw) if isinstance(row["details"], dict) else row["details"]
    else:
        details = {key: raw[key] for key in audit.SHOWN_DETAILS.get(row["action"], ()) if key in raw}
    if isinstance(details, dict):
        for key, named in (("folder_id", "folder_path"), ("parent_id", "parent_path")):
            if details.get(key) is not None:
                details[named] = names.folders.get(str(details[key]))
        if row["action"] == "document.owner_change":
            for key in ("from", "to"):
                details[f"{key}_name"] = names.users.get(str(details.get(key)))
    actor, folder_id, about = row["actor_user_id"], row["folder_id"], row.get("subject_user_id")
    subject = row["target_id"] if row["target_type"] == "user" else about
    document = None
    if row["target_type"] == "document":
        exists = bool(row["document_exists"])
        document = {
            "id": row["target_id"],
            "name": row["document_name"] if exists else DELETED_DOCUMENT,
            "version_no": row["document_version_no"],
            "deleted": not exists,
        }
    return {
        "id": int(row["id"]),
        "action": row["action"],
        "actor_id": None if actor is None else str(actor),
        "actor_name": None if actor is None else names.users.get(str(actor)),
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "subject_id": None if about is None else str(about),  # PRD 7 (#601): whom it is about
        **({"subject_name": names.users.get(str(subject))} if subject is not None else {}),
        "family_id": None if row["family_id"] is None else str(row["family_id"]),
        "document": document,
        "folder": None
        if folder_id is None
        else {"id": str(folder_id), "path": names.folders.get(str(folder_id))},
        "details": details,
        "created_at": iso(row["created_at"]),
    }


def without_money(values: Mapping[str, Any]) -> dict[str, Any]:
    """``values`` without any key that names money (``usd``, ``price``, ``budget``,
    ``cost``): org admins and members never see money (Tushar, 15 Oct 2026)."""
    return {key: value for key, value in values.items() if not _MONEY_KEY.search(key)}


# --- Ensemble after the scan (scan review UX rules, 16 Oct 2026; kaagaz/api/routers/ensemble.py) ---

EN_DASH = "\u2013"  # between the first and last page of a run of pages

# Why a run could not finish (Run.error.message: what happened, what it means).
CAUSES = {
    "timeout": "The connection timed out.",
    "outage": "The reading service is not answering right now.",
    "refused": "The reading service declined the request.",
    "key_problem": "The reading service's key needs your admin's attention.",
    "retries_exhausted": "The run stopped after its last try.",
    "ensemble_not_allowed": "Ensemble is no longer turned on for your organisation.",
    "document_changed": "The document changed while Ensemble was reading it.",
}


def page_ranges(pages: Sequence[int]) -> str:
    """Runs of pages, each first and last joined by an en dash (EN_DASH): 9 to 14, "3 and 5 to 7",
    "1, 3 and 5 to 7" with the dash in place of "to"."""
    runs: list[list[int]] = []
    for page in sorted(set(pages)):
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    parts = [str(first) if first == last else f"{first}{EN_DASH}{last}" for first, last in runs]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def pages_text(pages: Sequence[int]) -> str:
    """ "page 9", or "pages " and ``page_ranges``."""
    return ("page " if len(set(pages)) == 1 else "pages ") + page_ranges(pages)


def error_message(code: str, failed: Sequence[int], done: Sequence[int]) -> str:
    """ "Ensemble couldn't finish pages 9 to 14. The connection timed out. Pages 1 to 8 are done
    and saved.", each "to" an en dash (spec section 5, the owner's wording); the button under it
    says what to do."""
    cause = CAUSES.get(code, CAUSES["refused"])
    if done:
        finished = pages_text(done)
        kept = f"{finished[0].upper()}{finished[1:]} {'is' if len(set(done)) == 1 else 'are'} done and saved."
    else:
        kept = "Nothing was changed."
    return f"Ensemble couldn't finish {pages_text(failed)}. {cause} {kept}"


def run_json(conn: Connection, customer_id: str, run: RowMapping) -> dict[str, Any]:
    """``Run``: one run's progress, counts and error. One statement more."""
    counts = ensemble_sql.run_counts(conn, customer_id, str(run["document_id"]), str(run["id"]))
    pages, done, failed = list(run["pages"]), list(run["pages_done"]), list(run["pages_failed"])
    code = run["error_code"]
    return {
        "id": str(run["id"]),
        "scope": run["scope"],
        "status": run["status"],
        "pages_total": len(pages),
        "pages_done": len(done),
        "page_now": len(done) + 1 if run["status"] == "running" and len(done) < len(pages) else None,
        "words_total": len(run["word_keys"]) if run["word_keys"] else counts["total"],
        "agreed": counts["agreed"],
        "changed": counts["changed"],
        "unresolved": counts["unresolved"],
        "kept": counts["kept"],
        "failed_pages": failed,
        "started_at": _iso(run["started_at"]),
        "finished_at": _iso(run["finished_at"]),
        # #520: a run held by the reading pause says so, in a held document's words.
        "error": None
        if code is None
        else {"code": code, "message": _PAUSE_WORDS.get(code) or error_message(code, failed, done)},
    }


def latest_run_json(conn: Connection, customer_id: str, document_id: str) -> dict[str, Any] | None:
    run = ensemble_sql.latest_run(conn, customer_id, document_id)
    return None if run is None else run_json(conn, customer_id, run)


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()
