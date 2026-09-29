"""The folder routes: list, create, rename or move, and delete (PRD 1 F1).

Why some choices:

* **Thin.** The tree rules (at most 5 levels, no cycles, one name per parent
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
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection

from kaagaz.api.auth import CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.names import FOLDER_NAME_MAX, clean_name
from kaagaz.api.views import folder
from kaagaz.db import audit
from kaagaz.db import folders as folders_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]


class NewFolderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    parent_id: uuid.UUID | None = None  # None: at the top level


class FolderChangesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    parent_id: uuid.UUID | None = None  # an explicit null: the top level; left out: don't move


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
        raise ApiError(400, "bad_request", f"Enter a folder name of 1 to {FOLDER_NAME_MAX} characters.")
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
    parent_id = None if body.parent_id is None else str(body.parent_id)
    with _refused(), context.db.customer(user.customer_id) as conn:
        row = folders_sql.create_folder(
            conn, user.customer_id, name=name, parent_id=parent_id, created_by=user.user_id
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
        # The body names at least one of the two (checked above), so ``row`` is always set.
        # The move goes first: it takes the tree lock before anything is written.
        if "parent_id" in given:
            row = folders_sql.move_folder(conn, user.customer_id, target, parent_id=parent_id)
            _audit(conn, user, "folder.move", target, {"parent_id": parent_id})
        if name is not None:
            row = folders_sql.rename_folder(conn, user.customer_id, target, name=name)
            _audit(conn, user, "folder.rename", target)
    return folder(row)


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: uuid.UUID, user: User, context: Context) -> Response:
    target = str(folder_id)
    with _refused(), context.db.customer(user.customer_id) as conn:
        folders_sql.delete_folder(conn, user.customer_id, target)
        _audit(conn, user, "folder.delete", target)
    return Response(status_code=204)
