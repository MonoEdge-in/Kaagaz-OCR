"""The version routes (Phase 2 PRD 1, V6, V7, V10, V11): accept or reject a suggestion, Undo a
link, make a version current, and compare two documents.

Any signed-in user of the customer may use them (the PRD restricts nothing). Each change is
one transaction as the signed-in customer, under the family locks (``db/versions.py``), with
its audit row. Another customer's suggestion or document is the same 404 as one that is not
there (row level security and the customer filter). A family in Trash is 409 ``in_trash``.

* ``POST /version-suggestions/{id}/accept`` (optional ``version_note``, V9): the document joins
  the candidate family as its next, current version; the reply is its summary. 409
  ``already_decided`` or ``not_ready``; 404 once either side went to Trash or was deleted.
* ``POST /version-suggestions/{id}/reject``: kept separate; the pair is never suggested again.
* ``POST /documents/{id}/version-undo``: an accepted or automatic link undone while it is the
  family's newest version; 409 ``not_newest`` after a later one, ``not_linked`` otherwise.
* ``POST /documents/{id}/make-current``: a ready version out of Trash; 409 ``not_ready``.
* ``GET /documents/{id}/compare?with=``: the same family, or a pending suggestion's pair (404
  otherwise); ``with`` is the left side (removed words), ``id`` the right (added words), page by
  page up to MAX_PAGES.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection

from kaagaz import versions
from kaagaz.api.auth import CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.documents import clean_note
from kaagaz.api.views import document_summary
from kaagaz.db import listing, trash
from kaagaz.db import tags as tags_sql
from kaagaz.db import versions as versions_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

_IN_TRASH = "This document is in the Trash. Restore it first."
_MESSAGES = {
    "already_decided": "Someone already answered this suggestion.",
    "not_ready": "This version is still being read. Try again once it is ready.",
    "not_linked": "This version was uploaded, not linked from a suggestion, so there is nothing to undo.",
    "not_newest": "A newer version was added since, so this link can't be undone.",
}


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_note: str | None = None


def _refused(refused: versions_sql.Refused) -> ApiError:
    if refused.code == "not_found":
        return ApiError(404, "not_found")
    return ApiError(409, refused.code, _MESSAGES[refused.code])


def _summary(conn: Connection, context: AppContext, user: CurrentUser, document_id: str) -> dict[str, Any]:
    row = listing.get_document(conn, user.customer_id, document_id)
    if row is None:
        raise ApiError(404, "not_found")
    tags = tags_sql.tags_for_documents(conn, user.customer_id, [document_id])[document_id]
    return document_summary(
        row, max_pages=context.settings.max_pages, tags=tags, user=user, now=context.clock()
    )


@router.post("/version-suggestions/{suggestion_id}/accept")
def accept_suggestion(
    suggestion_id: uuid.UUID, user: User, context: Context, body: AcceptBody | None = None
) -> dict[str, Any]:
    """Make it version N (V6): the new version's summary."""
    # V9 and O3: this version's note when given, and the family's details stamp (the folder,
    # tags, owner and label it took).
    given = body is not None and "version_note" in body.model_fields_set
    changes = {"version_note": clean_note(body.version_note)} if body is not None and given else {}
    customer_id = user.customer_id
    try:
        with context.db.customer(customer_id) as conn:
            document = versions_sql.link(conn, customer_id, str(suggestion_id), by=user.user_id)
            listing.set_details(conn, customer_id, document, by=user.user_id, **changes)
            return _summary(conn, context, user, document)
    except versions_sql.Refused as refused:
        raise _refused(refused) from None


@router.post("/version-suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Keep separate (V6)."""
    try:
        with context.db.customer(user.customer_id) as conn:
            document = versions_sql.reject(conn, user.customer_id, str(suggestion_id), by=user.user_id)
    except versions_sql.Refused as refused:
        raise _refused(refused) from None
    return {"id": str(suggestion_id), "status": "rejected", "document_id": document}


@router.post("/documents/{document_id}/version-undo")
def undo_version(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Undo a link (V7): the document's summary, on its own again."""
    try:
        with context.db.customer(user.customer_id) as conn:
            versions_sql.undo(conn, user.customer_id, str(document_id), by=user.user_id)
            return _summary(conn, context, user, str(document_id))
    except versions_sql.Refused as refused:
        raise _refused(refused) from None
    except trash.InTrash:
        raise ApiError(409, "in_trash", _IN_TRASH) from None


@router.post("/documents/{document_id}/make-current")
def make_current(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Make current (V10): the version's summary, now current."""
    try:
        with context.db.customer(user.customer_id) as conn:
            versions_sql.make_current(conn, user.customer_id, str(document_id), by=user.user_id)
            return _summary(conn, context, user, str(document_id))
    except versions_sql.Refused as refused:
        raise _refused(refused) from None
    except trash.InTrash:
        raise ApiError(409, "in_trash", _IN_TRASH) from None


@router.get("/documents/{document_id}/compare")
def compare(
    document_id: uuid.UUID,
    user: User,
    context: Context,
    other: Annotated[uuid.UUID, Query(alias="with")],
) -> dict[str, Any]:
    """Compare (V11): ``{left_id, right_id, pages: [{page_no, changes, truncated, runs: [{kind,
    text}]}], summary}``, kind same, removed (only in ``with``) or added (only in ``id``)."""
    customer_id, right, left = user.customer_id, str(document_id), str(other)
    with context.db.customer(customer_id) as conn:
        rows = [listing.get_document(conn, customer_id, each) for each in (left, right)]
        if any(row is None for row in rows) or not versions_sql.may_compare(conn, customer_id, left, right):
            raise ApiError(404, "not_found")
        if any(row is not None and row["deleted_at"] is not None for row in rows):
            raise ApiError(409, "in_trash", _IN_TRASH)
        texts = [versions_sql.page_texts(conn, customer_id, each) for each in (left, right)]
    # The diff runs after the transaction, so no pooled connection waits on it.
    found = versions.compare(*texts, max_pages=context.settings.max_pages)
    return {"left_id": left, "right_id": right, **found}
