"""Possible versions (V17, #648, V-B): the groups the version finder stored, and their decisions.
Read and decide only; linking is V-A's (``POST /documents/link-versions``).

Only Admins and Management (Tushar, 16 Nov 2026); anyone else gets 403 ``forbidden`` before
anything is looked up. Each sees only groups whose every file they can see
(``kaagaz/db/version_groups.py``): a group, or a file of one, they aren't shown is 404
``not_found``, the same reply as an id that never was. Each decision is one transaction, all or none, with an audit row per file.

* ``GET /version-groups``: ``{items: [VersionGroup], more}``, at most MAX_GROUPS.
* ``GET /version-groups/{id}``: one ``VersionGroup``: ``{id, files: [{id, name, folder_id,
  doc_type, document_date, uploaded_at, page_count, position, state, reasons: [{with,
  text_overlap, text_overlap_estimated, name_similarity, shared_identifier, words}]}],
  suggested_latest_id, kept_apart: [{id, name}]}``.
* ``POST /version-groups/{id}/keep-apart {document_ids}``: "Not versions" for these files
  (``version.kept_apart``); ``POST .../keep-apart/undo {document_ids}`` puts them back
  (``version.kept_apart_undone``); ``POST .../later {document_ids}``: "Decide later"
  (``version.later``). Each answers ``{group}``, the group as now shown (null once it isn't).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api.auth import CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.db import access as access_sql
from kaagaz.db import version_groups as groups_sql
from kaagaz.versions import MAX_GROUP

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

ONLY_ADMINS = "Only Admins and Management can see possible versions."


class FilesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_GROUP)


def _allowed(conn: Connection, user: CurrentUser) -> None:
    """An Admin or Management, else 403 (before any lookup, so it says nothing about a group)."""
    if user.role != "admin" and not access_sql.me(conn)[0]:
        raise ApiError(403, "forbidden", ONLY_ADMINS)


@router.get("/version-groups")
def list_groups(user: User, context: Context) -> dict[str, Any]:
    """The groups shown to this person (V17), with a file not yet left for later first."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        _allowed(conn, user)
        found = groups_sql.groups(conn, user.customer_id)
    return {"items": found[: groups_sql.MAX_GROUPS], "more": len(found) > groups_sql.MAX_GROUPS}


@router.get("/version-groups/{group_id}")
def get_group(group_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        _allowed(conn, user)
        found = groups_sql.groups(conn, user.customer_id, str(group_id))
    if not found:
        raise ApiError(404, "not_found")
    return found[0]


def _decide(
    group_id: uuid.UUID, body: FilesBody, user: CurrentUser, context: AppContext, to: str
) -> dict[str, Any]:
    group, ids = str(group_id), [str(each) for each in body.document_ids]
    try:
        with context.db.user(user.customer_id, user.user_id) as conn:
            _allowed(conn, user)
            groups_sql.decide(conn, user.customer_id, group, ids, to=to, by=user.user_id)
            found = groups_sql.groups(conn, user.customer_id, group)
    except groups_sql.Refused:
        raise ApiError(404, "not_found") from None
    return {"group": found[0] if found else None}


@router.post("/version-groups/{group_id}/keep-apart")
def keep_apart(group_id: uuid.UUID, body: FilesBody, user: User, context: Context) -> dict[str, Any]:
    """Not versions (V17, V18): these files leave the group, until Undo."""
    return _decide(group_id, body, user, context, "kept_apart")


@router.post("/version-groups/{group_id}/keep-apart/undo")
def undo_keep_apart(group_id: uuid.UUID, body: FilesBody, user: User, context: Context) -> dict[str, Any]:
    """Undo of Not versions: these files are possible versions in the group again."""
    return _decide(group_id, body, user, context, "open")


@router.post("/version-groups/{group_id}/later")
def decide_later(group_id: uuid.UUID, body: FilesBody, user: User, context: Context) -> dict[str, Any]:
    """Decide later: these files stay in the group, after the groups still to look at."""
    return _decide(group_id, body, user, context, "later")
