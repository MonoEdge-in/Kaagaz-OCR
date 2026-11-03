"""Accept one upload for one customer (issue #14).

The steps, in order:

1. Read the stream once: fingerprint, size check, spooled copy (spool.py).
   Reading stops past the largest limit of any allowed type.
2. Refuse empty files and types that are not allowed (sniff.py), then a file
   over its own type's limit (PRD 1, U2), with its kind, size and limit.
3. If this customer already has this fingerprint, reply "duplicate" and stop.
4. Count its pages (pagecount.py, PRD 5 Q4): a document of at most
   ``express_max_pages`` pages is queued in the express lane.
5. Store the file, record it, queue a job.

Time O(n) in the bytes read, at most the largest limit plus one byte.

The customer id is an argument. It must come from the server side (how the
server knows the customer is PRD question 3), never from the request body or
the file. The service never sees the file name.

Invariant: a record exists only if its file is stored and its job is queued.
Each partial failure is undone in reverse order:

* storing fails: nothing was recorded, the error propagates.
* recording fails: the stored file is deleted, the error propagates. That
  includes a folder deleted since the API checked it (UnknownFolder), and for
  a new version, a family gone or moved to Trash since then (UnknownFamily).
* lost a race (another identical upload was recorded first): our stored file
  is deleted and the winner's document is returned as a duplicate.
* queueing fails: the record and the file are removed, so the customer can
  simply upload again. Without this undo, the retry would be reported as a
  duplicate of a document that is never processed.

Undo steps run in order and stop at the first one that fails, because each
depends on the one before: the file is never deleted while a record still
points at it. The original error is always the one raised; the failed step is
added to it as a note.

Known leftovers, when the undo itself cannot reach the backend: a record and
its file with no job (removing the record failed), or a stored file with no
record (deleting the file failed). Finding and fixing those later (an
outbox, or re-queueing records that never ran) is database and queue design,
so it is PROVISIONAL and Vrushit's (#1, #16). Also rare: a race loser is told
"duplicate" of a winner that then fails to queue and is rolled back.

A new version (``accept_version``, PRD 1 F6) takes the same steps. Only
recording differs: the repository adds the record to the family, under the
family lock (plan 9.4).

PROVISIONAL: document ids are random UUID4 here; the id format belongs to the
database design (#1).
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from kaagaz.ingestion.errors import BackendUnavailable, RejectCode, UploadRejected
from kaagaz.ingestion.pagecount import count_pages, goes_express
from kaagaz.ingestion.policy import KIND_OF, UploadPolicy
from kaagaz.ingestion.ports import (
    BlobStorage,
    DocumentRecord,
    DocumentRepository,
    Job,
    JobQueue,
    UnknownFamily,
    UnknownFolder,
)
from kaagaz.ingestion.sniff import detect_type
from kaagaz.ingestion.spool import ByteSource, read_limited


@dataclass(frozen=True, slots=True)
class UploadResult:
    document_id: str
    duplicate: bool


def _new_document_id() -> str:
    # Random, so ids reveal nothing about the file and cannot be guessed.
    return str(uuid.uuid4())


class UploadService:
    def __init__(
        self,
        policy: UploadPolicy,
        repository: DocumentRepository,
        storage: BlobStorage,
        queue: JobQueue,
        new_id: Callable[[], str] = _new_document_id,
        *,
        express_max_pages: int = 0,
    ) -> None:
        """``express_max_pages`` is EXPRESS_MAX_PAGES (PRD 5 Q4): an upload of 1 to
        that many pages is queued in the express lane. 0 counts no pages and queues
        every upload in the main lane."""
        self._policy = policy
        self._repository = repository
        self._storage = storage
        self._queue = queue
        self._new_id = new_id
        self._express_max_pages = express_max_pages

    def accept(
        self,
        customer_id: str,
        stream: ByteSource,
        *,
        display_name: str = "document",
        uploaded_by: str | None = None,
        folder_id: str | None = None,
        ocr_choice: str | None = None,
        batch_id: str | None = None,
        keep_place: bool = False,
    ) -> UploadResult:
        """``display_name`` must already be cleaned by the caller (the API).
        ``folder_id`` files a new document there (None: the top level); a
        duplicate stays where it is. ``ocr_choice`` is the scan reader the API
        checked (None: Auto); a duplicate keeps the one it was read with.
        ``batch_id`` is the drop the API checked (PRD 5 Q1; None: in none); a
        duplicate stays in its own. ``keep_place`` (a folder upload's file, #484): it is never
        auto-filed out of ``folder_id``."""
        return self._accept(
            customer_id,
            stream,
            display_name=display_name,
            uploaded_by=uploaded_by,
            folder_id=folder_id,
            ocr_choice=ocr_choice,
            batch_id=batch_id,
            add=self._repository.add_if_absent,
            keep_place=keep_place,
        )

    def accept_version(
        self,
        customer_id: str,
        stream: ByteSource,
        *,
        of_document_id: str,
        display_name: str,
        uploaded_by: str | None,
        ocr_choice: str | None = None,
    ) -> UploadResult:
        """A new version of ``of_document_id``'s family: the checks, duplicate
        rule and undo steps of ``accept``. The repository gives it the family's
        folder, tags and next version_no. Raises UnknownFamily when the document
        is not this customer's or its family is in Trash; the stored file is
        deleted again and nothing is queued."""

        def add_version(record: DocumentRecord) -> tuple[DocumentRecord, bool]:
            added = self._repository.add_version_if_absent(record, of_document_id=of_document_id)
            if added is None:
                raise UnknownFamily(of_document_id)
            return added

        return self._accept(
            customer_id,
            stream,
            display_name=display_name,
            uploaded_by=uploaded_by,
            folder_id=None,
            ocr_choice=ocr_choice,
            batch_id=None,
            add=add_version,
        )

    def _accept(
        self,
        customer_id: str,
        stream: ByteSource,
        *,
        display_name: str,
        uploaded_by: str | None,
        folder_id: str | None,
        ocr_choice: str | None,
        batch_id: str | None,
        add: Callable[[DocumentRecord], tuple[DocumentRecord, bool]],
        keep_place: bool = False,
    ) -> UploadResult:
        """The steps in the module docstring; ``add`` records the new document."""
        if not isinstance(customer_id, str) or not customer_id:
            raise ValueError("customer_id is required")

        with read_limited(stream, self._policy.largest_limit) as spooled:
            if spooled.size == 0:
                raise UploadRejected(RejectCode.EMPTY_FILE)
            detected = detect_type(spooled.head)
            if detected is None or detected not in self._policy.allowed_types:
                raise UploadRejected(RejectCode.UNSUPPORTED_TYPE)
            limit = self._policy.limit_for(detected)
            if spooled.size > limit:
                raise UploadRejected(
                    RejectCode.TOO_LARGE, kind=KIND_OF[detected], size=spooled.size, limit=limit
                )

            # Fast path: a duplicate never touches storage. The lookup is for
            # this customer only, so it cannot reveal other customers' files.
            existing = self._repository.find_by_fingerprint(customer_id, spooled.sha256)
            if existing is not None:
                return UploadResult(existing.document_id, duplicate=True)

            record = DocumentRecord(
                customer_id=customer_id,
                document_id=self._new_id(),
                sha256=spooled.sha256,
                size=spooled.size,
                detected_type=detected,
                display_name=display_name,
                uploaded_by=uploaded_by,
                folder_id=folder_id,
                ocr_choice=ocr_choice,
                batch_id=batch_id,
                keep_place=keep_place,
            )
            # The server's own count picks the lane; it leaves the file at its start.
            express = self._express_max_pages > 0 and goes_express(
                count_pages(spooled.file, detected), self._express_max_pages
            )
            # Store the exact bytes that were hashed.
            self._storage.put(customer_id, record.document_id, spooled.file)

        def delete_file() -> None:
            self._storage.delete(customer_id, record.document_id)

        def remove_record() -> None:
            self._repository.remove(record)

        try:
            stored, created = add(record)
        except (BackendUnavailable, UnknownFolder, UnknownFamily) as error:
            _undo(error, delete_file)
            raise

        if not created:
            delete_file()
            return UploadResult(stored.document_id, duplicate=True)

        try:
            self._queue.enqueue(Job(customer_id=customer_id, document_id=record.document_id), express=express)
        except BackendUnavailable as error:
            _undo(error, remove_record, delete_file)
            raise

        return UploadResult(record.document_id, duplicate=False)


def _undo(error: Exception, *steps: Callable[[], None]) -> None:
    """Run the undo steps in order, stopping at the first that fails.

    ``error`` is never replaced; a failed step is noted on it by name only,
    never with data. Only BackendUnavailable is caught; any other exception
    is a bug and propagates.
    """
    for step in steps:
        try:
            step()
        except BackendUnavailable:
            error.add_note(f"undo step failed: {step.__name__}")
            return
