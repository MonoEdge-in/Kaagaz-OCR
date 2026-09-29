"""Database rows into the JSON of ``frontend/lib/types.ts``, field for field.

Why one place: the frontend's types are the contract. Each function here is
named after the type it builds, so a reviewer can hold the two side by side.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import RowMapping

from kaagaz.api.auth import CurrentUser
from kaagaz.scanning.gate import CUSTOMER_MESSAGES

_STATUS_MESSAGES = {
    "received": "Waiting for the virus check.",
    "clean": "Reading the document…",
    "ready": "Ready. You can ask about it.",
    "needs_reader": "Stored safely. This kind of file can't be read yet.",
}


def iso(value: datetime) -> str:
    return value.isoformat()


def status_message(status: str, failure_code: str | None) -> str:
    if status == "rejected":
        return CUSTOMER_MESSAGES.get(failure_code or "", "This file was not processed.")
    if status == "failed":
        return "This file could not be read." + (f" ({failure_code})" if failure_code else "")
    return _STATUS_MESSAGES.get(status, "")


def document_summary(row: RowMapping) -> dict[str, Any]:
    """``DocumentSummary``."""
    return {
        "id": str(row["id"]),
        "display_name": row["display_name"],
        "detected_type": row["detected_type"],
        "size_bytes": int(row["size_bytes"]),
        "status": row["status"],
        "failure_code": row["failure_code"],
        "status_message": status_message(row["status"], row["failure_code"]),
        "page_count": row["page_count"],
        "created_at": iso(row["created_at"]),
        "uploaded_by_name": row["uploaded_by_name"],
    }


def page_summary(row: RowMapping) -> dict[str, Any]:
    """``PageSummary``."""
    return {
        "page_no": int(row["page_no"]),
        "width": float(row["width"]),
        "height": float(row["height"]),
        "unit": row["unit"],
        "has_image": bool(row["has_image"]),
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
