"""In-memory stand-ins for the database, file storage and queue.

Test-only. They follow the port contracts in kaagaz.ingestion.ports,
kaagaz.scanning.gate (status store, scanner, quarantine) and kaagaz.pipeline
(documents, pipeline store, page pictures, preparer, socket, models),
keyed by customer first, so a cross-customer test can
actually fail. They are never used by product code.
"""

import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from typing import IO, Any

from kaagaz.chunking.piece import Piece
from kaagaz.extraction.learned import LearnedCorrection
from kaagaz.extraction.values import ExtractedValue
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobNotFound, DocumentRecord, Job, UnknownFolder
from kaagaz.pipeline import DocumentContent, PageRecord, ReaderOutput
from kaagaz.reading.socket import NoReader
from kaagaz.reading.spans import CellLocation, PageInfo, ReadError, Span
from kaagaz.scanning.gate import DocumentStatus, ScannerError


class MemoryRepository:
    """Documents keyed by (customer_id, sha256): O(1) average lookup and insert.

    One lock makes check-and-insert atomic, like a UNIQUE (customer_id, sha256)
    constraint would in the real database. A record filed in a folder that is
    not in ``folders`` (keyed by customer) raises UnknownFolder, like the
    folder foreign key.
    """

    def __init__(self) -> None:
        self._by_fingerprint: dict[tuple[str, str], DocumentRecord] = {}
        self._by_id: dict[tuple[str, str], DocumentRecord] = {}
        self.folders: set[tuple[str, str]] = set()  # (customer_id, folder_id)
        self.trashed_families: set[tuple[str, str]] = set()  # (customer_id, family_id) in Trash
        self._lock = threading.Lock()
        self.fail_add = False
        self.fail_remove = False

    def get(self, customer_id: str, document_id: str) -> DocumentRecord | None:
        with self._lock:
            return self._by_id.get((customer_id, document_id))

    def find_by_fingerprint(self, customer_id: str, sha256: str) -> DocumentRecord | None:
        with self._lock:
            return self._by_fingerprint.get((customer_id, sha256))

    def add_if_absent(self, record: DocumentRecord) -> tuple[DocumentRecord, bool]:
        if self.fail_add:
            raise BackendUnavailable("database down")
        key = (record.customer_id, record.sha256)
        with self._lock:
            existing = self._by_fingerprint.get(key)
            if existing is not None:
                return existing, False
            if record.folder_id is not None and (record.customer_id, record.folder_id) not in self.folders:
                raise UnknownFolder(record.document_id)
            self._by_fingerprint[key] = record
            self._by_id[(record.customer_id, record.document_id)] = record
            return record, True

    def add_version_if_absent(
        self, record: DocumentRecord, *, of_document_id: str
    ) -> tuple[DocumentRecord, bool] | None:
        """Like PgDocuments.add_version_if_absent: None for a document this customer
        does not have or a family in ``trashed_families``; else the duplicate rule of
        ``add_if_absent``, then the family's next number, id and folder (no tags here)."""
        if self.fail_add:
            raise BackendUnavailable("database down")
        customer_id = record.customer_id
        with self._lock:
            target = self._by_id.get((customer_id, of_document_id))
            if target is None:
                return None
            family_id = target.family_id or target.document_id
            if (customer_id, family_id) in self.trashed_families:
                return None
            existing = self._by_fingerprint.get((customer_id, record.sha256))
            if existing is not None:
                return existing, False
            numbers = [
                r.version_no
                for (c, _), r in self._by_id.items()
                if c == customer_id and (r.family_id or r.document_id) == family_id
            ]
            stored = replace(
                record, folder_id=target.folder_id, family_id=family_id, version_no=max(numbers) + 1
            )
            self._by_fingerprint[(customer_id, record.sha256)] = stored
            self._by_id[(customer_id, record.document_id)] = stored
            return stored, True

    def remove(self, record: DocumentRecord) -> None:
        if self.fail_remove:
            raise BackendUnavailable("database down")
        key = (record.customer_id, record.sha256)
        with self._lock:
            stored = self._by_fingerprint.get(key)
            if stored is not None and stored.document_id == record.document_id:
                del self._by_fingerprint[key]
                del self._by_id[(record.customer_id, record.document_id)]

    def records_for(self, customer_id: str) -> list[DocumentRecord]:
        with self._lock:
            return [r for (c, _), r in self._by_fingerprint.items() if c == customer_id]


class MemoryStorage:
    """Files keyed by (customer_id, document_id). No key strings are built, so
    no storage naming rule (#11) is invented."""

    def __init__(self) -> None:
        self._blobs: dict[tuple[str, str], bytes] = {}
        self.quarantined: dict[tuple[str, str], bytes] = {}
        self._lock = threading.Lock()
        self.fail_put = False
        self.fail_delete = False
        self.last_put_stream: IO[bytes] | None = None

    def put(self, customer_id: str, document_id: str, data: IO[bytes]) -> None:
        self.last_put_stream = data
        if self.fail_put:
            raise BackendUnavailable("storage down")
        content = data.read()
        with self._lock:
            self._blobs[(customer_id, document_id)] = content

    def get(self, customer_id: str, document_id: str) -> bytes:
        with self._lock:
            try:
                return self._blobs[(customer_id, document_id)]
            except KeyError:
                raise BlobNotFound(document_id) from None

    def delete(self, customer_id: str, document_id: str) -> None:
        if self.fail_delete:
            raise BackendUnavailable("storage down")
        with self._lock:
            self._blobs.pop((customer_id, document_id), None)

    def count(self) -> int:
        with self._lock:
            return len(self._blobs)

    # Quarantine port (kaagaz.scanning.gate.Quarantine): the file moves to a
    # separate dict that get() never reads.
    def quarantine(self, customer_id: str, document_id: str) -> None:
        with self._lock:
            content = self._blobs.pop((customer_id, document_id), None)
            if content is not None:
                self.quarantined[(customer_id, document_id)] = content


class MemoryStatuses:
    """Document status keyed by (customer_id, document_id).

    Like the real store must, it never moves a document out of REJECTED.
    """

    def __init__(self) -> None:
        self._statuses: dict[tuple[str, str], DocumentStatus] = {}

    def get_status(self, customer_id: str, document_id: str) -> DocumentStatus | None:
        return self._statuses.get((customer_id, document_id))

    def set_status(self, customer_id: str, document_id: str, status: DocumentStatus) -> None:
        key = (customer_id, document_id)
        if self._statuses.get(key) is DocumentStatus.REJECTED:
            return  # final
        self._statuses[key] = status


class FakeScanner:
    """Returns a preset verdict, or raises ScannerError. Records what it saw."""

    def __init__(self, verdict: object = None, *, error: bool = False) -> None:
        self.verdict = verdict
        self._error = error
        self.scanned: list[bytes] = []

    def scan(self, data: bytes) -> Any:
        self.scanned.append(data)
        if self._error:
            raise ScannerError("scanner down")
        return self.verdict


class MemoryQueue:
    """Records enqueued jobs in order."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []
        self.fail_enqueue = False

    def enqueue(self, job: Job) -> None:
        if self.fail_enqueue:
            raise BackendUnavailable("queue down")
        self.jobs.append(job)


class MemoryDocuments(MemoryRepository):
    """MemoryRepository plus the pipeline's two writes (kaagaz.pipeline.DocumentStore),
    like kaagaz.db.documents.PgDocuments: ``set_failed`` marks the status failed
    in ``statuses`` (which never leaves REJECTED) and keeps the code. Both do
    nothing for a document this customer does not have, like an UPDATE whose
    WHERE matches no row."""

    def __init__(self, statuses: MemoryStatuses) -> None:
        super().__init__()
        self._statuses = statuses
        self.failure_codes: dict[tuple[str, str], str] = {}
        self.page_counts: dict[tuple[str, str], int] = {}

    def set_failed(self, customer_id: str, document_id: str, code: str) -> None:
        if self.get(customer_id, document_id) is None:
            return
        self._statuses.set_status(customer_id, document_id, DocumentStatus.FAILED)
        self.failure_codes[(customer_id, document_id)] = code

    def set_page_count(self, customer_id: str, document_id: str, page_count: int) -> None:
        if self.get(customer_id, document_id) is not None:
            self.page_counts[(customer_id, document_id)] = page_count

    def mark_exhausted(self, customer_id: str, document_id: str) -> None:
        """The worker's last delivery, like PgDocuments.mark_exhausted: only a document
        still in progress (received or clean) becomes failed with retries_exhausted."""
        if self._statuses.get_status(customer_id, document_id) in (None, DocumentStatus.CLEAN):
            self.set_failed(customer_id, document_id, "retries_exhausted")


class MemoryPipelineStore:
    """What the pipeline saves, keyed by (customer_id, document_id). save() swaps
    the whole content, so running a job twice never duplicates anything
    (kaagaz.pipeline.PipelineStore)."""

    def __init__(self) -> None:
        self._content: dict[tuple[str, str], DocumentContent] = {}
        self.learned: dict[str, list[LearnedCorrection]] = {}  # customer -> corrections
        self.saves = 0

    def save(self, content: DocumentContent) -> None:
        self.saves += 1
        self._content[(content.customer_id, content.document_id)] = content

    def clear(self, customer_id: str, document_id: str) -> None:
        self._content.pop((customer_id, document_id), None)

    def load_learned(self, customer_id: str, fields: Collection[str]) -> list[LearnedCorrection]:
        return [item for item in self.learned.get(customer_id, []) if item.field in fields]

    def content(self, customer_id: str, document_id: str) -> DocumentContent | None:
        return self._content.get((customer_id, document_id))

    def for_document(self, customer_id: str, document_id: str) -> list[Piece]:
        content = self.content(customer_id, document_id)
        return [] if content is None else list(content.pieces)

    def values_for(self, customer_id: str, document_id: str) -> list[ExtractedValue]:
        content = self.content(customer_id, document_id)
        return [] if content is None else list(content.values)

    def outputs_for(self, customer_id: str, document_id: str) -> list[ReaderOutput]:
        content = self.content(customer_id, document_id)
        return [] if content is None else list(content.outputs)

    def pages_for(self, customer_id: str, document_id: str) -> list[PageRecord]:
        content = self.content(customer_id, document_id)
        return [] if content is None else list(content.pages)


class MemoryPageImages:
    """Page pictures keyed by (customer_id, document_id, page_no)."""

    def __init__(self) -> None:
        self.images: dict[tuple[str, str, int], bytes] = {}

    def put_page_image(self, customer_id: str, document_id: str, page_no: int, png: bytes) -> None:
        self.images[(customer_id, document_id, page_no)] = png


@dataclass(frozen=True, slots=True)
class FakePrepared:
    pages: list[PageInfo]
    images: dict[int, bytes]


class FakePreparer:
    """Gives preset pages per file type, and none for any other type (like
    kaagaz.preparing.pages.prepare_pages does for spreadsheets). ``error`` makes
    it raise ReadError with that code instead."""

    def __init__(self, by_type: dict[str, FakePrepared] | None = None, *, error: str | None = None) -> None:
        self._by_type = dict(by_type or {})
        self._error = error

    def prepare(self, detected_type: str, data: bytes) -> FakePrepared:
        if self._error is not None:
            raise ReadError(self._error)
        return self._by_type.get(detected_type, FakePrepared([], {}))


def fake_sheet_pages(spans: list[Span]) -> list[PageInfo]:
    """One page per sheet: width = last column, height = last row (the same rule
    as kaagaz.preparing.pages.sheet_pages, written out here for the tests)."""
    extent: dict[int, tuple[int, int]] = {}
    for span in spans:
        if isinstance(span.location, CellLocation):
            cell = span.location
            columns, rows = extent.get(cell.sheet, (0, 0))
            extent[cell.sheet] = (max(columns, cell.column), max(rows, cell.row))
    return [PageInfo(sheet, float(c), float(r), "cells") for sheet, (c, r) in sorted(extent.items())]


class PresetSocket:
    """A reader socket that gives preset spans per file type
    (kaagaz.pipeline.ReadingSocket). A type with no preset has no reader.
    A preset that is a ReadError is raised instead."""

    def __init__(
        self,
        first: dict[str, list[Span] | ReadError],
        second: dict[str, list[Span] | ReadError] | None = None,
    ) -> None:
        self._first = first
        self._second = second or {}

    def read(
        self, detected_type: str, data: bytes, *, on_page: Callable[[], None] | None = None
    ) -> list[Span]:
        if detected_type not in self._first:
            raise NoReader(detected_type)
        return self._give(self._first[detected_type])

    def read_second(self, detected_type: str, data: bytes) -> list[Span] | None:
        if detected_type not in self._second:
            return None
        return self._give(self._second[detected_type])

    def reader_id(self, detected_type: str, second: bool = False) -> tuple[str, str]:
        return ("preset-second" if second else "preset-first", "1")

    @staticmethod
    def _give(preset: list[Span] | ReadError) -> list[Span]:
        if isinstance(preset, ReadError):
            raise preset
        return list(preset)


class ModelDown(Exception):
    """Stands in for kaagaz.ai.client.ModelUnavailable in the pipeline tests."""

    code = "model_down"


class FakeVision:
    """A vision model: gives preset text for every picture, or raises ModelDown.
    Records each picture it was sent."""

    def __init__(self, text: str = "", *, down: bool = False) -> None:
        self.text = text
        self.down = down
        self.pictures: list[bytes] = []

    def read_image(self, *, png: bytes, instruction: str) -> str:
        self.pictures.append(png)
        if self.down:
            raise ModelDown
        return self.text


class FakeEmbedder:
    """Records (customer_id, document_id) for each call; raises ModelDown if down."""

    def __init__(self, *, down: bool = False) -> None:
        self.down = down
        self.calls: list[tuple[str, str]] = []

    def __call__(self, customer_id: str, document_id: str) -> int:
        self.calls.append((customer_id, document_id))
        if self.down:
            raise ModelDown
        return 0
