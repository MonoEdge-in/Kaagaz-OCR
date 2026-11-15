"""Opening a locked file (PRD 7 section 6, decisions 3, 5, 6 and 7; #605, PR 13).

* ``GET /documents/locked-open``: ``{open, until, via}``: whether this browser's 10-minute check
  is live (``kaagaz/api/locked_open.py``), when it ends, and how this person confirms
  (``password``, or ``google`` for a person whose role signs in with Google only).
* ``POST /documents/locked-open`` {password}: the person's own Kaagaz password and nothing else; a
  two-step code is never asked for (decision 5). In this order: a Google-only person gets 409
  ``google_confirm_required`` (``POST /auth/oidc/google/confirm``, ``routers/oidc.py``); at most
  OPENS_PER_HOUR a person (429 ``rate_limited``); the try is counted and committed
  (``db/locked_open.begin_try``) and while five wrong tries in a row block it, 429
  ``open_blocked`` with Retry-After and no audit row (the block was audited when it started); the
  password is checked as a signed-in sign-in's is (``_check_current_password``'s steps: counted on
  the person's sign-in lock first, 400 ``password_locked`` while that is on). Wrong: 400
  ``wrong_password``, ``auth.login_failed`` (and ``auth.locked``) as at sign-in, ``lock.open_failed``
  and, on the fifth, ``lock.open_blocked``. Right: both counts start again, ``lock.open`` {via:
  password}, and the check is set.
* ``GET /documents/{id}/locked-pages/{n}``: one page picture of a locked file, the only route that
  ever sends a locked file's bytes. Checked in this order: the file through the access view (R0
  first, then R1 to R5), else 404 exactly as an id that never was; not locked, 404; still being
  prepared, 409 ``not_ready``; in Trash, 409 ``in_trash``; no live check, 403
  ``locked_open_required``; a page outside 1..page_count or with no picture, 404; then the picture,
  ``lock.view`` once per person, file and VIEW_EVERY_MINUTES, and "do not store" headers. The file
  itself, its text, and any download or print are never served for a locked file (PR 12 refuses
  every other route).

Why the visibility read comes before the check: a person who may not open the file must never
learn from a 403 that the id is a locked file.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api import locked_open
from kaagaz.api.auth import CurrentUser, User, session_claims, verify_password
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers.admin import MAX_PASSWORD
from kaagaz.api.routers.auth import _begin, _failed, _per_user_limit, _succeeded
from kaagaz.api.twostep import unix
from kaagaz.db import audit
from kaagaz.db import locked_open as locked_sql
from kaagaz.ingestion.ports import BlobNotFound

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

# Tries an hour for one person (PROVISIONAL): beside the 5-in-a-row block, a cap on a session that
# keeps waiting the block out.
OPENS_PER_HOUR = 30
open_limits = RateLimiter(OPENS_PER_HOUR, window_seconds=3600)
TOO_MANY_OPENS = "Too many tries to open locked files. Try again in an hour."
GOOGLE_INSTEAD = "Confirm with Google to open locked files."
WRONG_PASSWORD = "Your password is not right."  # noqa: S105 - words, not a password
NOT_READY = "This file is still being prepared. Try again in a minute."
IN_TRASH = "This document is in the Trash. Restore it first."
OPEN_FIRST = "Enter your password to open locked files."
# Never cached, never sniffed, never framed or fetched by another site, never sent on as a referrer.
_PICTURE_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
}


class OpenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


def _state(until: int | None, user: CurrentUser, context: AppContext) -> dict[str, Any]:
    return {
        "open": until is not None,
        "until": None if until is None else datetime.fromtimestamp(until, UTC).isoformat(),
        "via": "google" if locked_open.confirms_with_google(user, context.settings) else "password",
    }


def _record(conn: Connection, user: CurrentUser, action: str, details: dict[str, audit.DetailValue]) -> None:
    """``lock.open`` and its failures are about the person (target type user)."""
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="user",
        target_id=user.user_id,
        details=details,
    )


@router.get("/documents/locked-open")
def get_locked_open(request: Request, user: User, context: Context) -> dict[str, Any]:
    now = unix(context.clock())
    return _state(locked_open.open_until(request, user, context.settings, now), user, context)


@router.post("/documents/locked-open")
def open_locked_files(body: OpenBody, request: Request, user: User, context: Context) -> JSONResponse:
    if locked_open.confirms_with_google(user, context.settings):  # nothing counted
        raise ApiError(409, "google_confirm_required", GOOGLE_INSTEAD)
    _per_user_limit(open_limits, user, TOO_MANY_OPENS)
    now = context.clock()
    session = session_claims(request, context.settings, unix(now))
    if session is None:  # never: ``user`` was read from these claims
        raise ApiError(401, "not_signed_in")
    customer_id, user_id = user.customer_id, user.user_id
    with context.db.user(customer_id, user_id) as conn:
        tries = locked_sql.begin_try(conn, customer_id, user_id, now=now)
    if tries is None:
        with context.db.user(customer_id, user_id) as conn:
            blocked = locked_sql.blocked_until(conn, customer_id, user_id, now=now)
        wait = max(1, int(((blocked or now) - now).total_seconds()))
        minutes = -(-wait // 60)
        raise ApiError(
            429,
            "open_blocked",
            f"Too many wrong passwords. Try again in {minutes} minutes.",
            headers={"Retry-After": str(wait)},
        )
    attempt = _begin(context, customer_id, user_id, now, signed_in=True)  # the sign-in lock (p2:58)
    if attempt is None:  # no audit row: the sign-in lock was audited when it started
        minutes = context.settings.login_lock_minutes
        raise ApiError(
            400,
            "password_locked",
            f"Too many wrong passwords were tried on this account. Try again in {minutes} minutes,"
            " or ask an admin to unlock it.",
        )
    if not verify_password(body.password, attempt.password_hash):
        _failed(context, attempt, "wrong_password", signed_in=True)
        with context.db.user(customer_id, user_id) as conn:
            _record(conn, user, "lock.open_failed", {"via": "password", "reason": "wrong_password"})
            if tries >= locked_sql.MAX_TRIES:
                _record(conn, user, "lock.open_blocked", {"minutes": locked_sql.BLOCK_MINUTES})
        raise ApiError(400, "wrong_password", WRONG_PASSWORD)
    _succeeded(context, attempt, signed_in=True)
    with context.db.user(customer_id, user_id) as conn:
        locked_sql.succeeded(conn, customer_id, user_id)
        _record(conn, user, "lock.open", {"via": "password"})
    until = unix(now) + locked_open.LOCKED_OPEN_SECONDS
    response = JSONResponse(_state(until, user, context))
    locked_open.set_cookie(response, user, session.session_id, context.settings, until=until)
    return response


@router.get("/documents/{document_id}/locked-pages/{page_no}")
def get_locked_page(
    document_id: uuid.UUID, page_no: int, request: Request, user: User, context: Context
) -> Response:
    customer_id, target = user.customer_id, str(document_id)
    with context.db.user(customer_id, user.user_id) as conn:
        row = locked_sql.document(conn, customer_id, target)
        if row is None or not row["locked"]:
            raise ApiError(404, "not_found")
        if row["status"] != "locked":
            raise ApiError(409, "not_ready", NOT_READY)
        if row["in_trash"]:
            raise ApiError(409, "in_trash", IN_TRASH)
        if locked_open.open_until(request, user, context.settings, unix(context.clock())) is None:
            raise ApiError(403, "locked_open_required", OPEN_FIRST)
        if not 1 <= page_no <= (row["page_count"] or 0) or not locked_sql.has_picture(
            conn, customer_id, target, page_no
        ):
            raise ApiError(404, "not_found")
    try:
        image = context.storage.get_page_image(customer_id, target, page_no)
    except BlobNotFound:
        raise ApiError(404, "not_found") from None
    with context.db.user(customer_id, user.user_id) as conn:
        locked_sql.record_view(conn, customer_id, user.user_id, target)
    return Response(content=image, media_type="image/webp", headers=_PICTURE_HEADERS)
