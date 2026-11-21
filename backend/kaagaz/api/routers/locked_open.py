"""Opening a locked file (PRD 7 section 6, decisions 3, 5, 6 and 7; #605, PR 13).

* ``GET /documents/locked-open``: ``{open, until, seconds, via}``: whether this browser's
  10-minute check is live (``kaagaz/api/auth.py``), when it ends, the seconds left by the
  server's clock (the screen counts down from them, never from the PC's clock), and how this
  person confirms (always ``keycloak``).
* ``POST /documents/locked-open``: needs a fresh Keycloak sign-in for ``locked_open``
  (``routers/keycloak.py`` ``reauth_start``; else 403 ``keycloak_reauth_required``, no try is
  counted); at most OPENS_PER_HOUR a person (429 ``rate_limited``). Right: ``lock.open`` {via:
  keycloak} and the check is set.
* ``GET /documents/{id}/locked-pages/{n}``: one page picture of a locked file, the only route that
  ever sends a locked file's bytes. Checked in this order: the file through the access view (R0
  first, then R1 to R5), else 404 exactly as an id that never was; not locked, 404; still being
  prepared, 409 ``not_ready``; in Trash, 409 ``in_trash``; no live check, 403
  ``locked_open_required``; a page outside 1..page_count or with no picture, 404; then the picture,
  ``lock.view`` once per person, file and VIEW_EVERY_MINUTES, and "do not store" headers. The file
  itself, its text, and any download or print are never served for a locked file (PR 12 refuses
  every other route). A fetch that says it is not for an image (``Sec-Fetch-Dest`` other than
  ``image``: the picture opened as a page, saved or framed) is 404: it deters, it cannot stop
  developer tools or a photograph of the screen.

Why the visibility read comes before the check: a person who may not open the file must never
learn from a 403 that the id is a locked file.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import Connection

from kaagaz.api.auth import (
    LOCKED_OPEN_SECONDS,
    CurrentUser,
    User,
    locked_open_until,
    require_locked_open,
    session_claims,
    set_locked_open_cookie,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers.auth import _per_user_limit, require_proof
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
NOT_READY = "This file is still being prepared. Try again in a minute."
IN_TRASH = "This document is in the Trash. Restore it first."
# Never cached, never sniffed, never framed or fetched by another site, never sent on as a referrer.
_PICTURE_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
}


def _state(until: int | None, user: CurrentUser, *, now: int) -> dict[str, Any]:
    """``seconds``: what is left by the server's clock, so the screen never trusts the PC's."""
    return {
        "open": until is not None,
        "until": None if until is None else datetime.fromtimestamp(until, UTC).isoformat(),
        "seconds": None if until is None else until - now,
        "via": "keycloak",
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
    return _state(locked_open_until(request, user, context.settings, now), user, now=now)


@router.post("/documents/locked-open")
def open_locked_files(request: Request, user: User, context: Context) -> JSONResponse:
    require_proof(user, "locked_open")
    _per_user_limit(open_limits, user, TOO_MANY_OPENS)
    now = context.clock()
    session = session_claims(request, context.settings, unix(now))
    if session is None:  # never: ``user`` was read from these claims
        raise ApiError(401, "not_signed_in")
    with context.db.user(user.customer_id, user.user_id) as conn:
        _record(conn, user, "lock.open", {"via": "keycloak"})
    until = unix(now) + LOCKED_OPEN_SECONDS
    response = JSONResponse(_state(until, user, now=unix(now)))
    set_locked_open_cookie(response, user, session.session_id, context.settings, until=until)
    return response


@router.get("/documents/{document_id}/locked-pages/{page_no}")
def get_locked_page(
    document_id: uuid.UUID, page_no: int, request: Request, user: User, context: Context
) -> Response:
    if request.headers.get("sec-fetch-dest", "image") != "image":  # opened as a page, saved, framed
        raise ApiError(404, "not_found")
    customer_id, target = user.customer_id, str(document_id)
    with context.db.user(customer_id, user.user_id) as conn:
        row = locked_sql.document(conn, customer_id, target)
        if row is None or not row["locked"]:
            raise ApiError(404, "not_found")
        if row["status"] != "locked":
            raise ApiError(409, "not_ready", NOT_READY)
        if row["in_trash"]:
            raise ApiError(409, "in_trash", IN_TRASH)
        require_locked_open(request, user, context.settings, unix(context.clock()))
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
