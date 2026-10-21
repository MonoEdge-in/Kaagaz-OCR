"""The tag routes, and the tag set of a document (PRD 1 F2).

Why some choices:

* **Anyone** in the customer may create a tag and tag a document. **Only an
  admin session** (the admin sign-in screen) may delete a tag, as for deleting
  a document forever: it comes off every document and can't be undone.
* **Trash means deleted:** a document in Trash can't be tagged (409 ``in_trash``).
* **PUT replaces the whole set** on the document's family: every version gets
  the same tags (plan 9.3), under the family lock (``kaagaz.db.tags``). One
  unknown tag, or another customer's, is 404 and nothing is applied. At most
  50 ids, checked here by pydantic.
* Errors: ``TagError`` carries the API code. 404 for a tag or document that
  isn't there (another customer's included, never 403), 409 for a taken name.
* Each change writes one audit row with ids and counts only, never a tag name.
"""

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from kaagaz.api.auth import AdminWithTwoStep, CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.names import TAG_NAME_MAX, clean_name
from kaagaz.api.views import tag
from kaagaz.db import audit, listing
from kaagaz.db import tags as tags_sql
from kaagaz.db.trash import InTrash

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MAX_TAGS_PER_DOCUMENT = 50


class NewTagBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class TagSetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_ids: list[uuid.UUID] = Field(max_length=MAX_TAGS_PER_DOCUMENT)


@contextmanager
def _refused() -> Iterator[None]:
    """A tag change the helpers refused becomes its API error."""
    try:
        yield
    except tags_sql.TagError as error:
        raise ApiError(404 if error.code == "not_found" else 409, error.code) from None


def _audit(
    conn: Connection,
    user: CurrentUser,
    action: str,
    target_type: str,
    target_id: str,
    details: Mapping[str, audit.DetailValue] | None = None,
) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )


@router.get("/tags")
def list_tags(user: User, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        rows = tags_sql.list_tags(conn, user.customer_id)
    return {"items": [tag(row) for row in rows]}


@router.post("/tags", status_code=201)
def create_tag(body: NewTagBody, user: User, context: Context) -> dict[str, Any]:
    name = clean_name(body.name, max_length=TAG_NAME_MAX)
    if name is None:
        raise ApiError(400, "bad_request", f"Enter a tag name of 1 to {TAG_NAME_MAX} characters.")
    with _refused(), context.db.customer(user.customer_id) as conn:
        row = tags_sql.create_tag(conn, user.customer_id, name=name, created_by=user.user_id)
        _audit(conn, user, "tag.create", "tag", row.id)
    return tag(row)


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: uuid.UUID, user: AdminWithTwoStep, context: Context) -> Response:
    target = str(tag_id)
    with context.db.customer(user.customer_id) as conn:
        if not tags_sql.delete_tag(conn, user.customer_id, target):
            raise ApiError(404, "not_found")
        _audit(conn, user, "tag.delete", "tag", target)
    return Response(status_code=204)


@router.put("/documents/{document_id}/tags")
def set_document_tags(
    document_id: uuid.UUID, body: TagSetBody, user: User, context: Context
) -> dict[str, Any]:
    target = str(document_id)
    wanted = [str(tag_id) for tag_id in body.tag_ids]
    try:
        with _refused(), context.db.customer(user.customer_id) as conn:
            rows = tags_sql.set_document_tags(conn, user.customer_id, target, wanted)
            listing.set_details(conn, user.customer_id, target, by=user.user_id)  # File management O3
            _audit(conn, user, "document.tags", "document", target, {"tag_count": len(rows)})
    except InTrash:  # Trash means deleted: nothing was changed
        raise ApiError(409, "in_trash", "This document is in the Trash. Restore it first.") from None
    return {"items": [tag(row) for row in rows]}
