"""Story of this PDF (council, 3 Oct 2026): the signed-in user's own story about a document.

Why some choices:

* **Only mine.** Every route works as the signed-in user (``Database.user``), so
  another user's story, the same customer's included, is never seen: their
  GET is ``{"story": null}`` and their DELETE 404. A document of another
  customer is 404. A story belongs to the document's family, so every version
  reaches the same story.
* **Trash** (PROVISIONAL): a document whose family is in Trash is 404 for GET,
  PUT and DELETE. PUT and DELETE lock the live family (``trash.lock_live_family``)
  in the same transaction as the write, so a move to Trash or a purge can't slip
  in between. Deleting the family forever deletes its stories (0009, CASCADE).
* **The text** is checked by ``check_story`` (400 saying why). PUT is 201 when
  it created the story and 200 when it replaced it.
* **The vector.** After the save has committed, with the model on, the story is
  embedded with the pieces' model (``embed_story``) unless its vector is
  already current. The model client's own timeout (MODEL_TIMEOUT_SECONDS)
  bounds the wait. A model that is off, down or slow never fails the save: the
  story is saved without a vector and the error's type is logged; the worker's
  sweep (``StoryVectors``) embeds it later.
* **The session check.** PUT may send ``user_id`` in the body and DELETE in
  the query: who the tab had signed in. Someone else signed in since gets 409
  ``session_changed``, as for uploads.
* Audit rows (``document_story.saved``, ``document_story.deleted``) hold ids
  only; the story is never logged, audited or put in an error.
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

from kaagaz.ai.client import ModelClient, ModelOutputError, ModelUnavailable
from kaagaz.api.auth import CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.documents import _same_user
from kaagaz.db import audit, listing, trash
from kaagaz.db import document_stories as stories_sql
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.search.story_vectors import embed_story

router = APIRouter()
logger = logging.getLogger(__name__)

Context = Annotated[AppContext, Depends(get_context)]

SESSION_CHANGED = (
    "Not saved. A different account has signed in on this browser since this page opened."
    " Reload the page to see who is signed in now."
)
_TEXT = {
    "empty": "Write the story first.",
    "too_long": f"A story can be at most {stories_sql.STORY_MAX} characters.",
    "no_letter": "The story needs at least one letter or digit.",
}


class StoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story: str = Field(max_length=4 * stories_sql.STORY_MAX)  # before tidying; check_story checks the limit
    user_id: str | None = None


def _same_tab(user_id: str | None, user: CurrentUser) -> None:
    try:
        _same_user(user_id, user)
    except ApiError:
        raise ApiError(409, "session_changed", SESSION_CHANGED) from None


def _live_family(conn: Connection, user: CurrentUser, document_id: str) -> str:
    """Lock the document's family until the transaction ends and return its id. 404
    when there is no such document for this customer, or its family is in Trash.
    O(v log D) for the family's v versions."""
    try:
        family_id = trash.lock_live_family(conn, user.customer_id, document_id)
    except trash.InTrash:
        raise ApiError(404, "not_found") from None
    if family_id is None:
        raise ApiError(404, "not_found")
    return family_id


def _audit(conn: Connection, user: CurrentUser, action: str, family_id: str, document_id: str) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="document_story",
        target_id=family_id,
        details={"document_id": document_id},
    )


def _current(model: ModelClient | None, row: stories_sql.StoryRow) -> bool:
    """The story has a vector of today's model and recipe."""
    return model is not None and row.embedding_model == stories_sql.recipe_tag(model.embed_model)


def _view(row: stories_sql.StoryRow, *, embedded: bool) -> dict[str, Any]:
    return {
        "family_id": row.family_id,
        "story": row.story,
        "embedded": embedded,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _embed(context: AppContext, user: CurrentUser, row: stories_sql.StoryRow) -> bool:
    """Embed the saved story unless the model is off or its vector is current. True
    when it has a current vector afterwards. Never raises for the model or the database."""
    model = context.model
    if model is None or _current(model, row):
        return model is not None
    try:
        return embed_story(context.db, model, user.customer_id, user.user_id, row.family_id, row.story)
    except (ModelUnavailable, ModelOutputError, BackendUnavailable, SQLAlchemyError) as error:
        logger.warning("story saved without a vector", extra={"code": type(error).__name__})
        return False


@router.get("/documents/{document_id}/my-story")
def get_my_story(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """This user's story of the document's family, or ``{"story": null}``. Two indexed reads."""
    target = str(document_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        document = listing.get_document(conn, user.customer_id, target)
        if document is None or document["deleted_at"] is not None:  # Trash takes the whole family
            raise ApiError(404, "not_found")
        row = stories_sql.get_story(conn, user.customer_id, user.user_id, target)
    return {"story": None if row is None else _view(row, embedded=_current(context.model, row))}


@router.put("/documents/{document_id}/my-story", status_code=201)
def put_my_story(
    document_id: uuid.UUID, body: StoryBody, user: User, context: Context, response: Response
) -> dict[str, Any]:
    """Create (201) or replace (200) this user's story, then embed it (see the module
    docstring). One transaction, then at most one model call and one more transaction."""
    _same_tab(body.user_id, user)
    try:
        story = stories_sql.check_story(body.story)
    except stories_sql.StoryTextError as error:
        raise ApiError(400, "bad_request", _TEXT[error.reason]) from None
    target = str(document_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        _live_family(conn, user, target)
        row = stories_sql.put_story(conn, user.customer_id, user.user_id, target, story)
        if row is None:  # the family went (a purge) before the lock
            raise ApiError(404, "not_found")
        _audit(conn, user, "document_story.saved", row.family_id, target)
    # One transaction's now(): equal only on the row's first insert (the touch trigger
    # sets updated_at on every replace, in a later transaction).
    if row.updated_at != row.created_at:
        response.status_code = 200
    return {"story": _view(row, embedded=_embed(context, user, row))}


@router.delete("/documents/{document_id}/my-story", status_code=204)
def delete_my_story(
    document_id: uuid.UUID, user: User, context: Context, user_id: Annotated[str | None, Query()] = None
) -> Response:
    """Delete this user's story of the document's family. 404 when there is none."""
    _same_tab(user_id, user)
    target = str(document_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        family_id = _live_family(conn, user, target)
        if not stories_sql.delete_story(conn, user.customer_id, user.user_id, target):
            raise ApiError(404, "not_found")
        _audit(conn, user, "document_story.deleted", family_id, target)
    return Response(status_code=204)
