"""The folder routes: list, create, rename or move, and delete (PRD 1 F1).

Why some choices:

* **Thin.** The tree rules (at most 100 levels, no cycles, one name per parent
  in any letter case, only an empty folder can go) live in
  ``kaagaz.db.folders``, which raises a ``FolderError`` carrying the API code.
  ``_refused`` turns it into the reply: 404 for a folder that isn't there
  (another customer's included, never 403), 409 for the others.
* **"Don't move" is not "move to the top level".** PATCH moves a folder only
  when the body has a ``parent_id`` key, and an explicit null means the top
  level. Pydantic's ``model_fields_set`` tells the two apart.
* **Any signed-in user of the customer** may change folders (PRD 1 F1). Each
  change writes one audit row with ids only, never a folder name.
* A refused change raises out of the ``with`` block, so its transaction rolls
  back, audit row included.
"""

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api.auth import CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.names import FOLDER_NAME_MAX, clean_name
from kaagaz.api.routers.documents import ActivityCursor, ActivityLimit
from kaagaz.api.views import folder, timeline
from kaagaz.db import audit
from kaagaz.db import folders as folders_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]


FolderKind = Literal["department", "category", "party", "property", "year", "custom"]
SortOrder = Annotated[int, Field(strict=True, ge=0, le=10_000)]


class NewFolderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    parent_id: uuid.UUID | None = None  # None: at the top level
    kind: FolderKind = "custom"  # File management F1: department is an admin's, top level only
    sort_order: SortOrder | None = None


class FolderChangesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    parent_id: uuid.UUID | None = None  # an explicit null: the top level; left out: don't move
    kind: FolderKind | None = None  # left out or null: keep it
    sort_order: SortOrder | None = None  # an explicit null clears it; left out: keep it


def _admin_for_departments(user: CurrentUser, *kinds: str | None) -> None:
    """F1: only an admin creates, renames, moves, orders or changes a department."""
    if "department" in kinds and user.role != "admin":
        raise ApiError(403, "forbidden", "Only an admin can change departments.")


@contextmanager
def _refused() -> Iterator[None]:
    """A folder change the helpers refused becomes its API error."""
    try:
        yield
    except folders_sql.FolderError as error:
        raise ApiError(404 if error.code == "not_found" else 409, error.code) from None


def _name(name: str | None) -> str:
    cleaned = clean_name(name or "", max_length=FOLDER_NAME_MAX)
    if cleaned is None:
        raise ApiError(
            400,
            "bad_request",
            f"Enter a folder name of 1 to {FOLDER_NAME_MAX} characters, with no hidden characters.",
        )
    return cleaned


def _audit(
    conn: Connection,
    user: CurrentUser,
    action: str,
    folder_id: str,
    details: Mapping[str, audit.DetailValue] | None = None,
) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="folder",
        target_id=folder_id,
        details=details,
    )


@router.get("/folders")
def list_folders(user: User, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        rows = folders_sql.list_folders(conn, user.customer_id)
    return {"items": [folder(row) for row in rows]}


@router.post("/folders", status_code=201)
def create_folder(body: NewFolderBody, user: User, context: Context) -> dict[str, Any]:
    name = _name(body.name)
    _admin_for_departments(user, body.kind)
    parent_id = None if body.parent_id is None else str(body.parent_id)
    with _refused(), context.db.customer(user.customer_id) as conn:
        row = folders_sql.create_folder(
            conn,
            user.customer_id,
            name=name,
            parent_id=parent_id,
            created_by=user.user_id,
            kind=body.kind,
            sort_order=body.sort_order,
        )
        _audit(conn, user, "folder.create", row.id, {"parent_id": parent_id})
    return folder(row)


@router.patch("/folders/{folder_id}")
def update_folder(
    folder_id: uuid.UUID, body: FolderChangesBody, user: User, context: Context
) -> dict[str, Any]:
    given = body.model_fields_set
    if not given:
        raise ApiError(400, "bad_request", "Say what to change: the name, the place, or both.")
    name = _name(body.name) if "name" in given else None
    target = str(folder_id)
    parent_id = None if body.parent_id is None else str(body.parent_id)
    with _refused(), context.db.customer(user.customer_id) as conn:
        path = folders_sql.folder_path(conn, user.customer_id, target)
        if not path:
            raise folders_sql.FolderNotFound()
        _admin_for_departments(user, path[-1].kind, body.kind)
        # The body names at least one change (checked above), so ``row`` is always set.
        # The move goes first: it takes the tree lock before anything is written.
        if "parent_id" in given:
            row = folders_sql.move_folder(conn, user.customer_id, target, parent_id=parent_id)
            _audit(conn, user, "folder.move", target, {"parent_id": parent_id})
        if name is not None:
            row = folders_sql.rename_folder(conn, user.customer_id, target, name=name)
            _audit(conn, user, "folder.rename", target)
        if body.kind is not None or "sort_order" in given:
            row = folders_sql.set_kind(
                conn,
                user.customer_id,
                target,
                kind=body.kind,
                sort_order=body.sort_order,
                order="sort_order" in given,
            )
            _audit(conn, user, "folder.kind", target, {"kind": row.kind, "sort_order": row.sort_order})
    return folder(row)


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: uuid.UUID, user: User, context: Context) -> Response:
    target = str(folder_id)
    with _refused(), context.db.customer(user.customer_id) as conn:
        path = folders_sql.folder_path(conn, user.customer_id, target)
        _admin_for_departments(user, path[-1].kind if path else None)
        folders_sql.delete_folder(conn, user.customer_id, target)
        _audit(conn, user, "folder.delete", target)
    return Response(status_code=204)


@router.get("/folders/{folder_id}/activity")
def get_folder_activity(
    folder_id: uuid.UUID,
    user: User,
    context: Context,
    limit: ActivityLimit = 50,
    cursor: ActivityCursor = None,
) -> dict[str, Any]:
    """File management A5: ``{items: [AuditEntry], next_cursor}``, what happened in the folder
    and every folder below it, newest first (document, version and folder actions only).
    Another customer's folder, or none, is 404."""
    with context.db.customer(user.customer_id) as conn:
        subtree = folders_sql.descendant_ids(conn, user.customer_id, str(folder_id))
        if not subtree:
            raise ApiError(404, "not_found")
        filters = audit.Filters(folder_ids=tuple(subtree))
        return timeline(conn, user.customer_id, filters, limit=limit, cursor=cursor)
