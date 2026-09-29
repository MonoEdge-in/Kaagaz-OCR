"""The document routes: list, upload, detail, the original file, page images,
spreadsheet cells and delete.

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
"""

import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import Connection, RowMapping
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from kaagaz.api.auth import CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.upload_guard import check_upload_request
from kaagaz.api.views import document_summary, extracted_value, page_summary
from kaagaz.db import admin as admin_sql
from kaagaz.db import audit, listing
from kaagaz.db.documents import PgDocuments
from kaagaz.ingestion.errors import RejectCode, UploadRejected
from kaagaz.ingestion.messages import over_every_limit_message, too_large_message
from kaagaz.ingestion.policy import UploadPolicy, limits_by_type
from kaagaz.ingestion.ports import BlobNotFound
from kaagaz.ingestion.service import UploadService
from kaagaz.settings import Settings

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MIB = 1024 * 1024
MAX_SHEET_ROWS = 500
MAX_NAME = 255

DocumentStatusParam = Literal["received", "clean", "rejected", "needs_reader", "ready", "failed"]

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
_REJECT_STATUS = {
    RejectCode.EMPTY_FILE: 400,
    RejectCode.UNSUPPORTED_TYPE: 400,
    RejectCode.TOO_LARGE: 413,
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def display_name(filename: str | None) -> str:
    """The name shown for an upload: path stripped, control characters removed,
    at most 255 characters, "document" if nothing is left."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL.sub("", name).strip()[:MAX_NAME].strip()
    return name or "document"


@router.get("/documents")
def list_documents(
    user: User,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: DocumentStatusParam | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        rows, total = listing.list_documents(
            conn,
            user.customer_id,
            limit=limit,
            offset=offset,
            status=status,
            name_contains=q.strip() if q else None,
        )
    max_pages = context.settings.max_pages
    return {"items": [document_summary(row, max_pages=max_pages) for row in rows], "total": total}


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


def _refusal_message(rejected: UploadRejected, policy: UploadPolicy) -> str | None:
    """The words for a refused upload; None means the code's usual message."""
    if rejected.code is not RejectCode.TOO_LARGE:
        return None
    if rejected.kind is None or rejected.size is None or rejected.limit is None:
        return over_every_limit_message(policy.largest_limit)  # refused before its type was known
    return too_large_message(rejected.kind, rejected.size, rejected.limit)


def _accept(
    context: AppContext, user: CurrentUser, upload: UploadFile, policy: UploadPolicy
) -> dict[str, Any]:
    service = UploadService(policy, PgDocuments(context.db), context.storage, context.queue)
    try:
        result = service.accept(
            user.customer_id,
            upload.file,
            display_name=display_name(upload.filename),
            uploaded_by=user.user_id,
        )
    except UploadRejected as rejected:
        message = _refusal_message(rejected, policy)
        raise ApiError(_REJECT_STATUS[rejected.code], rejected.code.value, message) from None
    with context.db.customer(user.customer_id) as conn:
        if not result.duplicate:
            audit.record(
                conn,
                user.customer_id,
                actor_user_id=user.user_id,
                action="document.upload",
                target_type="document",
                target_id=result.document_id,
            )
        row = listing.get_document(conn, user.customer_id, result.document_id)
    if row is None:  # deleted in the moment since; nothing to show
        raise ApiError(404, "not_found")
    summary = document_summary(row, max_pages=context.settings.max_pages)
    return {"document": summary, "duplicate": result.duplicate}


@router.post("/documents")
async def upload_document(request: Request, user: User, context: Context) -> JSONResponse:
    policy = await run_in_threadpool(_upload_policy, context, user)
    check_upload_request(request, policy)
    async with request.form(max_files=1, max_fields=10) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiError(400, "bad_request", "Choose a file to upload.")
        body = await run_in_threadpool(_accept, context, user, upload, policy)
    return JSONResponse(body, status_code=200 if body["duplicate"] else 201)


def _document_or_404(conn: Connection, user: CurrentUser, document_id: uuid.UUID) -> RowMapping:
    row = listing.get_document(conn, user.customer_id, str(document_id))
    if row is None:
        raise ApiError(404, "not_found")
    return row


@router.get("/documents/{document_id}")
def get_document(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        row = _document_or_404(conn, user, document_id)
        pages = listing.list_pages(conn, user.customer_id, str(document_id))
        values = listing.list_values(conn, user.customer_id, str(document_id))
    return {
        **document_summary(row, max_pages=context.settings.max_pages),
        "pages": [page_summary(page) for page in pages],
        "values": [extracted_value(value) for value in values],
    }


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


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, user: User, context: Context) -> Response:
    with context.db.customer(user.customer_id) as conn:
        row = _document_or_404(conn, user, document_id)
        uploader = None if row["uploaded_by"] is None else str(row["uploaded_by"])
        if user.role != "admin" and uploader != user.user_id:
            raise ApiError(403, "forbidden")
        listing.delete_document(conn, user.customer_id, str(document_id))
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="document.delete",
            target_type="document",
            target_id=str(document_id),
            details={"status": row["status"]},
        )
        # Inside the transaction: if storage cannot be reached, the row stays and
        # the admin can simply try again.
        context.storage.delete(user.customer_id, str(document_id))
    return Response(status_code=204)
