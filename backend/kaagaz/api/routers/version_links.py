"""Link files already uploaded as versions of each other, and Undo (V13 to V16, V18, #648, V-A).

Only Admins (in an admin session: the confirm step names people, decision 20) and Management
(Tushar, 16 Nov 2026); anyone else gets 403 ``forbidden``, "Ask an admin to link these.", before
anything is looked up. A file the person can't see, or a version of its family they can't see, is
404 ``not_found``, the same reply as an id that never was. Each write is one transaction, all or
none, run again when PostgreSQL breaks a deadlock (``kaagaz/db/version_links.py`` has the rules and
the locks).

* ``POST /documents/link-versions/preview {document_ids, order, current_id}``: the confirm step
  (V13, V15): ``{latest: {id, name}, folder: {id, path} | null, held, versions, losing, gaining}``
  (people as ``{id, name}``), from one snapshot; nothing is locked or written.
* ``POST /documents/link-versions {document_ids, order, current_id, password?}``: 2 to 20 files,
  ``order`` each of them once (oldest first), ``current_id`` among them (the one Ask answers from).
  ``{link_id, document}``, the latest's summary. 409 ``in_trash``, ``locked``, ``not_ready``,
  ``already_versions`` or ``changed``; 403 ``password_required`` when the link opens a file to the
  Admin linking it (a wall opened to themselves, as a folder move).
* ``POST /documents/{id}/link-versions/undo {password?}``: Undo of the newest link of the file's
  family, while it holds exactly the versions the link left; the file's summary. 409
  ``not_linked``, ``not_newest``, ``folder_gone``, ``locked`` or ``in_trash``.
* ``GET /version-kept-apart``: V18's Kept apart list: ``{pairs, groups}``, pairs kept separate or
  made separate whose two sides the person sees (each can be linked again here), and files kept
  apart in a group they see whole (V-B's Undo).
"""

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api.auth import CurrentUser, User, require_admin_session
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.locked import UNLOCK_FIRST
from kaagaz.api.routers.access import Password, _password_given, _self_grant
from kaagaz.api.views import document_summary
from kaagaz.db import access as access_sql
from kaagaz.db import listing, trash
from kaagaz.db import tags as tags_sql
from kaagaz.db import version_groups as groups_sql
from kaagaz.db import version_links as links_sql
from kaagaz.db.sqlstate import retry_on_deadlock
from kaagaz.versions import MAX_GROUP

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

ONLY_ADMINS = "Only Admins and Management can link versions. Ask an admin to link these."
MAX_KEPT_APART = 200
_MESSAGES = {
    "in_trash": "This document is in the Trash. Restore it first.",
    "locked": UNLOCK_FIRST,
    "not_ready": "The file Ask will answer from is still being read. Choose another, or try again once it"
    " is ready.",
    "already_versions": "Some of these files are already versions of one file.",
    "changed": "These files changed just now. Try again.",
    "not_linked": "These versions weren't linked here, so there is nothing to undo.",
    "not_newest": "The versions changed since they were linked, so this can't be undone.",
    "folder_gone": "A folder these files were in is gone, so this can't be undone. Move them by hand.",
}


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[uuid.UUID] = Field(min_length=2, max_length=MAX_GROUP)
    order: list[uuid.UUID] = Field(min_length=2, max_length=MAX_GROUP)
    current_id: uuid.UUID


class LinkWithPassword(LinkBody):
    password: Password = None


class UndoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: Password = None


def _allowed(conn: Connection, user: CurrentUser) -> None:
    """An Admin in an admin session, or Management; else 403 before any lookup."""
    if user.role == "admin":
        require_admin_session(user)
    elif not access_sql.me(conn)[0]:
        raise ApiError(403, "forbidden", ONLY_ADMINS)


def _asked(body: LinkBody) -> tuple[list[str], list[str], str]:
    """The files, their order and the latest; 400 unless the order holds each file once and the
    latest is one of them."""
    ids = [str(each) for each in body.document_ids]
    order = [str(each) for each in body.order]
    current = str(body.current_id)
    if len(set(ids)) != len(ids) or sorted(order) != sorted(ids) or current not in ids:
        raise ApiError(
            400, "bad_request", "Put each file in the order once, and choose the latest from them."
        )
    return ids, order, current


def _seen(conn: Connection, user: CurrentUser, ids: list[str]) -> None:
    """PRD 7 (#595): a file the person can't see is not found, before any lock."""
    if len(listing.documents_seen(conn, user.customer_id, ids)) < len(ids):
        raise ApiError(404, "not_found")


def _audience(conn: Connection, user: CurrentUser, moves: list[links_sql.Moved]) -> tuple[set[str], set[str]]:
    """Who loses and who gains the files the link or its Undo moves (PRD 7 section 5)."""
    losing: set[str] = set()
    gaining: set[str] = set()
    for move, held, held_after in moves:
        lost, gained = access_sql.moved(conn, user.customer_id, move, held, held_after)
        losing |= lost
        gaining |= gained
    return losing, gaining


def _refused(refused: links_sql.Refused) -> ApiError:
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


def _written[T](user: CurrentUser, first: str, transaction: Callable[[], T]) -> T:
    """``transaction`` run again on a deadlock, its refusals as replies."""
    try:
        return retry_on_deadlock(transaction, customer_id=user.customer_id, document_id=first)
    except links_sql.Refused as refused:
        raise _refused(refused) from None
    except trash.InTrash:
        raise ApiError(409, "in_trash", _MESSAGES["in_trash"]) from None


@router.post("/documents/link-versions/preview")
def preview_link(body: LinkBody, user: User, context: Context) -> dict[str, Any]:
    """The confirm step (V13, V15): which file Ask will answer from, where the files will be kept,
    and who would lose or gain them."""
    ids, order, current = _asked(body)
    with context.db.user(user.customer_id, user.user_id, repeatable_read=True) as conn:
        _allowed(conn, user)
        _seen(conn, user, ids)
        try:
            plan = links_sql.prepare(conn, user.customer_id, ids, order, current, lock=False)
        except links_sql.Refused as refused:
            raise _refused(refused) from None
        latest = plan.rows[current]
        losing, gaining = _audience(conn, user, plan.moves)
        folder = latest["folder_id"]
        path = None if folder is None else access_sql.paths(conn, user.customer_id, [folder]).get(folder)
        return {
            "latest": {"id": current, "name": latest["display_name"]},
            "folder": None if folder is None else {"id": folder, "path": path},
            "held": latest["held_for"] is not None,
            "versions": len(plan.order),
            "losing": access_sql.names(conn, user.customer_id, losing),
            "gaining": access_sql.names(conn, user.customer_id, gaining),
        }


@router.post("/documents/link-versions")
def link_versions(body: LinkWithPassword, user: User, context: Context) -> dict[str, Any]:
    """Link these files as versions of one file (V13, V14, V15)."""
    ids, order, current = _asked(body)
    _password_given(context, user, body.password)  # holding no connection (access.py)

    def transaction() -> dict[str, Any]:
        with context.db.user(user.customer_id, user.user_id) as conn:
            _allowed(conn, user)
            _seen(conn, user, ids)
            plan = links_sql.prepare(conn, user.customer_id, ids, order, current)
            if user.user_id in _audience(conn, user, plan.moves)[1]:
                _self_grant(user, body.password)
            link = links_sql.apply(conn, user.customer_id, plan, by=user.user_id)
            return {"link_id": link, "document": _summary(conn, context, user, current)}

    return _written(user, current, transaction)


@router.post("/documents/{document_id}/link-versions/undo")
def undo_link(
    document_id: uuid.UUID, user: User, context: Context, body: UndoBody | None = None
) -> dict[str, Any]:
    """Undo the link (V16): every version back as it was; this file's summary."""
    target = str(document_id)
    password = None if body is None else body.password
    _password_given(context, user, password)

    def transaction() -> dict[str, Any]:
        with context.db.user(user.customer_id, user.user_id) as conn:
            _allowed(conn, user)
            _seen(conn, user, [target])
            plan = links_sql.prepare_undo(conn, user.customer_id, target)
            if user.user_id in _audience(conn, user, plan.moves)[1]:
                _self_grant(user, password)
            links_sql.apply_undo(conn, user.customer_id, plan, by=user.user_id)
            return _summary(conn, context, user, target)

    return _written(user, target, transaction)


@router.get("/version-kept-apart")
def kept_apart(user: User, context: Context) -> dict[str, Any]:
    """V18's Kept apart list."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        _allowed(conn, user)
        return {
            "pairs": links_sql.kept_apart(conn, user.customer_id, MAX_KEPT_APART),
            "groups": groups_sql.kept_apart(conn, user.customer_id),
        }
