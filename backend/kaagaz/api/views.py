"""Database rows into the JSON of ``frontend/lib/types.ts``, field for field.

Why one place: the frontend's types are the contract. Each function here is
named after the type it builds, so a reviewer can hold the two side by side.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import RowMapping

from kaagaz.api.auth import CurrentUser
from kaagaz.db.folders import FolderRow
from kaagaz.db.tags import TagRow
from kaagaz.scanning.gate import CUSTOMER_MESSAGES

_STATUS_MESSAGES = {
    "received": "Waiting for the virus check.",
    "clean": "Reading the document…",
    "ready": "Ready. You can ask about it.",
    "needs_reader": "Stored safely. This kind of file can't be read yet.",
}
_IN_PROGRESS = ("received", "clean")

# How far the worker has got (PRD 2 X3), by step. PROVISIONAL wording (plan 3.3).
# "done" counts the pages finished, so the page being worked on is the next one.
_STEP_MESSAGES = {
    "scanning": "Checking for viruses…",
    "understanding": "Understanding the document…",
    "indexing": "Making it searchable…",
}
_PAGE_STEPS = {"preparing": "Preparing", "reading": "Reading"}


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
) -> str:
    """``max_pages`` is the MAX_PAGES setting, named when a file had too many pages
    (a PDF's pages, or a TIFF's frames: ``detected_type`` says which to call it),
    with its real ``page_count`` when the worker stored it. ``attempts`` and
    ``max_attempts`` are the worker's tries: shown from the second try on, and
    named when they ran out (``retries_exhausted``). ``progress`` is the worker's
    ``{step, done, total}`` (PRD 2 X3), used only while the document is being
    checked or read, so an old one on a finished document is ignored."""
    if status == "rejected":
        return CUSTOMER_MESSAGES.get(failure_code or "", "This file was not processed.")
    if status == "failed" and failure_code == "too_many_pages":
        what = "This PDF" if detected_type == "pdf" else "This file"
        if page_count is None:  # failed before the count was stored
            return f"{what} has more than {max_pages} pages. The limit is {max_pages}. Split it into parts."
        split = "Split it into parts and upload each one."
        return f"{what} has {page_count} pages. The limit is {max_pages}. {split}"
    if status == "failed" and failure_code == "retries_exhausted":
        tries = f"{max_attempts} times" if max_attempts else "several times"
        return (
            f"We tried {tries} and couldn't read this file. Upload it again, or ask your admin to look at it."
        )
    if status == "failed":
        return "This file could not be read." + (f" ({failure_code})" if failure_code else "")
    message = _STATUS_MESSAGES.get(status, "")
    if status in _IN_PROGRESS and progress is not None:
        message = progress_message(progress) or message
    if status in _IN_PROGRESS and attempts >= 2 and max_attempts:
        message += f" Try {min(attempts, max_attempts)} of {max_attempts}."
    return message


def progress_message(progress: Mapping[str, Any]) -> str | None:
    """ "Reading page 3 of 40…" and the like, or None for a step it does not know.
    A sheet is read as a whole (total 0), so it has no page number. O(1)."""
    step, done, total = progress.get("step"), progress.get("done"), progress.get("total")
    if step in _STEP_MESSAGES:
        return _STEP_MESSAGES[step]
    if step not in _PAGE_STEPS or not isinstance(done, int) or not isinstance(total, int):
        return None
    if total <= 0:
        return "Reading the document…"
    return f"{_PAGE_STEPS[step]} page {max(1, min(done + 1, total))} of {total}…"


def in_progress(status: str, progress: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """``DocumentProgress``: the stored progress while the document is being
    checked or read, else None (an old one left on a finished document)."""
    if status not in _IN_PROGRESS or progress is None:
        return None
    return {"step": progress["step"], "done": int(progress["done"]), "total": int(progress["total"])}


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
    row: RowMapping, *, max_pages: int, tags: Sequence[TagRow], user: CurrentUser
) -> dict[str, Any]:
    """``DocumentSummary``. ``max_pages`` is passed on to ``status_message``;
    ``tags`` are the document's own, from one ``tags_for_documents`` call per page;
    ``user`` decides ``can_delete``."""
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
        ),
        "progress": in_progress(row["status"], row["progress"]),
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
    }


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
    return {"id": row.id, "name": row.name, "parent_id": row.parent_id, "created_at": iso(row.created_at)}


def tag(row: TagRow) -> dict[str, Any]:
    """``Tag``."""
    return {"id": row.id, "name": row.name}


def page_summary(row: RowMapping) -> dict[str, Any]:
    """``PageSummary``."""
    return {
        "page_no": int(row["page_no"]),
        "width": float(row["width"]),
        "height": float(row["height"]),
        "unit": row["unit"],
        "has_image": bool(row["has_image"]),
        "route": row["route"],  # PRD 2 R1; null when the page was not read
        "effective_dpi": row["effective_dpi"],  # R4
        "low_resolution": bool(row["low_resolution"]),
    }


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


def review_item(row: RowMapping) -> dict[str, Any]:
    """``ReviewItem``: the value, its document and its page."""
    return {
        "value": extracted_value(row),
        "document_id": str(row["document_id"]),
        "document_name": row["document_name"],
        "detected_type": row["detected_type"],
        "page": page_summary(row),
    }


def message(row: RowMapping, feedback: str | None = None) -> dict[str, Any]:
    """``Message``."""
    return {
        "id": str(row["id"]),
        "role": row["role"],
        "text": row["text"],
        "created_at": iso(row["created_at"]),
        "answer": row["answer"],
        "feedback": feedback,
    }


# The words that stand in for a sentence whose every source has been deleted.
GONE_SENTENCE = "This part of the answer came from a document that has since been deleted."
_GONE = ("in_trash", "deleted")


def cited_document_ids(answer: Mapping[str, Any] | None) -> list[str]:
    """Every document a saved answer points at: its citations and closest documents."""
    if not answer:
        return []
    ids = [str(each["document_id"]) for each in answer.get("citations", []) if each.get("document_id")]
    ids += [str(each["id"]) for each in answer.get("closest_documents", []) if each.get("id")]
    return ids


def answer_with_source_states(answer: Mapping[str, Any], states: Mapping[str, str]) -> dict[str, Any]:
    """A saved answer as it may be shown now (``states`` from db/chat.py
    ``document_states``; a missing id was deleted forever).

    * Each citation gets ``source_state``. One from a document in Trash or
      deleted loses its snippet and its highlight regions: Trash means deleted,
      so its words and its place on the page must not show any more.
    * A sentence whose every citation is gone is replaced by ``GONE_SENTENCE``.
    * Closest documents that are gone are left out.
    A citation of an older version keeps its text and link, marked
    ``older_version``: it was true of that version. The stored answer is never
    changed, so a restore brings everything back. O(citations + sentences).
    """
    citations = []
    gone: set[int] = set()
    for each in answer.get("citations", []):
        state = states.get(str(each.get("document_id")), "deleted")
        citation = {**each, "source_state": state}
        if state in _GONE:
            citation["snippet"] = None
            citation["regions"] = []
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
    return {**answer, "citations": citations, "sentences": sentences, "closest_documents": closest}


def conversation_summary(row: RowMapping) -> dict[str, Any]:
    """``ConversationSummary``."""
    return {"id": str(row["id"]), "title": row["title"], "created_at": iso(row["created_at"])}


def me_json(user: CurrentUser) -> dict[str, Any]:
    """``Me``: who is signed in, their customer, and the session's scope ("app" from
    /login, "admin" from /admin/login; the screen shows Admin only for "admin")."""
    return {
        "user": {
            "id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "must_change_password": user.must_change_password,
            "totp_enabled": user.totp_enabled,
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
    }


def audit_entry(row: RowMapping) -> dict[str, Any]:
    """``AuditEntry``."""
    return {
        "id": int(row["id"]),
        "action": row["action"],
        "actor_name": row["actor_name"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "details": row["details"],
        "created_at": iso(row["created_at"]),
    }
