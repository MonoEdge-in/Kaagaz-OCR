"""GET /api/review and POST /api/review/{value_id}: the correction screen.
POST /api/review/verify-all: every value of the queue verified as it reads now.

Why one transaction for a resolve: the value is locked, changed, its
correction row written, the correction remembered (if asked) and the audit row
written together, so a half-done correction can never be seen. Numbers and
dates are parsed again from the new text with ``kaagaz.extraction.parsing``,
the same rules the pipeline uses, so the fixed queries see the corrected value.

Verify all is the same resolve, value by value (``_resolve_locked``), with what the
document screen's Verify all sends for each one: the value as it reads now, typed,
nothing remembered. It runs in chunks of ``VERIFY_ALL_CHUNK``, one transaction each, so a
long queue never holds one long transaction, and each value still gets its own correction
row and review.resolve audit row.
"""

import uuid
from collections import Counter
from datetime import datetime
from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api.auth import CurrentUser, Reviewer
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.views import review_item
from kaagaz.db import review as review_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

# PRD 3 Y5, and PRD 5 U6's four for the reading model's marks: the reasons the queue can
# be filtered by, each one of db/review.py's EVERY_REASON; any other is 400 bad_request.
QueueReason = Literal[
    "check_failed",
    "readers_disagree",
    "low_confidence",
    "single_reader",
    "unsure_text",
    "box_unanchored",
    "local_reader",
    "ocr_script",
    "mixed_digits",  # #524: the amounts the rules doubt
    "amount_commas",
    "amount_mark",
    "amount_outlier",
]

MAX_VALUE_LENGTH = 500
VERIFY_ALL_CHUNK = 200

Choice = Literal["first", "second", "typed"]
# Why Verify all left a value out, in the order the reply lists them: nothing to verify,
# longer than a resolve takes, or it left the queue (resolved, or sent to Trash) meanwhile.
SkipReason = Literal["no_reading", "too_long", "left_queue"]
SKIP_REASONS: tuple[SkipReason, ...] = get_args(SkipReason)


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Choice
    value: str | None = Field(default=None, max_length=MAX_VALUE_LENGTH)
    remember: bool


class VerifyAllBody(BaseModel):
    """The queue screen's filters, as GET /review takes them."""

    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID | None = None
    reason: list[QueueReason] | None = Field(default=None, max_length=len(get_args(QueueReason)))


@router.get("/review")
def list_review(
    user: Reviewer,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    document_id: uuid.UUID | None = None,
    reason: Annotated[list[QueueReason] | None, Query(max_length=len(get_args(QueueReason)))] = None,
) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows, total = review_sql.list_queue(
            conn,
            user.customer_id,
            limit=limit,
            offset=offset,
            document_id=None if document_id is None else str(document_id),
            reasons=reason,
        )
    return {"items": [review_item(row) for row in rows], "total": total}


def _typed_text(choice: Choice, value: str | None) -> str | None:
    """The typed value, trimmed; 400 when it is empty. None for the other choices."""
    if choice != "typed":
        return None
    typed = (value or "").strip()
    if not typed:
        raise ApiError(400, "bad_request", "Type the correct value.")
    return typed


def _resolve_locked(
    conn: Connection,
    user: CurrentUser,
    value_id: str,
    *,
    choice: Choice,
    typed: str | None,
    remember: bool,
    details: dict[str, bool] | None = None,
) -> None:
    """One resolve in the caller's transaction: the value locked, checked, then verified
    (``review_sql.resolve_value``). Every refusal is an ApiError raised before anything is
    written, so a caller may catch it and go on in the same transaction."""
    customer_id = user.customer_id
    current = review_sql.lock_value(conn, customer_id, value_id)
    if current is None:
        raise ApiError(404, "not_found")
    # Checked under the lock and after the 404, so a verified value (or one another
    # reviewer resolved a moment ago) is never overwritten, and another customer's
    # value stays a 404.
    if current["label"] != "needs_review":
        raise ApiError(409, "not_needs_review")
    if choice == "first":
        new_text = str(current["value_raw"])
    elif choice == "second":
        if current["second_reading"] is None:
            raise ApiError(400, "bad_request", "There is no second reading for this value.")
        new_text = str(current["second_reading"])
    else:
        new_text = str(typed)

    review_sql.resolve_value(
        conn,
        customer_id,
        current,
        new_text=new_text,
        choice=choice,
        remember=remember,
        user_id=user.user_id,
        details=details,
    )


def _verify_as_read(conn: Connection, user: CurrentUser, value_id: str, value_text: str) -> SkipReason | None:
    """Verify all's one value: verified as it reads now, as the document screen's Verify
    sends it (typed, unchanged, nothing remembered), through the same checks as one POST
    /review/{value_id}. None when verified, else why it was left out."""
    if len(value_text) > MAX_VALUE_LENGTH:  # ResolveBody's limit on a typed value
        return "too_long"
    try:
        typed = _typed_text("typed", value_text)
        _resolve_locked(
            conn, user, value_id, choice="typed", typed=typed, remember=False, details={"verify_all": True}
        )
    except ApiError as refused:
        # 400: nothing to verify. 404 or 409: its document went to Trash, or it was
        # resolved, after this chunk was read; nothing was written for it.
        return "no_reading" if refused.status == 400 else "left_queue"
    return None


# Before /review/{value_id}, so "verify-all" is never read as a value id.
@router.post("/review/verify-all")
def verify_all(body: VerifyAllBody, user: Reviewer, context: Context) -> dict[str, Any]:
    """Verifies every value of the queue the screen shows (same filters), as each reads now,
    up to the values there when it began. A value another reviewer is resolving is waited
    for and left out once resolved, so it is never verified or audited twice. Replies how
    many were verified, how many were left out and why, and how many are still in the
    queue. O(n) resolves over the n values, in about n / VERIFY_ALL_CHUNK transactions."""
    customer_id = user.customer_id
    document_id = None if body.document_id is None else str(body.document_id)
    verified = 0
    skipped: Counter[SkipReason] = Counter()
    after: tuple[datetime, str] | None = None
    until: datetime | None = None
    while True:
        with context.db.user(customer_id, user.user_id) as conn:
            if until is None:
                until = review_sql.database_now(conn)
            chunk = review_sql.next_chunk(
                conn,
                customer_id,
                document_id=document_id,
                reasons=body.reason,
                until=until,
                after=after,
                limit=VERIFY_ALL_CHUNK,
            )
            for row in chunk:
                skip = _verify_as_read(conn, user, str(row["id"]), str(row["value_text"]))
                if skip is None:
                    verified += 1
                else:
                    skipped[skip] += 1
        # Never stop on a short chunk: a value resolved by someone else while this one
        # waited for its lock is left out of the chunk, which is not the end of the queue.
        if not chunk:
            break
        after = (chunk[-1]["created_at"], str(chunk[-1]["id"]))
    with context.db.user(customer_id, user.user_id) as conn:
        left = review_sql.count_queue(conn, customer_id, document_id=document_id, reasons=body.reason)
    return {
        "verified": verified,
        "skipped": [
            {"reason": reason, "count": skipped[reason]} for reason in SKIP_REASONS if skipped[reason]
        ],
        "left": left,
    }


@router.post("/review/{value_id}")
def resolve(value_id: uuid.UUID, body: ResolveBody, user: Reviewer, context: Context) -> dict[str, Any]:
    typed = _typed_text(body.choice, body.value)
    customer_id, value = user.customer_id, str(value_id)
    with context.db.user(customer_id, user.user_id) as conn:
        _resolve_locked(conn, user, value, choice=body.choice, typed=typed, remember=body.remember)
        item = review_sql.get_item(conn, customer_id, value)
        if item is None:  # no page row: not reviewable, and raising here rolls back
            raise ApiError(404, "not_found")
    return review_item(item)
