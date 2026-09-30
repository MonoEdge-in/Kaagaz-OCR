"""The document routes: list, upload, a new version, detail, rename or move,
the original file, page images, spreadsheet cells, and Trash (move to Trash,
restore, delete forever).

Why some choices:

* **Upload streams.** The request is checked before the body is parsed
  (``upload_guard.py``), and the parsed part's file object goes straight into
  ``UploadService.accept``, which reads it once with a hard limit and then
  checks the limit of the file's own type. The file is never read whole into
  memory here.
* **The file name** has its path and control characters stripped and is cut to
  255 characters before it is stored. The raw name is never stored or logged.
* **The original file** is served only once the virus check passed (clean,
  ready or needs_reader). A rejected file is never served, and neither is one
  that was never scanned (received, or failed before the scan could finish).
  It goes out with ``Content-Security-Policy: sandbox`` and nosniff, and the
  name ``document.<ext>``, so nothing in it can run on our origin.
* Another customer's id is simply "not found": every lookup runs as the
  signed-in customer.
* **Folder and tags belong to the family** (plan 9.3). PATCH ``folder_id``
  moves every version, under the family lock; a new name is for this version
  only, cleaned by ``clean_document_name`` (no path strip). A missing key
  means "don't change"; an explicit null folder means the top level.
* **Upload into a folder** (PRD 1 U3): the folder is checked before anything is
  stored or queued. If it is deleted in the moment after, the insert is refused
  by the foreign key, the stored file is removed again, and the reply is 404.
* **The list filters** are picked as fixed SQL fragments (``db/listing.py``).
  An unknown folder or tag is 404, another customer's included. Upload days
  are read in the customer's timezone (TIMEZONE), and the end day is not
  included.
* **Trash** (PRD 1 F5) acts on the whole family, under the family lock. A
  delete only moves it to Trash; nothing is removed and storage is not touched,
  so a restore brings everything back. Because both act on every version, only
  an admin or a user who uploaded every version may do them (``can_delete`` in
  the summary, kaagaz/api/views.py). Only an admin session (the admin sign-in
  screen) can delete forever, and only a family in Trash (409 ``not_in_trash``
  otherwise); its files go inside the same transaction, so if storage cannot be
  reached every row stays and the admin can try again.
* **Trash means deleted.** A family in Trash can't be renamed, moved, tagged
  or given a new version: 409 ``in_trash``, checked under the family lock.
* **Read again** (PRD 2 X1) is for a reviewer or an admin: a ready, failed or
  unreadable document out of Trash goes back to the start and its job is
  queued, inside one transaction under the family lock, so a queue that is down
  changes nothing (503). Anything else is 409 ``not_reprocessable``; another
  customer's id is 404. A customer may start REPROCESS_PER_HOUR in an hour, and
  one document once in 10 minutes (429 ``rate_limited``, security A8). The
  pipeline reads it again from the scan on, and this customer's remembered fixes
  are applied again; other fixes are lost, which the page says first.
* **Versions** (PRD 1 F6) have the checks and replies of upload. A family not
  there is 404, and one in Trash 409, checked before storing. Under the lock it
  is checked again: one moved to Trash in the moment between is 404, and the
  stored file is removed. The list shows each family's current version, with a newer one
  that is still being read or failed beside it (``newer_version``); by id, any
  version opens. A duplicate in Trash says ``in_trash: true``.
"""

import uuid
from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, RowMapping
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from kaagaz.api.auth import AdminWithTwoStep, CurrentUser, Reviewer, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.names import CONTROL_CHARACTERS, DOCUMENT_NAME_MAX, clean_document_name
from kaagaz.api.upload_guard import check_upload_request
from kaagaz.api.views import (
    can_delete,
    document_folder,
    document_summary,
    document_version,
    extracted_value,
    page_summary,
)
from kaagaz.db import admin as admin_sql
from kaagaz.db import audit, listing, trash
from kaagaz.db import folders as folders_sql
from kaagaz.db import tags as tags_sql
from kaagaz.db.documents import (
    NotInTrash,
    NotReprocessable,
    PgDocuments,
    ReprocessLimited,
    delete_family_forever,
    mark_for_reprocess,
)
from kaagaz.ingestion import sniff
from kaagaz.ingestion.errors import RejectCode, UploadRejected
from kaagaz.ingestion.messages import over_every_limit_message, too_large_message
from kaagaz.ingestion.policy import UploadPolicy, limits_by_type
from kaagaz.ingestion.ports import BlobNotFound, Job, UnknownFamily, UnknownFolder
from kaagaz.ingestion.service import UploadResult, UploadService
from kaagaz.settings import Settings

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MIB = 1024 * 1024
MAX_SHEET_ROWS = 500
MAX_TAG_FILTERS = 50

DocumentStatusParam = Literal["received", "clean", "rejected", "needs_reader", "ready", "failed"]
DetectedTypeParam = Literal["pdf", "png", "jpeg", "tiff", "zip", "ole2", "text"]
IsoDay = Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")]

_SERVABLE = frozenset({"clean", "ready", "needs_reader"})
_CONTENT_TYPES = {
    "pdf": ("application/pdf", "pdf"),
    "png": ("image/png", "png"),
    "jpeg": ("image/jpeg", "jpg"),
    "tiff": ("image/tiff", "tif"),
    "zip": ("application/octet-stream", "zip"),
    "ole2": ("application/octet-stream", "bin"),
    "text": ("text/plain; charset=utf-8", "csv"),
}
_REPROCESS_LIMITED = {
    "hourly": "Too many documents were sent to be read again in the last hour. Please try again later.",
    "cooldown": (
        "This document was sent to be read again less than 10 minutes ago. Please wait and try again."
    ),
}
_REJECT_STATUS = {
    RejectCode.EMPTY_FILE: 400,
    RejectCode.UNSUPPORTED_TYPE: 400,
    RejectCode.TOO_LARGE: 413,
}


def display_name(filename: str | None) -> str:
    """The name shown for an upload: path stripped, control characters removed,
    at most 255 characters, "document" if nothing is left."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = CONTROL_CHARACTERS.sub("", name).strip()[:DOCUMENT_NAME_MAX].strip()
    return name or "document"


def day_start(day: str, timezone: str) -> datetime:
    """The first instant of ``day`` (YYYY-MM-DD) in ``timezone``, the customer's
    zone from the settings, never the server's. ValueError if it is not a real date."""
    return datetime.combine(date.fromisoformat(day), time.min, tzinfo=ZoneInfo(timezone))


def _folder_scope(
    conn: Connection, customer_id: str, folder_id: uuid.UUID | Literal["root"] | None, subfolders: bool
) -> list[str] | None:
    """The folders a list is narrowed to: None for no folder filter ("root" is
    ``top_level_only``), else the folder, or with ``subfolders`` its whole
    subtree. 404 for a folder this customer does not have. O(s log F) for the
    subtree's s folders."""
    if not isinstance(folder_id, uuid.UUID):
        return None
    subtree = folders_sql.descendant_ids(conn, customer_id, str(folder_id))
    if not subtree:
        raise ApiError(404, "not_found")
    return subtree if subfolders else [str(folder_id)]


@router.get("/documents")
def list_documents(
    user: User,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: DocumentStatusParam | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    folder_id: Annotated[uuid.UUID | Literal["root"] | None, Query()] = None,
    include_subfolders: bool = False,
    tag_id: Annotated[list[uuid.UUID] | None, Query(max_length=MAX_TAG_FILTERS)] = None,
    detected_type: DetectedTypeParam | None = None,
    uploaded_from: IsoDay = None,
    uploaded_to: IsoDay = None,
    trashed: bool = False,
) -> dict[str, Any]:
    """One page and its total. The top level with its subfolders is everything,
    so it filters nothing. At most four data queries, whatever the page size:
    the folder's subtree, the page, the count and the page's tags (one batch)."""
    zone = context.settings.timezone
    try:
        start = None if uploaded_from is None else day_start(uploaded_from, zone)
        end = None if uploaded_to is None else day_start(uploaded_to, zone)
    except ValueError:
        raise ApiError(400, "bad_request", "Use real dates, written YYYY-MM-DD.") from None
    customer_id = user.customer_id
    tag_ids = [str(tag) for tag in tag_id or []]
    with context.db.customer(customer_id) as conn:
        folder_ids = _folder_scope(conn, customer_id, folder_id, include_subfolders)
        if tag_ids and tags_sql.count_existing(conn, customer_id, tag_ids) != len(set(tag_ids)):
            raise ApiError(404, "not_found")  # a tag this customer does not have
        rows, total = listing.list_documents(
            conn,
            customer_id,
            limit=limit,
            offset=offset,
            status=status,
            name_contains=q.strip() if q else None,
            top_level_only=folder_id == "root" and not include_subfolders,
            folder_ids=folder_ids,
            tag_ids=tag_ids,
            detected_type=detected_type,
            uploaded_from=start,
            uploaded_to=end,
            trashed=trashed,
        )
        tags = tags_sql.tags_for_documents(conn, customer_id, [str(row["id"]) for row in rows])
    max_pages = context.settings.max_pages
    items = [document_summary(row, max_pages=max_pages, tags=tags[str(row["id"])], user=user) for row in rows]
    return {"items": items, "total": total}


def upload_policy(settings: Settings, customer_max_mb: int | None) -> UploadPolicy:
    """The limits for one customer's upload (PRD 1, U2): each type's own limit,
    lowered by the customer's own limit when an admin set one."""
    max_bytes = settings.max_upload_bytes
    if customer_max_mb is not None:
        max_bytes = min(max_bytes, customer_max_mb * MIB)
    return UploadPolicy(
        max_bytes=max_bytes,
        allowed_types=settings.allowed_types,
        type_limits=limits_by_type(
            pdf=settings.max_upload_bytes, image=settings.max_image_bytes, sheet=settings.max_sheet_bytes
        ),
    )


def _upload_policy(context: AppContext, user: CurrentUser) -> UploadPolicy:
    with context.db.customer(user.customer_id) as conn:
        row = admin_sql.get_settings(conn, user.customer_id)
    customer_max_mb = None if row is None or row["max_upload_mb"] is None else int(row["max_upload_mb"])
    return upload_policy(context.settings, customer_max_mb)


@router.get("/upload-limits")
def get_upload_limits(user: User, context: Context) -> dict[str, int]:
    """``UploadLimits``: the limits this user's uploads are checked against, each
    already lowered by the customer's own limit, so the browser can refuse a file
    before sending it (PRD 1 U2) and count a PDF's pages first (``max_pages``).
    The server still checks everything itself. One query."""
    policy = _upload_policy(context, user)
    return {
        "pdf_bytes": policy.limit_for(sniff.PDF),
        "image_bytes": policy.limit_for(sniff.PNG),
        "sheet_bytes": policy.limit_for(sniff.ZIP),
        "max_pages": context.settings.max_pages,
    }


def _refusal_message(rejected: UploadRejected, policy: UploadPolicy) -> str | None:
    """The words for a refused upload; None means the code's usual message."""
    if rejected.code is not RejectCode.TOO_LARGE:
        return None
    if rejected.kind is None or rejected.size is None or rejected.limit is None:
        return over_every_limit_message(policy.largest_limit)  # refused before its type was known
    return too_large_message(rejected.kind, rejected.size, rejected.limit)


def _form_folder_id(value: str | UploadFile | None) -> str | None:
    """The optional ``folder_id`` form field: None when it is not sent, else a UUID (400)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, "bad_request")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ApiError(400, "bad_request") from None


def _accept(
    context: AppContext, user: CurrentUser, upload: UploadFile, policy: UploadPolicy, folder_id: str | None
) -> dict[str, Any]:
    if folder_id is not None:
        with context.db.customer(user.customer_id) as conn:
            if not folders_sql.folder_path(conn, user.customer_id, folder_id):
                raise ApiError(404, "not_found")  # before anything is stored or queued
    service = UploadService(policy, PgDocuments(context.db), context.storage, context.queue)
    try:
        result = service.accept(
            user.customer_id,
            upload.file,
            display_name=display_name(upload.filename),
            uploaded_by=user.user_id,
            folder_id=folder_id,
        )
    except UploadRejected as rejected:
        message = _refusal_message(rejected, policy)
        raise ApiError(_REJECT_STATUS[rejected.code], rejected.code.value, message) from None
    except UnknownFolder:  # deleted since the check; the service removed the stored file
        raise ApiError(404, "not_found") from None
    return _upload_reply(context, user, result, action="document.upload")


def _upload_reply(
    context: AppContext,
    user: CurrentUser,
    result: UploadResult,
    *,
    action: str,
    details: Mapping[str, audit.DetailValue] | None = None,
) -> dict[str, Any]:
    """The reply to an upload or a new version: the document, and whether it was a
    duplicate and is in Trash. A new file (not a duplicate) is audited first."""
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        if not result.duplicate:
            audit.record(
                conn,
                customer_id,
                actor_user_id=user.user_id,
                action=action,
                target_type="document",
                target_id=result.document_id,
                details=details,
            )
        row = listing.get_document(conn, customer_id, result.document_id)
        tags = tags_sql.tags_for_documents(conn, customer_id, [result.document_id])
    if row is None:  # deleted in the moment since; nothing to show
        raise ApiError(404, "not_found")
    summary = document_summary(
        row, max_pages=context.settings.max_pages, tags=tags[result.document_id], user=user
    )
    in_trash = result.duplicate and row["deleted_at"] is not None
    return {"document": summary, "duplicate": result.duplicate, "in_trash": in_trash}


@router.post("/documents")
async def upload_document(request: Request, user: User, context: Context) -> JSONResponse:
    policy = await run_in_threadpool(_upload_policy, context, user)
    check_upload_request(request, policy)
    async with request.form(max_files=1, max_fields=10) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiError(400, "bad_request", "Choose a file to upload.")
        folder_id = _form_folder_id(form.get("folder_id"))
        body = await run_in_threadpool(_accept, context, user, upload, policy, folder_id)
    return JSONResponse(body, status_code=200 if body["duplicate"] else 201)


def _accept_version(
    context: AppContext, user: CurrentUser, upload: UploadFile, policy: UploadPolicy, of_document_id: str
) -> dict[str, Any]:
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        target = listing.get_document(conn, customer_id, of_document_id)
    if target is None:
        raise ApiError(404, "not_found")  # before anything is stored or queued
    if target["deleted_at"] is not None:
        raise ApiError(409, "in_trash", "This document is in the Trash. Restore it first.")
    service = UploadService(policy, PgDocuments(context.db), context.storage, context.queue)
    try:
        result = service.accept_version(
            customer_id,
            upload.file,
            of_document_id=of_document_id,
            display_name=display_name(upload.filename),
            uploaded_by=user.user_id,
        )
    except UploadRejected as rejected:
        message = _refusal_message(rejected, policy)
        raise ApiError(_REJECT_STATUS[rejected.code], rejected.code.value, message) from None
    except UnknownFamily:  # gone or moved to Trash since the check; the service removed the stored file
        raise ApiError(404, "not_found") from None
    details = {"family_id": str(target["family_id"])}
    return _upload_reply(context, user, result, action="document.version", details=details)


@router.post("/documents/{document_id}/versions")
async def upload_version(
    document_id: uuid.UUID, request: Request, user: User, context: Context
) -> JSONResponse:
    """A new version of the document's family (PRD 1 F6). Any user of the customer."""
    policy = await run_in_threadpool(_upload_policy, context, user)
    check_upload_request(request, policy)
    async with request.form(max_files=1, max_fields=10) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiError(400, "bad_request", "Choose a file to upload.")
        body = await run_in_threadpool(_accept_version, context, user, upload, policy, str(document_id))
    return JSONResponse(body, status_code=200 if body["duplicate"] else 201)


def _document_or_404(conn: Connection, user: CurrentUser, document_id: uuid.UUID) -> RowMapping:
    row = listing.get_document(conn, user.customer_id, str(document_id))
    if row is None:
        raise ApiError(404, "not_found")
    return row


@router.get("/documents/{document_id}")
def get_document(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        row = _document_or_404(conn, user, document_id)
        pages = listing.list_pages(conn, customer_id, target)
        values = listing.list_values(conn, customer_id, target)
        tags = tags_sql.tags_for_documents(conn, customer_id, [target])[target]
        folder = row["folder_id"]
        path = [] if folder is None else folders_sql.folder_path(conn, customer_id, str(folder))
        versions = listing.family_versions(conn, customer_id, str(row["family_id"]))
        fixes = listing.reviewer_fix_count(conn, customer_id, target)
    return {
        **document_summary(row, max_pages=context.settings.max_pages, tags=tags, user=user),
        "pages": [page_summary(page) for page in pages],
        "values": [extracted_value(value) for value in values],
        "folder": document_folder(path),
        "versions": [document_version(version) for version in versions],
        "reviewer_fixes": fixes,
        "summary": row["summary"],  # PRD 2 D3: the card's summary (PR 5); null until then
    }


@router.post("/documents/{document_id}/reprocess")
def reprocess_document(document_id: uuid.UUID, user: Reviewer, context: Context) -> dict[str, Any]:
    """Read the document again (PRD 2 X1), and return its summary, now ``received``.
    One transaction: the family lock, the change, the audit row, then the job."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _document_or_404(conn, user, document_id)
        try:
            previous = mark_for_reprocess(
                conn, customer_id, target, per_hour=context.settings.reprocess_per_hour
            )
        except NotReprocessable:
            raise ApiError(409, "not_reprocessable") from None
        except ReprocessLimited as limited:
            raise ApiError(429, "rate_limited", _REPROCESS_LIMITED[limited.reason]) from None
        if previous is None:  # deleted in the moment since
            raise ApiError(404, "not_found")
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="document.reprocess",
            target_type="document",
            target_id=target,
            details={"status": previous},
        )
        row = _document_or_404(conn, user, document_id)
        tags = tags_sql.tags_for_documents(conn, customer_id, [target])[target]
        reply = document_summary(row, max_pages=context.settings.max_pages, tags=tags, user=user)
        # Last, inside the transaction: a queue that is down rolls everything back (503).
        context.queue.enqueue(Job(customer_id, target))
    return reply


class DocumentChangesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    folder_id: uuid.UUID | None = None  # an explicit null: the top level; left out: don't move


@router.patch("/documents/{document_id}")
def update_document(
    document_id: uuid.UUID, body: DocumentChangesBody, user: User, context: Context
) -> dict[str, Any]:
    """Rename this version and/or move the whole family. Any user of the customer (PRD 1 F3)."""
    given = body.model_fields_set
    if not given:
        raise ApiError(400, "bad_request", "Say what to change: the name, the folder, or both.")
    name = None
    if "display_name" in given:
        name = clean_document_name(body.display_name or "")
        if name is None:
            raise ApiError(400, "bad_request", f"Enter a name of 1 to {DOCUMENT_NAME_MAX} characters.")
    customer_id, target = user.customer_id, str(document_id)
    folder_id = None if body.folder_id is None else str(body.folder_id)
    try:
        with context.db.customer(customer_id) as conn:
            if trash.lock_live_family(conn, customer_id, target) is None:
                raise ApiError(404, "not_found")
            if "folder_id" in given:
                listing.move_family(conn, customer_id, target, folder_id=folder_id)
                audit.record(
                    conn,
                    customer_id,
                    actor_user_id=user.user_id,
                    action="document.move",
                    target_type="document",
                    target_id=target,
                    details={"folder_id": folder_id},
                )
            if name is not None:
                listing.rename_document(conn, customer_id, target, name=name)
                audit.record(
                    conn,
                    customer_id,
                    actor_user_id=user.user_id,
                    action="document.rename",
                    target_type="document",
                    target_id=target,
                )
            row = _document_or_404(conn, user, document_id)
            tags = tags_sql.tags_for_documents(conn, customer_id, [target])[target]
    except folders_sql.FolderNotFound:  # not this customer's folder; nothing was changed
        raise ApiError(404, "not_found") from None
    except trash.InTrash:  # Trash means deleted: nothing was changed
        raise ApiError(409, "in_trash", "This document is in the Trash. Restore it first.") from None
    return document_summary(row, max_pages=context.settings.max_pages, tags=tags, user=user)


@router.get("/documents/{document_id}/file")
def get_file(document_id: uuid.UUID, user: User, context: Context) -> Response:
    with context.db.customer(user.customer_id) as conn:
        row = _document_or_404(conn, user, document_id)
    if row["status"] not in _SERVABLE:
        raise ApiError(404, "not_found")
    try:
        data = context.storage.get(user.customer_id, str(document_id))
    except BlobNotFound:
        raise ApiError(404, "not_found") from None
    content_type, extension = _CONTENT_TYPES.get(row["detected_type"], ("application/octet-stream", "bin"))
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="document.{extension}"',
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/documents/{document_id}/pages/{page_no}/image")
def get_page_image(document_id: uuid.UUID, page_no: int, user: User, context: Context) -> Response:
    with context.db.customer(user.customer_id) as conn:
        row = _document_or_404(conn, user, document_id)
    if row["status"] == "rejected" or page_no < 1:
        raise ApiError(404, "not_found")
    try:
        image = context.storage.get_page_image(user.customer_id, str(document_id), page_no)
    except BlobNotFound:
        raise ApiError(404, "not_found") from None
    return Response(
        content=image,
        media_type="image/webp",
        headers={"Content-Security-Policy": "sandbox", "Cache-Control": "private, no-store"},
    )


@router.get("/documents/{document_id}/pages/{page_no}/cells")
def get_sheet_cells(
    document_id: uuid.UUID,
    page_no: int,
    user: User,
    context: Context,
    row_from: Annotated[int, Query(ge=1)] = 1,
    row_to: Annotated[int, Query(ge=1)] = 200,
) -> dict[str, Any]:
    if row_to < row_from:
        raise ApiError(400, "bad_request")
    row_to = min(row_to, row_from + MAX_SHEET_ROWS - 1)
    with context.db.customer(user.customer_id) as conn:
        _document_or_404(conn, user, document_id)
        page = listing.get_page(conn, user.customer_id, str(document_id), page_no)
        if page is None or page["unit"] != "cells":
            raise ApiError(404, "not_found")
        cells = listing.sheet_cells(conn, user.customer_id, str(document_id), page_no, row_from, row_to)
    rows: list[dict[str, Any]] = []
    for cell in cells:
        if not rows or rows[-1]["row"] != cell["row_no"]:
            rows.append({"row": int(cell["row_no"]), "cells": []})
        rows[-1]["cells"].append({"column": int(cell["column_no"]), "text": cell["text"] or ""})
    return {"page_no": page_no, "total_rows": int(page["height"]), "rows": rows}


# --- Trash (PRD 1 F5) -------------------------------------------------------------


def _may_trash(user: CurrentUser, row: RowMapping) -> None:
    """Trash and restore act on every version, so the rule looks at every version's
    uploader, not only this one's (``can_delete``)."""
    if not can_delete(row, user):
        raise ApiError(403, "forbidden")


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, user: User, context: Context) -> Response:
    """Move the whole family to Trash. A family already there keeps its first time."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        row = _document_or_404(conn, user, document_id)
        _may_trash(user, row)
        if not trash.trash_document(conn, customer_id, target, by=user.user_id, now=context.clock()):
            raise ApiError(404, "not_found")
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="document.trash",
            target_type="document",
            target_id=target,
            details={"status": row["status"]},
        )
    return Response(status_code=204)


@router.post("/documents/{document_id}/restore")
def restore_document(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Bring the whole family back from Trash, and return this version's summary."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _may_trash(user, _document_or_404(conn, user, document_id))
        if not trash.restore_document(conn, customer_id, target):
            raise ApiError(404, "not_found")
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="document.restore",
            target_type="document",
            target_id=target,
        )
        row = _document_or_404(conn, user, document_id)
        tags = tags_sql.tags_for_documents(conn, customer_id, [target])[target]
    return document_summary(row, max_pages=context.settings.max_pages, tags=tags, user=user)


@router.delete("/documents/{document_id}/permanent", status_code=204)
def delete_document_forever(document_id: uuid.UUID, user: AdminWithTwoStep, context: Context) -> Response:
    """Delete every version of a family in Trash for good, rows and files (today's
    full delete). It can't be undone, so it needs an admin session whose admin has
    two-step sign-in on (``require_admin_with_two_step``)."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        try:
            versions = delete_family_forever(conn, context.storage, customer_id, target)
        except NotInTrash:
            raise ApiError(409, "not_in_trash") from None
        if versions is None:
            raise ApiError(404, "not_found")
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="document.delete",
            target_type="document",
            target_id=target,
            details={"versions": versions},
        )
    return Response(status_code=204)
