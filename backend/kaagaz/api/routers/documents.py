"""The document routes: list, upload, a new version, detail and its summary, rename or move,
the original file, page images, each page's scanned text, spreadsheet cells, and
Trash (move to Trash, restore, delete forever).

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
  the summary, kaagaz/api/views.py). Only an admin session with two-step sign-in,
  or a member or reviewer an admin allowed who typed their password in the last
  5 minutes (``CanDeleteForever``), can delete forever, and only a family in Trash (409 ``not_in_trash``
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
* **Read with** (model choice, council 11 Oct 2026, MS4, MS5): ``/upload-limits`` lists
  the organisation's usable models, the default first (``kaagaz/api/readers.py``); Auto
  alone in keyless mode or when the operator set none, so the select hides itself. An
  upload or a version may name one in the optional ``read_model`` form field: any other
  is 403 ``model_not_allowed`` before anything is stored. The model and who asked
  (``read_requested_by``, the user its scan spend is billed to) are written to the new
  document before its job is queued (``_ReadingQueue``), so the worker never reads it
  without them. The old ``ocr_choice`` field is still accepted and ignored (RT3), so old
  browsers don't break.
* **Read one page again** (PRD 5 F5) is for a reviewer or an admin, and is a read
  again of its document (above) in every rule: the same states (409
  ``not_reprocessable`` while the document is being read, until the run ends), one
  transaction under the family lock (the page's mark, ``pages.read_again_requested_at``,
  the document back to the start, one ``document.reprocess`` audit row naming the
  page, then the job), and the same REPROCESS_PER_HOUR and 10-minute cooldown. Each
  page read again is so counted against the hour, and a page, like its document, is
  read again at most once in 10 minutes (the council's security finding: a harder
  read costs more). The worker reads that page again, harder, and the rest of the
  document from the page cache (G7). Only a page read from a scan or photo (route
  ``ocr``) can be: another page is 409 ``not_reprocessable``; a page the document
  does not have is 404, and nothing is counted.
* **Who asked to read again** (model choice): a Read again of the document may name a
  ``read_model`` in an optional JSON body, checked as an upload's, and its audit row
  names it; a page's Read again keeps the document's model (no re-billing of the cache).
  Both make the asker ``read_requested_by``.
* **Batches** (PRD 5 Q1): an upload may name its drop in the optional ``batch_id`` form
  field, a UUID the browser made (anything else is 400). A drop holds at most
  MAX_FILES_PER_BATCH of the uploader's files: the next is 409 ``batch_full``, checked
  before anything is stored. ponytail: the check and the insert are not one
  transaction, so uploads sent at once at the cap can pass it by a few; a lock on the
  batch if that matters. The drop is read back with ``GET /api/batches/{id}``.
* **Needs attention** (PRD 5 U2): ``attention=true`` lists the documents with a page
  unreadable or uncertain still on the attention list, or a value to review.
* **Keep as is** (F5) is for a reviewer or an admin: the page leaves the attention
  list but stays flagged on the page itself and in answers (``accept_page``, under
  the family lock). One ``document.page_accept`` audit row names the page. A
  document in Trash is 409 ``in_trash``; a page it does not have is 404.
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

from kaagaz.api.auth import CanDeleteForever, CurrentUser, Reviewer, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.names import CONTROL_CHARACTERS, DOCUMENT_NAME_MAX, clean_document_name
from kaagaz.api.readers import check_read_model, readers, usable
from kaagaz.api.upload_guard import check_upload_request
from kaagaz.api.views import (
    can_delete,
    document_folder,
    document_link,
    document_summary,
    document_version,
    extracted_value,
    invoice,
    page_readings,
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
    accept_page,
    delete_family_forever,
    mark_for_reprocess,
    request_page_read_again,
)
from kaagaz.db.engine import Database, unavailable
from kaagaz.ingestion import sniff
from kaagaz.ingestion.errors import RejectCode, UploadRejected
from kaagaz.ingestion.messages import over_every_limit_message, too_large_message
from kaagaz.ingestion.pagecount import goes_express
from kaagaz.ingestion.policy import UploadPolicy, limits_by_type
from kaagaz.ingestion.ports import BlobNotFound, Job, JobQueue, UnknownFamily, UnknownFolder
from kaagaz.ingestion.service import UploadResult, UploadService
from kaagaz.settings import Settings

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MIB = 1024 * 1024
MAX_SHEET_ROWS = 500
MAX_TAG_FILTERS = tags_sql.MAX_TAG_FILTERS

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
# PRD 5 F5: a page read from the file itself (its text layer or cells) or blank has no scan
# to read again.
_NOT_SCANNED = "Only a page read from a scan or photo can be read again."
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
    attention: bool = False,
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
            attention=attention,
        )
        tags = tags_sql.tags_for_documents(conn, customer_id, [str(row["id"]) for row in rows])
    max_pages, now = context.settings.max_pages, context.clock()
    items = [
        document_summary(row, max_pages=max_pages, tags=tags[str(row["id"])], user=user, now=now)
        for row in rows
    ]
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
def get_upload_limits(user: User, context: Context) -> dict[str, Any]:
    """``UploadLimits``: the limits this user's uploads are checked against, each
    already lowered by the customer's own limit, so the browser can refuse a file
    before sending it (PRD 1 U2) and count a PDF's pages first (``max_pages``).
    ``readers`` is the models an upload may read with, the default first, or Auto alone
    (``kaagaz/api/readers.py``). The server still checks everything itself. Two queries."""
    policy = _upload_policy(context, user)
    with context.db.customer(user.customer_id) as conn:
        rows = usable(conn, user.customer_id, context.settings)
    return {
        "pdf_bytes": policy.limit_for(sniff.PDF),
        "image_bytes": policy.limit_for(sniff.PNG),
        "sheet_bytes": policy.limit_for(sniff.ZIP),
        "max_pages": context.settings.max_pages,
        "readers": readers(rows, context.settings),
    }


def _refusal_message(rejected: UploadRejected, policy: UploadPolicy) -> str | None:
    """The words for a refused upload; None means the code's usual message."""
    if rejected.code is not RejectCode.TOO_LARGE:
        return None
    if rejected.kind is None or rejected.size is None or rejected.limit is None:
        return over_every_limit_message(policy.largest_limit)  # refused before its type was known
    return too_large_message(rejected.kind, rejected.size, rejected.limit)


def _form_uuid(value: str | UploadFile | None) -> str | None:
    """An optional id form field (``folder_id``, ``batch_id``): None when it is not sent,
    else a UUID (400)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, "bad_request")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ApiError(400, "bad_request") from None


def _same_user(value: str | UploadFile | None, user: CurrentUser, message: str | None = None) -> None:
    """The optional ``user_id`` form field: who the browser tab had signed in when
        the file was chosen. Someone else signed in since (in another tab, critical
        #5) gets 409 ``session_changed`` before anything is stored, so the file never
        lands in their account. Not sent: no check (PROVISIONAL). ``message`` is
        another route's own words (my words). Compared as UUIDs, so an upper-case id
    of the same user passes."""
    if value is not None and _uuid_or_none(value) != uuid.UUID(user.user_id):
        raise ApiError(409, "session_changed", message)


def _form_model(value: str | UploadFile | None) -> str | None:
    """The optional ``read_model`` form field: None when it is not sent or empty (the
    default), else the model ID asked for (checked by ``check_read_model``)."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ApiError(400, "bad_request")
    return value


class _ReadingQueue:
    """The queue an upload's service queues its job through (MS5, SP1): first, in its own
    transaction, the new document gets who asked for its reading and the model chosen
    (``listing.set_reading``), so the worker never reads it without them. A database that
    can't be reached is BackendUnavailable, which the service undoes like a queue that is
    down (the row and the file are removed). A duplicate queues nothing, so it is unchanged."""

    def __init__(self, queue: JobQueue, db: Database, *, requested_by: str, model: str | None) -> None:
        self._queue, self._db = queue, db
        self._requested_by, self._model = requested_by, model

    def enqueue(self, job: Job, *, express: bool = False) -> None:
        with unavailable(), self._db.customer(job.customer_id) as conn:
            listing.set_reading(
                conn, job.customer_id, job.document_id, requested_by=self._requested_by, model=self._model
            )
        self._queue.enqueue(job, express=express)


def _service(
    context: AppContext, user: CurrentUser, policy: UploadPolicy, read_model: str | None
) -> UploadService:
    queue = _ReadingQueue(context.queue, context.db, requested_by=user.user_id, model=read_model)
    return UploadService(
        policy,
        PgDocuments(context.db),
        context.storage,
        queue,
        express_max_pages=context.settings.express_max_pages,
    )


def _uuid_or_none(value: str | UploadFile) -> uuid.UUID | None:
    """The id as a UUID, so letter case and hyphens don't matter; None for anything else."""
    try:
        return uuid.UUID(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _accept(
    context: AppContext,
    user: CurrentUser,
    upload: UploadFile,
    policy: UploadPolicy,
    folder_id: str | None,
    batch_id: str | None,
    read_model: str | None,
) -> dict[str, Any]:
    customer_id, cap = user.customer_id, context.settings.max_files_per_batch
    if folder_id is not None or batch_id is not None or read_model is not None:
        with context.db.customer(customer_id) as conn:
            check_read_model(conn, customer_id, context.settings, read_model)  # before anything is stored
            if folder_id is not None and not folders_sql.folder_path(conn, customer_id, folder_id):
                raise ApiError(404, "not_found")  # before anything is stored or queued
            if (
                batch_id is not None
                and listing.batch_size(conn, customer_id, batch_id, user_id=user.user_id) >= cap
            ):
                raise ApiError(
                    409, "batch_full", f"Up to {cap} files at a time. Drop the rest after these start."
                )
    service = _service(context, user, policy, read_model)
    try:
        result = service.accept(
            user.customer_id,
            upload.file,
            display_name=display_name(upload.filename),
            uploaded_by=user.user_id,
            folder_id=folder_id,
            batch_id=batch_id,
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
        row,
        max_pages=context.settings.max_pages,
        tags=tags[result.document_id],
        user=user,
        now=context.clock(),
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
        _same_user(form.get("user_id"), user)
        folder_id = _form_uuid(form.get("folder_id"))  # ocr_choice: accepted and ignored (RT3)
        batch_id = _form_uuid(form.get("batch_id"))
        read_model = _form_model(form.get("read_model"))
        body = await run_in_threadpool(
            _accept, context, user, upload, policy, folder_id, batch_id, read_model
        )
    return JSONResponse(body, status_code=200 if body["duplicate"] else 201)


def _accept_version(
    context: AppContext,
    user: CurrentUser,
    upload: UploadFile,
    policy: UploadPolicy,
    of_document_id: str,
    read_model: str | None,
) -> dict[str, Any]:
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        target = listing.get_document(conn, customer_id, of_document_id)
        if target is not None:
            check_read_model(conn, customer_id, context.settings, read_model)  # before anything is stored
    if target is None:
        raise ApiError(404, "not_found")  # before anything is stored or queued
    if target["deleted_at"] is not None:
        raise ApiError(409, "in_trash", "This document is in the Trash. Restore it first.")
    service = _service(context, user, policy, read_model)
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
        _same_user(form.get("user_id"), user)  # ocr_choice: accepted and ignored (RT3)
        read_model = _form_model(form.get("read_model"))
        body = await run_in_threadpool(
            _accept_version, context, user, upload, policy, str(document_id), read_model
        )
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
        facts = listing.document_facts(conn, customer_id, target)
        typed = listing.invoice_for(conn, customer_id, target)
        links = listing.document_links(conn, customer_id, target)
    document_date = None if facts is None else facts["document_date"]
    return {
        **document_summary(
            row, max_pages=context.settings.max_pages, tags=tags, user=user, now=context.clock()
        ),
        "pages": [page_summary(page) for page in pages],
        "values": [extracted_value(value) for value in values],
        "folder": document_folder(path),
        "versions": [document_version(version) for version in versions],
        "reviewer_fixes": fixes,
        # PRD 2 D3: the document card, in the model's own words (summary) and with each
        # item's spot (card); null without a card. PRD 3 shows them, labelled as AI.
        "summary": row["summary"],
        "document_date": None if document_date is None else document_date.isoformat(),
        "card": None if facts is None else facts["card"],
        # PRD 2 E5: the invoice as a typed row with its lines; null for other documents.
        # A reviewer's fix to a value does not change it: it is rebuilt when the
        # document is read again (architect AR14).
        "invoice": None if typed is None else invoice(*typed),
        # PRD 3 Y2: the current, ready documents sharing an identifier with this one.
        "links": [document_link(each) for each in links],
        # MS5: the model the document asked to be read with (null: the organisation's
        # default), so Read again starts from it and sends a model only when one is changed.
        "read_model": row["read_model"],
    }


@router.get("/documents/{document_id}/summary")
def get_document_summary(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """The detail's own summary, as in the list (two queries): the viewer asks for it
    while a document is read, then for the whole detail once it settles."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        row = _document_or_404(conn, user, document_id)
        tags = tags_sql.tags_for_documents(conn, customer_id, [target])[target]
    return document_summary(
        row, max_pages=context.settings.max_pages, tags=tags, user=user, now=context.clock()
    )


class ReadAgainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_model: str | None = None  # left out or null: the document keeps its model


@router.post("/documents/{document_id}/reprocess")
def reprocess_document(
    document_id: uuid.UUID, user: Reviewer, context: Context, body: ReadAgainBody | None = None
) -> dict[str, Any]:
    """Read the document again (PRD 2 X1), and return its summary, now ``received``.
    One transaction: the model check, the family lock, the change, who asked and the
    model, the audit row, then the job."""
    customer_id, target = user.customer_id, str(document_id)
    read_model = None if body is None else body.read_model
    with context.db.customer(customer_id) as conn:
        _document_or_404(conn, user, document_id)
        check_read_model(conn, customer_id, context.settings, read_model)
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
        listing.set_reading(conn, customer_id, target, requested_by=user.user_id, model=read_model)
        details: dict[str, audit.DetailValue] = {"status": previous}
        if read_model is not None:
            details["read_model"] = read_model
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="document.reprocess",
            target_type="document",
            target_id=target,
            details=details,
        )
        row = _document_or_404(conn, user, document_id)
        tags = tags_sql.tags_for_documents(conn, customer_id, [target])[target]
        reply = document_summary(
            row, max_pages=context.settings.max_pages, tags=tags, user=user, now=context.clock()
        )
        # Last, inside the transaction: a queue that is down rolls everything back (503).
        context.queue.enqueue(Job(customer_id, target), express=_read_again_express(context, row))
    return reply


def _read_again_express(context: AppContext, row: RowMapping) -> bool:
    """Read again goes to the express lane by the page count its last read stored (PRD 5
    Q4); a document never counted goes to the main lane."""
    return goes_express(row["page_count"], context.settings.express_max_pages)


# --- one page: read it again, or keep it as is (PRD 5 F5) ----------------------------


@router.post("/documents/{document_id}/pages/{page_no}/read-again")
def read_page_again(document_id: uuid.UUID, page_no: int, user: Reviewer, context: Context) -> dict[str, Any]:
    """Read one page again, harder (F5), and return the document's summary, now
    ``received``. The read again of its document in every rule (the module notes).
    One transaction: the page checks, the family lock and the counts, the page's
    mark, the audit row, then the job."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _document_or_404(conn, user, document_id)
        page = listing.get_page(conn, customer_id, target, page_no) if page_no >= 1 else None
        if page is None:
            raise ApiError(404, "not_found")  # before anything is counted
        if page["route"] != "ocr":
            raise ApiError(409, "not_reprocessable", _NOT_SCANNED)
        try:
            previous = mark_for_reprocess(
                conn, customer_id, target, per_hour=context.settings.reprocess_per_hour
            )
        except NotReprocessable:
            raise ApiError(409, "not_reprocessable") from None
        except ReprocessLimited as limited:
            raise ApiError(429, "rate_limited", _REPROCESS_LIMITED[limited.reason]) from None
        if previous is None or not request_page_read_again(
            conn, customer_id, target, page_no, now=context.clock()
        ):
            raise ApiError(404, "not_found")  # deleted in the moment since: everything rolls back
        # The asker is billed; the page keeps the document's model (no choice here).
        listing.set_reading(conn, customer_id, target, requested_by=user.user_id, model=None)
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="document.reprocess",
            target_type="document",
            target_id=target,
            details={"status": previous, "page_no": page_no},
        )
        row = _document_or_404(conn, user, document_id)
        tags = tags_sql.tags_for_documents(conn, customer_id, [target])[target]
        reply = document_summary(
            row, max_pages=context.settings.max_pages, tags=tags, user=user, now=context.clock()
        )
        # Last, inside the transaction: a queue that is down rolls everything back (503).
        context.queue.enqueue(Job(customer_id, target), express=_read_again_express(context, row))
    return reply


@router.post("/documents/{document_id}/pages/{page_no}/accept")
def keep_page_as_is(document_id: uuid.UUID, page_no: int, user: Reviewer, context: Context) -> dict[str, Any]:
    """Keep as is (F5): accept the page as it was read, and return its ``PageSummary``,
    still flagged, with who accepted it and when. One transaction: the family lock,
    the change, the audit row."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        row = _document_or_404(conn, user, document_id)
        if row["deleted_at"] is not None:
            raise ApiError(409, "in_trash", "This document is in the Trash. Restore it first.")
        if page_no < 1 or not accept_page(
            conn, customer_id, target, page_no, user_id=user.user_id, now=context.clock()
        ):
            raise ApiError(404, "not_found")  # no such page, or moved to Trash in the moment since
        page = listing.get_page(conn, customer_id, target, page_no)
        if page is None:
            raise ApiError(404, "not_found")
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="document.page_accept",
            target_type="document",
            target_id=target,
            details={"page_no": page_no, "read_quality": page["read_quality"]},
        )
    return page_summary(page)


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
    return document_summary(
        row, max_pages=context.settings.max_pages, tags=tags, user=user, now=context.clock()
    )


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


@router.get("/documents/{document_id}/pages/{page_no}/readings")
def get_page_readings(document_id: uuid.UUID, page_no: int, user: User, context: Context) -> dict[str, Any]:
    """Scanned text: every line each reader read on this page, as read. Who may see it
    is exactly who may see the page image (above): any version, in Trash or not
    (PROVISIONAL), never a rejected one; and the page must exist. Never logged."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        row = _document_or_404(conn, user, document_id)
        if row["status"] == "rejected" or page_no < 1:
            raise ApiError(404, "not_found")
        if listing.get_page(conn, customer_id, target, page_no) is None:
            raise ApiError(404, "not_found")
        rows = listing.page_readings(conn, customer_id, target, page_no)
        marks = listing.page_marks(conn, customer_id, target, page_no)  # PRD 5 U3
    return page_readings(page_no, rows, marks)


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
    return document_summary(
        row, max_pages=context.settings.max_pages, tags=tags, user=user, now=context.clock()
    )


@router.delete("/documents/{document_id}/permanent", status_code=204)
def delete_document_forever(document_id: uuid.UUID, user: CanDeleteForever, context: Context) -> Response:
    """Delete every version of a family in Trash for good, rows and files (today's
    full delete). It can't be undone, so it needs an admin session whose admin has
    two-step sign-in on, or an admin's grant and a typed password (``require_delete_forever``).
    The audit row says ``by_grant`` when a grant was used."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        try:
            versions = delete_family_forever(conn, context.storage, customer_id, target)
        except NotInTrash:
            raise ApiError(409, "not_in_trash") from None
        if versions is None:
            raise ApiError(404, "not_found")
        details: dict[str, audit.DetailValue] = {"versions": versions}
        if user.role != "admin":
            details["by_grant"] = True
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="document.delete",
            target_type="document",
            target_id=target,
            details=details,
        )
    return Response(status_code=204)
