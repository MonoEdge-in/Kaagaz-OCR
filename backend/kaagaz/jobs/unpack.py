"""Unpack a zip upload into a folder, in the worker (#522 PR 3, Tushar, 6 Nov 2026).

The pipeline hands a document whose kind is ``zip`` (``resolve.ZIP``: no Office file), after its
own virus check, to ``ZipUnpacker``. The zip is unpacked as a folder upload the server makes
itself, so every folder upload rule holds; nothing in it is ever read here but its listing and
each file's bytes (``kaagaz/ingestion/unzip.py``).

1. ``place``: only a zip a person uploaded on its own is unpacked (``db/zip_unpacks.py``); any
   other (a zip inside a zip or a folder upload, a new version, one in Trash) is kept as an
   ordinary file, not read: ``__call__`` returns False and the pipeline marks it so.
2. The listing (``unzip.open_zip``, ``list_zip``), then the whole zip is refused, before
   anything is made, with its code on the zip's document: more than 50,000 entries, or more
   files or folders than the organisation's folder upload limits (``zip_too_many_files``,
   ``zip_too_many_folders``); unpacking to more than its folder upload byte limit
   (``zip_too_large``), more than 100 times the zip's own size (``zip_too_compressed``) or more
   than the storage left (``storage_full``); entries that overlap or can't be listed
   (``zip_damaged``).
3. The plan, in ONE transaction, so a half-made folder tree can't be left without what explains
   it: the folder named after the zip (less ".zip"; "Name (2)" when one is there already) in the
   place it was uploaded to, its folders under it (``plan_folders``, depth 100 and the folder
   limit checked), the upload tied to the zip (``zip_unpacks.attach``), its audit row, and one
   item a file, refused ones with their code (``register_items``: the file limit, the byte
   limit, a name twice in a folder). Any refusal rolls it all back.
4. The zip's own document moves into that folder (``listing.move_family``), kept as a file.
5. Each waiting item, in order: unpacked with every limit checked as it comes
   (``unzip.read_file``: its kind's limit, the ratio, the zip's budget), virus checked on its own
   (the same scanner as every upload: a refusal is that file's ``virus_found``,
   ``blocked_content`` or ``unscannable``, the rest go on), its kind told (``resolve_kind``,
   after the scan) and checked against that kind's limit, then stored through the upload service
   (``UploadService.accept``: the type, the storage limit, the duplicate rule, the queue), in
   its folder and batch, never auto-filed. Its own job reads it with its kind, scanning it once
   more. Each item ends stored, duplicate ("already in Kaagaz", nothing new stored) or failed
   with its code.

Run again (a crash, a backend down, a Read again) it goes on where it stopped: the same upload
id (``unpack_id``) finds the same folders and items, and only waiting items are unpacked. A
scanner that is down fails the job to be tried again; a database, storage or queue that is down
is BackendUnavailable, the same.

Customer separation: every statement runs in ``Database.customer(customer_id)`` of the job's own
customer, with ``customer_id`` in every filter, and a job naming another customer's document finds
no document (the pipeline stops it before), so it can only make folders and documents for its own
customer.
"""

import io
import zipfile
from dataclasses import dataclass

from sqlalchemy import Connection

from kaagaz.api.names import shown_upload_name, upload_name
from kaagaz.api.readers import ReadingChoice, check_reading
from kaagaz.db import audit, listing
from kaagaz.db import folder_upload_items as items_sql
from kaagaz.db import zip_unpacks as zip_sql
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.folder_uploads import (
    FolderUploadLimits,
    PlannedFolder,
    PlanRefused,
    effective_limits,
    plan_folders,
)
from kaagaz.db.folders import (
    MAX_DEPTH,
    FolderError,
    FolderNameTaken,
    FolderNotFound,
    FolderTooDeep,
    folder_path,
)
from kaagaz.db.storage_limits import storage_room
from kaagaz.db.trash import lock_family
from kaagaz.db.upload_policy import customer_policy
from kaagaz.ingestion import unzip
from kaagaz.ingestion.errors import UploadRejected
from kaagaz.ingestion.policy import KIND_OF, UploadPolicy
from kaagaz.ingestion.ports import BlobStorage, DocumentRecord, Job, JobQueue, UnknownFolder
from kaagaz.ingestion.resolve import UNSUPPORTED, resolve_kind
from kaagaz.ingestion.service import UploadService
from kaagaz.ingestion.sniff import detect_type
from kaagaz.ingestion.spool import HEAD_SIZE
from kaagaz.jobs.worker import Heartbeat, JobFailed
from kaagaz.reading.spans import ReadError
from kaagaz.scanning.gate import Scanner, ScannerError, Verdict
from kaagaz.settings import Settings

PAGE = 1000  # items registered, and read back, at a time (the folder upload's page)
FOLDER_NAME_MAX = 100  # folders.name's CHECK
_VERDICTS = {
    Verdict.INFECTED: "virus_found",
    Verdict.BLOCKED: "blocked_content",
    Verdict.ENCRYPTED: unzip.ENCRYPTED,  # #552: "It has a password..."
}
_PLAN_CODES = {
    "too_many_files": "zip_too_many_files",
    "too_large_upload": "zip_too_large",
    "too_many_folders": "zip_too_many_folders",
}


@dataclass(frozen=True, slots=True)
class _Limits:
    folders: FolderUploadLimits
    policy: UploadPolicy
    reading: ReadingChoice
    depth: int  # the depth the zip's own folder will be at


class ZipUnpacker:
    def __init__(
        self, db: Database, storage: BlobStorage, queue: JobQueue, scanner: Scanner, settings: Settings
    ) -> None:
        """``queue``: where each new document's job goes (the upload's queue). ``scanner``: the
        worker's own, which the scan gate uses."""
        self._db = db
        self._storage = storage
        self._queue = queue
        self._scanner = scanner
        self._settings = settings

    def __call__(self, job: Job, record: DocumentRecord, data: bytes, heartbeat: Heartbeat) -> bool:
        """Unpack the zip ``data`` (its scanned bytes) of ``job``'s document, ``record``; False,
        with nothing done, for a zip kept as an ordinary file (module notes). Raises JobFailed
        with the zip's code, retryable only for a plan being made at that moment (``plan_busy``)
        or the scanner down."""
        customer_id, document_id = job.customer_id, job.document_id
        with unavailable(), self._db.customer(customer_id) as conn:
            found = zip_sql.place(conn, customer_id, document_id)
            if found is None or found.uploaded_by is None or not found.unpacks:
                return False
            parent = found.parent_folder_id if found.planned else found.folder_id
            source = found.source_name if found.planned else folder_name(record.display_name)
            limits = self._limits(conn, customer_id, parent)
        uploader = found.uploaded_by
        try:
            archive = unzip.open_zip(data)
        except unzip.ZipRefused as refused:
            raise JobFailed(f"zip_{refused.code}", retryable=False) from None
        with archive:
            unpacked = unzip.list_zip(archive, depth_left=MAX_DEPTH - limits.depth)
            _check(unpacked, limits.folders, len(data))
            upload_id, root = self._plan(
                job, uploader, parent, source, unpacked, limits, found.planned, heartbeat
            )
            self._move(customer_id, document_id, root)
            self._unpack(job, uploader, upload_id, archive, unpacked, limits, len(data), heartbeat)
        return True

    def _limits(self, conn: Connection, customer_id: str, parent: str | None) -> _Limits:
        """The organisation's limits now, the reading its files get, and the zip folder's depth
        (FolderNotFound for a parent gone: ``folder_gone``)."""
        depth = 1
        if parent is not None:
            path = folder_path(conn, customer_id, parent)
            if not path:
                raise JobFailed("folder_gone", retryable=False)
            depth = len(path) + 1
        if depth > MAX_DEPTH:
            raise JobFailed("folder_too_deep", retryable=False)
        return _Limits(
            folders=effective_limits(conn, customer_id),
            policy=customer_policy(conn, customer_id, self._settings),
            # As an upload into a folder with nothing asked (routers/documents.py ``_accept``).
            reading=check_reading(conn, customer_id, self._settings),
            depth=depth,
        )

    def _plan(
        self,
        job: Job,
        uploader: str,
        parent: str | None,
        source: str,
        unpacked: unzip.Listing,
        limits: _Limits,
        planned: bool,
        heartbeat: Heartbeat,
    ) -> tuple[str, str]:
        """Step 3 of the module notes in one transaction; (the upload's id, its root folder).
        ``source``: the zip folder's name (its upload's, when run again: the zip may be renamed)."""
        customer_id, document_id = job.customer_id, job.document_id
        index = {(): 0} | {path: at for at, path in enumerate(unpacked.folders, start=1)}
        folders = [PlannedFolder(source, None)]
        folders += [PlannedFolder(path[-1], index[path[:-1]]) for path in unpacked.folders]
        files = len(unpacked.files) + len(unpacked.refused)
        upload_id = zip_sql.unpack_id(document_id)
        try:
            with unavailable(), self._db.customer(customer_id) as conn:
                room = storage_room(conn, customer_id)
                if not planned and room.used_bytes + unpacked.declared_bytes > room.limit_bytes:
                    raise JobFailed("storage_full", retryable=False)
                plan = plan_folders(
                    conn,
                    customer_id,
                    upload_id=upload_id,
                    user_id=uploader,
                    target="folder",
                    parent_folder_id=parent,
                    merge=False,  # a folder of its own, "Name (2)" beside one of that name
                    folders=folders,
                    file_count=files,
                    total_bytes=unpacked.declared_bytes,
                    skipped_count=unpacked.junk,
                    limits=limits.folders,
                    alone=False,
                )
                zip_sql.attach(conn, customer_id, plan.upload_id, document_id)
                if plan.new:
                    details: dict[str, audit.DetailValue] = {
                        "folder_upload_id": plan.upload_id,
                        "zip_document_id": document_id,
                        "files": files,
                        "bytes": unpacked.declared_bytes,
                        "folders_created": plan.created,
                        "skipped": unpacked.junk,
                    }
                    audit.record(
                        conn,
                        customer_id,
                        actor_user_id=uploader,
                        action="folder.upload",
                        target_type="folder",
                        target_id=plan.root_folder_id,
                        details=details,
                    )
                ids = plan.folder_ids
                items = [
                    items_sql.NewItem(entry.key, entry.name, ids[index[entry.folder]], entry.info.file_size)
                    for entry in unpacked.files
                ]  # first: a refused file's name never takes a file's (all go in the zip's folder)
                items += [items_sql.NewItem(r.key, r.name, ids[0], r.size, r.code) for r in unpacked.refused]
                for start in range(0, len(items), PAGE):
                    items_sql.register_items(
                        conn,
                        customer_id,
                        plan.upload_id,
                        user_id=uploader,
                        items=items[start : start + PAGE],
                        batch_cap=self._settings.max_files_per_batch,
                        largest_limit=limits.policy.largest_limit,
                        limits=limits.folders,
                    )
                    heartbeat()  # the job keeps its lease while a large zip is registered
        except (FolderError, items_sql.ItemsRefused) as refused:  # rolled back: nothing made
            raise JobFailed(_plan_code(refused), retryable=_plan_code(refused) == "plan_busy") from None
        return plan.upload_id, plan.root_folder_id

    def _move(self, customer_id: str, document_id: str, root: str) -> None:
        """Step 4: the zip into its folder, every version (it has one), under the family lock."""
        try:
            with unavailable(), self._db.customer(customer_id) as conn:
                found = zip_sql.place(conn, customer_id, document_id)
                if (
                    found is not None
                    and found.folder_id != root
                    and lock_family(conn, customer_id, document_id)
                ):
                    listing.move_family(conn, customer_id, document_id, folder_id=root)
        except FolderNotFound:  # the folder was deleted in the moment since
            raise JobFailed("folder_gone", retryable=False) from None

    def _unpack(
        self,
        job: Job,
        uploader: str,
        upload_id: str,
        archive: zipfile.ZipFile,
        unpacked: unzip.Listing,
        limits: _Limits,
        zip_size: int,
        heartbeat: Heartbeat,
    ) -> None:
        """Step 5 for every waiting item, a page at a time, in order."""
        customer_id = job.customer_id
        entries = {entry.key: entry for entry in unpacked.files}
        queue = _ReadingQueue(self._db, self._queue, requested_by=uploader, reading=limits.reading)
        service = UploadService(
            limits.policy,
            PgDocuments(self._db, storage=self._storage),
            self._storage,
            queue,
            express_max_pages=self._settings.express_max_pages,
        )
        budget = min(limits.folders.max_bytes, max(unzip.RATIO_FLOOR, unzip.MAX_RATIO * zip_size))
        after = -1
        while True:
            with unavailable(), self._db.customer(customer_id) as conn:
                rows = items_sql.list_items(
                    conn, customer_id, upload_id, view="waiting", after_seq=after, limit=PAGE
                )
            for row in rows:
                entry = entries.get(row.path_key)
                if entry is None:  # the same bytes always list the same files
                    self._fail(customer_id, row.id, unzip.DAMAGED)
                    continue
                budget -= self._one(job, uploader, archive, entry, row, limits.policy, service, budget)
                heartbeat()
            if len(rows) < PAGE:
                return
            after = rows[-1].seq

    def _one(
        self,
        job: Job,
        uploader: str,
        archive: zipfile.ZipFile,
        entry: unzip.Entry,
        row: items_sql.ItemRow,
        policy: UploadPolicy,
        service: UploadService,
        budget: int,
    ) -> int:
        """One file (step 5); the bytes it unpacked to."""
        customer_id = job.customer_id
        with unavailable(), self._db.customer(customer_id) as conn:
            made = items_sql.own_document(conn, customer_id, row.id, user_id=uploader)
        if made is not None:  # A14: stored by a run that stopped before it was marked: its own
            self._finish(job, uploader, row, made, duplicate=False)
            return 0

        def limit_for(sniffed: str | None) -> int | None:
            return policy.limit_for(sniffed) if sniffed in policy.allowed_types else None

        try:
            data = unzip.read_file(archive, entry, limit_for=limit_for, budget=budget)
        except unzip.FileRefused as refused:
            self._fail(customer_id, row.id, refused.code)
            return refused.inflated  # what it unpacked to before it stopped counts too
        code = self._refusal(data, row.name, policy)
        if code is not None:
            self._fail(customer_id, row.id, code)
            return len(data)
        try:
            result = service.accept(
                customer_id,
                io.BytesIO(data),
                display_name=row.name,
                uploaded_by=uploader,
                folder_id=row.folder_id,
                batch_id=row.batch_id,
                keep_place=True,
            )
        except UploadRejected as rejected:
            self._fail(customer_id, row.id, rejected.code.value)
            return len(data)
        except UnknownFolder:  # deleted since it was planned
            self._fail(customer_id, row.id, "folder_gone")
            return len(data)
        self._finish(job, uploader, row, result.document_id, duplicate=result.duplicate)
        return len(data)

    def _finish(
        self, job: Job, uploader: str, row: items_sql.ItemRow, document_id: str, *, duplicate: bool
    ) -> None:
        """The item stored (audited as an upload) or a duplicate, in one transaction."""
        with unavailable(), self._db.customer(job.customer_id) as conn:
            items_sql.finish_item(
                conn, job.customer_id, row.id, document_id=document_id, duplicate=duplicate, user_id=uploader
            )
            if not duplicate:
                audit.record(
                    conn,
                    job.customer_id,
                    actor_user_id=uploader,
                    action="document.upload",
                    target_type="document",
                    target_id=document_id,
                    details={"zip_document_id": job.document_id},
                )

    def _refusal(self, data: bytes, name: str, policy: UploadPolicy) -> str | None:
        """The file's own virus check, then its kind's limit (told after the scan); None: clean
        and within it. JobFailed (retryable) when the scanner gives no answer."""
        try:
            verdict = self._scanner.scan(data, name=name)
        except ScannerError:
            raise JobFailed("scanner_unavailable", retryable=True) from None
        if verdict is not Verdict.CLEAN:
            return _VERDICTS.get(verdict, "unscannable") if isinstance(verdict, Verdict) else "unscannable"
        decoding_failed = False
        try:
            kind = resolve_kind(data, detect_type(data[:HEAD_SIZE]), name)
        except ReadError:
            return None  # damaged inside: its own job fails it with plain words, as any upload
        except unzip.DECODE_ERRORS:  # any other reader's or decoder's: this file's, never the zip's
            decoding_failed = True
        if decoding_failed:
            return unzip.DAMAGED
        if kind == UNSUPPORTED:
            return "unsupported_type"
        return "too_large" if kind in KIND_OF and len(data) > policy.limit_for(kind) else None

    def _fail(self, customer_id: str, item_id: str, code: str) -> None:
        with unavailable(), self._db.customer(customer_id) as conn:
            items_sql.fail_item(conn, customer_id, item_id, code)


class _ReadingQueue:
    """As the upload's (``routers/documents.py``): the new document gets who asked for its
    reading and the reading chosen, then its job is queued."""

    def __init__(self, db: Database, queue: JobQueue, *, requested_by: str, reading: ReadingChoice) -> None:
        self._db, self._queue = db, queue
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


def _check(unpacked: unzip.Listing, limits: FolderUploadLimits, zip_size: int) -> None:
    """Step 2's limits: JobFailed with the zip's code, before anything is made."""
    files = len(unpacked.files) + len(unpacked.refused)
    if files > limits.max_files:
        raise JobFailed("zip_too_many_files", retryable=False)
    if len(unpacked.folders) + 1 > limits.max_folders:
        raise JobFailed("zip_too_many_folders", retryable=False)
    if unpacked.declared_bytes > limits.max_bytes:
        raise JobFailed("zip_too_large", retryable=False)
    if unpacked.declared_bytes > max(unzip.RATIO_FLOOR, unzip.MAX_RATIO * zip_size):
        raise JobFailed("zip_too_compressed", retryable=False)


def _plan_code(refused: Exception) -> str:
    if isinstance(refused, FolderTooDeep):
        return "folder_too_deep"
    if isinstance(refused, FolderNotFound):
        return "folder_gone"
    if isinstance(refused, FolderNameTaken):
        return "folder_name_taken"
    code = getattr(refused, "code", "")
    if isinstance(refused, PlanRefused | items_sql.ItemsRefused) and code in _PLAN_CODES:
        return _PLAN_CODES[code]
    return "plan_busy" if code == "plan_busy" else "zip_damaged"


def folder_name(zip_name: str) -> str:
    """The folder a zip is unpacked into: its name less ".zip", as a folder name may be (at most
    100 characters; a character upload_name refuses shown as U+FFFD), "Zip" for none."""
    stem = zip_name[:-4] if zip_name.lower().endswith(".zip") else zip_name
    stem = stem.strip()[:FOLDER_NAME_MAX].strip() or "Zip"
    return upload_name(stem, max_length=FOLDER_NAME_MAX) or shown_upload_name(
        stem, max_length=FOLDER_NAME_MAX
    )
