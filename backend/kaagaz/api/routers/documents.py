"""The document routes: list, upload, a new version, detail and its summary, rename or move,
the original file, page images, each page's scanned text and the lines holding a search's
words (``get_document_words``), spreadsheet cells, and
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
  the summary, kaagaz/api/views.py). Only an admin session with two-step sign-in, or an
  admin session without it (#538) or a member or reviewer an admin allowed, either having
  typed their password in the last 5 minutes (``CanDeleteForever``), can delete forever,
  and only a family in Trash (409 ``not_in_trash``
  otherwise); its files go inside the same transaction, so if storage cannot be
  reached every row stays and the admin can try again.
* **Trash means deleted.** A family in Trash can't be renamed, moved, tagged
  or given a new version: 409 ``in_trash``, checked under the family lock.
* **Read again** (PRD 2 X1) is for a reviewer or an admin: a ready, failed or
  unreadable document out of Trash, or one still in progress RETRY_STUCK_HOURS after it
  was asked for (issue 407, kaagaz/db/documents.py), goes back to the start and its job is
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
* **Read with** (model choice, council 11 Oct 2026, MS4, MS5): ``/upload-limits`` offers
  Standard (the organisation's default) and, when a usable model costs measurably more,
  Careful (``kaagaz/api/readers.py``, UI quality council 15 Oct 2026); Auto alone in
  keyless mode or when the operator set none, so the select hides itself. An
  upload or a version may name one in the optional ``read_model`` form field: any other
  is 403 ``model_not_allowed`` before anything is stored. The model and who asked
  (``read_requested_by``, the user its scan spend is billed to) are written to the new
  document before its job is queued (``_ReadingQueue``), so the worker never reads it
  without them. The old ``ocr_choice`` field is still accepted and ignored (RT3), so old
  browsers don't break.
* **Dynamic** (feature 26, council 15 Oct 2026): ``/upload-limits`` also offers Dynamic,
  the default when the organisation may use it. An upload, a version or a Read again may
  send ``read_mode`` (single or dynamic), checked by ``readers.check_reading`` before
  anything is stored (400 or 403), then written with the model as ``documents.read_mode``
  and ``read_model``, and named in the audit row. With no mode an upload is read with
  Dynamic when it may be; a Read again that names nothing keeps the document's reading.
  Ensemble is offered only after the scan (Tushar, 16 Oct 2026; ``routers/ensemble.py``):
  ``read_mode`` ensemble and ``read_agents`` (older browsers) are accepted and ignored,
  read as with no mode, the audit row noting ``ensemble_after_scan``.
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
  does not have is 404, and nothing is counted. An optional JSON body may name
  ``regions`` of the page (#385: 1 to MAX_READ_AGAIN_REGIONS boxes [x0, y0, x1, y1] in
  page fractions, each with an area, else 400): only the lines there (and the page's
  own unsure ones) are read again, cut out and zoomed, the rest of the page kept as read;
  the audit row counts the regions. Every rule above holds the same.
* **Who asked to read again** (model choice): a Read again of the document may name a
  ``read_model`` in an optional JSON body, checked as an upload's, and its audit row
  names it; a page's Read again keeps the document's model (no re-billing of the cache).
  Both make the asker ``read_requested_by``, written by ``mark_for_reprocess`` itself.
* **While reading is paused** (#478, QA P1 and C3, 4 Nov 2026): a Read again of a ready
  document, or of one of its pages, is 409 ``reading_paused`` with plain words, and nothing
  changes: it would be held, out of search and Ask, until the pause ends. A failed or unreadable
  one is in neither, so it may be read again, and waits for the pause.
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
* **Every read is logged** (#482, 3 Nov 2026): the detail, and each way of reading a document
  one by one (the viewer's own file fetch, page images, word lists, readings, cells), write
  ``document.view`` at most once per person per document every half hour (``audit.record_view``,
  ``{"via": ...}`` naming the way), in the transaction that found the document readable; the
  Download button's ``document.download`` stays as it was.
* **A folder upload's file** (#484): ``POST /documents?folder_upload_item_id=`` or the
  resumable start with ``folder_upload_item_id``. The item names the folder, batch and name (a
  ``folder_id``, ``department_id`` or ``batch_id`` beside it is 400); it is this user's (404),
  not failed (409 ``item_refused``), exactly its registered size (409 ``size_changed``); one
  already in is 200 with the item, storing nothing. A folder deleted since or a refused file
  fails the item with that code. Never auto-filed. ``_accept_item`` holds a send slot (429
  ``rate_limited``, ``Retry-After: 2``, past FOLDER_UPLOAD_FILES_AT_ONCE[_PER_ORG]).
* **Storage limit** (#516): a new file that would take the organisation past its storage limit
  (``db/storage_limits.py``) is 413 ``storage_full``, with ``details`` {used_bytes, limit_bytes,
  limit_gb} and the words of ``storage_full_message``, before anything is stored: in the upload
  service (an upload, a new version, a resumable complete and a folder upload's file all go
  through it). The resumable start is not checked: its bytes are not known yet, so it can't
  tell a duplicate, and a duplicate costs nothing; a resumable file refused at its complete has
  its parts and session removed, as any refusal there. A folder upload's file refused so stays
  waiting, to be sent again.
* **Undo auto-filing** (feature 28, R16): any user of the customer may move a family
  Kaagaz filed back to the top level, under the family lock; the folder stays. In Trash
  is 409 ``in_trash``; filed no more (moved since, or its folder deleted) is 409
  ``already_moved``. One ``document.autofile_undo`` audit row names the folder.
"""

import hashlib
import math
import uuid
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime, time
from os.path import splitext
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, RowMapping
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile

from kaagaz.api.auth import CanDeleteForever, CurrentUser, Reviewer, User, deletes_forever_by_password
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.names import CONTROL_CHARACTERS, DOCUMENT_NAME_MAX, clean_document_name, without_hidden
from kaagaz.api.readers import SINGLE, ReadingChoice, check_reading, check_shape, offer
from kaagaz.api.upload_guard import check_upload_request
from kaagaz.api.views import (
    CURSOR,
    answer_with_source_states,
    can_delete,
    cited_document_ids,
    document_folder,
    document_link,
    document_summary,
    document_version,
    extracted_value,
    folder_upload_item,
    invoice,
    iso,
    page_readings,
    page_summary,
    timeline,
)
from kaagaz.api.views import (
    folder as folder_view,
)
from kaagaz.db import audit, autofile, listing, reading_days, storage_limits, trash
from kaagaz.db import chat as chat_sql
from kaagaz.db import folder_upload_items as items_sql
from kaagaz.db import folders as folders_sql
from kaagaz.db import tags as tags_sql
from kaagaz.db import trashed_folders as trashed_sql
from kaagaz.db import upload_sessions as upload_sessions_sql
from kaagaz.db import versions as versions_sql
from kaagaz.db import zip_unpacks as unpacks_sql
from kaagaz.db.documents import (
    EnsembleRunning,
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
from kaagaz.db.folder_upload_items import ItemRow
from kaagaz.db.folder_uploads import effective_limits
from kaagaz.db.folders import MAX_DEPTH
from kaagaz.db.upload_policy import customer_policy
from kaagaz.db.zip_unpacks import Unpack
from kaagaz.ingestion import resolve, sniff
from kaagaz.ingestion.errors import RejectCode, UploadRejected
from kaagaz.ingestion.messages import over_every_limit_message, storage_full_message, too_large_message
from kaagaz.ingestion.pagecount import goes_express
from kaagaz.ingestion.policy import UploadPolicy
from kaagaz.ingestion.ports import BlobNotFound, Job, JobQueue, StorageRoom, UnknownFamily, UnknownFolder
from kaagaz.ingestion.service import UploadResult, UploadService
from kaagaz.jobs.reading_pause import BUDGET, DAILY_LIMIT, pause_reason
from kaagaz.reading.ocr import GEMINI
from kaagaz.reading.office import PDF_KINDS as OFFICE_PDF_KINDS
from kaagaz.search import page_words
from kaagaz.settings import Settings

router = APIRouter()

_UPLOAD_PART_BYTES = 8 * 1024 * 1024


class ResumableUploadStart(BaseModel):
    file_name: str
    file_size: int
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    user_id: str
    folder_id: str | None = None
    department_id: str | None = None
    batch_id: str | None = None
    read_mode: str | None = None
    read_model: str | None = None
    version_document_id: str | None = None
    version_note: str | None = None
    resume_session_id: str | None = None
    folder_upload_id: str | None = None  # #484: optional, checked against the item's
    folder_upload_item_id: str | None = None  # #484: the item names folder, batch and name

    model_config = ConfigDict(extra="forbid")


Context = Annotated[AppContext, Depends(get_context)]

MAX_SHEET_ROWS = 500
MAX_TAG_FILTERS = tags_sql.MAX_TAG_FILTERS

DocumentStatusParam = Literal["received", "clean", "rejected", "needs_reader", "ready", "failed"]
# The sniffed types, then the kinds the worker tells after the scan (#522, kaagaz/ingestion/resolve.py).
DetectedTypeParam = Literal[
    "pdf", "png", "jpeg", "tiff", "heic", "zip", "ole2", "text",
    "xlsx", "xls", "csv", "docx", "dotx", "doc", "pptx", "ppt", "txt", "md", "json", "html",
]  # fmt: skip
IsoDay = Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")]
SortParam = Literal[
    "name", "doc_type", "owner", "sensitivity", "version", "document_date", "updated_at", "created_at"
]
Sensitivity = Literal["public", "internal", "confidential", "restricted"]

_SERVABLE = frozenset({"clean", "ready", "needs_reader"})
_CONTENT_TYPES = {
    "pdf": ("application/pdf", "pdf"),
    "png": ("image/png", "png"),
    "jpeg": ("image/jpeg", "jpg"),
    "tiff": ("image/tiff", "tif"),
    "heic": ("image/heic", "heic"),
    # #522: an old Office file not told yet, then the kinds. A zip is a folder of files now, kept as
    # one (PR 3; an .xlsx is told "xlsx" once scanned, and a ready one before 0078 is xlsx). Text
    # of any kind is sent as plain text, never as a page to render.
    "zip": ("application/zip", "zip"),
    "ole2": ("application/octet-stream", "bin"),
    "text": ("text/plain; charset=utf-8", "txt"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "xls": ("application/vnd.ms-excel", "xls"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "dotx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.template", "dotx"),
    "doc": ("application/msword", "doc"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
    "ppt": ("application/vnd.ms-powerpoint", "ppt"),
    "csv": ("text/plain; charset=utf-8", "csv"),
    "txt": ("text/plain; charset=utf-8", "txt"),
    "md": ("text/plain; charset=utf-8", "md"),
    "json": ("text/plain; charset=utf-8", "json"),
    # #557 (UAT FF-12): and saved as text, or the copy on the computer would open as a web page.
    "html": ("text/plain; charset=utf-8", "txt"),
}
# #522: 0078 told only ready documents' kinds, so one not read since (needs_reader, failed)
# keeps its sniffed type until it is read again. Its name's ending chooses among the kinds that
# type can be, for Download only (the reader tells the kind from the bytes): all a zip's are
# Office packages or a zip, all text's are sent as plain text, so no name can make it run.
_KINDS_BY_ENDING = {
    sniff.ZIP: {"xlsx": "xlsx", "docx": "docx", "dotx": "dotx", "pptx": "pptx", "zip": "zip"},
    sniff.TEXT: {"csv": "csv", "txt": "txt", "md": "md", "json": "json", "html": "html", "htm": "html"},
}


def served_type(detected_type: str, display_name: str | None) -> str:
    """What a document downloads as: its kind, or for one still of its sniffed zip or text type,
    the kind its name's ending names among that type's (``_KINDS_BY_ENDING``)."""
    kinds = _KINDS_BY_ENDING.get(detected_type, {})
    return kinds.get(resolve.ending(display_name or ""), detected_type)


_REPROCESS_LIMITED = {
    "hourly": "Too many documents were sent to be read again in the last hour. Please try again later.",
    "cooldown": (
        "This document was sent to be read again less than 10 minutes ago. Please wait and try again."
    ),
}
# PRD 5 F5: a page read from the file itself (its text layer or cells) or blank has no scan
# to read again.
_NOT_SCANNED = "Only a page read from a scan or photo can be read again."
_ENSEMBLE_RUNNING = (
    "Ensemble is reading this document. Read it again when Ensemble finishes, or stop it first."
)
# QA C3 (4 Nov 2026): never money, as the list's words (kaagaz/api/views.py).
_READING_PAUSED = {
    DAILY_LIMIT: "Reading is paused, so this document can't be read again now. It stays searchable."
    " Try again when the daily limit resets.",
    BUDGET: "Reading is paused, so this document can't be read again now. It stays searchable."
    " Try again when the monthly AI limit is raised or the new month begins.",
}
_REJECT_STATUS = {
    RejectCode.EMPTY_FILE: 400,
    RejectCode.UNSUPPORTED_TYPE: 400,
    RejectCode.TOO_LARGE: 413,
    RejectCode.STORAGE_FULL: 413,
}


def display_name(filename: str | None) -> str:
    """The name shown for an upload: path stripped, control and hidden characters removed
    (``names.without_hidden``, #520), at most 255 characters, "document" if nothing, "." or
    ".." is left."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = without_hidden(CONTROL_CHARACTERS.sub("", name)).strip()[:DOCUMENT_NAME_MAX].strip()
    return name if name not in ("", ".", "..") else "document"


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
    trashed_folder_id: uuid.UUID | None = None,  # #542: the files of a folder in Trash
    attention: bool = False,
    department_id: Annotated[uuid.UUID | Literal["none"] | None, Query()] = None,  # "none": in no department
    owner_id: uuid.UUID | None = None,
    sensitivity: Annotated[list[Sensitivity] | None, Query(max_length=4)] = None,
    party: Annotated[str | None, Query(max_length=200)] = None,
    property: Annotated[str | None, Query(max_length=64)] = None,
    doc_date_from: IsoDay = None,
    doc_date_to: IsoDay = None,
    has_newer_version: bool = False,
    sort: SortParam = "created_at",
    order: Literal["asc", "desc"] = "desc",
) -> dict[str, Any]:
    """One page and its total. The top level with its subfolders is everything,
    so it filters nothing. At most five data queries, whatever the page size:
    the folder's and the department's subtrees, the page, the count and the page's
    tags (one batch). File management Q3 and Q4: the new filters and the sort. #542: Trash's
    loose files leave out those inside a folder in Trash unless a search or filter is set;
    ``trashed_folder_id`` lists one's (with ``trashed`` only; 404 for a folder not in this
    customer's Trash)."""
    zone = context.settings.timezone
    try:
        start = None if uploaded_from is None else day_start(uploaded_from, zone)
        end = None if uploaded_to is None else day_start(uploaded_to, zone)
        dated_from = None if doc_date_from is None else date.fromisoformat(doc_date_from)
        dated_to = None if doc_date_to is None else date.fromisoformat(doc_date_to)
    except ValueError:
        raise ApiError(400, "bad_request", "Use real dates, written YYYY-MM-DD.") from None
    customer_id = user.customer_id
    tag_ids = [str(tag) for tag in tag_id or []]
    number = None if property is None else autofile.survey_digits(property)
    with context.db.customer(customer_id) as conn:
        folder_ids = _folder_scope(conn, customer_id, folder_id, include_subfolders)
        department = (
            _folder_scope(conn, customer_id, department_id, True)
            if isinstance(department_id, uuid.UUID)
            else None
        )
        if tag_ids and tags_sql.count_existing(conn, customer_id, tag_ids) != len(set(tag_ids)):
            raise ApiError(404, "not_found")  # a tag this customer does not have
        in_trashed = None if trashed_folder_id is None else str(trashed_folder_id)
        if in_trashed is not None and not trashed:
            raise ApiError(400, "bad_request", "A folder in Trash is listed with trashed=true.")
        if in_trashed is not None and not trashed_sql.exists(conn, customer_id, in_trashed):
            raise ApiError(404, "not_found")
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
            trashed_folder_id=in_trashed,
            attention=attention,
            department_folders=department,
            outside_departments=department_id == "none",
            owner_id=None if owner_id is None else str(owner_id),
            sensitivities=sensitivity or (),
            party=party.strip() if party else None,
            property_number=number,
            doc_date_from=dated_from,
            doc_date_to=dated_to,
            has_newer_version=has_newer_version,
            sort=sort,
            descending=order == "desc",
        )
        tags = tags_sql.tags_for_documents(conn, customer_id, [str(row["id"]) for row in rows])
        unpacks = zip_unpacks(conn, customer_id, rows)
    max_pages, now = context.settings.max_pages, context.clock()
    items = [
        document_summary(
            row,
            max_pages=max_pages,
            tags=tags[str(row["id"])],
            user=user,
            now=now,
            unpack=unpacks.get(str(row["id"])),
        )
        for row in rows
    ]
    return {"items": items, "total": total}


def zip_unpacks(conn: Connection, customer_id: str, rows: Sequence[RowMapping]) -> dict[str, Unpack]:
    """#522 PR 3: the unpacking of each zip among ``rows`` (``DocumentSummary.unpack``); no
    query when there is none."""
    zips = [str(row["id"]) for row in rows if row["detected_type"] == "zip"]
    return unpacks_sql.unpacks(conn, customer_id, zips) if zips else {}


def user_upload_policy(context: AppContext, user: CurrentUser) -> UploadPolicy:
    """This user's organisation's upload limits (``db/upload_policy.py``), a zip's included."""
    with context.db.customer(user.customer_id) as conn:
        return customer_policy(conn, user.customer_id, context.settings)


@router.get("/upload-limits")
def get_upload_limits(user: User, context: Context) -> dict[str, Any]:
    """``UploadLimits``: the limits this user's uploads are checked against, each
    already lowered by the customer's own limit, so the browser can refuse a file
    before sending it (PRD 1 U2) and count a PDF's pages first (``max_pages``).
    ``readers`` is the readings an upload may ask for, the default first, or Auto alone
    (``kaagaz/api/readers.py``). The server still checks everything itself. Three queries."""
    policy = user_upload_policy(context, user)
    with context.db.customer(user.customer_id) as conn:
        readers = offer(conn, user.customer_id, context.settings)
        folders = effective_limits(conn, user.customer_id)  # #484
        sort_available = autofile.applies(conn, user.customer_id, user.user_id)
    settings = context.settings
    return {
        "pdf_bytes": policy.limit_for(sniff.PDF),
        "image_bytes": policy.limit_for(sniff.PNG),
        "sheet_bytes": policy.limit_for(resolve.XLSX),
        "zip_bytes": policy.limit_for(sniff.ZIP),  # #522: the folder upload limit, or a larger type's
        "max_pages": settings.max_pages,
        "readers": readers,
        # Folder upload (#484): its limits, the pace and sends at once its files are taken at,
        # and whether "Let Kaagaz sort them" files an upload with no folder.
        "folder_upload": {
            "max_files": folders.max_files,
            "max_bytes": folders.max_bytes,
            "max_folders": folders.max_folders,
            "max_depth": MAX_DEPTH,
            "calls_per_minute": settings.folder_upload_calls_per_minute,
            "files_at_once": settings.folder_upload_files_at_once_per_org,
        },
        "sort_available": sort_available,
    }


def storage_full(room: StorageRoom) -> ApiError:
    """#516: the refusal of a new upload past the organisation's storage limit (module notes)."""
    limit_gb = room.limit_bytes // storage_limits.GIB
    details = {"used_bytes": room.used_bytes, "limit_bytes": room.limit_bytes, "limit_gb": limit_gb}
    return ApiError(413, RejectCode.STORAGE_FULL.value, storage_full_message(limit_gb), details=details)


def _refused(rejected: UploadRejected, policy: UploadPolicy) -> ApiError:
    """The reply to a file the upload service refused."""
    if rejected.code is RejectCode.STORAGE_FULL and rejected.used is not None and rejected.limit is not None:
        return storage_full(StorageRoom(limit_bytes=rejected.limit, used_bytes=rejected.used))
    return ApiError(_REJECT_STATUS[rejected.code], rejected.code.value, _refusal_message(rejected, policy))


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


def same_user(value: str | UploadFile | None, user: CurrentUser, message: str | None = None) -> None:
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


@dataclass(frozen=True, slots=True)
class _Asked:
    """The reading an upload's form asks for (``read_mode``, ``read_model``), not checked
    yet beyond its shape (``readers.check_shape``)."""

    mode: str | None = None
    model: str | None = None


def _form_reading(form: FormData) -> _Asked:
    """The optional reading form fields; 400 for one that is not text or can't be a choice.
    ``read_agents`` (upload-time Ensemble, removed 16 Oct 2026) is not read."""
    asked = _Asked(_form_model(form.get("read_mode")), _form_model(form.get("read_model")))
    check_shape(asked.mode)
    return asked


def _checked_reading(conn: Connection, user: CurrentUser, settings: Settings, asked: _Asked) -> ReadingChoice:
    return check_reading(conn, user.customer_id, settings, mode=asked.mode, model=asked.model)


class _ReadingQueue:
    """The queue an upload's service queues its job through (MS5, SP1): first, in its own
    transaction, the new document gets who asked for its reading and the reading chosen
    (``listing.set_reading``), so the worker never reads it without them. A database that
    can't be reached is BackendUnavailable, which the service undoes like a queue that is
    down (the row and the file are removed). A duplicate queues nothing, so it is unchanged."""

    def __init__(self, queue: JobQueue, db: Database, *, requested_by: str, reading: ReadingChoice) -> None:
        self._queue, self._db = queue, db
        self._requested_by, self._reading = requested_by, reading

    def enqueue(self, job: Job, *, express: bool = False) -> None:
        reading = self._reading
        with unavailable(), self._db.customer(job.customer_id) as conn:
            listing.set_reading(
                conn,
                job.customer_id,
                job.document_id,
                requested_by=self._requested_by,
                model=reading.model,
                mode=reading.mode,
                also=reading.also,
            )
        self._queue.enqueue(job, express=express)


def _service(
    context: AppContext, user: CurrentUser, policy: UploadPolicy, reading: ReadingChoice
) -> UploadService:
    queue = _ReadingQueue(context.queue, context.db, requested_by=user.user_id, reading=reading)
    return UploadService(
        policy,
        PgDocuments(context.db, storage=context.storage),
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


def _department(context: AppContext, user: CurrentUser, department_id: str) -> str:
    """F2: the upload's department, a top-level department folder of this customer (404
    otherwise, before anything is stored). The file goes in it, and auto-filing files it
    further (``db/autofile.py``)."""
    with context.db.customer(user.customer_id) as conn:
        path = folders_sql.folder_path(conn, user.customer_id, department_id)
    if len(path) != 1 or path[0].kind != "department":
        raise ApiError(404, "not_found")
    return department_id


def _accept(
    context: AppContext,
    user: CurrentUser,
    upload: UploadFile,
    policy: UploadPolicy,
    folder_id: str | None,
    batch_id: str | None,
    asked: _Asked,
    item: ItemRow | None = None,
) -> dict[str, Any]:
    """``item``: a folder upload's file (#484, module notes), in its folder and batch."""
    customer_id, cap = user.customer_id, context.settings.max_files_per_batch
    reading = ReadingChoice(SINGLE)  # keyless, nothing asked: read as before, no query
    if (
        folder_id is not None
        or batch_id is not None
        or asked != _Asked()
        or context.settings.ocr_engine == GEMINI
    ):
        with context.db.customer(customer_id) as conn:
            reading = _checked_reading(conn, user, context.settings, asked)  # before anything is stored
            if folder_id is not None and not folders_sql.folder_path(conn, customer_id, folder_id):
                if item is not None:
                    raise ApiError(409, "folder_gone", _FOLDER_GONE)
                raise ApiError(404, "not_found")  # before anything is stored or queued
            if (
                batch_id is not None
                and item is None
                and listing.batch_size(conn, customer_id, batch_id, user_id=user.user_id) >= cap
            ):
                raise ApiError(
                    409, "batch_full", f"Up to {cap} files at a time. Drop the rest after these start."
                )
    service = _service(context, user, policy, reading)
    try:
        result = service.accept(
            user.customer_id,
            upload.file,
            display_name=display_name(upload.filename) if item is None else item.name,
            uploaded_by=user.user_id,
            folder_id=folder_id,
            batch_id=batch_id,
            keep_place=item is not None,
        )
    except UploadRejected as rejected:
        raise _refused(rejected, policy) from None
    except UnknownFolder:  # deleted since the check; the service removed the stored file
        if item is not None:
            raise ApiError(409, "folder_gone", _FOLDER_GONE) from None
        raise ApiError(404, "not_found") from None
    return _upload_reply(context, user, result, action="document.upload", details=reading.details() or None)


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


_FOLDER_GONE = "The folder this file was meant for was deleted after the upload started."
_SIZE_CHANGED = "This file changed after you chose the folder. Choose the folder again with Continue upload."
_OWN_PLACE = "A folder upload's file goes to the folder it was registered for."
_ITEM_REFUSED = {
    "too_large": "This file can't be uploaded: it is too large.",
    "unsupported_type": "This file can't be uploaded: Kaagaz doesn't take this kind of file.",
    "empty_file": "This file can't be uploaded: it is empty.",
    "folder_gone": _FOLDER_GONE,
    "bad_name": "This file can't be uploaded: its name holds a character Kaagaz can't keep,"
    " or another file in its folder has the same name.",
}


def _sendable_item(context: AppContext, user: CurrentUser, item_id: str) -> ItemRow:
    """#484: this user's item, not failed (404, 409 ``item_refused``). One query."""
    with context.db.customer(user.customer_id) as conn:
        row = items_sql.item(conn, user.customer_id, item_id, user_id=user.user_id)
    if row is None:
        raise ApiError(404, "not_found")
    if row.failure_code is not None:
        raise ApiError(409, "item_refused", _ITEM_REFUSED[row.failure_code])
    return row


def _fail_if_folder_gone(context: AppContext, user: CurrentUser, item: ItemRow) -> None:
    """#484: an item whose folder was deleted since fails (``folder_gone``, committed), and the
    reply is 409: it never lands at the top level. One transaction."""
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        gone = not folders_sql.folder_path(conn, customer_id, item.folder_id)
        if gone:
            items_sql.fail_item(conn, customer_id, item.id, "folder_gone")
    if gone:
        raise ApiError(409, "folder_gone", _FOLDER_GONE)


def _send_slot(context: AppContext, user: CurrentUser) -> AbstractContextManager[None]:
    """#484: one of the sends at once this process takes (429 past the caps), held only around
    ``_accept_item``, never while a body is read."""
    settings = context.settings
    return context.sends.held(
        user.customer_id,
        per_org=settings.folder_upload_files_at_once_per_org,
        total=settings.folder_upload_files_at_once,
    )


def _item_reply(context: AppContext, user: CurrentUser, item: ItemRow) -> dict[str, Any]:
    """#484: the reply for an item already in (stored or duplicate), nothing stored again."""
    customer_id, document, in_trash = user.customer_id, None, False
    if item.document_id is not None:
        with context.db.customer(customer_id) as conn:
            row = listing.get_document(conn, customer_id, item.document_id)
            tags = tags_sql.tags_for_documents(conn, customer_id, [item.document_id])
        if row is not None:
            now, pages = context.clock(), context.settings.max_pages
            document = document_summary(row, max_pages=pages, tags=tags[item.document_id], user=user, now=now)
            in_trash = row["deleted_at"] is not None
    duplicate = item.state == "duplicate"
    return {
        "document": document,
        "duplicate": duplicate,
        "in_trash": in_trash,
        "item": folder_upload_item(item),
    }


def _accept_item(
    context: AppContext,
    user: CurrentUser,
    upload: UploadFile,
    policy: UploadPolicy,
    item: ItemRow,
    asked: _Asked,
) -> dict[str, Any]:
    """#484: the item's file, into its folder and batch, then the item finished (or failed with
    the file's refusal). Duplicate is the item's own state."""
    customer_id = user.customer_id
    try:
        body = _accept(context, user, upload, policy, item.folder_id, item.batch_id, asked, item=item)
    except ApiError as refused:
        if refused.code in items_sql.FAILURE_CODES:
            with context.db.customer(customer_id) as conn:
                items_sql.fail_item(conn, customer_id, item.id, refused.code)
        raise
    with context.db.customer(customer_id) as conn:
        items_sql.finish_item(
            conn,
            customer_id,
            item.id,
            document_id=body["document"]["id"],
            duplicate=body["duplicate"],
            user_id=user.user_id,
        )
        done = items_sql.item(conn, customer_id, item.id, user_id=user.user_id)
    if done is None:  # its customer was deleted meanwhile
        raise ApiError(404, "not_found")
    return {**body, "duplicate": done.state == "duplicate", "item": folder_upload_item(done)}


async def _upload_item(
    request: Request, user: User, context: AppContext, policy: UploadPolicy, item_id: str
) -> JSONResponse:
    """#484: one file of a folder upload (module notes)."""
    item = await run_in_threadpool(_sendable_item, context, user, item_id)
    if item.state != "waiting":
        return JSONResponse(await run_in_threadpool(_item_reply, context, user, item), status_code=200)
    async with request.form(max_files=1, max_fields=10) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiError(400, "bad_request", "Choose a file to upload.")
        same_user(form.get("user_id"), user)
        if any(form.get(name) is not None for name in ("folder_id", "department_id", "batch_id")):
            raise ApiError(400, "bad_request", _OWN_PLACE)
        if upload.size != item.size:
            raise ApiError(409, "size_changed", _SIZE_CHANGED)
        asked = _form_reading(form)
        with _send_slot(context, user):
            body = await run_in_threadpool(_accept_item, context, user, upload, policy, item, asked)
    return JSONResponse(body, status_code=200 if body["duplicate"] else 201)


@router.post("/documents")
async def upload_document(
    request: Request,
    user: User,
    context: Context,
    folder_upload_item_id: uuid.UUID | None = None,  # #484: a folder upload's file
) -> JSONResponse:
    policy = await run_in_threadpool(user_upload_policy, context, user)
    check_upload_request(request, policy)
    if folder_upload_item_id is not None:
        return await _upload_item(request, user, context, policy, str(folder_upload_item_id))
    async with request.form(max_files=1, max_fields=10) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiError(400, "bad_request", "Choose a file to upload.")
        same_user(form.get("user_id"), user)
        folder_id = _form_uuid(form.get("folder_id"))  # ocr_choice: accepted and ignored (RT3)
        # File management F2: the department, used when no folder is named (a folder wins).
        department_id = _form_uuid(form.get("department_id"))
        batch_id = _form_uuid(form.get("batch_id"))
        if folder_id is None and department_id is not None:
            folder_id = await run_in_threadpool(_department, context, user, department_id)
        asked = _form_reading(form)
        body = await run_in_threadpool(_accept, context, user, upload, policy, folder_id, batch_id, asked)
    return JSONResponse(body, status_code=200 if body["duplicate"] else 201)


def _accept_version(
    context: AppContext,
    user: CurrentUser,
    upload: UploadFile,
    policy: UploadPolicy,
    of_document_id: str,
    asked: _Asked,
    note: str | None = None,
) -> dict[str, Any]:
    customer_id = user.customer_id
    reading = ReadingChoice(SINGLE)
    with context.db.customer(customer_id) as conn:
        target = listing.get_document(conn, customer_id, of_document_id)
        if target is not None:
            reading = _checked_reading(conn, user, context.settings, asked)  # before anything is stored
    if target is None:
        raise ApiError(404, "not_found")  # before anything is stored or queued
    if target["deleted_at"] is not None:
        raise ApiError(409, "in_trash", "This document is in the Trash. Restore it first.")
    service = _service(context, user, policy, reading)
    try:
        result = service.accept_version(
            customer_id,
            upload.file,
            of_document_id=of_document_id,
            display_name=display_name(upload.filename),
            uploaded_by=user.user_id,
        )
    except UploadRejected as rejected:
        raise _refused(rejected, policy) from None
    except UnknownFamily:  # gone or moved to Trash since the check; the service removed the stored file
        raise ApiError(404, "not_found") from None
    if note is not None and not result.duplicate:  # V9: this version's note
        with context.db.customer(customer_id) as conn:
            listing.set_details(conn, customer_id, result.document_id, by=user.user_id, version_note=note)
    details = {"family_id": str(target["family_id"]), **reading.details()}
    return _upload_reply(context, user, result, action="document.version", details=details)


@router.post("/documents/{document_id}/versions")
async def upload_version(
    document_id: uuid.UUID, request: Request, user: User, context: Context
) -> JSONResponse:
    """A new version of the document's family (PRD 1 F6), with an optional ``version_note``
    form field (File management V9). Any user of the customer."""
    policy = await run_in_threadpool(user_upload_policy, context, user)
    check_upload_request(request, policy)
    async with request.form(max_files=1, max_fields=10) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiError(400, "bad_request", "Choose a file to upload.")
        same_user(form.get("user_id"), user)  # ocr_choice: accepted and ignored (RT3)
        asked = _form_reading(form)
        written = form.get("version_note")  # V9: optional, checked before anything is stored
        note = clean_note(written if isinstance(written, str) else None)
        body = await run_in_threadpool(
            _accept_version, context, user, upload, policy, str(document_id), asked, note
        )
    return JSONResponse(body, status_code=200 if body["duplicate"] else 201)


@router.post("/documents/upload-sessions/candidates")
def resumable_candidates(body: ResumableUploadStart, user: User, context: Context) -> dict[str, Any]:
    """Find this user's pending same-name, same-size uploads; the browser verifies saved chunks."""
    same_user(body.user_id, user)
    policy = user_upload_policy(context, user)
    if body.file_size <= 0 or body.file_size > policy.largest_limit:
        raise ApiError(413, "too_large", over_every_limit_message(policy.largest_limit))
    filename = display_name(body.file_name)
    with context.db.customer(user.customer_id) as conn:
        return {
            "candidates": upload_sessions_sql.candidates(
                conn, user.customer_id, user.user_id, filename, body.file_size, body.fingerprint
            )
        }


@router.post("/documents/upload-sessions")
def create_resumable_session(body: ResumableUploadStart, user: User, context: Context) -> dict[str, Any]:
    """The session (``id``, ``part_size``, ``part_count``); for a folder upload's item already in,
    the upload reply with the item instead (nothing to send)."""
    same_user(body.user_id, user)
    policy = user_upload_policy(context, user)
    if body.file_size <= 0 or body.file_size > policy.largest_limit:
        raise ApiError(413, "too_large", over_every_limit_message(policy.largest_limit))
    count = math.ceil(body.file_size / _UPLOAD_PART_BYTES)
    if count > 10_000:
        raise ApiError(413, "too_large")
    item = None
    if body.folder_upload_item_id is not None:  # #484 (module notes)
        named = (body.folder_id, body.department_id, body.batch_id, body.version_document_id)
        if any(value is not None for value in named):
            raise ApiError(400, "bad_request", _OWN_PLACE)
        item = _sendable_item(context, user, str(_form_uuid(body.folder_upload_item_id)))
        if body.folder_upload_id is not None and _form_uuid(body.folder_upload_id) != item.upload_id:
            raise ApiError(404, "not_found")
        if item.state != "waiting":
            return _item_reply(context, user, item)
        if body.file_size != item.size:
            raise ApiError(409, "size_changed", _SIZE_CHANGED)
        _fail_if_folder_gone(context, user, item)
    if body.resume_session_id:
        session_id = str(uuid.UUID(body.resume_session_id))
        with context.db.customer(user.customer_id) as conn:
            row = upload_sessions_sql.get(conn, user.customer_id, user.user_id, session_id)
        if (
            row is None
            or row["file_size"] != body.file_size
            or cast(dict[str, Any], row["metadata"]).get("filename") != display_name(body.file_name)
            or cast(dict[str, Any], row["metadata"]).get("fingerprint") != body.fingerprint
        ):
            raise ApiError(404, "not_found")
        return {"id": session_id, "part_size": _UPLOAD_PART_BYTES, "part_count": count}

    folder_id = _form_uuid(body.folder_id) if item is None else item.folder_id
    department_id = _form_uuid(body.department_id)
    if folder_id is None and department_id is not None:
        folder_id = _department(context, user, department_id)
    batch_id = _form_uuid(body.batch_id) if item is None else item.batch_id
    asked = _Asked(body.read_mode, body.read_model)
    check_shape(asked.mode)
    if body.version_document_id is not None:
        try:
            target_id = str(uuid.UUID(body.version_document_id))
        except ValueError:
            raise ApiError(400, "bad_request") from None
        with context.db.customer(user.customer_id) as conn:
            target = listing.get_document(conn, user.customer_id, target_id)
        if target is None:
            raise ApiError(404, "not_found")
        if target["deleted_at"] is not None:
            raise ApiError(409, "in_trash", "This document is in the Trash. Restore it first.")
    if (
        folder_id is not None
        or batch_id is not None
        or asked != _Asked()
        or context.settings.ocr_engine == GEMINI
    ):
        with context.db.customer(user.customer_id) as conn:
            _checked_reading(conn, user, context.settings, asked)
            if folder_id is not None and not folders_sql.folder_path(conn, user.customer_id, folder_id):
                raise ApiError(404, "not_found")
            if (
                batch_id is not None
                and item is None
                and listing.batch_size(conn, user.customer_id, batch_id, user_id=user.user_id)
                >= context.settings.max_files_per_batch
            ):
                raise ApiError(409, "batch_full")

    session_id = str(uuid.uuid4())
    storage_key, upload_id = context.storage.start_resumable(user.customer_id, user.user_id, session_id)
    metadata: dict[str, object] = {
        "filename": display_name(body.file_name),
        "fingerprint": body.fingerprint,
        "folder_id": folder_id,
        "batch_id": batch_id,
        "read_mode": asked.mode,
        "read_model": asked.model,
        "version_document_id": body.version_document_id,
        "version_note": clean_note(body.version_note),
        "folder_upload_item_id": None if item is None else item.id,
        "parts": {},
    }
    try:
        with context.db.customer(user.customer_id) as conn:
            upload_sessions_sql.create(
                conn,
                customer_id=user.customer_id,
                user_id=user.user_id,
                session_id=session_id,
                storage_key=storage_key,
                storage_upload_id=upload_id,
                file_size=body.file_size,
                part_count=count,
                metadata=metadata,
            )
    except Exception:
        context.storage.abort_resumable(storage_key, upload_id)
        raise
    return {"id": session_id, "part_size": _UPLOAD_PART_BYTES, "part_count": count}


@router.put("/documents/upload-sessions/{session_id}/parts/{part_number}")
async def upload_resumable_part(
    session_id: uuid.UUID, part_number: int, request: Request, user: User, context: Context
) -> Response:
    with context.db.customer(user.customer_id) as conn:
        session = upload_sessions_sql.get(conn, user.customer_id, user.user_id, str(session_id))
    if session is None:
        raise ApiError(404, "not_found")
    count, size = int(session["part_count"]), int(session["file_size"])
    if part_number < 1 or part_number > count:
        raise ApiError(400, "bad_request")
    expected = _UPLOAD_PART_BYTES if part_number < count else size - _UPLOAD_PART_BYTES * (count - 1)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > expected:
            raise ApiError(413, "too_large")
        body.extend(chunk)
    if len(body) != expected:
        raise ApiError(400, "bad_request", "The upload part was incomplete.")
    digest = hashlib.sha256(body).hexdigest()
    await run_in_threadpool(
        context.storage.upload_resumable_part,
        str(session["storage_key"]),
        str(session["storage_upload_id"]),
        part_number,
        bytes(body),
    )
    with context.db.customer(user.customer_id) as conn:
        upload_sessions_sql.set_part_hash(
            conn, user.customer_id, user.user_id, str(session_id), part_number, digest
        )
    return Response(status_code=204)


@router.post("/documents/upload-sessions/{session_id}/complete")
def complete_resumable_upload(session_id: uuid.UUID, user: User, context: Context) -> JSONResponse:
    with context.db.customer(user.customer_id) as conn:
        session = upload_sessions_sql.get(conn, user.customer_id, user.user_id, str(session_id))
    if session is None:
        raise ApiError(404, "not_found")
    metadata = cast(dict[str, Any], session["metadata"])
    if not context.storage.resumable_complete(str(session["storage_key"])):
        parts = context.storage.resumable_parts(
            str(session["storage_key"]), str(session["storage_upload_id"])
        )
        hashes = metadata.get("parts", {})
        count, size = int(session["part_count"]), int(session["file_size"])
        if [part[0] for part in parts] != list(range(1, count + 1)) or len(hashes) != count:
            raise ApiError(409, "upload_incomplete", "Upload is incomplete. Reconnect and try again.")
        fingerprint = hashlib.sha256(
            "".join(hashes[str(number)] for number in range(1, count + 1)).encode()
        ).hexdigest()
        if fingerprint != metadata.get("fingerprint"):
            raise ApiError(
                409, "upload_mismatch", "The selected file differs from the saved upload. Start it again."
            )
        if any(
            part[1] != (_UPLOAD_PART_BYTES if part[0] < count else size - _UPLOAD_PART_BYTES * (count - 1))
            for part in parts
        ):
            raise ApiError(409, "upload_incomplete")
        context.storage.complete_resumable(
            str(session["storage_key"]),
            str(session["storage_upload_id"]),
            [(number, etag) for number, _, etag in parts],
        )
    item_id = metadata.get("folder_upload_item_id")  # #484
    status = None
    with context.storage.open_resumable(str(session["storage_key"])) as stream:
        try:
            upload = UploadFile(filename=str(metadata["filename"]), file=stream)
            policy = user_upload_policy(context, user)
            asked = _Asked(metadata.get("read_mode"), metadata.get("read_model"))
            if item_id:
                item = _sendable_item(context, user, str(item_id))
                if item.state == "waiting":
                    with _send_slot(context, user):
                        result = _accept_item(context, user, upload, policy, item, asked)
                else:
                    result, status = _item_reply(context, user, item), 200
            elif metadata.get("version_document_id"):
                result = _accept_version(
                    context,
                    user,
                    upload,
                    policy,
                    str(metadata["version_document_id"]),
                    asked,
                    metadata.get("version_note"),
                )
            else:
                result = _accept(
                    context, user, upload, policy, metadata.get("folder_id"), metadata.get("batch_id"), asked
                )
        except ApiError as refused:
            if refused.status == 429:  # a send slot: the session is kept for the retry
                raise
            context.storage.delete_resumable(str(session["storage_key"]))
            with context.db.customer(user.customer_id) as conn:
                upload_sessions_sql.delete(conn, user.customer_id, user.user_id, str(session_id))
            raise
    context.storage.delete_resumable(str(session["storage_key"]))
    with context.db.customer(user.customer_id) as conn:
        upload_sessions_sql.delete(conn, user.customer_id, user.user_id, str(session_id))
    return JSONResponse(result, status_code=status or (200 if result["duplicate"] else 201))


def _document_or_404(conn: Connection, user: CurrentUser, document_id: uuid.UUID) -> RowMapping:
    row = listing.get_document(conn, user.customer_id, str(document_id))
    if row is None:
        raise ApiError(404, "not_found")
    return row


def _readable_or_409(conn: Connection, user: CurrentUser, document_id: uuid.UUID) -> RowMapping:
    """File management T1: the file, page images, readings and cells of a family in Trash
    are not served (409 ``in_trash``); Trash moves every version, so the row says it."""
    row = _document_or_404(conn, user, document_id)
    if row["deleted_at"] is not None:
        raise ApiError(409, "in_trash", "This document is in the Trash. Restore it first.")
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
        suggestion = versions_sql.for_document(conn, customer_id, target, str(row["family_id"]))
        unpack = zip_unpacks(conn, customer_id, [row]).get(target)
        audit.record_view(conn, customer_id, actor_user_id=user.user_id, document_id=target)  # A1
    document_date = None if facts is None else facts["document_date"]
    return {
        **document_summary(
            row,
            max_pages=context.settings.max_pages,
            tags=tags,
            user=user,
            now=context.clock(),
            unpack=unpack,
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
        # File management V6: the pending suggestion with the banner's reasons in plain words.
        "version_suggestion": suggestion,
    }


@router.get("/documents/{document_id}/summary")
def get_document_summary(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """The detail's own summary, as in the list (two queries): the viewer asks for it
    while a document is read, then for the whole detail once it settles."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        row = _document_or_404(conn, user, document_id)
        tags = tags_sql.tags_for_documents(conn, customer_id, [target])[target]
        unpack = zip_unpacks(conn, customer_id, [row]).get(target)
    return document_summary(
        row, max_pages=context.settings.max_pages, tags=tags, user=user, now=context.clock(), unpack=unpack
    )


class ReadAgainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_model: str | None = None  # left out or null: the document keeps its model
    # Features 26 and 27: left out or null, the document keeps its reading (readers.check_reading).
    read_mode: str | None = None
    read_agents: list[str] | None = None  # ignored: Ensemble runs only after the scan (16 Oct 2026)


# #385: the most regions one page's Read again may name (migration 0057 holds the same).
MAX_READ_AGAIN_REGIONS = 10


class PageReadAgainBody(BaseModel):
    # Other fields (a document's read_model, read_mode) are ignored, as before #385: a page keeps
    # the document's reading.
    model_config = ConfigDict(extra="ignore")

    # #385: the regions of the page to read again, each [x0, y0, x1, y1] in page fractions;
    # left out or null, the whole page (F5).
    regions: list[Annotated[list[float], Field(min_length=4, max_length=4)]] | None = Field(
        default=None, min_length=1, max_length=MAX_READ_AGAIN_REGIONS
    )


def _regions(body: PageReadAgainBody | None) -> list[tuple[float, float, float, float]] | None:
    """The body's regions as boxes (#385), None for the whole page; 400 ``bad_request`` for a
    box that is not finite, outside the page, or with no area. O(regions)."""
    if body is None or body.regions is None:
        return None
    boxes = []
    for x0, y0, x1, y1 in body.regions:
        if not (all(map(math.isfinite, (x0, y0, x1, y1))) and 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ApiError(400, "bad_request")
        boxes.append((x0, y0, x1, y1))
    return boxes


ActivityLimit = Annotated[int, Query(ge=1, le=100)]
ActivityCursor = Annotated[str | None, Query(pattern=CURSOR)]


@router.get("/documents/{document_id}/activity")
def get_document_activity(
    document_id: uuid.UUID,
    user: User,
    context: Context,
    limit: ActivityLimit = 50,
    cursor: ActivityCursor = None,
) -> dict[str, Any]:
    """File management A5: ``{items: [AuditEntry], next_cursor}``, what happened to every
    version of the family, newest first (document, version and folder actions only)."""
    with context.db.customer(user.customer_id) as conn:
        family = str(_document_or_404(conn, user, document_id)["family_id"])
        return timeline(conn, user.customer_id, audit.Filters(family_id=family), limit=limit, cursor=cursor)


@router.get("/documents/{document_id}/questions")
def get_document_questions(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """File management P2: ``{items: [{id, conversation_id, question, asked_at, answer}]}``,
    this person's questions asked about any version of the family or answered from one,
    newest first, at most chat_sql.QUESTIONS_SHOWN."""
    with context.db.customer(user.customer_id) as conn:
        family = str(_document_or_404(conn, user, document_id)["family_id"])
        rows = chat_sql.document_questions(conn, user.customer_id, user.user_id, family)
        # As the chat view: sources in Trash, deleted or replaced since (views.answer_with_source_states).
        cited = [each for row in rows if row["answer"] for each in cited_document_ids(row["answer"])]
        states = chat_sql.document_states(conn, user.customer_id, cited)
    return {
        "items": [
            {
                "id": str(row["id"]),
                "conversation_id": str(row["conversation_id"]),
                "question": row["question"],
                "asked_at": iso(row["created_at"]),
                "answer": answer_with_source_states(row["answer"], states)
                if row["answer"]
                else row["answer"],
            }
            for row in rows
        ]
    }


@router.post("/documents/{document_id}/reprocess")
def reprocess_document(
    document_id: uuid.UUID, user: Reviewer, context: Context, body: ReadAgainBody | None = None
) -> dict[str, Any]:
    """Read the document again (PRD 2 X1), and return its summary, now ``received``.
    One transaction: the reading's check, the family lock, the change, who asked and the
    reading, the audit row, then the job."""
    customer_id, target = user.customer_id, str(document_id)
    body = body or ReadAgainBody()
    read_model = body.read_model
    with context.db.customer(customer_id) as conn:
        _document_or_404(conn, user, document_id)
        reading = check_reading(
            conn,
            customer_id,
            context.settings,
            mode=body.read_mode,
            model=read_model,
            keep=True,
        )
        try:
            previous = mark_for_reprocess(
                conn,
                customer_id,
                target,
                per_hour=context.settings.reprocess_per_hour,
                requested_by=user.user_id,
                read_model=reading.model,
                read_mode=reading.mode,
                read_also=reading.also,
            )
        except EnsembleRunning:
            raise ApiError(409, "ensemble_running", _ENSEMBLE_RUNNING) from None
        except NotReprocessable:
            raise ApiError(409, "not_reprocessable") from None
        except ReprocessLimited as limited:
            raise ApiError(429, "rate_limited", _REPROCESS_LIMITED[limited.reason]) from None
        _refuse_while_paused(conn, customer_id, previous, context.clock())
        if previous is None:  # deleted in the moment since
            raise ApiError(404, "not_found")
        details: dict[str, audit.DetailValue] = {"status": previous, **reading.details()}
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


def _refuse_while_paused(conn: Connection, customer_id: str, previous: str | None, now: datetime) -> None:
    """409 ``reading_paused`` for a document that was ready while its organisation's reading is
    paused (the module notes); the caller's transaction rolls back. One statement for a ready one."""
    if previous == "ready":
        refuse_while_paused(conn, customer_id, now)


def refuse_while_paused(conn: Connection, customer_id: str, now: datetime) -> None:
    """409 ``reading_paused``, in Read again's words, while the organisation's reading is paused
    (also Ensemble's start and retry, #520). One statement."""
    reason = pause_reason(reading_days.state(conn, customer_id, reading_days.india_day(now)))
    if reason is not None:
        raise ApiError(409, "reading_paused", _READING_PAUSED[reason])


def _read_again_express(context: AppContext, row: RowMapping) -> bool:
    """Read again goes to the express lane by the page count its last read stored (PRD 5
    Q4); a document never counted goes to the main lane."""
    return goes_express(row["page_count"], context.settings.express_max_pages)


# --- one page: read it again, or keep it as is (PRD 5 F5) ----------------------------


@router.post("/documents/{document_id}/pages/{page_no}/read-again")
def read_page_again(
    document_id: uuid.UUID,
    page_no: int,
    user: Reviewer,
    context: Context,
    body: PageReadAgainBody | None = None,
) -> dict[str, Any]:
    """Read one page again, harder (F5), or only some regions of it (#385), and return the
    document's summary, now ``received``. The read again of its document in every rule
    (the module notes).
    One transaction: the page checks, the family lock and the counts, the page's
    mark, the audit row, then the job."""
    customer_id, target = user.customer_id, str(document_id)
    regions = _regions(body)
    with context.db.customer(customer_id) as conn:
        _document_or_404(conn, user, document_id)
        page = listing.get_page(conn, customer_id, target, page_no) if page_no >= 1 else None
        if page is None:
            raise ApiError(404, "not_found")  # before anything is counted
        if page["route"] != "ocr":
            raise ApiError(409, "not_reprocessable", _NOT_SCANNED)
        try:
            # The asker is billed; the page keeps the document's model (no choice here).
            previous = mark_for_reprocess(
                conn,
                customer_id,
                target,
                per_hour=context.settings.reprocess_per_hour,
                requested_by=user.user_id,
            )
        except EnsembleRunning:
            raise ApiError(409, "ensemble_running", _ENSEMBLE_RUNNING) from None
        except NotReprocessable:
            raise ApiError(409, "not_reprocessable") from None
        except ReprocessLimited as limited:
            raise ApiError(429, "rate_limited", _REPROCESS_LIMITED[limited.reason]) from None
        _refuse_while_paused(conn, customer_id, previous, context.clock())
        if previous is None or not request_page_read_again(
            conn, customer_id, target, page_no, now=context.clock(), regions=regions
        ):
            raise ApiError(404, "not_found")  # deleted in the moment since: everything rolls back
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="document.reprocess",
            target_type="document",
            target_id=target,
            details={
                "status": previous,
                "page_no": page_no,
                **({"regions": len(regions)} if regions is not None else {}),
            },
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


VERSION_NOTE_MAX = 200


def clean_note(value: str | None) -> str | None:
    """V9: a version note with its spaces collapsed, None when empty; 400 over
    VERSION_NOTE_MAX characters or with a control character."""
    note = " ".join((value or "").split()) or None
    if note is not None and (len(note) > VERSION_NOTE_MAX or CONTROL_CHARACTERS.search(note)):
        raise ApiError(400, "bad_request", f"Write a note of at most {VERSION_NOTE_MAX} characters.")
    return note


class DocumentChangesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    folder_id: uuid.UUID | None = None  # an explicit null: the top level; left out: don't move
    # File management O1, O2 (the whole family) and V9 (this version; null clears it).
    owner_id: uuid.UUID | None = None
    sensitivity: Sensitivity | None = None
    version_note: str | None = None


def _details(conn: Connection, user: CurrentUser, body: DocumentChangesBody, old: RowMapping) -> None:
    """O1 to O4 and V9: the owner (an active user of this customer, else 400), the label
    and the note in one statement with the family's stamp; owner and label changes audited
    with the old and new values (ids and labels only)."""
    given, customer_id, target = body.model_fields_set, user.customer_id, str(old["id"])
    changes: dict[str, Any] = {}
    if "owner_id" in given:
        owner = None if body.owner_id is None else str(body.owner_id)
        if owner is None or not listing.active_user(conn, customer_id, owner):
            raise ApiError(400, "bad_request", "Choose an active person in your company as the owner.")
        changes["owner_id"] = owner
    if "sensitivity" in given:
        if body.sensitivity is None:
            raise ApiError(400, "bad_request", "Choose a label.")
        changes["sensitivity"] = body.sensitivity
    if "version_note" in given:
        changes["version_note"] = clean_note(body.version_note)
    listing.set_details(conn, customer_id, target, by=user.user_id, **changes)
    for field, action in (("owner_id", "document.owner_change"), ("sensitivity", "document.label_change")):
        old_value = None if old[field] is None else str(old[field])
        if field in changes and changes[field] != old_value:
            audit.record(
                conn,
                customer_id,
                actor_user_id=user.user_id,
                action=action,
                target_type="document",
                target_id=target,
                details={"from": old_value, "to": changes[field]},
            )


@router.patch("/documents/{document_id}")
def update_document(
    document_id: uuid.UUID, body: DocumentChangesBody, user: User, context: Context
) -> dict[str, Any]:
    """Rename this version and/or move the whole family, set its owner and label, and this
    version's note. Any user of the customer (PRD 1 F3; File management O1 to O3, V9)."""
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
            _details(conn, user, body, _document_or_404(conn, user, document_id))
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


@router.post("/documents/{document_id}/autofile/undo")
def undo_autofile(document_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    """Move a family Kaagaz filed back where it was (R16): its department when it was filed
    inside one (File management F3), else "Not in a folder"; and return its summary."""
    customer_id, target = user.customer_id, str(document_id)
    try:
        with context.db.customer(customer_id) as conn:
            family_id = trash.lock_live_family(conn, customer_id, target)
            if family_id is None:
                raise ApiError(404, "not_found")
            folder_id = autofile.undo(conn, customer_id, family_id)
            if folder_id is None:
                raise ApiError(409, "already_moved", "This document was moved since. Nothing was changed.")
            audit.record(
                conn,
                customer_id,
                actor_user_id=user.user_id,
                action="document.autofile_undo",
                target_type="document",
                target_id=target,
                details={"folder_id": folder_id},
            )
            row = _document_or_404(conn, user, document_id)
            tags = tags_sql.tags_for_documents(conn, customer_id, [target])[target]
    except trash.InTrash:
        raise ApiError(409, "in_trash", "This document is in the Trash. Restore it first.") from None
    return document_summary(
        row, max_pages=context.settings.max_pages, tags=tags, user=user, now=context.clock()
    )


@router.get("/documents/{document_id}/file")
def get_file(document_id: uuid.UUID, user: User, context: Context, download: bool = False) -> Response:
    """The original file. File management A2: the Download button asks ``download=1``, which
    logs ``document.download`` once the file is found; the viewer's own fetch is a view (#482).
    #522 PR 2: the viewer's fetch of a Word or PowerPoint file gets its PDF rendition, which the
    worker stored when it converted it (404 until then); Download gets the original."""
    with context.db.customer(user.customer_id) as conn:
        row = _readable_or_409(conn, user, document_id)
        if not download and row["status"] in _SERVABLE:
            _viewed(conn, user, document_id, "file")
    if row["status"] not in _SERVABLE:
        raise ApiError(404, "not_found")
    rendition = not download and row["detected_type"] in OFFICE_PDF_KINDS
    try:
        if rendition:
            data = context.storage.get_rendition(user.customer_id, str(document_id))
        else:
            data = context.storage.get(user.customer_id, str(document_id))
    except BlobNotFound:
        raise ApiError(404, "not_found") from None
    if download:
        with context.db.customer(user.customer_id) as conn:
            audit.record(
                conn,
                user.customer_id,
                actor_user_id=user.user_id,
                action="document.download",
                target_type="document",
                target_id=str(document_id),
            )
    detected = sniff.PDF if rendition else served_type(row["detected_type"], row["display_name"])
    content_type, extension = _CONTENT_TYPES.get(detected, ("application/octet-stream", "bin"))
    # The user's own name with the file's true extension, percent-encoded (RFC 6266), so no
    # quote or line break reaches the header; "document.<ext>" for browsers without filename*.
    name = quote(f"{splitext(row['display_name'] or '')[0] or 'document'}.{extension}", safe="")
    return Response(
        content=data,
        media_type=content_type,
        headers={
            # A download, never opened in a tab (the viewer fetches it with pdf.js).
            "Content-Disposition": f"attachment; filename=\"document.{extension}\"; filename*=UTF-8''{name}",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/documents/{document_id}/pages/{page_no}/image")
def get_page_image(document_id: uuid.UUID, page_no: int, user: User, context: Context) -> Response:
    with context.db.customer(user.customer_id) as conn:
        row = _readable_or_409(conn, user, document_id)
        if row["status"] == "rejected" or page_no < 1:
            raise ApiError(404, "not_found")
        _viewed(conn, user, document_id, "page_image")
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
    is exactly who may see the page image (above): any version out of Trash (T1: 409
    ``in_trash``), never a rejected one; and the page must exist. A view (#482)."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        row = _readable_or_409(conn, user, document_id)
        if row["status"] == "rejected" or page_no < 1:
            raise ApiError(404, "not_found")
        if listing.get_page(conn, customer_id, target, page_no) is None:
            raise ApiError(404, "not_found")
        rows = listing.page_readings(conn, customer_id, target, page_no)
        marks = listing.page_marks(conn, customer_id, target, page_no)  # PRD 5 U3
        _viewed(conn, user, document_id, "readings")
    return page_readings(page_no, rows, marks)


@router.get("/documents/{document_id}/words")
def get_document_words(
    document_id: uuid.UUID,
    user: User,
    context: Context,
    w: Annotated[list[str] | None, Query(max_length=page_words.MAX_WORDS)] = None,
) -> dict[str, Any]:
    """Your words on the page (package H): the regions of the document's lines that hold the
    words (``w``, at most 8, cleaned by ``page_words.clean_words``; at most
    ``page_words.MAX_BOXES``), which word lit each, every page with such a line, and each
    word's lines and pages (``page_words.word_finds``). Asked once a document and
    words: the viewer keeps it while its pages change. Who may ask is who may see the page
    image: another customer's id and a rejected file are 404, a family in Trash 409
    ``in_trash`` (T1). Regions, page numbers and the asker's own words only, never the
    document's text. One statement, and the view (#482)."""
    words = page_words.clean_words(w or [])
    with context.db.customer(user.customer_id) as conn:
        found = listing.word_lines(conn, user.customer_id, str(document_id))
        if found is None:
            raise ApiError(404, "not_found")
        in_trash, status, pieces = found
        if in_trash:
            raise ApiError(409, "in_trash", "This document is in the Trash. Restore it first.")
        if status == "rejected":
            raise ApiError(404, "not_found")
        _viewed(conn, user, document_id, "words")
    return page_words.word_finds(pieces, words)


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
        _readable_or_409(conn, user, document_id)
        page = listing.get_page(conn, user.customer_id, str(document_id), page_no)
        if page is None or page["unit"] != "cells":
            raise ApiError(404, "not_found")
        cells = listing.sheet_cells(conn, user.customer_id, str(document_id), page_no, row_from, row_to)
        _viewed(conn, user, document_id, "cells")
    rows: list[dict[str, Any]] = []
    for cell in cells:
        if not rows or rows[-1]["row"] != cell["row_no"]:
            rows.append({"row": int(cell["row_no"]), "cells": []})
        rows[-1]["cells"].append({"column": int(cell["column_no"]), "text": cell["text"] or ""})
    return {"page_no": page_no, "total_rows": int(page["height"]), "rows": rows}


def _viewed(conn: Connection, user: CurrentUser, document_id: uuid.UUID, via: str) -> None:
    """#482: this read is a view of the document, once per person per half hour (A1)."""
    audit.record_view(
        conn, user.customer_id, actor_user_id=user.user_id, document_id=str(document_id), via=via
    )


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
    """Bring the whole family back from Trash, and return this version's summary. #542: one
    that went with a folder goes back into its folder, which comes back with its chain of folders
    (``trashed_folders.restore_document``; audited ``folder.restore`` too)."""
    customer_id, target = user.customer_id, str(document_id)
    with context.db.customer(customer_id) as conn:
        _may_trash(user, _document_or_404(conn, user, document_id))
        try:
            found, chain = trashed_sql.restore_document(conn, customer_id, target, admin=user.role == "admin")
        except trashed_sql.DepartmentAdminOnly:  # its folders could not come back: nothing changed
            raise ApiError(
                403, "forbidden", "Its folder is a department, which only an admin can restore."
            ) from None
        except folders_sql.FolderNameTaken:
            raise ApiError(
                409, "folder_names_taken", "Its folder can't come back: too many folders there have its name."
            ) from None
        except folders_sql.FolderError as error:
            raise ApiError(404 if error.code == "not_found" else 409, error.code) from None
        if not found:
            raise ApiError(404, "not_found")
        if chain is not None:
            audit.record(
                conn,
                customer_id,
                actor_user_id=user.user_id,
                action="folder.restore",
                target_type="folder",
                target_id=chain.folder.id,
                details=trashed_sql.audit_details(chain),
            )
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
    summary = document_summary(
        row, max_pages=context.settings.max_pages, tags=tags, user=user, now=context.clock()
    )
    # #542: the folders that came back with it, so the screen can say where (null: none did).
    summary["restored_folder"] = (
        None
        if chain is None
        else {
            "folder": folder_view(chain.folder),
            "location": list(chain.location),
            "renamed": chain.renamed,
            "relocated": chain.relocated,
        }
    )
    return summary


@router.delete("/documents/{document_id}/permanent", status_code=204)
def delete_document_forever(document_id: uuid.UUID, user: CanDeleteForever, context: Context) -> Response:
    """Delete every version of a family in Trash for good, rows and files (today's
    full delete). It can't be undone, so it needs an admin session whose admin has
    two-step sign-in on, or a typed password from an admin session without it (#538) or a
    user an admin allowed (``require_delete_forever``). The audit row says ``by_grant`` when
    a grant was used, ``by_password`` for an admin's typed password."""
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
        elif deletes_forever_by_password(user):
            details["by_password"] = True
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
