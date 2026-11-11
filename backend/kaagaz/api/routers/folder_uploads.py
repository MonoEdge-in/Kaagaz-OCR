"""Upload a whole folder (#484, Tushar, 3 Nov 2026): plan its folders, register its files, read
its progress. The files themselves go through ``POST /api/documents`` with
``?folder_upload_item_id=`` (``routers/documents.py``), so the virus scan, the upload limits,
batches and duplicates stay exactly as for any upload.

* ``POST /folder-uploads`` (``PlanBody``, at most MAX_PLAN_BYTES, checked before it is parsed):
  makes or merges the picked folder's tree under the tree lock, in one transaction
  (``db/folder_uploads.py``), and returns the folder ids in the list's order. Any role may plan
  into a folder (no per-folder rights: any signed-in user may change folders); "As a new
  department" is an admin's. Every folder and file name follows ``names.upload_name``. Audited
  once (``folder.upload``, counts and ids only) on the first plan and on a re-plan that made
  folders. One user's uploads send at a time per organisation (409 ``upload_in_progress``, until
  the other has been quiet for 30 minutes) and one plan is built at a time (429 ``plan_busy``,
  ``Retry-After: 2``). A plan whose bytes would take the organisation past its storage limit is
  413 ``storage_full`` before any folder is made (#516, ``documents.storage_full``); a re-plan
  counts only the bytes it still has to send (not a file stored, a duplicate or failed), the
  browser leaves out the files it refuses, and "Add new files only" is not checked there (its
  bytes hold the files already in Kaagaz, which cost nothing): each new file is, as it is sent.
* ``POST /folder-uploads/{id}/items`` (at most MAX_REGISTER_BYTES, checked before it is parsed):
  a page of at most 1,000 files; each gets its folder and batch now (``db/folder_upload_items.py``).
  A file with an unsafe name is registered failed ``bad_name``, never a 400 for the page.
* "Add new files only": the plan says ``only_new`` (its declared counts checked against the
  ceilings, not the organisation's limits; ``folder.upload`` says it), and each file Kaagaz
  could take comes with the browser's ``sha256``. A file already in this organisation (that
  hash and size) comes back ``duplicate`` with its document and is never sent; the hash is
  only looked up, never kept, and moves or stores nothing. The page stays on the general
  per-user rate limit, at most 1,000 hashes a call.
* ``GET /folder-uploads/previous?source_name=``: this user's latest upload of a folder of that
  name whose root folder is still there, so "Add new files only" can offer the same place.
* ``GET /folder-uploads`` (this user's of the last 7 days, counts by state only),
  ``GET /folder-uploads/{id}`` (also where its stored files' reading is) and
  ``GET /folder-uploads/{id}/items?view=waiting|attention|duplicate|all&after=-1&limit=500``.
  Another user's or customer's upload is 404.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Connection
from starlette.concurrency import run_in_threadpool

from kaagaz.api.auth import CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.names import DOCUMENT_NAME_MAX, FOLDER_NAME_MAX, shown_upload_name, upload_name
from kaagaz.api.routers.documents import same_user, storage_full, user_upload_policy
from kaagaz.api.views import folder_upload_item, held_message, iso
from kaagaz.db import audit
from kaagaz.db import folder_upload_items as items_sql
from kaagaz.db.folder_upload_items import NewItem, Summary
from kaagaz.db.folder_uploads import (
    GIB,
    MOST_FILES,
    MOST_FOLDERS,
    MOST_GB,
    FolderUploadLimits,
    Plan,
    PlannedFolder,
    effective_limits,
    plan_folders,
)
from kaagaz.db.folders import MAX_DEPTH, FolderError
from kaagaz.db.storage_limits import storage_room

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MAX_PLAN_BYTES = 8 * 1024 * 1024  # 50,000 folders' names fit with room to spare
REGISTER_PAGE = 1000
# A register page's body (production QA S6: it was parsed whole up to Caddy's 60 MB). A full page
# of 1,000 files with 255-character Devanagari names and "Add new files only" hashes is about 1 MB.
MAX_REGISTER_BYTES = 2 * 1024 * 1024
_BAD_NAME = "A folder name is empty, longer than 100 characters, or holds a character Kaagaz can't keep."


class PlanFolderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=4 * FOLDER_NAME_MAX)  # checked by upload_name
    parent: int | None = Field(default=None, ge=0)


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    user_id: str
    target: Literal["department", "folder"]
    parent_folder_id: uuid.UUID | None = None  # the top level when None ("folder" only)
    merge: bool = True  # False: "Keep separate"
    folders: list[PlanFolderBody] = Field(min_length=1, max_length=MOST_FOLDERS)
    file_count: int = Field(ge=0, le=MOST_FILES)
    total_bytes: int = Field(ge=0, le=MOST_GB * GIB)
    skipped_count: int = Field(ge=0, le=2**31 - 1)
    only_new: bool = False  # "Add new files only": the files already in Kaagaz aren't sent


class ItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(max_length=4 * DOCUMENT_NAME_MAX)  # checked by upload_name
    folder_id: uuid.UUID
    size: int = Field(ge=0, le=MOST_GB * GIB)
    refused: Literal["unsupported_type"] | None = None  # too_large and empty_file: the server's
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")  # the browser's: looked up only


class RegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    items: list[ItemBody] = Field(min_length=1, max_length=REGISTER_PAGE)


def _words(code: str, limits: FolderUploadLimits) -> str:
    return {
        "too_many_files": f"A folder upload can hold at most {limits.max_files:,} files.",
        "too_large_upload": f"A folder upload can hold at most {limits.max_bytes // GIB:,} GB.",
        "too_many_folders": f"A folder upload can make at most {limits.max_folders:,} folders.",
        "plan_changed": "This upload was started with a different folder or place. Start a new upload.",
        "folder_too_deep": f"This folder would go deeper than {MAX_DEPTH} levels here.",
        "upload_in_progress": "Another person's folder upload is still sending in this organisation."
        " Try again when it has finished.",
    }.get(code, "")


async def _body(
    request: Request, most: int, words: str = "This folder is too big to plan in one go."
) -> bytes:
    """The request body, refused (413, ``words``) past ``most`` bytes before it is read whole or
    parsed."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > most:
        raise ApiError(413, "too_large", words)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > most:
            raise ApiError(413, "too_large", words)
    return bytes(body)


@router.post("/folder-uploads")
async def plan_upload(request: Request, user: User, context: Context) -> dict[str, Any]:
    raw = await _body(request, MAX_PLAN_BYTES)
    try:
        body = PlanBody.model_validate_json(raw)
    except ValidationError:
        raise ApiError(400, "bad_request") from None
    return await run_in_threadpool(_plan, context, user, body)


def _plan(context: AppContext, user: CurrentUser, body: PlanBody) -> dict[str, Any]:
    same_user(body.user_id, user)
    if body.target == "department" and user.role != "admin":
        raise ApiError(403, "forbidden", "Only an admin can add a department.")
    if body.target == "department" and body.parent_folder_id is not None:
        raise ApiError(400, "bad_request")
    folders = []
    for index, entry in enumerate(body.folders):
        if (entry.parent is None) != (index == 0) or (entry.parent is not None and entry.parent >= index):
            raise ApiError(400, "bad_request", "Each folder must come after the folder it is in.")
        name = upload_name(entry.name, max_length=FOLDER_NAME_MAX)
        if name is None:
            raise ApiError(400, "bad_folder_name", _BAD_NAME)
        folders.append(PlannedFolder(name, entry.parent))
    customer_id = user.customer_id
    parent = None if body.parent_folder_id is None else str(body.parent_folder_id)
    try:
        with context.db.customer(customer_id) as conn:
            limits = effective_limits(conn, customer_id)
            if not body.only_new:  # #516: the bytes not sent yet must fit (module notes)
                room = storage_room(conn, customer_id)
                unsent = body.total_bytes - items_sql.sent_bytes(
                    conn, customer_id, str(body.id), user_id=user.user_id
                )
                if room.used_bytes + unsent > room.limit_bytes:
                    raise storage_full(room)
            plan = _plan_and_audit(conn, user, body, folders, parent, limits)
    except FolderError as refused:  # out of the block first: the plan is rolled back
        if refused.code == "not_found":
            raise ApiError(404, "not_found") from None
        if refused.code == "plan_busy":
            raise ApiError(
                429,
                "plan_busy",
                "Another folder upload is being planned in this organisation. Trying again in a moment.",
                headers={"Retry-After": "2"},
            ) from None
        raise ApiError(409, refused.code, _words(refused.code, limits) or None) from None
    return {
        "id": plan.upload_id,
        "root_folder_id": plan.root_folder_id,
        "root_name": plan.root_name,
        "folder_ids": plan.folder_ids,
        "created": plan.created,
        "merged": plan.merged,
    }


def _plan_and_audit(
    conn: Connection,
    user: CurrentUser,
    body: PlanBody,
    folders: list[PlannedFolder],
    parent: str | None,
    limits: FolderUploadLimits,
) -> Plan:
    """The plan, and its audit row (counts and ids only) on the first plan or one that made folders."""
    plan = plan_folders(
        conn,
        user.customer_id,
        upload_id=str(body.id),
        user_id=user.user_id,
        target=body.target,
        parent_folder_id=parent,
        merge=body.merge,
        folders=folders,
        file_count=body.file_count,
        total_bytes=body.total_bytes,
        skipped_count=body.skipped_count,
        limits=limits,
        only_new=body.only_new,
    )
    if plan.new or plan.created:
        details: dict[str, audit.DetailValue] = {
            "folder_upload_id": plan.upload_id,
            "files": body.file_count,
            "bytes": body.total_bytes,
            "folders_created": plan.created,
            "folders_merged": plan.merged,
            "skipped": body.skipped_count,
            "target": body.target,
            "only_new": body.only_new,
        }
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="folder.upload",
            target_type="folder",
            target_id=plan.root_folder_id,
            details=details,
        )
    return plan


@router.post("/folder-uploads/{upload_id}/items")
async def register_items(
    upload_id: uuid.UUID, request: Request, user: User, context: Context
) -> dict[str, Any]:
    raw = await _body(request, MAX_REGISTER_BYTES, "This page of files is too big to register in one go.")
    try:
        body = RegisterBody.model_validate_json(raw)
    except ValidationError:
        raise ApiError(400, "bad_request") from None
    return await run_in_threadpool(_register, context, user, upload_id, body)


def _register(
    context: AppContext, user: CurrentUser, upload_id: uuid.UUID, body: RegisterBody
) -> dict[str, Any]:
    same_user(body.user_id, user)
    items = []
    for entry in body.items:
        name = upload_name(entry.name, max_length=DOCUMENT_NAME_MAX)
        refused: str | None = entry.refused
        if name is None:
            name, refused = shown_upload_name(entry.name, max_length=DOCUMENT_NAME_MAX), "bad_name"
        items.append(NewItem(entry.path_key, name, str(entry.folder_id), entry.size, refused, entry.sha256))
    largest = user_upload_policy(context, user).largest_limit
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        limits = effective_limits(conn, customer_id)
        try:
            rows = items_sql.register_items(
                conn,
                customer_id,
                str(upload_id),
                user_id=user.user_id,
                items=items,
                batch_cap=context.settings.max_files_per_batch,
                largest_limit=largest,
                limits=limits,
            )
        except items_sql.ItemsRefused as refused:
            status = {"not_found": 404, "bad_request": 400}.get(refused.code, 409)
            raise ApiError(status, refused.code, _words(refused.code, limits) or None) from None
    return {"items": [folder_upload_item(row) for row in rows]}


@router.get("/folder-uploads")
def list_folder_uploads(user: User, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        rows = items_sql.summaries(conn, user.customer_id, user_id=user.user_id)
    return {"items": [summary_json(row) for row in rows]}


@router.get("/folder-uploads/previous")
def previous_folder_upload(
    source_name: Annotated[str, Query(max_length=4 * FOLDER_NAME_MAX)], user: User, context: Context
) -> dict[str, Any]:
    """``{previous: {id, target, parent_folder_id, root_folder_id, created_at} | null}``: this
    user's latest upload of a folder of this name whose root folder is still there."""
    name = upload_name(source_name, max_length=FOLDER_NAME_MAX)
    found = None
    if name is not None:
        with context.db.customer(user.customer_id) as conn:
            found = items_sql.previous(conn, user.customer_id, user_id=user.user_id, source_name=name)
    if found is None:
        return {"previous": None}
    return {
        "previous": {
            "id": found.id,
            "target": found.target,
            "parent_folder_id": found.parent_folder_id,
            "root_folder_id": found.root_folder_id,
            "created_at": iso(found.created_at),
        }
    }


@router.get("/folder-uploads/{upload_id}")
def get_folder_upload(upload_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        row = items_sql.progress(conn, user.customer_id, str(upload_id), user_id=user.user_id)
    if row is None:
        raise ApiError(404, "not_found")
    return summary_json(row)


@router.get("/folder-uploads/{upload_id}/items")
def list_folder_upload_items(
    upload_id: uuid.UUID,
    user: User,
    context: Context,
    view: items_sql.View = "all",
    after: Annotated[int, Query(ge=-1)] = -1,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> dict[str, Any]:
    """``{items: [FolderUploadItem], next_after}``: one page in order; ``next_after`` is the
    ``after`` of the next page, null after the last."""
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        if not items_sql.owned(conn, customer_id, str(upload_id), user_id=user.user_id):
            raise ApiError(404, "not_found")
        rows = items_sql.list_items(
            conn, customer_id, str(upload_id), view=view, after_seq=after, limit=limit
        )
    return {
        "items": [folder_upload_item(row) for row in rows],
        "next_after": rows[-1].seq if len(rows) == limit else None,
    }


def summary_json(row: Summary) -> dict[str, Any]:
    """``FolderUploadSummary``; reading, ready, problem and held are null in the list. ``held``:
    stored files the reading pause holds (#478), with its words (``held_message``)."""
    return {
        "id": row.id,
        "root_folder_id": row.root_folder_id,
        "root_name": row.root_name,
        "source_name": row.source_name,
        "target": row.target,
        "parent_folder_id": row.parent_folder_id,
        "merge": row.merge,
        "created_at": iso(row.created_at),
        "file_count": row.file_count,
        "total_bytes": row.total_bytes,
        "skipped_count": row.skipped_count,
        "registered": row.registered,
        "waiting": row.waiting,
        "stored": row.stored,
        "duplicate": row.duplicate,
        "failed": row.failed,
        "reading": row.reading,
        "ready": row.ready,
        "problem": row.problem,
        "held": row.held,
        "held_message": held_message(row.held_reason),
    }
