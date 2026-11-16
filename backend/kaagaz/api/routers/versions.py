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
* ``GET /documents/{id}/compare?with=``: the same family, a pending suggestion's pair, or for
  Admins and Management two files of the part a possible-versions group shows them (#648; 404
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
from kaagaz.api.locked import UNLOCK_FIRST, refuse_locked
from kaagaz.api.names import DOCUMENT_NAME_MAX, upload_name
from kaagaz.api.routers.documents import MOVE_REFUSED, clean_note
from kaagaz.api.views import document_summary
from kaagaz.db import access as access_sql
from kaagaz.db import audit, listing, trash
from kaagaz.db import folders as folders_sql
from kaagaz.db import tags as tags_sql
from kaagaz.db import version_groups as groups_sql
from kaagaz.db import versions as versions_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

_IN_TRASH = "This document is in the Trash. Restore it first."
_MESSAGES = {
    "already_decided": "Someone already answered this suggestion.",
    "not_ready": "This version is still being read. Try again once it is ready.",
    "not_linked": "This version was uploaded, not linked from a suggestion, so there is nothing to undo.",
    "not_newest": "A newer version was added since, so this link can't be undone.",
    "locked": UNLOCK_FIRST,  # PRD 7 (#605, question 11)
}


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_note: str | None = None


def _refused(refused: versions_sql.Refused) -> ApiError:
    if refused.code == "not_found":
        return ApiError(404, "not_found")
    if refused.code == "forbidden":  # #594 (Q4)
        return ApiError(
            403, "forbidden", "Only the person this file was kept for, or Management, can undo this."
        )
    return ApiError(409, refused.code, _MESSAGES[refused.code])


def _rights(conn: Connection, user: CurrentUser) -> folders_sql.Rights:
    """PRD 7 decision 18 (#601): the person's rights to a join that changes who can see a file,
    read under the tree lock (taken here first; the join takes it again)."""
    folders_sql._lock_tree(conn, user.customer_id)
    return access_sql.rights(conn, user.role == "admin")


def _visible(conn: Connection, user: CurrentUser, document_id: str) -> None:
    """PRD 7 (#595): a file the person cannot see is not found, before anything else is checked.
    PRD 7 (#605, question 11): then a locked one is 409 ``locked``, "Unlock it first." (the version
    functions refuse a locked family again under its lock)."""
    row = listing.get_document(conn, user.customer_id, document_id)
    if row is None:
        raise ApiError(404, "not_found")
    if row["locked"]:
        raise ApiError(409, "locked", UNLOCK_FIRST)


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
        with context.db.user(customer_id, user.user_id) as conn:
            # PRD 7 (#595): a suggestion is accepted only by a person who sees both its sides.
            if not versions_sql.is_live(conn, customer_id, str(suggestion_id)):
                raise ApiError(404, "not_found")
            rights = _rights(conn, user)
            document = versions_sql.link(
                conn, customer_id, str(suggestion_id), by=user.user_id, rights=rights
            )
            listing.set_details(conn, customer_id, document, by=user.user_id, **changes)
            return _summary(conn, context, user, document)
    except versions_sql.Refused as refused:
        raise _refused(refused) from None
    except folders_sql.MoveChangesAccess:
        raise ApiError(403, "move_changes_access", MOVE_REFUSED) from None


@router.post("/version-suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Keep separate (V6)."""
    try:
        with context.db.user(user.customer_id, user.user_id) as conn:
            document = versions_sql.reject(conn, user.customer_id, str(suggestion_id), by=user.user_id)
    except versions_sql.Refused as refused:
        raise _refused(refused) from None
    return {"id": str(suggestion_id), "status": "rejected", "document_id": document}


@router.post("/documents/{document_id}/version-undo")
def undo_version(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Undo a link (V7): the document's summary, on its own again."""
    try:
        with context.db.user(user.customer_id, user.user_id) as conn:
            _visible(conn, user, str(document_id))
            rights = _rights(conn, user)
            versions_sql.undo(conn, user.customer_id, str(document_id), by=user.user_id, rights=rights)
            return _summary(conn, context, user, str(document_id))
    except versions_sql.Refused as refused:
        raise _refused(refused) from None
    except folders_sql.MoveChangesAccess:
        raise ApiError(403, "move_changes_access", MOVE_REFUSED) from None
    except trash.InTrash:
        raise ApiError(409, "in_trash", _IN_TRASH) from None


@router.post("/documents/{document_id}/make-current")
def make_current(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Make current (V10): the version's summary, now current."""
    try:
        with context.db.user(user.customer_id, user.user_id) as conn:
            _visible(conn, user, str(document_id))
            versions_sql.make_current(conn, user.customer_id, str(document_id), by=user.user_id)
            return _summary(conn, context, user, str(document_id))
    except versions_sql.Refused as refused:
        raise _refused(refused) from None
    except trash.InTrash:
        raise ApiError(409, "in_trash", _IN_TRASH) from None


class SeparateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class JoinAgainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family_id: uuid.UUID
    name: str | None = None  # the name it had, given back by Undo


def _document_name(name: str) -> str:
    """The name rule of a folder upload's file (``api/names.upload_name``): no hidden or bidi
    characters, 1 to DOCUMENT_NAME_MAX characters; 400 otherwise."""
    cleaned = upload_name(name, max_length=DOCUMENT_NAME_MAX)
    if cleaned is None:
        raise ApiError(
            400,
            "bad_request",
            f"Enter a name of 1 to {DOCUMENT_NAME_MAX} characters, with no hidden characters.",
        )
    return cleaned


@router.post("/documents/{document_id}/separate")
def separate_version(
    document_id: uuid.UUID, body: SeparateBody, user: User, context: Context
) -> dict[str, Any]:
    """Make it a separate file (#521): ``{document, family_id}``, the family it left. 409
    ``name_taken`` (a file of that name in the folder), ``first_version``, ``only_version`` or
    ``in_trash``."""
    name = _document_name(body.name)
    customer_id, document = user.customer_id, str(document_id)
    try:
        with context.db.user(customer_id, user.user_id) as conn:
            _visible(conn, user, document)
            family = versions_sql.separate(conn, customer_id, document, name=name, by=user.user_id)
            listing.rename_document(conn, customer_id, document, name=name)
            return {"document": _summary(conn, context, user, document), "family_id": family}
    except versions_sql.Refused as refused:
        if refused.code == "name_taken":
            raise ApiError(409, "name_taken", f'A file named "{name}" is already in this folder.') from None
        if refused.code == "first_version":
            raise ApiError(
                409,
                "first_version",
                "The first version can't be made a separate file. Make the newer versions separate instead.",
            ) from None
        if refused.code == "only_version":
            raise ApiError(
                409, "only_version", "This is the only version, so it is its own file already."
            ) from None
        raise _refused(refused) from None
    except trash.InTrash:
        raise ApiError(409, "in_trash", _IN_TRASH) from None


@router.post("/documents/{document_id}/separate/undo")
def join_again(document_id: uuid.UUID, body: JoinAgainBody, user: User, context: Context) -> dict[str, Any]:
    """Undo of Make it a separate file (#521): back in the family as its newest version (Accept),
    with the name it had when given; the summary. 404 when that pair was not kept apart."""
    name = None if body.name is None else _document_name(body.name)
    customer_id, document = user.customer_id, str(document_id)
    try:
        with context.db.user(customer_id, user.user_id) as conn:
            _visible(conn, user, document)
            _visible(conn, user, str(body.family_id))  # the family it rejoins too, before any lock
            rights = _rights(conn, user)
            versions_sql.join_again(
                conn, customer_id, document, str(body.family_id), by=user.user_id, rights=rights
            )
            if name is not None:
                listing.rename_document(conn, customer_id, document, name=name)
            return _summary(conn, context, user, document)
    except versions_sql.Refused as refused:
        raise _refused(refused) from None
    except folders_sql.MoveChangesAccess:
        raise ApiError(403, "move_changes_access", MOVE_REFUSED) from None


@router.get("/documents/{document_id}/compare")
def compare(
    document_id: uuid.UUID,
    user: User,
    context: Context,
    other: Annotated[uuid.UUID, Query(alias="with")],
) -> dict[str, Any]:
    """Compare (V11): ``{left_id, right_id, pages: [{page_no, changes, truncated, runs: [{kind,
    text}]}], summary, overview}``, kind same, removed (only in ``with``) or added (only in
    ``id``); each page also counts its words changed and each side's words, and ``overview``
    says it all in plain words (#521, See the differences)."""
    customer_id, right, left = user.customer_id, str(document_id), str(other)
    with context.db.user(customer_id, user.user_id) as conn:
        rows = [listing.get_document(conn, customer_id, each) for each in (left, right)]
        allowed = versions_sql.may_compare(conn, customer_id, left, right)
        if allowed is False and (user.role == "admin" or access_sql.me(conn)[0]):
            allowed = groups_sql.same_part(conn, customer_id, left, right)  # #648: two files of a group
        if any(row is None for row in rows) or not allowed:
            raise ApiError(404, "not_found")
        for row in rows:
            if row is not None:
                refuse_locked(row)  # PRD 7 (#605): either side locked
        if any(row is not None and row["deleted_at"] is not None for row in rows):
            raise ApiError(409, "in_trash", _IN_TRASH)
        texts = [versions_sql.page_texts(conn, customer_id, each) for each in (left, right)]
        for each in sorted({left, right}):  # #482: a view of each, in one order (the view locks)
            audit.record_view(conn, customer_id, actor_user_id=user.user_id, document_id=each, via="compare")
    # The diff runs after the transaction, so no pooled connection waits on it.
    found = versions.compare(*texts, max_pages=context.settings.max_pages)
    share = versions.exact_overlap(*texts)  # #521: See the differences' summary line
    said = versions.overview(found["pages"], share, truncated=found["truncated"])
    counts = {"left_pages": rows[0]["page_count"], "right_pages": rows[1]["page_count"]}  # type: ignore[index]
    return {"left_id": left, "right_id": right, **found, "overview": said, **counts}
