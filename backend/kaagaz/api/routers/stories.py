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
  already current. The reply waits for it at most EMBED_WAIT_SECONDS, never for
  the model client's own timeout (MODEL_TIMEOUT_SECONDS, 60 s and more): the
  site's /api proxy (Next.js rewrites) answers 500 after 30 s, so a slow model
  made a saved story look failed (3 Oct 2026). A slower embed goes on in the
  background (at most EMBEDS_AT_ONCE at a time; beyond that it is left to the
  sweep) and the reply says ``embedded: false``; its write is dropped if the
  story changed meanwhile. A model that is off, down or slow never fails the
  save: the story is saved without a vector and the error's type is logged; the
  worker's sweep (``StoryVectors``) embeds it later.
* **The session check.** PUT may send ``user_id`` in the body and DELETE in
  the query: who the tab had signed in. Someone else signed in since gets 409
  ``session_changed``, as for uploads.
* Audit rows (``document_story.saved``, ``document_story.deleted``) hold ids
  only; the story is never logged, audited or put in an error.
* **Story matches** (S3): ``POST /api/me/story-matches`` with a JSON body (the
  question, the chat's scope, ``limit`` 1 to 10 and ``mode``: "cards" beside a
  chat answer, "stories" for "Search my stories"), which the chat screen calls
  beside ``POST /api/chat`` or instead of it, never through it: a story never
  reaches an answer, a message or a prompt, and the call saves nothing. POST,
  not GET, so what a user remembers of a story never sits in a URL that edge or
  proxy logs keep (council 3 Oct 2026, "story search mode"); the old ``GET
  ?q=`` still answers the same way, with 3 side cards. The question is embedded
  only while the meaning threshold is calibrated (OFF today), so it makes no
  model call. The reply holds ids, names and bands, how many stories were read
  (``searched``) and whether that was all (``all``); a question with no content
  word is ``[]`` with ``reason: "no_content_words"`` before any SQL.
* **Load** (PROVISIONAL): ranking is CPU work on the API process, so story
  matches have their own limit, STORY_MATCHES_PER_MINUTE a user (429 with
  Retry-After, beside the site-wide one), and at most RANKING_AT_ONCE run at a
  time in the process: one more is 503 at once, never a queue.
* **AI spend** (model choice, council 11 Oct 2026, SP1): a story's embedding and a story
  match's question embedding are billed to the user, kind ``story`` (``usage_scope``,
  opened in the embedding's own thread, so a slow one is still counted).
"""

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as WaitedTooLong
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

from kaagaz.ai.client import ModelClient, ModelOutputError, ModelUnavailable
from kaagaz.ai.usage import usage_scope
from kaagaz.api.auth import CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers.documents import _same_user
from kaagaz.db import audit, listing, trash
from kaagaz.db import document_stories as stories_sql
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.search.indexing import embed_question
from kaagaz.search.scope import ScopeBody, resolve
from kaagaz.search.story_match import MATCHES_MAX, MOST_MAX, Mode, question_words
from kaagaz.search.story_search import meaning_threshold, search_stories, story_matches_json
from kaagaz.search.story_vectors import embed_story

router = APIRouter()
logger = logging.getLogger(__name__)

Context = Annotated[AppContext, Depends(get_context)]

SESSION_CHANGED = (
    "Not saved. A different account has signed in on this browser since this page opened."
    " Reload the page to see who is signed in now."
)
# PROVISIONAL (3 Oct 2026): how long a PUT waits for the inline embed (well under
# the site proxy's 30 s), and how many embeds may run in the background at once.
EMBED_WAIT_SECONDS = 5.0
EMBEDS_AT_ONCE = 4
_embeds = ThreadPoolExecutor(max_workers=EMBEDS_AT_ONCE, thread_name_prefix="story-embed")
_embed_slots = threading.BoundedSemaphore(EMBEDS_AT_ONCE)
# PROVISIONAL (council 3 Oct 2026): story matches a user may ask a minute, and
# rankings at once in this process.
STORY_MATCHES_PER_MINUTE = 20
RANKING_AT_ONCE = 2
story_limits = RateLimiter(STORY_MATCHES_PER_MINUTE)
ranking_slots = threading.BoundedSemaphore(RANKING_AT_ONCE)
_TEXT = {
    "empty": "Write the story first.",
    "too_long": f"A story can be at most {stories_sql.STORY_MAX} characters.",
    "no_letter": "The story needs at least one letter or digit.",
}


class StoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story: str = Field(max_length=4 * stories_sql.STORY_MAX)  # before tidying; check_story checks the limit
    user_id: str | None = None


class StoryMatchParams(ScopeBody):
    q: str = Field(min_length=1, max_length=1000)  # a chat question's limit


class StoryMatchBody(StoryMatchParams):
    limit: int = Field(default=MATCHES_MAX, ge=1, le=MOST_MAX, strict=True)
    mode: Mode = "cards"


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


def _embed_now(context: AppContext, model: ModelClient, user: CurrentUser, row: stories_sql.StoryRow) -> bool:
    """One embed of the saved story, then frees its slot. Never raises for the model
    or the database."""
    try:
        with usage_scope(context.db, customer_id=user.customer_id, user_id=user.user_id, kind="story"):
            return embed_story(context.db, model, user.customer_id, user.user_id, row.family_id, row.story)
    except (ModelUnavailable, ModelOutputError, BackendUnavailable, SQLAlchemyError) as error:
        logger.warning("story saved without a vector", extra={"code": type(error).__name__})
        return False
    finally:
        _embed_slots.release()


def _embed(context: AppContext, user: CurrentUser, row: stories_sql.StoryRow) -> bool:
    """Embed the saved story unless the model is off or its vector is current. True
    when it has a current vector afterwards. Waits at most EMBED_WAIT_SECONDS: a
    slower embed goes on in the background and this says False. With every slot
    taken it does not embed (the sweep will). Never raises for the model or the
    database."""
    model = context.model
    if model is None or _current(model, row):
        return model is not None
    if not _embed_slots.acquire(blocking=False):
        logger.warning("story saved without a vector", extra={"code": "embeds_busy"})
        return False
    try:
        pending = _embeds.submit(_embed_now, context, model, user, row)
    except RuntimeError:  # the pool was shut down: the app is stopping
        _embed_slots.release()
        return False
    try:
        return pending.result(timeout=EMBED_WAIT_SECONDS)
    except WaitedTooLong:
        logger.warning("story saved, its vector is still on its way", extra={"code": "embed_slow"})
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


@router.get("/me/story-matches")
def get_my_story_matches(
    params: Annotated[StoryMatchParams, Query()], user: User, context: Context
) -> dict[str, Any]:
    """The first form, kept: 3 side cards for ``q`` (``my_story_matches``)."""
    return my_story_matches(StoryMatchBody.model_validate(params.model_dump()), user, context)


@router.post("/me/story-matches")
def my_story_matches(body: StoryMatchBody, user: User, context: Context) -> dict[str, Any]:
    """``{story_matches: [{document_id, display_name, doc_type, band}], searched, all}``
    for ``q`` in the scope. Two short transactions: the scope as the customer, the
    stories as the user. Writes nothing."""
    if not story_limits.allow(user.user_id):
        raise ApiError(429, "rate_limited", headers={"Retry-After": "60"})
    question = body.q.strip()
    if not question_words(question):
        return {"story_matches": [], "searched": 0, "all": True, "reason": "no_content_words"}
    settings = context.settings
    with context.db.customer(user.customer_id) as conn:
        scope = resolve(conn, user.customer_id, body, timezone=settings.timezone)
    if scope.matches_nothing:
        return {"story_matches": [], "searched": 0, "all": True}
    on = meaning_threshold(settings.model_embed) is not None
    embedding = None
    if on:
        with usage_scope(context.db, customer_id=user.customer_id, user_id=user.user_id, kind="story"):
            embedding = embed_question(context.model, question)
    if not ranking_slots.acquire(blocking=False):
        raise ApiError(503, "backend_unavailable")
    try:
        found = search_stories(
            context.db,
            customer_id=user.customer_id,
            user_id=user.user_id,
            question=question,
            embedding=embedding,
            model_embed=settings.model_embed,
            scope=scope,
            mode=body.mode,
            most=body.limit,
        )
    finally:
        ranking_slots.release()
    return {"story_matches": story_matches_json(found.matches), "searched": found.searched, "all": found.all}
