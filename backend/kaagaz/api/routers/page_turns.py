"""Page turns saved for everyone (PRD 7 section 14 step 2, #600). The detail reads them
(``page_turns`` in GET /api/documents/{id}); this route writes them.

* **Who.** Anyone who can see the file (there is no read-only role). A file the person cannot see
  answers exactly as an id that does not exist (404), before its family is locked: Trash, an
  unread file and a bad page are never told for it.
* **PATCH** ``{"changes": [{"page_no", "turn"}], "user_id"}`` sets each named page's turn (0: not
  turned), so the last save wins per page and two people turning different pages both keep theirs.
  There is no whole-file write (the lead's decision on the review): putting pages back as scanned
  is a save of turn 0 for the pages the person sees turned, so it never takes away a turn someone
  else saved on another page. The reply is the whole set after the save.
* **Order**, in one transaction: the session check (409 ``session_changed``), the body (400), the
  file as the person sees it (404), the family lock (409 ``in_trash``; saves of one family queue
  on it), its page count (409 ``not_ready`` while unknown, or for a file that could not be read;
  400 for a page past the end), the write, one audit row (ids and counts only).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, RowMapping

from kaagaz.api.auth import CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.documents import same_user
from kaagaz.api.routers.stories import SESSION_CHANGED
from kaagaz.db import audit, listing, page_turns, trash

router = APIRouter()
Context = Annotated[AppContext, Depends(get_context)]
TURNS = (0, 90, 180, 270)  # clockwise degrees; 0, not turned
IN_TRASH = "This document is in the Trash. Restore it first."
NOT_READY = "Kaagaz is still counting this file's pages. Try again in a minute."
UNREADABLE = "Kaagaz couldn't read this file's pages, so its pages can't be turned."


class TurnChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    page_no: int = Field(ge=1)
    turn: int  # one of TURNS; strict, so never 90.0 or "90"


class PageTurnsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[TurnChange] = Field(min_length=1)
    user_id: str | None = None


def _live_version(conn: Connection, user: CurrentUser, document_id: uuid.UUID) -> RowMapping:
    """The version, its family locked until the transaction ends. The visibility check comes
    first, so a hidden file is never locked and answers as a random id."""
    row = listing.get_document(conn, user.customer_id, str(document_id))
    if row is None:
        raise ApiError(404, "not_found")
    try:
        family_id = trash.lock_live_family(conn, user.customer_id, str(document_id))
    except trash.InTrash:
        raise ApiError(409, "in_trash", IN_TRASH) from None
    if family_id is None:  # deleted forever since it was read
        raise ApiError(404, "not_found")
    return row


@router.patch("/documents/{document_id}/page-turns")
def save_page_turns(
    document_id: uuid.UUID, body: PageTurnsBody, user: User, context: Context
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
        row = _live_version(conn, user, document_id)
        if row["page_count"] is None:
            failed = row["status"] in ("failed", "rejected")
            raise ApiError(409, "not_ready", UNREADABLE if failed else NOT_READY)
        if (last := max(changes)) > row["page_count"]:
            raise ApiError(400, "bad_request", f"Page {last} is not in this file.")
        page_turns.save(conn, user.customer_id, target, changes, by=user.user_id)
        turns = page_turns.for_document(conn, user.customer_id, target)
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="document.page_turns",
            target_type="document",
            target_id=target,
            details={"pages": len(changes), "turned": len(turns)},
        )
    return {"page_turns": turns}
