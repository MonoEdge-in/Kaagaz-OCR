"""Process one uploaded document: the worker's job handler.

The steps, in order, for one (customer, document):

1. Look up this customer's record. A job naming another customer's document
   stops here, before any file is read, and nothing is written.
2. Scan the file (ScanGate). Nothing below runs unless it is clean, and the
   bytes used below are exactly the bytes that were scanned.
3. Check the scanned bytes still match the fingerprint taken at upload.
4. Prepare the pages: sizes, and a picture of each page (PDFs, photos),
   stored one by one. Spreadsheets get one page per sheet after reading.
5. Read the bytes through the reader socket, then read them again with the
   second reader if the type has one. A type with no reader is marked "needs
   a reader" (its pages are kept) and the job ends normally.
6. Mask identity numbers in every span, text and raw value, first and second
   reading, before anything is kept (CLAUDE.md never-do 3).
7. Pieces for search (kaagaz.chunking) and values for fixed queries
   (kaagaz.extraction), each labelled verified or needs review. For scans,
   when a vision model is set, it reads each value's box again as the second
   reading. This customer's remembered corrections are applied last.
8. Just before saving, the document must still be clean. Everything is saved
   in one transaction that replaces what an earlier run saved, so running the
   same job twice is safe (queues deliver at least once).
9. Meaning vectors for the pieces, if a model is set. A model that is down
   only means no vectors: word and typo search still work.
10. The page count, and the document is ready.

A failure a retry cannot fix marks the document failed with its short code.
A rejected document stays rejected and keeps the reason it was rejected for,
and anything saved from an earlier clean run is removed.

Every value keeps its page and region; one without a valid one is dropped
(CLAUDE.md never-do 5).

Memory: the file's bytes, its spans and one decoded page picture at a time
(vision readings). Time is linear in the spans apart from the sorts in the
chunkers and extractors (O(n log n)).
"""

import functools
import hashlib
import io
import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from PIL import Image

from kaagaz.chunking.lines import pieces_from_lines
from kaagaz.chunking.piece import Piece
from kaagaz.chunking.rows import pieces_from_rows
from kaagaz.extraction.labelled import values_from_lines
from kaagaz.extraction.labels import label_values
from kaagaz.extraction.learned import LearnedCorrection, apply_learned
from kaagaz.extraction.parsing import parse_amount, parse_date
from kaagaz.extraction.spreadsheet import values_from_cells
from kaagaz.extraction.values import ExtractedValue
from kaagaz.ingestion.ports import BlobStorage, DocumentRecord, Job
from kaagaz.jobs.worker import Heartbeat, JobFailed
from kaagaz.reading.socket import NoReader
from kaagaz.reading.spans import BoxLocation, CellLocation, Location, PageInfo, ReadError, Span
from kaagaz.scanning.gate import (
    REJECTION_CODES,
    DocumentStatus,
    Quarantine,
    ScanGate,
    Scanner,
    StatusStore,
    require_clean,
)

if TYPE_CHECKING:
    from kaagaz.ai.client import ModelClient
    from kaagaz.db.engine import Database
    from kaagaz.settings import Settings

_log = logging.getLogger(__name__)

# The scan's own verdicts: recorded as the reason a document was rejected.
_VERDICT_CODES = REJECTION_CODES - {"rejected"}

# Sent with each cropped box. Fixed text: no document text is ever put in it.
VISION_INSTRUCTION = (
    "Read the text in this image exactly as it is written. Reply with that text only, on one line."
)
_CROP_PADDING = 0.005  # a little of the page around a box, as a fraction of the page


# --- what is saved ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageRecord:
    info: PageInfo
    has_image: bool


@dataclass(frozen=True, slots=True)
class ReaderOutput:
    role: str  # "first" or "second"
    reader: str
    version: str
    spans: tuple[Span, ...]  # masked


@dataclass(frozen=True, slots=True)
class DocumentContent:
    """Everything one run keeps for one document, saved (and replaced) as a whole."""

    customer_id: str
    document_id: str
    pages: list[PageRecord]
    outputs: list[ReaderOutput]
    pieces: list[Piece]
    values: list[ExtractedValue]
    reader: str  # the first reader's name, kept with every value


# --- what the processor needs (ports) ------------------------------------------


class IdentityMasker(Protocol):
    def mask(self, text: str) -> str:
        """Return ``text`` with every full Aadhaar or PAN number masked."""
        ...


class DocumentStore(Protocol):
    """kaagaz.db.documents.PgDocuments in production."""

    def get(self, customer_id: str, document_id: str) -> DocumentRecord | None: ...

    def set_failed(self, customer_id: str, document_id: str, code: str) -> None: ...

    def set_page_count(self, customer_id: str, document_id: str, page_count: int) -> None: ...


class ReadingSocket(Protocol):
    """kaagaz.reading.socket.ReaderSocket in production."""

    def read(self, detected_type: str, data: bytes) -> list[Span]: ...

    def read_second(self, detected_type: str, data: bytes) -> list[Span] | None: ...

    def reader_id(self, detected_type: str, second: bool = False) -> tuple[str, str]: ...


class Prepared(Protocol):
    """kaagaz.preparing.pages.PreparedPages in production."""

    @property
    def pages(self) -> Sequence[PageInfo]: ...

    @property
    def images(self) -> Mapping[int, bytes]: ...  # page number -> PNG


class PageImageStore(Protocol):
    def put_page_image(self, customer_id: str, document_id: str, page_no: int, png: bytes) -> None: ...


class PipelineStore(Protocol):
    """kaagaz.db.pipeline_store.PgPipelineStore in production."""

    def save(self, content: DocumentContent) -> None:
        """Replace everything saved for this document with ``content``, atomically."""
        ...

    def clear(self, customer_id: str, document_id: str) -> None:
        """Remove everything saved for this document."""
        ...

    def load_learned(self, customer_id: str, fields: Collection[str]) -> list[LearnedCorrection]: ...


class VisionModel(Protocol):
    def read_image(self, *, png: bytes, instruction: str) -> str: ...


class ProcessorStorage(BlobStorage, Quarantine, PageImageStore, Protocol):
    """What build_processor needs from file storage (kaagaz.adapters.storage.S3Storage)."""


# --- the processor ------------------------------------------------------------


class DocumentProcessor:
    def __init__(
        self,
        *,
        gate: ScanGate,
        statuses: StatusStore,
        documents: DocumentStore,
        socket: ReadingSocket,
        masker: IdentityMasker,
        prepare: Callable[[str, bytes], Prepared],
        sheet_pages: Callable[[list[Span]], Sequence[PageInfo]],
        page_images: PageImageStore,
        store: PipelineStore,
        embed: Callable[[str, str], object] | None = None,
        vision: VisionModel | None = None,
        model_errors: tuple[type[Exception], ...] = (),
    ) -> None:
        """``embed(customer_id, document_id)`` gives the saved pieces their vectors;
        ``vision`` reads scanned boxes again. Either failing with one of
        ``model_errors`` is logged and skipped, never a failed document."""
        self._gate = gate
        self._statuses = statuses
        self._documents = documents
        self._socket = socket
        self._masker = masker
        self._prepare = prepare
        self._sheet_pages = sheet_pages
        self._page_images = page_images
        self._store = store
        self._embed = embed
        self._vision = vision
        self._model_errors = model_errors

    def __call__(self, job: Job, heartbeat: Heartbeat) -> None:
        record = self._documents.get(job.customer_id, job.document_id)
        if record is None:
            # No record for this customer: nothing to mark, nothing read.
            raise JobFailed("document_missing", retryable=False)
        try:
            self._process(job, record, heartbeat)
        except JobFailed as failure:
            if not failure.retryable:
                self._record_failure(job, failure.code)
            raise

    def _process(self, job: Job, record: DocumentRecord, heartbeat: Heartbeat) -> None:
        customer_id, document_id = job.customer_id, job.document_id
        data = self._scan(job)
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise JobFailed("content_changed", retryable=False)
        heartbeat()

        prepared = _or_fail(lambda: self._prepare(record.detected_type, data))
        for page_no, png in prepared.images.items():
            self._page_images.put_page_image(customer_id, document_id, page_no, png)
            heartbeat()
        pages = [PageRecord(info, info.page in prepared.images) for info in prepared.pages]

        try:
            spans = _or_fail(lambda: self._socket.read(record.detected_type, data))
        except NoReader:
            require_clean(self._statuses, job)
            self._store.save(DocumentContent(customer_id, document_id, pages, [], [], [], reader=""))
            self._documents.set_page_count(customer_id, document_id, len(pages))
            self._statuses.set_status(customer_id, document_id, DocumentStatus.NEEDS_READER)
            return
        second = self._read_second(job, record.detected_type, data)
        heartbeat()

        masked = [self._mask(span) for span in spans]
        masked_second = None if second is None else [self._mask(span) for span in second]
        # Lines that held an identity number: their pictures never go to a model.
        with_identity = {s.location for s, m in zip(spans, masked, strict=True) if m != s}
        if not pages:
            cells = [span for span in masked if isinstance(span.location, CellLocation)]
            pages = [PageRecord(info, False) for info in self._sheet_pages(cells)] if cells else []

        # The socket already refuses spans without a real location; this keeps the
        # rule here too, so no piece or value can be saved without one.
        usable = [span for span in masked if _points_at(span.location)]
        pieces = pieces_from_rows(customer_id, document_id, usable)
        pieces += pieces_from_lines(customer_id, document_id, usable)
        values = values_from_cells(usable) + values_from_lines(usable)
        readings = self._vision_readings(job, values, prepared.images, with_identity, heartbeat)
        values = label_values(values, masked_second, readings)
        if values:
            learned = self._store.load_learned(customer_id, {value.field for value in values})
            values = [_with_parsed(value) for value in apply_learned(values, learned)]

        reader, version = self._socket.reader_id(record.detected_type)
        outputs = [ReaderOutput("first", reader, version, tuple(masked))]
        if masked_second is not None:
            second_reader, second_version = self._socket.reader_id(record.detected_type, second=True)
            outputs.append(ReaderOutput("second", second_reader, second_version, tuple(masked_second)))

        require_clean(self._statuses, job)  # still clean just before writing
        self._store.save(DocumentContent(customer_id, document_id, pages, outputs, pieces, values, reader))
        heartbeat()
        self._index(job)
        self._documents.set_page_count(customer_id, document_id, len(pages))
        self._statuses.set_status(customer_id, document_id, DocumentStatus.READY)

    def _scan(self, job: Job) -> bytes:
        try:
            return self._gate.check(job)
        except JobFailed as failure:
            if failure.code in REJECTION_CODES:
                # A file rejected on a later scan (new signatures) must not
                # leave pages, pieces or values from an earlier clean run behind.
                self._store.clear(job.customer_id, job.document_id)
            raise

    def _read_second(self, job: Job, detected_type: str, data: bytes) -> list[Span] | None:
        """The second reading, or None. A second reader that cannot read the file
        only means there is no second reading (single reader); the first stands."""
        try:
            return self._socket.read_second(detected_type, data)
        except ReadError as error:
            _log.warning(
                "second reading skipped",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": error.code},
            )
            return None

    def _mask(self, span: Span) -> Span:
        return replace(span, text=self._masker.mask(span.text), raw=self._masker.mask(span.raw))

    def _record_failure(self, job: Job, code: str) -> None:
        """Mark the document failed with ``code``. A document that is already
        rejected keeps the reason it was rejected for (only the scan's own
        verdict is recorded on it)."""
        rejected = self._statuses.get_status(job.customer_id, job.document_id) is DocumentStatus.REJECTED
        if rejected and code not in _VERDICT_CODES:
            return
        self._documents.set_failed(job.customer_id, job.document_id, code)

    def _index(self, job: Job) -> None:
        if self._embed is None:
            return
        try:
            self._embed(job.customer_id, job.document_id)
        except self._model_errors as error:
            _log.warning(
                "pieces saved without meaning vectors",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": _code(error)},
            )

    def _vision_readings(
        self,
        job: Job,
        values: list[ExtractedValue],
        images: Mapping[int, bytes],
        skip: set[Location],
        heartbeat: Heartbeat,
    ) -> dict[Location, str]:
        """A vision model's reading of each scanned value's box, by box. Boxes whose
        line held an identity number are never sent. Stops at the first model error
        (those values stay single reader). One page picture decoded at a time."""
        if self._vision is None:
            return {}
        wanted: dict[int, dict[BoxLocation, None]] = {}  # page -> boxes, in order, once each
        for value in values:
            box = value.location
            if not isinstance(box, BoxLocation) or value.confidence is None:
                continue  # read directly, not scanned
            if box not in skip and box.page in images:
                wanted.setdefault(box.page, {})[box] = None
        readings: dict[Location, str] = {}
        for page_no, boxes in wanted.items():
            with Image.open(io.BytesIO(images[page_no])) as page:
                for box in boxes:
                    try:
                        text = self._vision.read_image(png=_crop(page, box), instruction=VISION_INSTRUCTION)
                    except self._model_errors as error:
                        _log.warning(
                            "vision reading skipped",
                            extra={
                                "customer_id": job.customer_id,
                                "document_id": job.document_id,
                                "code": _code(error),
                            },
                        )
                        return readings
                    masked = self._masker.mask(text).strip()
                    if masked:
                        readings[box] = masked
            heartbeat()
        return readings


def _or_fail[T](call: Callable[[], T]) -> T:
    """``call()``, with a ReadError turned into a failure that cannot be retried.
    Raised outside the except block, so no parser error (which can quote document
    content) is chained onto it."""
    try:
        return call()
    except ReadError as error:
        code = error.code
    raise JobFailed(code, retryable=False)


def _points_at(location: Location) -> bool:
    if isinstance(location, BoxLocation):
        return location.is_valid()
    return min(location.sheet, location.row, location.column) >= 1


def _with_parsed(value: ExtractedValue) -> ExtractedValue:
    return replace(
        value, value_number=parse_amount(value.value_text), value_date=parse_date(value.value_text)
    )


def _crop(page: Image.Image, box: BoxLocation) -> bytes:
    width, height = page.size
    left = max(0, int((box.x0 - _CROP_PADDING) * width))
    top = max(0, int((box.y0 - _CROP_PADDING) * height))
    right = min(width, int((box.x1 + _CROP_PADDING) * width) + 1)
    bottom = min(height, int((box.y1 + _CROP_PADDING) * height) + 1)
    out = io.BytesIO()
    page.crop((left, top, right, bottom)).save(out, format="PNG")
    return out.getvalue()


def _code(error: Exception) -> str:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else "model_error"


# --- wiring -------------------------------------------------------------------


def build_processor(
    *,
    settings: "Settings",
    db: "Database",
    storage: ProcessorStorage,
    scanner: Scanner,
    model: "ModelClient | None",
) -> DocumentProcessor:
    """The production processor. The concrete parts are imported here only, so the
    processor itself depends on the ports above and is tested with fakes."""
    from kaagaz.ai.client import ModelOutputError, ModelUnavailable
    from kaagaz.db.documents import PgDocuments
    from kaagaz.db.pipeline_store import PgPipelineStore
    from kaagaz.masking.identity import IndianIdMasker
    from kaagaz.preparing.pages import prepare_pages, sheet_pages
    from kaagaz.reading.ocr import build_ocr_engine
    from kaagaz.reading.wiring import build_reader_socket
    from kaagaz.search.indexing import embed_pieces

    documents = PgDocuments(db)

    def prepare(detected_type: str, data: bytes) -> Prepared:
        return prepare_pages(
            detected_type,
            data,
            dpi=settings.page_image_dpi,
            max_pages=settings.max_pages,
            max_image_pixels=settings.max_image_pixels,
        )

    embed: Callable[[str, str], object] | None = None
    if model is not None:
        embed = functools.partial(embed_pieces, db, model=model)

    return DocumentProcessor(
        gate=ScanGate(storage, scanner, storage, documents),
        statuses=documents,
        documents=documents,
        socket=build_reader_socket(settings, build_ocr_engine(settings)),
        masker=IndianIdMasker(),
        prepare=prepare,
        sheet_pages=sheet_pages,
        page_images=storage,
        store=PgPipelineStore(db),
        embed=embed,
        vision=model if model is not None and settings.model_vision else None,
        model_errors=(ModelUnavailable, ModelOutputError),
    )
