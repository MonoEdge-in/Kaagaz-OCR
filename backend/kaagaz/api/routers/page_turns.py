"""Page turns saved for everyone (PRD 7 section 14 step 2, #600). The detail reads them
(``page_turns`` in GET /api/documents/{id}); these two routes write them.

* **Who.** Anyone who can see the file (there is no read-only role). A file the person cannot see
  answers exactly as an id that does not exist (404), before its family is locked: Trash, an
  unread file and a bad page are never told for it.
* **PATCH** ``{"changes": [{"page_no", "turn"}], "user_id"}`` sets each named page's turn (0: not
  turned), so the last save wins per page and two people turning different pages both keep theirs.
  **DELETE** removes every turn of the version ("back as scanned"). Both reply with the whole set.
* **Order**, in one transaction: the session check (409 ``session_changed``), the body (400), the
  file as the person sees it (404), the family lock (409 ``in_trash``; saves of one family queue
  on it), its page count (409 ``not_ready`` while unknown; 400 for a page past the end), the write,
  one audit row (ids and counts only).
* **A locked file** (PRD 7 question 12, #605 PR 13): only a person who may open it sees it, and
  they turn its pages only while their 10-minute check is live (403 ``locked_open_required``),
  checked after the 404, so a hidden file still answers as a random id. The lock keeps the rows.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, RowMapping

from kaagaz.api.auth import CurrentUser, User, require_locked_open
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.documents import same_user
from kaagaz.api.routers.stories import SESSION_CHANGED
from kaagaz.api.twostep import unix
from kaagaz.db import audit, listing, page_turns, trash

router = APIRouter()
Context = Annotated[AppContext, Depends(get_context)]
TURNS = (0, 90, 180, 270)  # clockwise degrees; 0, not turned
IN_TRASH = "This document is in the Trash. Restore it first."
NOT_READY = "Kaagaz is still counting this file's pages. Try again in a minute."


class TurnChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    page_no: int = Field(ge=1)
    turn: int  # one of TURNS; strict, so never 90.0 or "90"


class PageTurnsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[TurnChange] = Field(min_length=1)
    user_id: str | None = None


def _live_version(
    conn: Connection, user: CurrentUser, document_id: uuid.UUID, request: Request, context: AppContext
) -> RowMapping:
    """The version, its family locked until the transaction ends. The visibility check comes
    first, so a hidden file is never locked and answers as a random id; a locked one then needs
    the person's live check."""
    row = listing.get_document(conn, user.customer_id, str(document_id))
    if row is None:
        raise ApiError(404, "not_found")
    if row["locked"]:
        require_locked_open(request, user, context.settings, unix(context.clock()))
    try:
        family_id = trash.lock_live_family(conn, user.customer_id, str(document_id))
    except trash.InTrash:
        raise ApiError(409, "in_trash", IN_TRASH) from None
    if family_id is None:  # deleted forever since it was read
        raise ApiError(404, "not_found")
    return row


def _record(conn: Connection, user: CurrentUser, action: str, target: str, details: dict[str, int]) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="document",
        target_id=target,
        details=details,
    )


@router.patch("/documents/{document_id}/page-turns")
def save_page_turns(
    document_id: uuid.UUID, body: PageTurnsBody, request: Request, user: User, context: Context
) -> dict[str, Any]:
    same_user(body.user_id, user, SESSION_CHANGED)
    changes = {change.page_no: change.turn for change in body.changes}
    if (
        len(changes) != len(body.changes)
        or len(changes) > context.settings.max_pages
        or not set(changes.values()) <= set(TURNS)
    ):
        raise ApiError(400, "bad_request")
    target = str(document_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        row = _live_version(conn, user, document_id, request, context)
        if row["page_count"] is None:
            raise ApiError(409, "not_ready", NOT_READY)
        if (last := max(changes)) > row["page_count"]:
            raise ApiError(400, "bad_request", f"Page {last} is not in this file.")
        page_turns.save(conn, user.customer_id, target, changes, by=user.user_id)
        turns = page_turns.for_document(conn, user.customer_id, target)
        _record(conn, user, "document.page_turns", target, {"pages": len(changes), "turned": len(turns)})
    return {"page_turns": turns}


@router.delete("/documents/{document_id}/page-turns")
def reset_page_turns(
    document_id: uuid.UUID,
    request: Request,
    user: User,
    context: Context,
    user_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    same_user(user_id, user, SESSION_CHANGED)
    target = str(document_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        _live_version(conn, user, document_id, request, context)
        removed = page_turns.reset(conn, user.customer_id, target)
        _record(conn, user, "document.page_turns_reset", target, {"pages": removed})
    return {"page_turns": []}
