"""Process one uploaded document: the worker's job handler.

The steps, in order, for one (customer, document):

1. Look up this customer's record. A job naming another customer's document
   stops here, before any file is read, and nothing is written.
2. Scan the file (ScanGate). Nothing below runs unless it is clean, and the
   bytes used below are exactly the bytes that were scanned.
3. Check the scanned bytes still match the fingerprint taken at upload.
4. Prepare the pages: sizes, and a picture of each page (PDFs, photos),
   stored one by one as WebP. The PNGs kept in memory stay lossless for the
   vision crops. Spreadsheets get one page per sheet after reading.
5. Read the bytes through the reader socket, then read them again with the
   second reader if the type has one. A type with no reader is marked "needs
   a reader" (its pages are kept) and the job ends normally. A reader that
   reads page by page (PDFs, photos) beats the heartbeat after each page, so a
   long scan keeps its lease. Each page then gets the route its reader
   reported through ``on_page`` (PRD 2 R1, ``_with_routes``; a sheet page is
   ``cells``), and the second reading of a page read by OCR, or
   blank, is left out: a scan's hidden text layer must never agree with itself
   (the council's ML 4).
6. Pieces for search (kaagaz.chunking) and the rule values for fixed queries
   (kaagaz.extraction). Then the model stage (PRD 2 D1 to D6,
   ``extraction.understanding``), when a chat model is set: the document card
   (and PR 6's invoice windows), with the heartbeat and progress
   "understanding i of n" after each call and after its searches. The card
   gives the document's type, languages, summary, date and card JSON; with the
   model off or down there is none, the languages come from the pieces (L2),
   and the document is saved exactly as with no model (the run keeps the
   error code, ``model_<code>``). Each piece gets its context header (with the
   card's type and date, C4), and a Latin spelling when it is Devanagari. For
   scans, when a vision model is set, it reads each value's box again as the
   second reading. The rule values are labelled by ``label_values`` and the
   values the model located by ``label_quoted`` (ML 2), each counting the
   other's boxes (council fix 2); a card value is never verified, and no box is
   cropped for a card value or an unsure quote. This customer's remembered
   corrections are applied, then the invoice stage (PRD 2 E5, K1 to K3,
   ``extraction.invoice.finish_invoice``): the invoice row and its checks, from
   the E4 values or, with no type or an invoice type, from labelled.py's values
   (the check's failures put their values in review). Last, the link
   identifiers (D5), only with a card.
7. Just before saving, the document must still be clean. Everything is saved
   in one transaction that replaces what an earlier run saved, so running the
   same job twice is safe (queues deliver at least once).
8. Meaning vectors for the pieces, if a model is set. A model that is down
   only means no vectors: word and typo search still work.
9. The page count, and the document is ready, in one transaction that also
   makes it its family's current version unless a newer version is ready
   already (PRD 1 F6). A version that fails never gets here, so the one before
   it stays current.

A failure a retry cannot fix marks the document failed with its short code.
A rejected document stays rejected and keeps the reason it was rejected for,
and anything saved from an earlier clean run is removed.

Every value keeps its page and region; one without a valid one is dropped
(CLAUDE.md never-do 5).

Runs and progress (PRD 2 R6, X2, X3), when the processor is given them:

* Each delivery starts a run (``RunStore.start``), which marks an older run
  still going as superseded, and finishes it on every path: ready, needs a
  reader, failed or rejected with its code, a backend that is down
  (``<backend>_unavailable``), and superseded. A run finished on a failure path
  is best effort: a database that is down is logged (``run_not_closed``) and the
  first error is raised; the next start closes the run.
* The run fence (Lead's resolution 2): the save, ``mark_ready`` and marking a
  document failed go ahead only while this run is still open. A superseded
  delivery ends quietly (the job is acked, the newer run does the work), so a
  stale delivery after a "read again" never fails the document.
* How far it has got (``ProgressStore.set_progress``) is written in its own
  short transaction between steps, never inside the save, and best effort
  (AR10): a database that is down only means no progress (``progress_not_saved``).
  It is fenced too, so a superseded delivery never overwrites the newer run's
  progress: it ends there, quietly, as above.
* The pages are counted by route as each is read (``on_page``), so a run that
  fails part way records the pages it read; once every page has its route, the
  counts are those of the stored routes.

Memory: the file's bytes, its spans and one decoded page picture at a time
(vision readings). Time is linear in the spans apart from the sorts in the
chunkers and extractors (O(n log n)).
"""

import functools
import hashlib
import io
import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from importlib import metadata
from typing import TYPE_CHECKING, Any, Literal, Protocol

from PIL import Image

from kaagaz.chunking.context import with_context
from kaagaz.chunking.lines import pieces_from_lines
from kaagaz.chunking.piece import Piece
from kaagaz.chunking.rows import pieces_from_rows
from kaagaz.chunking.script import languages_without_model
from kaagaz.extraction.card import ChatModel
from kaagaz.extraction.invoice import finish_invoice, local_today
from kaagaz.extraction.labelled import tax_components, values_from_lines
from kaagaz.extraction.labels import label_quoted, label_values, spot_unsure
from kaagaz.extraction.learned import LearnedCorrection, apply_learned
from kaagaz.extraction.parsing import parse_amount, parse_date
from kaagaz.extraction.quotes import reader_text
from kaagaz.extraction.spreadsheet import values_from_cells
from kaagaz.extraction.understanding import (
    ModelBudget,
    Understanding,
    document_links,
    model_config,
    understand,
)
from kaagaz.extraction.values import CARD_RECORD, DocumentFacts, ExtractedValue, InvoiceRow, LinkIdentifier
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobStorage, DocumentRecord, Job
from kaagaz.jobs.ports import LeaseLost
from kaagaz.jobs.worker import Heartbeat, JobFailed, Superseded
from kaagaz.preparing.page_checks import PageCheck
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import NoReader, OnPage
from kaagaz.reading.spans import (
    DIRECT_ORIGINS,
    BoxLocation,
    CellLocation,
    Location,
    PageInfo,
    ReadError,
    Span,
    TooManyPages,
)
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

# Stored page pictures are for people to look at (PRD 1, S1), so they are WebP.
_WEBP_QUALITY = 80
_WEBP_METHOD = 4
_WEBP_MAX_SIDE = 16383  # the longest side a WebP picture can have

# Security A8 (PROVISIONAL): at most this many value boxes of one document are
# sent to the vision model; the values past it stay single_reader.
MAX_VISION_CROPS = 200
_BACKEND_NAME = re.compile(r"[a-z]{1,32}")  # "ocr", "storage", ...: a run's <backend>_unavailable
# The PDF reader adds ";ocr_dpi=N" to its version when a page was drawn below OCR_DPI.
_LOWERED_OCR_DPI = re.compile(r";ocr_dpi=([0-9]{1,5})\b")


# --- what is saved ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageRecord:
    info: PageInfo
    has_image: bool
    route: str | None = None  # a Route (kaagaz.reading.route); None when the page was not read
    effective_dpi: int | None = None
    low_resolution: bool = False


@dataclass(frozen=True, slots=True)
class ReaderOutput:
    role: str  # "first" or "second"
    reader: str
    version: str
    spans: tuple[Span, ...]


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
    facts: DocumentFacts = field(default_factory=DocumentFacts)
    invoice: InvoiceRow | None = None
    links: tuple[LinkIdentifier, ...] = ()  # this document's own identifiers


# --- runs and progress (R6, X2, X3) -------------------------------------------

Step = Literal["scanning", "preparing", "reading", "understanding", "indexing"]


@dataclass(frozen=True, slots=True)
class Progress:
    step: Step
    done: int
    total: int

    def to_json(self) -> dict[str, object]:
        return {"step": self.step, "done": self.done, "total": self.total}


@dataclass(frozen=True, slots=True)
class RunTotals:
    status: str  # "ready", "failed", "needs_reader", "rejected" or "superseded"
    error_code: str | None
    ocr_engine: str | None  # "<name> <version>" when any page went to OCR
    model_chat: str | None  # the model tag, when a chat call was made
    model_vision: str | None  # when a crop was read
    model_embed: str | None  # when embedding ran
    pages_cells: int
    pages_text_layer: int
    pages_ocr: int
    pages_blank: int
    vision_crops: int
    model_calls: int
    dropped_quotes: int
    seconds_total: float | None
    seconds_ocr: float | None
    quotes_returned: int = 0
    values_verified: int = 0
    values_needs_review: int = 0
    config: Mapping[str, Any] = field(default_factory=dict)  # how the run was set up, plain JSON (ML10)


@dataclass(frozen=True, slots=True)
class FinishedRun:
    run_id: str
    totals: RunTotals
    now: datetime


# --- what the processor needs (ports) ------------------------------------------


class DocumentStore(Protocol):
    """kaagaz.db.documents.PgDocuments in production."""

    def get(self, customer_id: str, document_id: str) -> DocumentRecord | None: ...

    def set_failed(self, customer_id: str, document_id: str, code: str, *, run_id: str | None = None) -> None:
        """Failed with ``code``. With ``run_id``, raise Superseded and change nothing
        unless that run is still open (the run fence)."""
        ...

    def set_page_count(self, customer_id: str, document_id: str, page_count: int) -> None: ...

    def mark_ready(
        self, customer_id: str, document_id: str, page_count: int, *, run: FinishedRun | None = None
    ) -> None:
        """Ready with its page count, and current unless a newer version is ready.
        Never moves a document out of rejected. With ``run``, finish that run in the
        same transaction, or raise Superseded when it is no longer open."""
        ...


class RunStore(Protocol):
    """kaagaz.db.runs.PgRuns in production."""

    def start(self, customer_id: str, document_id: str, *, now: datetime) -> str: ...

    def finish(self, customer_id: str, run: FinishedRun) -> None:
        """Write the run's outcome and counts. A run superseded meanwhile keeps its status
        and gets its counts only."""
        ...


class ProgressStore(Protocol):
    """kaagaz.db.documents.PgDocuments in production."""

    def set_progress(
        self, customer_id: str, document_id: str, progress: Progress, *, run_id: str | None = None
    ) -> None:
        """With ``run_id``, raise Superseded and change nothing unless that run is still
        open (the run fence)."""
        ...


class OcrMeter(Protocol):
    """kaagaz.reading.ocr.MeteredOcrEngine in production: the engine the readers use."""

    name: str

    @property
    def version(self) -> str: ...

    def take(self) -> tuple[int, float]:
        """(pages sent to OCR, seconds) since the last take, then zero again."""
        ...


class ReadingSocket(Protocol):
    """kaagaz.reading.socket.ReaderSocket in production."""

    def read(self, detected_type: str, data: bytes, *, on_page: OnPage | None = None) -> list[Span]: ...

    def read_second(self, detected_type: str, data: bytes) -> list[Span] | None: ...

    def reader_id(self, detected_type: str, second: bool = False) -> tuple[str, str]: ...


class Prepared(Protocol):
    """kaagaz.preparing.pages.PreparedPages in production."""

    @property
    def pages(self) -> Sequence[PageInfo]: ...

    @property
    def images(self) -> Mapping[int, bytes]: ...  # page number -> PNG

    @property
    def checks(self) -> Mapping[int, PageCheck]: ...  # page number -> blank, effective DPI (R3, R4)


class PageImageStore(Protocol):
    def put_page_image(self, customer_id: str, document_id: str, page_no: int, image: bytes) -> None: ...


class PipelineStore(Protocol):
    """kaagaz.db.pipeline_store.PgPipelineStore in production."""

    def save(self, content: DocumentContent, *, run_id: str | None = None) -> None:
        """Replace everything saved for this document with ``content``, atomically.
        With ``run_id``, raise Superseded and write nothing unless that run is still
        open (the run fence)."""
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


@dataclass
class _Run:
    """One delivery's run (R6) and what it has counted so far. ``run_id`` is None
    when the processor keeps no runs."""

    run_id: str | None
    started: float
    routes: Counter[str] = field(default_factory=Counter)
    vision_crops: int = 0
    values_verified: int = 0
    values_needs_review: int = 0
    embedded: bool = False
    lowest_ocr_dpi: int | None = None  # when a page was drawn for OCR below OCR_DPI
    model_calls: int = 0  # the card and the invoice windows called (PRD 2 D1, E2)
    model_error: str | None = None  # "model_<code>" when a call failed; the document still ends ready
    quotes_returned: int = 0
    dropped_quotes: int = 0
    quote_config: dict[str, int] = field(default_factory=dict)  # loose, ambiguous, summary_dropped (ML 10)


class DocumentProcessor:
    def __init__(
        self,
        *,
        gate: ScanGate,
        statuses: StatusStore,
        documents: DocumentStore,
        socket: ReadingSocket,
        prepare: Callable[[str, bytes], Prepared],
        sheet_pages: Callable[[list[Span]], Sequence[PageInfo]],
        page_images: PageImageStore,
        store: PipelineStore,
        embed: Callable[[str, str], object] | None = None,
        vision: VisionModel | None = None,
        model_errors: tuple[type[Exception], ...] = (),
        runs: RunStore | None = None,
        progress: ProgressStore | None = None,
        ocr_meter: OcrMeter | None = None,
        clock: Callable[[], datetime] | None = None,
        timer: Callable[[], float] | None = None,
        max_vision_crops: int = MAX_VISION_CROPS,
        run_config: Mapping[str, Any] | None = None,
        vision_model: str | None = None,
        embed_model: str | None = None,
        chat: ChatModel | None = None,
        chat_model: str | None = None,
        model_budget: ModelBudget | None = None,
        timezone: str = "UTC",
    ) -> None:
        """``embed(customer_id, document_id)`` gives the saved pieces their vectors;
        ``vision`` reads scanned boxes again. Either failing with one of
        ``model_errors`` is logged and skipped, never a failed document.

        The rest are for PRD 2 and all optional: ``runs`` keeps one run per delivery
        (it needs ``clock`` for its times and ``timer`` for its seconds);
        ``progress`` shows how far a document has got; ``ocr_meter`` is the OCR
        engine the readers use, counted; ``run_config`` is the run's plain JSON set-up
        (ML 10); ``vision_model`` and ``embed_model`` are the tags a run records
        when it used them. ``chat`` is the model stage's chat model (PRD 2 D1, E2),
        with its ``model_budget``; ``chat_model`` is the tag a run records when a
        chat call was made. ``timezone`` (settings.timezone) says which day it is for
        the invoice checks (PRD 2 ML 6(j)): today is the clock's date there."""
        if runs is not None and (clock is None or timer is None):
            raise ValueError("runs need a clock and a timer")
        if max_vision_crops < 0:
            raise ValueError("max_vision_crops must be 0 or more")
        if chat is not None and model_budget is None:
            raise ValueError("a chat model needs its model_budget")
        self._gate = gate
        self._statuses = statuses
        self._documents = documents
        self._socket = socket
        self._prepare = prepare
        self._sheet_pages = sheet_pages
        self._page_images = page_images
        self._store = store
        self._embed = embed
        self._vision = vision
        self._model_errors = model_errors
        self._runs = runs
        self._progress_store = progress
        self._ocr_meter = ocr_meter
        self._clock = clock or _utc_now
        self._timer = timer or time.perf_counter
        self._max_vision_crops = max_vision_crops
        self._run_config = dict(run_config or {})
        self._vision_model = vision_model
        self._embed_model = embed_model
        self._chat = chat
        self._chat_model = chat_model
        self._model_budget = model_budget
        self._timezone = timezone

    def __call__(self, job: Job, heartbeat: Heartbeat) -> None:
        record = self._documents.get(job.customer_id, job.document_id)
        if record is None:
            # No record for this customer: nothing to mark, nothing read.
            raise JobFailed("document_missing", retryable=False)
        run = self._start_run(job)
        settled = False  # the run was finished, or is being, on a known path
        try:
            self._process(job, record, heartbeat, run)
            settled = True
        except Superseded:
            settled = True
            self._close_run(job, run, "superseded", None)  # acked: the newer run does the work
        except LeaseLost:
            settled = True
            self._close_run(job, run, "superseded", None)  # another delivery owns the job now
            raise
        except JobFailed as failure:
            settled = True
            if not failure.retryable and not self._record_failure(job, failure.code, run):
                self._close_run(job, run, "superseded", None)
                return  # a newer run has started: this stale delivery fails nothing
            status = "rejected" if failure.code in REJECTION_CODES else "failed"
            self._close_run(job, run, status, failure.code)
            raise
        except BackendUnavailable as error:
            settled = True
            self._close_run(job, run, "failed", _unavailable_code(error))
            raise
        finally:
            if not settled:
                # Anything else is a bug: it is not caught (no blanket except) and goes on
                # up to the worker, but its run ends failed rather than staying open.
                self._close_run(job, run, "failed", "internal_error")

    def _process(self, job: Job, record: DocumentRecord, heartbeat: Heartbeat, run: _Run) -> None:
        customer_id, document_id = job.customer_id, job.document_id
        self._progress(job, run, "scanning", 0, 1)
        data = self._scan(job, run)
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise JobFailed("content_changed", retryable=False)
        heartbeat()

        prepared = _or_fail(lambda: self._prepare_or_count(job, record.detected_type, data))
        for done, (page_no, png) in enumerate(prepared.images.items(), start=1):
            self._page_images.put_page_image(customer_id, document_id, page_no, _stored_picture(png))
            heartbeat()
            self._progress(job, run, "preparing", done, len(prepared.images))
        pages = [_page_record(info, prepared) for info in prepared.pages]

        self._progress(job, run, "reading", 0, len(pages))
        reads: list[RouteDecision | None] = []  # how each page was read, as the reader said
        on_page = self._reading_beat(job, run, heartbeat, len(pages), reads)
        try:
            spans = _or_fail(lambda: self._socket.read(record.detected_type, data, on_page=on_page))
        except NoReader:
            require_clean(self._statuses, job)
            pages = _with_routes(pages, reads, prepared.checks)  # a blank page is blank still
            run.routes = Counter(page.route for page in pages if page.route is not None)
            content = DocumentContent(customer_id, document_id, pages, [], [], [], reader="")
            self._store.save(content, run_id=run.run_id)
            self._documents.set_page_count(customer_id, document_id, len(pages))
            self._statuses.set_status(customer_id, document_id, DocumentStatus.NEEDS_READER)
            self._close_run(job, run, "needs_reader", None)
            return
        second = self._read_second(job, record.detected_type, data)
        heartbeat()

        if not pages:
            cells = [span for span in spans if isinstance(span.location, CellLocation)]
            pages = [PageRecord(info, False) for info in self._sheet_pages(cells)] if cells else []

        # The socket already refuses spans without a real location; this keeps the
        # rule here too, so no piece or value can be saved without one.
        usable = [span for span in spans if _points_at(span.location)]
        pages = _with_routes(pages, reads, prepared.checks)
        run.routes = Counter(page.route for page in pages if page.route is not None)
        # The raw second reading is kept as read; only the labels leave pages out.
        compared = None if second is None else _without_unread_pages(second, pages)
        pieces = pieces_from_rows(customer_id, document_id, usable)
        pieces += pieces_from_lines(customer_id, document_id, usable)
        understanding = self._understand(
            job, run, heartbeat, usable, values_from_cells(usable) + values_from_lines(usable)
        )
        facts = understanding.facts
        # L2: the card says the languages; with no card (the model off or down) the pieces do.
        facts = replace(facts, languages=facts.languages or languages_without_model(pieces))
        # C4: the card's type and date go in every piece's context header.
        pieces = with_context(
            pieces,
            document_name=record.display_name,
            doc_type=facts.doc_type,
            document_date=facts.document_date,
        )
        rule, quoted = understanding.rule_values, understanding.quoted_values
        # A card value, or a located value whose spot is unsure (a loose, ambiguous or
        # several-span quote), is never verified, so its box is never cropped (security A8).
        cropped = rule + [
            value for value in quoted if value.record_key != CARD_RECORD and not spot_unsure(value)
        ]
        readings = self._vision_readings(job, cropped, prepared.images, heartbeat, run)
        values = label_values(rule, compared, readings, also_located=quoted)
        values += label_quoted(quoted, compared, readings, also_located=rule)
        if values:
            learned = self._store.load_learned(customer_id, {value.field for value in values})
            values = [_with_parsed(value) for value in apply_learned(values, learned)]
        values, invoice = self._invoice(understanding, facts, usable, values)
        links = document_links(facts, values)
        run.values_verified = sum(1 for value in values if value.label == "verified")
        run.values_needs_review = len(values) - run.values_verified

        reader, version = self._socket.reader_id(record.detected_type)
        lowered = _LOWERED_OCR_DPI.search(version)
        run.lowest_ocr_dpi = None if lowered is None else int(lowered.group(1))
        outputs = [ReaderOutput("first", reader, version, tuple(spans))]
        if second is not None:
            second_reader, second_version = self._socket.reader_id(record.detected_type, second=True)
            outputs.append(ReaderOutput("second", second_reader, second_version, tuple(second)))

        require_clean(self._statuses, job)  # still clean just before writing
        content = DocumentContent(
            customer_id,
            document_id,
            pages,
            outputs,
            pieces,
            values,
            reader,
            facts=facts,
            invoice=invoice,
            links=links,
        )
        self._store.save(content, run_id=run.run_id)
        heartbeat()
        self._progress(job, run, "indexing", 0, 1)
        run.embedded = self._index(job)
        finished = self._finished(run, "ready", run.model_error)
        self._documents.mark_ready(customer_id, document_id, len(pages), run=finished)

    def _invoice(
        self,
        understanding: Understanding,
        facts: DocumentFacts,
        spans: list[Span],
        values: list[ExtractedValue],
    ) -> tuple[list[ExtractedValue], InvoiceRow | None]:
        """PRD 2 E5 and K1 to K3 (``extraction.invoice.finish_invoice``): the invoice row
        and its checks, on the labelled values. A row comes from the E4 values, or with no
        type or an invoice type (the model off, down or silent) from labelled.py's values
        (PROVISIONAL). O(v) plus labelled.py's pass over the spans."""
        result = finish_invoice(
            values,
            extraction=understanding.invoice,
            labelled=values_from_lines(spans),
            components=tax_components(spans),
            today=local_today(self._clock(), self._timezone),
            fallback=facts.doc_type in (None, "invoice"),
        )
        return result.values, result.invoice

    def _understand(
        self, job: Job, run: _Run, heartbeat: Heartbeat, spans: list[Span], values: list[ExtractedValue]
    ) -> Understanding:
        """The model stage (``extraction.understanding``), and its numbers on the run.
        With no chat model it is today's result: no card, and the rule values as they are."""

        def beat(done: int, total: int) -> None:
            heartbeat()
            self._progress(job, run, "understanding", done, total)

        budget = self._model_budget
        chat = self._chat if budget is not None else None
        understanding = understand(
            chat,
            reader_text(spans if chat is not None else []),
            values,
            card_max_chars=0 if budget is None else budget.card_max_chars,
            card_max_tokens=0 if budget is None else budget.card_max_tokens,
            num_ctx=0 if budget is None else budget.num_ctx,
            run_id=run.run_id,
            beat=beat,
            spans=spans,
            invoice_windows=None if budget is None else budget.invoice_windows,
        )
        run.model_calls = understanding.model_calls
        run.model_error = understanding.error_code
        run.quotes_returned = understanding.quotes_returned
        run.dropped_quotes = understanding.dropped_quotes
        if understanding.counts is not None:
            run.quote_config = {
                "loose_quotes": understanding.loose_quotes,
                "ambiguous_quotes": understanding.ambiguous_quotes,
                "summary_dropped": understanding.counts.summary_dropped,
            }
        return understanding

    def _prepare_or_count(self, job: Job, detected_type: str, data: bytes) -> Prepared:
        """``prepare``; a file with too many pages keeps its real page count before
        it fails, so its message can name it."""
        try:
            return self._prepare(detected_type, data)
        except TooManyPages as error:
            self._documents.set_page_count(job.customer_id, job.document_id, error.pages)
            raise

    def _scan(self, job: Job, run: _Run) -> bytes:
        try:
            return self._gate.check(job, run_id=run.run_id)
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

    def _record_failure(self, job: Job, code: str, run: _Run) -> bool:
        """Mark the document failed with ``code``, and return True. A document that
        is already rejected keeps the reason it was rejected for (only the scan's
        own verdict is recorded on it). False, with nothing changed, when this run
        is no longer open: a newer run has started (the run fence)."""
        rejected = self._statuses.get_status(job.customer_id, job.document_id) is DocumentStatus.REJECTED
        if rejected and code not in _VERDICT_CODES:
            return True
        try:
            self._documents.set_failed(job.customer_id, job.document_id, code, run_id=run.run_id)
        except Superseded:
            return False
        return True

    def _index(self, job: Job) -> bool:
        """Make the pieces' meaning vectors; True if it ran."""
        if self._embed is None:
            return False
        try:
            self._embed(job.customer_id, job.document_id)
        except self._model_errors as error:
            _log.warning(
                "pieces saved without meaning vectors",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": _code(error)},
            )
            return False
        return True

    def _vision_readings(
        self,
        job: Job,
        values: list[ExtractedValue],
        images: Mapping[int, bytes],
        heartbeat: Heartbeat,
        run: _Run,
    ) -> dict[Location, str]:
        """A vision model's reading of each scanned value's box, by box, with a
        heartbeat after each (security A8). Stops at the first model error, and
        after ``max_vision_crops`` boxes; the values left stay single reader. One
        page picture decoded at a time."""
        if self._vision is None:
            return {}
        wanted: dict[int, dict[BoxLocation, None]] = {}  # page -> boxes, in order, once each
        for value in values:
            box = value.location
            if not isinstance(box, BoxLocation) or (
                value.confidence is None and value.origin in DIRECT_ORIGINS
            ):
                continue  # read directly, not scanned; no origin counts as scanned (fail closed)
            if box.page in images:
                wanted.setdefault(box.page, {})[box] = None
        readings: dict[Location, str] = {}
        for page_no, boxes in wanted.items():
            with Image.open(io.BytesIO(images[page_no])) as page:
                for box in boxes:
                    if run.vision_crops >= self._max_vision_crops:
                        return readings
                    run.vision_crops += 1
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
                    heartbeat()
                    text = text.strip()
                    if text:
                        readings[box] = text
        return readings

    # --- runs and progress (R6, X2, X3) -----------------------------------------

    def _start_run(self, job: Job) -> _Run:
        """A new run of this delivery, started now. The OCR meter starts from zero (AR11)."""
        if self._ocr_meter is not None:
            self._ocr_meter.take()
        run_id = None
        if self._runs is not None:
            run_id = self._runs.start(job.customer_id, job.document_id, now=self._clock())
        return _Run(run_id, self._timer())

    def _finished(self, run: _Run, status: str, error_code: str | None) -> FinishedRun | None:
        """The run as it ends now, with everything it counted; None without runs.
        The OCR meter is read and set back to zero either way (AR11)."""
        meter = self._ocr_meter
        pages_sent, ocr_seconds = meter.take() if meter is not None else (0, 0.0)
        if run.run_id is None:
            return None
        totals = RunTotals(
            status=status,
            error_code=error_code,
            ocr_engine=f"{meter.name} {meter.version}"[:100] if meter is not None and pages_sent else None,
            model_chat=self._chat_model if run.model_calls else None,
            model_vision=self._vision_model if run.vision_crops else None,
            model_embed=self._embed_model if run.embedded else None,
            pages_cells=run.routes["cells"],
            pages_text_layer=run.routes["text_layer"],
            pages_ocr=run.routes["ocr"],
            pages_blank=run.routes["blank"],
            vision_crops=run.vision_crops,
            model_calls=run.model_calls,
            dropped_quotes=run.dropped_quotes,
            quotes_returned=run.quotes_returned,
            seconds_total=max(0.0, self._timer() - run.started),
            seconds_ocr=ocr_seconds if meter is not None else None,
            values_verified=run.values_verified,
            values_needs_review=run.values_needs_review,
            config={
                **self._run_config,
                **run.quote_config,
                **({} if run.lowest_ocr_dpi is None else {"ocr_dpi_lowest": run.lowest_ocr_dpi}),
            },
        )
        return FinishedRun(run.run_id, totals, self._clock())

    def _close_run(self, job: Job, run: _Run, status: str, error_code: str | None) -> None:
        """Finish the run on a path that does not mark the document ready. Best
        effort: a database that is down is logged, and the next start closes it."""
        finished = self._finished(run, status, error_code)
        if finished is None or self._runs is None:
            return
        try:
            self._runs.finish(job.customer_id, finished)
        except BackendUnavailable:
            _log.warning(
                "run_not_closed",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": status},
            )

    def _progress(self, job: Job, run: _Run, step: Step, done: int, total: int) -> None:
        """Show how far it has got, in its own short transaction. Best effort (AR10), but
        fenced: the Superseded of a run no longer open goes on up."""
        if self._progress_store is None:
            return
        try:
            self._progress_store.set_progress(
                job.customer_id, job.document_id, Progress(step, done, total), run_id=run.run_id
            )
        except BackendUnavailable:
            _log.warning(
                "progress_not_saved",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": step},
            )

    def _reading_beat(
        self, job: Job, run: _Run, heartbeat: Heartbeat, total: int, reads: list[RouteDecision | None]
    ) -> OnPage:
        """The readers' ``on_page``: keep how the page was read and count it by its route
        (R6), the heartbeat, then "reading i of total"."""
        done = 0

        def on_page(read: RouteDecision | None) -> None:
            nonlocal done
            done += 1
            reads.append(read)
            if read is not None:
                run.routes[read.route] += 1
            heartbeat()
            self._progress(job, run, "reading", min(done, total), total)

        return on_page


def _or_fail[T](call: Callable[[], T]) -> T:
    """``call()``, with a ReadError turned into a failure that cannot be retried.
    Raised outside the except block, so no parser error (which can quote document
    content) is chained onto it."""
    try:
        return call()
    except ReadError as error:
        code = error.code
    raise JobFailed(code, retryable=False)


def _page_record(info: PageInfo, prepared: Prepared) -> PageRecord:
    """The page as saved, with its effective DPI and low-resolution flag (R4)."""
    check = prepared.checks.get(info.page)
    record = PageRecord(info, info.page in prepared.images)
    if check is None:
        return record
    return replace(record, effective_dpi=check.effective_dpi, low_resolution=check.low_resolution)


def _with_routes(
    pages: list[PageRecord], reads: Sequence[RouteDecision | None], checks: Mapping[int, PageCheck]
) -> list[PageRecord]:
    """Each page with its route (R1): a sheet's page is cells; any other page's is
    the one its reader said it read it by (``on_page``, in page order), so a stored
    route never disagrees with what was read (council PR 3 review, low 7).

    A page its reader gave no route (with OCR_ENGINE=none, a PDF page with no text
    layer: nothing read it) is ``blank`` when its picture is blank (R3, ``checks``),
    which needs no reader; any other stays None, "not read", and is never ``ocr``,
    since nothing went to OCR. A document none of whose pages could be read is
    needs_reader (the PDF reader raises NoReader), so on a ready document a None
    route names the pages that alone were not read. O(pages)."""
    routed = []
    for page in pages:
        index = page.info.page - 1
        if page.info.unit == "cells":
            route: str | None = "cells"
        else:
            read = reads[index] if 0 <= index < len(reads) else None
            check = checks.get(page.info.page)
            blank = check is not None and check.blank
            route = read.route if read is not None else ("blank" if blank else None)
        routed.append(replace(page, route=route))
    return routed


def _without_unread_pages(second: list[Span], pages: Sequence[PageRecord]) -> list[Span]:
    """The second reading without the pages that were read by OCR or are blank: a
    second reader reads text layers only, so there it could only agree with a
    text layer the route check threw out, such as a scan's hidden OCR layer
    (ML 4). PROVISIONAL (plan 4.1, step 7). O(spans + pages)."""
    left_out = {page.info.page for page in pages if page.route in ("ocr", "blank")}
    return [span for span in second if span.location.page not in left_out]


def _unavailable_code(error: BackendUnavailable) -> str:
    """The run's error code for a backend that is down: ``ocr_unavailable`` and so on."""
    name = str(error)
    return f"{name}_unavailable" if _BACKEND_NAME.fullmatch(name) else "backend_unavailable"


def _utc_now() -> datetime:
    return datetime.now(UTC)


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


def _stored_picture(png: bytes) -> bytes:
    """The page picture as stored: WebP, made only here, just before storing, so
    the PNG kept for the vision crops stays lossless. O(pixels), one page in memory.

    PROVISIONAL (plan 7.6): a side longer than WebP allows is shrunk for storage
    only. The page keeps its real size, and boxes are fractions of it, so they
    still fit the smaller picture.
    """
    with Image.open(io.BytesIO(png)) as page:
        picture: Image.Image = page
        longest = max(page.size)
        if longest > _WEBP_MAX_SIDE:
            scale = _WEBP_MAX_SIDE / longest
            size = (_scaled(page.width, scale), _scaled(page.height, scale))
            picture = page.resize(size, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        picture.save(out, format="WEBP", quality=_WEBP_QUALITY, method=_WEBP_METHOD)
    return out.getvalue()


def _scaled(side: int, scale: float) -> int:
    return min(_WEBP_MAX_SIDE, max(1, round(side * scale)))


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
    from kaagaz.db.runs import PgRuns
    from kaagaz.preparing.pages import prepare_pages, sheet_pages
    from kaagaz.reading.ocr import MeteredOcrEngine, build_ocr_engine
    from kaagaz.reading.wiring import build_reader_socket, reader_config
    from kaagaz.search.indexing import embed_pieces

    documents = PgDocuments(db)

    def prepare(detected_type: str, data: bytes) -> Prepared:
        return prepare_pages(
            detected_type,
            data,
            dpi=settings.page_image_dpi,
            max_pages=settings.max_pages,
            max_image_pixels=settings.max_image_pixels,
            blank_stddev=settings.blank_page_stddev,
            low_res_dpi=settings.low_res_dpi,
        )

    embed: Callable[[str, str], object] | None = None
    if model is not None:
        embed = functools.partial(embed_pieces, db, model=model)
    engine = build_ocr_engine(settings)
    meter = None if engine is None else MeteredOcrEngine(engine, timer=time.perf_counter)
    vision = model if model is not None and settings.model_vision else None

    return DocumentProcessor(
        gate=ScanGate(storage, scanner, storage, documents),
        statuses=documents,
        documents=documents,
        socket=build_reader_socket(settings, meter),
        prepare=prepare,
        sheet_pages=sheet_pages,
        page_images=storage,
        store=PgPipelineStore(db),
        embed=embed,
        vision=vision,
        model_errors=(ModelUnavailable, ModelOutputError),
        runs=PgRuns(db),
        progress=documents,
        ocr_meter=meter,
        clock=_utc_now,
        timer=time.perf_counter,
        run_config={
            "build": _build(),
            **reader_config(settings),
            "max_vision_crops": MAX_VISION_CROPS,
            **(model_config(settings) if model is not None else {}),
        },
        vision_model=settings.model_vision if vision is not None else None,
        embed_model=settings.model_embed if embed is not None else None,
        chat=model,
        chat_model=settings.model_chat if model is not None else None,
        model_budget=ModelBudget.from_settings(settings) if model is not None else None,
        timezone=settings.timezone,
    )


def _build() -> str:
    """The backend's version, as installed (ML 10's build). PROVISIONAL: a build's
    commit id needs the image to carry it, which is Vrushit's CI."""
    try:
        return metadata.version("kaagaz-backend")
    except metadata.PackageNotFoundError:
        return "unknown"
