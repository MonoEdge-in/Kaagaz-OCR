"""Database rows into the JSON of ``frontend/lib/types.ts``, field for field.

Why one place: the frontend's types are the contract. Each function here is
named after the type it builds, so a reviewer can hold the two side by side.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Connection, RowMapping

from kaagaz.answering.hard_to_read import FlaggedPage, not_found_note
from kaagaz.api.auth import CurrentUser
from kaagaz.db import audit
from kaagaz.db import ensemble as ensemble_sql
from kaagaz.db.folders import FolderRow
from kaagaz.db.listing import MAX_READING_LINES, MAX_READING_TEXT
from kaagaz.db.tags import TagRow
from kaagaz.eta import time_left
from kaagaz.reading.page_quality import ILLEGIBLE_WORDS, READERS_DISAGREE, UNPINNED, fix, says
from kaagaz.scanning.gate import CUSTOMER_MESSAGES

_STATUS_MESSAGES = {
    "received": "Waiting to start.",  # in the queue: the virus check runs later, in the worker
    "clean": "Reading the document…",
    "ready": "Ready. You can ask about it.",
    "needs_reader": "Stored safely. This kind of file can't be read yet.",
}
_IN_PROGRESS = ("received", "clean")
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
    says so (council 16 Oct 2026: such a document is ready, not failed, since e01b937d)."""
    if status == "rejected":
        return CUSTOMER_MESSAGES.get(failure_code or "", "This file was not processed.")
    if status == "failed" and failure_code == "too_many_pages":
        what = "This PDF" if detected_type == "pdf" else "This file"
        if page_count is None:  # failed before the count was stored
            return f"{what} has more than {max_pages} pages. The limit is {max_pages}. Split it into parts."
        split = "Split it into parts and upload each one."
        return f"{what} has {page_count} pages. The limit is {max_pages}. {split}"
    if status == "failed" and failure_code == "retries_exhausted":
        tries = f"{max_attempts} tries" if max_attempts else "several tries"
        return f"The scan stopped after {tries}. Read it again, or ask your admin to look at it."
    if status == "failed" and failure_code == "unreadable":
        # PRD 5 F4: every non-blank page was unreadable; a document with one readable page is ready.
        return "None of this file could be read. Upload a sharper scan."
    if status == "failed" and failure_code == "ocr_failed":
        # The scan reader failed on a page of this file (kaagaz/reading/ocr.py): not an outage.
        return "A page of this file needs another read. Read it again, or upload a clearer scan."
    if status == "failed" and failure_code == "customer_disabled":
        # The worker came to it while the organisation was turned off (0014): nothing was read.
        return "This file was not read because your organisation was turned off. Read it again."
    if status == "failed":
        return "This file needs another read. Read it again, or try another copy of it."
    if status == "ready" and pages_unreadable > 0:
        pages = "1 page" if pages_unreadable == 1 else f"{pages_unreadable} pages"
        return f"Ready. {pages} to check."
    message = _STATUS_MESSAGES.get(status, "")
    if status in _IN_PROGRESS and progress is not None and progress.get("step") == _WAITING_STEP:
        message = READER_WAITING
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
    if user.role == "admin":
        return True
    uploaders = row["family_uploaders"] or []
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
        ),
    }


def document_summary(
    row: RowMapping, *, max_pages: int, tags: Sequence[TagRow], user: CurrentUser, now: datetime
) -> dict[str, Any]:
    """``DocumentSummary``. ``max_pages`` is passed on to ``status_message``;
    ``tags`` are the document's own, from one ``tags_for_documents`` call per page;
    ``user`` decides ``can_delete``; ``now`` is the request's one clock reading."""
    return {
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
        ),
        "progress": in_progress(row["status"], row["progress"]),
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
        "pending_suggestion": row["pending_suggestion"],
    }


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
) -> dict[str, Any]:
    """``BatchView`` (PRD 5 Q1, U1): the uploader's own files of one drop, in upload
    order (``listing.batch_rows``), each a ``DocumentSummary`` with its first unreadable
    page (``first_unreadable``, U1's "Couldn't read page n") and its count of values
    (``values_found``), and the drop's counts:

    * failed: failed, rejected or can't be read yet (needs_reader);
    * waiting: in progress with no step yet (queued) or waiting for the reader (Q6);
    * reading: in progress with a step (scanning, preparing, reading, understanding);
    * attention: ready, with a page or a value that needs attention (U2's rule);
    * ready: ready with none.

    The bar is pages read over total pages: a file whose page count is not known yet
    counts as 1 page; a finished file (ready or failed) is read whole; one past reading
    (understanding, indexing) too; one being read counts its pages done. ``time_left`` is
    the slowest file being read's, plus the pages still waiting at its page rate shared
    over ``max_active`` (MAX_ACTIVE_PER_CUSTOMER, Q5); null while no file has an estimate.
    ponytail: one rate for the whole drop; per-file estimates if drops mix scans and text.
    Only this user's files, so no other customer's load reaches it. O(files)."""
    counts = {"total": len(rows), "ready": 0, "reading": 0, "waiting": 0, "attention": 0, "failed": 0}
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
                **document_summary(row, max_pages=max_pages, tags=tags[str(row["id"])], user=user, now=now),
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
    """``DocumentVersion``: one version in the detail's ``versions`` (``listing.family_versions``)."""
    return {
        "id": str(row["id"]),
        "version_no": int(row["version_no"]),
        "status": row["status"],
        "is_current": bool(row["is_current"]),
        "created_at": iso(row["created_at"]),
    }


def document_folder(path: Sequence[FolderRow]) -> dict[str, Any] | None:
    """``DocumentFolder``: the document's folder and the folders from the top down
    to it, itself last (``folders.folder_path``). None at the top level."""
    if not path:
        return None
    here = path[-1]
    return {"id": here.id, "name": here.name, "path": [{"id": each.id, "name": each.name} for each in path]}


def folder(row: FolderRow) -> dict[str, Any]:
    """``Folder``."""
    return {
        "id": row.id,
        "name": row.name,
        "parent_id": row.parent_id,
        "created_at": iso(row.created_at),
        "kind": row.kind,  # File management F1
        "sort_order": row.sort_order,
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
        reader["lines"].append(
            {
                "text": line,
                "confidence": row["confidence"],
                "origin": row["origin"],
                "kind": row["kind"],
                "region": row["region"],
            }
        )
        reader["truncated"] = bool(row["cut"])
    shown = [{"quality": mark["quality"], "region": mark["region"], "text": mark["text"]} for mark in marks]
    return {"page_no": page_no, "readers": readers, "marks": shown}


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
        "answer": row["answer"],
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
_GONE = ("in_trash", "deleted")


def cited_document_ids(answer: Mapping[str, Any] | None) -> list[str]:
    """Every document a saved answer points at: its citations, closest and related
    documents, and the documents of the pages that couldn't be read it names (PRD 5 F6)."""
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
    return ids


def answer_with_source_states(answer: Mapping[str, Any], states: Mapping[str, str]) -> dict[str, Any]:
    """A saved answer as it may be shown now (``states`` from db/chat.py
    ``document_states``; a missing id was deleted forever).

    * Each citation gets ``source_state``. One from a document in Trash or
      deleted loses its snippet and its highlight regions: Trash means deleted,
      so its words and its place on the page must not show any more.
    * A sentence whose every citation is gone is replaced by ``GONE_SENTENCE``.
    * Closest, related and "Also mentions" documents that are gone are left out.
    * PRD 5 F6: a page that couldn't be read, of a document that is gone, is left out
      of ``unreadable_pages``, its count and the note, so a deleted document's name
      never shows again (the note is written again from the pages left).
    A citation of an older version keeps its text and link, marked
    ``older_version``: it was true of that version. The stored answer is never
    changed, so a restore brings everything back. O(citations + sentences + pages).
    """
    citations = []
    gone: set[int] = set()
    for each in answer.get("citations", []):
        state = states.get(str(each.get("document_id")), "deleted")
        citation = {**each, "source_state": state}
        if state in _GONE:
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
    closest = [
        each
        for each in answer.get("closest_documents", [])
        if states.get(str(each.get("id"))) not in (None, *_GONE)
    ]
    related = [
        each
        for each in answer.get("related_documents", [])
        if states.get(str(each.get("document_id"))) not in (None, *_GONE)
    ]
    shown = {**answer, "citations": citations, "sentences": sentences, "closest_documents": closest}
    if "related_documents" in answer:
        shown["related_documents"] = related
    if answer.get("mentions"):
        # "Also mentions": a gone document leaves the list and the count.
        listed = answer["mentions"].get("documents", [])
        live = [each for each in listed if states.get(str(each.get("document_id"))) not in (None, *_GONE)]
        total = int(answer["mentions"].get("total") or 0) - (len(listed) - len(live))
        shown["mentions"] = {**answer["mentions"], "documents": live, "total": total}
    pages = answer.get("unreadable_pages") or []
    kept = [page for page in pages if states.get(str(page.get("document_id"))) not in (None, *_GONE)]
    if len(kept) != len(pages):
        total = int(answer.get("unreadable_page_count") or 0) - (len(pages) - len(kept)) if kept else 0
        named = [
            FlaggedPage(
                str(page["document_id"]),
                str(page["document_name"]),
                int(page["page_no"]),
                str(page["read_quality"]),
            )
            for page in kept
        ]
        shown.update(
            unreadable_pages=kept, unreadable_page_count=total, reading_note=not_found_note(named, total)
        )
    return shown


def conversation_summary(row: RowMapping) -> dict[str, Any]:
    """``ConversationSummary``."""
    return {"id": str(row["id"]), "title": row["title"], "created_at": iso(row["created_at"])}


def me_json(user: CurrentUser) -> dict[str, Any]:
    """``Me``: who is signed in, their customer, and the session's scope ("app" for a
    member or reviewer, "admin" for an admin; the screen shows Admin only for "admin")."""
    return {
        "user": {
            "id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "must_change_password": user.must_change_password,
            "totp_enabled": user.totp_enabled,
            "can_delete_forever": user.delete_forever_allowed and user.role != "admin",  # the grant
        },
        "customer": {"id": user.customer_id, "name": user.customer_name},
        "session": {"scope": user.scope},
    }


def admin_user(row: RowMapping, *, now: datetime) -> dict[str, Any]:
    """``AdminUser``. ``locked_until`` is null once the lock has passed, so the
    screen never shows a lock that no longer holds."""
    locked_until: datetime | None = row["locked_until"]
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
        "disabled": bool(row["disabled"]),
        "created_at": iso(row["created_at"]),
        "locked_until": iso(locked_until) if locked_until is not None and locked_until > now else None,
        "totp_enabled": row["totp_enabled_at"] is not None,
        "must_change_password": bool(row["must_change_password"]),
        "can_delete_forever": row["delete_forever_allowed_at"] is not None and row["role"] != "admin",
    }


DELETED_DOCUMENT = "a deleted document"
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
) -> dict[str, Any]:
    """A5's ``{items, next_cursor}``: one page of ``audit.entries`` with its names (three
    queries); ``next_cursor`` is null on the last page. ``cursor`` matches CURSOR."""
    after = None
    if cursor is not None:
        moment, row_id = cursor.split("-")
        after = (_EPOCH + int(moment) * _MICROSECOND, int(row_id))
    rows = audit.entries(conn, customer_id, filters, limit=limit + 1, cursor=after, offset=offset)
    page = rows[:limit]
    found = audit.names(conn, customer_id, page)
    last = page[-1] if len(rows) > limit else None
    return {
        "items": [audit_entry(row, found, full=full) for row in page],
        "next_cursor": None
        if last is None
        else f"{(last['created_at'] - _EPOCH) // _MICROSECOND}-{int(last['id'])}",
    }


def audit_entry(row: RowMapping, names: audit.Names = audit.NO_NAMES, *, full: bool) -> dict[str, Any]:
    """``AuditEntry`` (File management A5): names read now, a deleted document by
    DELETED_DOCUMENT. ``full`` (the admin's log): every detail but money; otherwise only
    ``audit.SHOWN_DETAILS``. The ids in details come with their names: ``folder_path``,
    ``parent_path``, and ``from_name`` and ``to_name`` for an owner change."""
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
    actor, folder_id = row["actor_user_id"], row["folder_id"]
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
    counts = ensemble_sql.run_counts(conn, customer_id, str(run["id"]))
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
        "error": None if code is None else {"code": code, "message": error_message(code, failed, done)},
    }


def latest_run_json(conn: Connection, customer_id: str, document_id: str) -> dict[str, Any] | None:
    run = ensemble_sql.latest_run(conn, customer_id, document_id)
    return None if run is None else run_json(conn, customer_id, run)


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()
