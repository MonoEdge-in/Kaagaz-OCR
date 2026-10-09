"""Process one uploaded document: the worker's job handler.

The steps, in order, for one (customer, document):

1. Look up this customer's record. A job naming another customer's document
   stops here, before any file is read, and nothing is written. A customer that
   is disabled or being deleted (0014) stops here too, before any file is read
   or any model called: the document is marked failed with
   ``customer_disabled``, and the job is not retried.
2. Scan the file (ScanGate). Nothing below runs unless it is clean, and the
   bytes used below are exactly the bytes that were scanned.
3. Check the scanned bytes still match the fingerprint taken at upload.
4. Prepare the pages: sizes, and a picture of each page (PDFs, photos),
   streamed (council A5b): each page is drawn, made WebP, stored, the
   heartbeat beats, and its picture is dropped before the next page is drawn,
   so memory is one page's picture, not one per page. A page that fails to
   draw after earlier pages were stored leaves those pictures behind with no
   rows; they are under this customer's document prefix, which delete and
   purge remove whole, and a later run writes the same keys again. A vision
   crop draws its page again (``Prepared.picture``), lossless. Spreadsheets
   get one page per sheet after reading.
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
   card's type and date, C4), and a Latin spelling when it is Devanagari. Then
   the document's map (PRD 5 CX2, CX3, ``chunking.sections``): its sections from
   the headings, with their summaries and the topics from one more chat call when
   the processor has its ``map_budget``. For scans, when a vision model is set,
   it reads each value's box again as the second reading. The rule values are
   labelled by ``label_values`` and the values the model located by
   ``label_quoted`` (ML 2), each counting the other's boxes (council fix 2); a
   card value is never verified, and no box is cropped for a card value or an
   unsure quote. This customer's remembered
   corrections are applied, then the invoice stage (PRD 2 E5, K1 to K3,
   ``extraction.invoice.finish_invoice``): the invoice row and its checks, from
   the E4 values or, with no type or an invoice type, from labelled.py's values
   (the check's failures put their values in review). Last, the link
   identifiers (D5), only with a card.
7. Just before saving, the document must still be clean. Everything is saved
   in one transaction that replaces what an earlier run saved, so running the
   same job twice is safe (queues deliver at least once).
8. Meaning vectors for the pieces and the section summaries, if a model is set, with the heartbeat and
   progress "indexing i of n" after each batch is written (a 20k-row sheet is
   about 626 batches, longer than one lease). A model that is down only means
   no vectors: word and typo search still work.
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
  progress: it ends there, quietly, as above. A try a backend stopped clears its
  progress (``clear_progress``, fenced, best effort: ``progress_not_cleared``), so
  the list says "Waiting to try again", not a stale "Reading page 1", until the
  job comes back.
* The pages are counted by route as each is read (``on_page``), so a run that
  fails part way records the pages it read; once every page has its route, the
  counts are those of the stored routes.
* The scan reader the upload chose (``documents.ocr_choice``, PRD 4 #17) reads
  this delivery's scans (``OcrChooser.choose``) until its run is closed, and
  then Auto again, whatever happened. A run of a document with a choice keeps
  ``ocr_choice`` and ``ocr_choice_honoured`` in its config; a choice this worker
  does not have is read with Auto and logged with its code only
  (``ocr_choice_unavailable``).

The Gemini reader (PRD 5), when the processor is given its parts:

* The page cache (G7, F5): one document's reading runs inside
  ``ocr_meter.document(DocumentPages(...))``, with this customer's page cache
  (``page_cache``, ``PgPageReads``) and the pages a user asked to read again
  (``read_again.pages_to_read_again``), and it is closed when the reading ends,
  whatever happened, before the next job. The save that replaces the pages clears
  those marks; a document that fails for good clears them too, in the transaction
  that marks it failed (``set_failed``), so a page asked to be read again once the
  failure is seen keeps its mark; a try a backend stopped keeps them, so the next
  try still reads those pages harder.
* Each page's ``PageRead`` (``ocr_meter.take_page_reads``) gives its reading
  result (RT2, F1, F2): ``read_by``, ``read_quality``, ``read_issues`` and the
  counts; the pipeline adds low_resolution and readers_disagree (an ocr page
  only: text-layer and cell pages are good, read by ``text_layer`` or ``cells``),
  and keeps ``continues_on_next_page`` (G3, CX1) on the page record for the pieces.
  Word marks (F3, ``is_word_mark``) are kept in the stored reader output for the
  viewer and left out of pieces and values here, at one point (``usable``).
* F4: a document whose every page that is not blank was read unreadable fails with
  ``unreadable``, before any model call; any other document reaches ready with its
  readable pages.
* The checker (V1): on a page the reading model read (``read_by`` is ``ocr_model``),
  each value's box, grown by ``CHECK_MARGIN`` of its own size on each side, is read
  again by RapidOCR's recogniser (``checker``), with the Devanagari model when its
  line has Devanagari; never on a page RapidOCR read itself (it would only agree
  with itself). Other scanned values keep the vision model's reading, as before.
  At most ``max_check_crops`` (MAX_CHECK_CROPS) boxes of one document, both kinds
  together; the run's ``vision_crops`` and ``model_vision`` now hold the checker's
  crop count and name. Then the labels (V2, V3) and ``label_reading_marks`` (V4,
  G5, Q7: unsure_text, box_unanchored, local_reader). local_reader and pages_local
  key on the page's issues (``HANDED_ON``: read_locally or reader_refused), never on
  ``read_by``: a page read with no key is read by RapidOCR too, and is not "local".
* The run's reader counters (``kaagaz.db.runs.READER_COUNTERS``) come from the page
  reads: pages read by the model and not cached, handed to the local reader, taken
  from the cache, read again on request, unreadable, and the tokens billed. The
  run's tokens (G9) add the model client's own calls in the job (the card, the
  invoice windows, vision crops, embeddings): ``model_usage`` read when the run
  starts and when it ends, the difference. A worker runs one job at a time
  (worker_main.run_until_stopped; its sweeps run between jobs), and the page reader
  has its own client, so the difference is this job's calls and no page twice.
  ``model_vision`` names the model that read the crops: OCR_MODEL when the chat
  model is on OpenRouter (``read_image`` reads with it), else MODEL_VISION.
* ``BackendUnavailable("gemini")`` (an outage) and ``BackendUnavailable("gemini_key")``
  (a wrong key or a spent cap, logged at error) come out of the reading as any
  backend that is down: the document waits for the next try, nothing falls back
  here (the reader does that itself after READER_OUTAGE_FALLBACK_MINUTES, Q7).
* With OCR_ENGINE=rapidocr and no model there is no checker, no page counted as
  the model's and no model call: everything is as before.

Memory: the file's bytes, its spans and one decoded page picture at a time
(vision readings and the checker). Time is linear in the spans apart from the
sorts in the chunkers and extractors (O(n log n)).
"""

import contextlib
import functools
import hashlib
import io
import logging
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from importlib import metadata
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from PIL import Image

from kaagaz.chunking.context import with_context
from kaagaz.chunking.lines import pieces_from_lines
from kaagaz.chunking.piece import Piece
from kaagaz.chunking.rows import pieces_from_rows
from kaagaz.chunking.script import languages_without_model
from kaagaz.chunking.sections import (
    MAP_PROMPT_HASH,
    DocumentMap,
    MapBudget,
    Section,
    Topic,
    map_document,
    sections_from_pieces,
)
from kaagaz.eta import (
    FLOORED_STEPS,
    Estimate,
    Pair,
    Rates,
    RunTiming,
    after_reading,
    fraction_at,
    preparing,
    rates,
    reading,
)
from kaagaz.extraction.card import ChatModel
from kaagaz.extraction.invoice import finish_invoice, local_today
from kaagaz.extraction.labelled import tax_components, values_from_lines
from kaagaz.extraction.labels import (
    LineMarks,
    label_quoted,
    label_reading_marks,
    label_values,
    spot_unsure,
)
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
from kaagaz.reading.ocr import LOCAL_READ_BY, DocumentPages, PageCache, PageRead, is_word_mark
from kaagaz.reading.page_quality import (
    ISSUES,
    LOW_RESOLUTION,
    READ_LOCALLY,
    READER_REFUSED,
    READERS_DISAGREE,
    grade,
)
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
from kaagaz.settings import is_gemini_model

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
# Method 2 (R2 finding 2): several times faster to encode than 4, for under 1 dB
# of PSNR and a few % more bytes. Viewer-only: OCR and vision draw their own.
_WEBP_QUALITY = 80
_WEBP_METHOD = 2
_WEBP_MAX_SIDE = 16383  # the longest side a WebP picture can have

# Security A8, PRD 5 V1 (MAX_CHECK_CROPS, renamed from MAX_VISION_CROPS): at most this many
# value boxes of one document are read again, by the checker or the vision model; the values
# past it stay single_reader. The product's comes from the setting of that name.
MAX_CHECK_CROPS = 200
# V1, question 9: the checker's crop is the value's box grown by this share of its own width
# and height on each side.
CHECK_MARGIN = 0.04
# G6: BackendUnavailable's name for a wrong key or a spent cap (a person's fix, logged at error).
_KEY_PROBLEM = "gemini_key"
# V1: a line with any Devanagari character is read by the Devanagari recogniser.
_DEVANAGARI = re.compile("[ऀ-ॿ]")
# "ocr", "storage", "gemini_key", ...: a run's <backend>_unavailable (PRD 5 G6: a wrong key or a
# spent cap is BackendUnavailable("gemini_key")).
_BACKEND_NAME = re.compile(r"[a-z][a-z_]{0,31}")
# Security A8 (PROVISIONAL): at most this many value boxes of one document are
# sent to the vision model; the values past it stay single_reader.
MAX_VISION_CROPS = 200
_BACKEND_NAME = re.compile(r"[a-z]{1,32}")  # "ocr", "storage", ...: a run's <backend>_unavailable
# The run config's keys for the marks a reader adds to its version: ";ocr_dpi=N" when a PDF
# page was drawn below OCR_DPI to fit the pixel limits; with OCR_DPI_RULE (council 5 Oct
# 2026), ";text_dpi=N", the rule's lowest DPI, and ";ocr_reread=N", the pages read twice.
_VERSION_MARKS = (
    ("ocr_dpi_lowest", re.compile(r";ocr_dpi=([0-9]{1,5})\b")),
    ("ocr_text_dpi_lowest", re.compile(r";text_dpi=([0-9]{1,5})\b")),
    ("pages_ocr_reread", re.compile(r";ocr_reread=([0-9]{1,5})\b")),
)

_NOTHING: Pair = (0.0, 0.0)  # no page of a kind measured yet (kaagaz.eta)

# --- what is saved ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageRecord:
    info: PageInfo
    has_image: bool
    route: str | None = None  # a Route (kaagaz.reading.route); None when the page was not read
    effective_dpi: int | None = None
    low_resolution: bool = False
    # PRD 5 RT2, F1, F2 (pages, 0021): who read it (the model ID, "rapidocr", "text_layer" or
    # "cells"; None for a blank page or one not read), its reading result and its counts.
    read_by: str | None = None
    read_quality: str = "good"  # one of page_quality.READ_QUALITIES
    read_issues: tuple[str, ...] = ()  # F1 codes, in the F1 table's order
    unsure_words: int = 0
    illegible_words: int = 0
    unpinned_lines: int = 0
    # G3, CX1: its last line's sentence goes on at the top of the next page. Kept for the
    # pieces (the page cache keeps it with the page's read); not a pages column.
    continues_on_next_page: bool = False


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
    sections: tuple[Section, ...] = ()  # CX2: the document's map, in reading order
    topics: tuple[Topic, ...] = ()  # CX3: the topics that join distant pages


# --- runs and progress (R6, X2, X3) -------------------------------------------

Step = Literal["scanning", "preparing", "reading", "understanding", "indexing"]


@dataclass(frozen=True, slots=True)
class Progress:
    step: Step
    done: int
    total: int
    # The time-left estimate (kaagaz.eta) written with it: not part of which step this is.
    estimate: Estimate | None = field(default=None, compare=False)
    started_at: datetime | None = field(default=None, compare=False)  # the run's start, for the percentage
    reached: float | None = field(default=None, compare=False)  # the run's highest fraction (kaagaz.eta)
    prior: tuple[Step, Estimate] | None = field(default=None, compare=False)  # the estimate written before

    def to_json(self) -> dict[str, object]:
        shown: dict[str, object] = {"step": self.step, "done": self.done, "total": self.total}
        if self.started_at is not None:
            shown["started_at"] = self.started_at.isoformat()
        if self.reached is not None:
            shown["reached"] = round(self.reached, 4)
        if self.prior is not None:
            step, estimate = self.prior
            timing = estimate.to_json()
            kept = ("moved_at", "finish_at", "after_s", "slow_after_s")
            shown["prior"] = {"step": step, **{key: timing[key] for key in kept}}
        return shown if self.estimate is None else {**shown, **self.estimate.to_json()}


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
    # PRD 5 (kaagaz.db.runs.READER_COUNTERS): pages read by the reading model and billed, read
    # locally, taken from the page cache, read again on request and unreadable; tokens billed:
    # the page reads' and the model client's own calls in the job (G9).
    pages_gemini: int = 0
    pages_local: int = 0
    pages_cached: int = 0
    pages_read_again: int = 0
    pages_unreadable: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


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
        """Failed with ``code``, and the document's read-again marks (PRD 5 F5) cleared
        in the same transaction. With ``run_id``, raise Superseded and change nothing
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

    def recent_timings(self, customer_id: str) -> list[RunTiming]:
        """This customer's last ready runs' timings, newest first (kaagaz.eta)."""
        ...


class ProgressStore(Protocol):
    """kaagaz.db.documents.PgDocuments in production."""

    def set_progress(
        self, customer_id: str, document_id: str, progress: Progress, *, run_id: str | None = None
    ) -> None:
        """With ``run_id``, raise Superseded and change nothing unless that run is still
        open (the run fence)."""
        ...

    def clear_progress(self, customer_id: str, document_id: str, *, run_id: str | None = None) -> None:
        """No progress shown. With ``run_id``, change nothing unless that run is still open."""
        ...


class OcrMeter(Protocol):
    """kaagaz.reading.ocr.MeteredOcrEngine in production: the engine the readers use."""

    @property
    def name(self) -> str: ...  # the engine in use: a chosen one's while its job runs

    @property
    def version(self) -> str: ...

    def take(self) -> tuple[int, float]:
        """(pages sent to OCR, seconds) since the last take, then zero again."""
        ...

    def take_fallback_pages(self) -> int:
        """Pages the fallback engine read since the last take, then zero again."""
        ...

    def take_page_reads(self) -> dict[int, PageRead]:
        """Each page read since the last take, by page number, then none again (PRD 5)."""
        ...

    def document(self, pages: DocumentPages) -> contextlib.AbstractContextManager[None]:
        """Read one document's pages with its page cache inside the block (PRD 5 G7)."""
        ...


class ReadAgainStore(Protocol):
    """kaagaz.db.documents.PgDocuments in production: the pages a user asked to read
    again, harder (PRD 5 F5)."""

    def pages_to_read_again(self, customer_id: str, document_id: str) -> tuple[int, ...]: ...


class Checker(Protocol):
    """kaagaz.reading.rapid_pool.RapidOcrPool in production: RapidOCR's recogniser alone
    (PRD 5 V1), "" when it reads nothing."""

    def read_crop(self, image: Image.Image, *, devanagari: bool) -> str: ...


class OcrChooser(Protocol):
    """kaagaz.reading.ocr.OcrEngines in production: which engine reads one job's scans."""

    def choose(self, choice: str | None) -> contextlib.AbstractContextManager[bool]:
        """Read with ``choice`` (None: Auto) inside the block; True when it was honoured."""
        ...


class OcrChooser(Protocol):
    """kaagaz.reading.ocr.OcrEngines in production: which engine reads one job's scans."""

    def choose(self, choice: str | None) -> contextlib.AbstractContextManager[bool]:
        """Read with ``choice`` (None: Auto) inside the block; True when it was honoured."""
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
    def checks(self) -> Mapping[int, PageCheck]: ...  # page number -> blank, effective DPI (R3, R4)

    def picture(self, page_no: int) -> Image.Image:
        """A page given to ``on_picture``, drawn again exactly as it was then."""
        ...


# What ``prepare`` calls with each page it draws: (page number, page count, picture).
OnPicture = Callable[[int, int, Image.Image], None]


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


class Embedder(Protocol):
    """kaagaz.search.indexing.embed_pieces in production, with its database and model."""

    def __call__(
        self, customer_id: str, document_id: str, *, on_batch: Callable[[int, int], None]
    ) -> object: ...


class VisionModel(Protocol):
    def read_image(self, *, png: bytes, instruction: str) -> str: ...


class TokenUsage(Protocol):
    """kaagaz.ai.openrouter.Usage in production: tokens counted so far (G9)."""

    @property
    def input_tokens(self) -> int: ...

    @property
    def output_tokens(self) -> int: ...


@runtime_checkable
class CountsTokens(Protocol):
    """A model client that counts the tokens of its replies (G9):
    kaagaz.ai.openrouter.OpenRouterClient in production."""

    def usage(self) -> TokenUsage: ...


class ProcessorStorage(BlobStorage, Quarantine, PageImageStore, Protocol):
    """What build_processor needs from file storage (kaagaz.adapters.storage.S3Storage)."""


# --- the processor ------------------------------------------------------------


@dataclass
class _Run:
    """One delivery's run (R6) and what it has counted so far. ``run_id`` is None
    when the processor keeps no runs."""

    run_id: str | None
    started: float
    started_at: datetime | None = None  # the clock at the start, shown with each progress
    routes: Counter[str] = field(default_factory=Counter)
    vision_crops: int = 0
    values_verified: int = 0
    values_needs_review: int = 0
    embedded: bool = False
    version_marks: dict[str, int] = field(default_factory=dict)  # from the first reader's version
    model_calls: int = 0  # the card, the invoice windows and the map called (PRD 2 D1, E2; PRD 5 CX2)
    model_error: str | None = None  # "model_<code>" when a call failed; the document still ends ready
    quotes_returned: int = 0
    dropped_quotes: int = 0
    quote_config: dict[str, int] = field(default_factory=dict)  # loose, ambiguous, summary_dropped (ML 10)
    # The time left (kaagaz.eta): marks from the timer, and this run's own pages by kind.
    rates: Rates | None = None
    preparing_started: float | None = None
    pages_prepared: int = 0
    reading_started: float | None = None
    reading_ended: float | None = None
    moved: float = 0.0  # the timer when the last page was read
    measured: dict[str, Pair] = field(default_factory=dict)  # "ocr", "fast": (seconds, pages)
    after: Estimate | None = None  # from the end of reading, kept for each understanding step
    shown: tuple[Step, Estimate] | None = None  # the last estimate written, and its step
    prior: tuple[Step, Estimate] | None = None  # the one written before it (its ``prior``)
    reached: float = 0.0  # the highest fraction shown so far (kaagaz.eta): it never goes back
    ocr_choice: dict[str, str | bool] = field(default_factory=dict)  # the upload's reader, when it chose one
    # PRD 5: each page's read, the pages asked to be read again, the checker's crops (also
    # counted in vision_crops) and the pages graded unreadable.
    page_reads: dict[int, PageRead] = field(default_factory=dict)
    read_again: frozenset[int] = frozenset()
    checker_crops: int = 0
    pages_unreadable: int = 0
    model_tokens_at_start: tuple[int, int] = (0, 0)  # the model client's (input, output) then


class DocumentProcessor:
    def __init__(
        self,
        *,
        gate: ScanGate,
        statuses: StatusStore,
        documents: DocumentStore,
        socket: ReadingSocket,
        prepare: Callable[[str, bytes, OnPicture], Prepared],
        sheet_pages: Callable[[list[Span]], Sequence[PageInfo]],
        page_images: PageImageStore,
        store: PipelineStore,
        embed: Embedder | None = None,
        vision: VisionModel | None = None,
        model_errors: tuple[type[Exception], ...] = (),
        runs: RunStore | None = None,
        progress: ProgressStore | None = None,
        ocr_meter: OcrMeter | None = None,
        ocr_engines: OcrChooser | None = None,
        clock: Callable[[], datetime] | None = None,
        timer: Callable[[], float] | None = None,
        max_check_crops: int = MAX_CHECK_CROPS,
        run_config: Mapping[str, Any] | None = None,
        vision_model: str | None = None,
        embed_model: str | None = None,
        chat: ChatModel | None = None,
        chat_model: str | None = None,
        model_budget: ModelBudget | None = None,
        timezone: str = "UTC",
        customer_active: Callable[[str], bool] | None = None,
        ocr_model: str | None = None,
        checker: Checker | None = None,
        checker_model: str | None = None,
        page_cache: PageCache | None = None,
        read_again: ReadAgainStore | None = None,
        model_usage: Callable[[], TokenUsage] | None = None,
        map_budget: MapBudget | None = None,
    ) -> None:
        """``embed(customer_id, document_id, on_batch=...)`` gives the saved pieces their vectors;
        ``vision`` reads scanned boxes again. Either failing with one of
        ``model_errors`` is logged and skipped, never a failed document.

        The rest are for PRD 2 and all optional: ``runs`` keeps one run per delivery
        (it needs ``clock`` for its times and ``timer`` for its seconds);
        ``progress`` shows how far a document has got; ``ocr_meter`` is the OCR
        engine the readers use, counted, and ``ocr_engines`` picks the engine behind it
        for each job (the upload's choice); ``run_config`` is the run's plain JSON set-up
        (ML 10); ``vision_model`` and ``embed_model`` are the tags a run records
        when it used them. ``chat`` is the model stage's chat model (PRD 2 D1, E2),
        with its ``model_budget``; ``chat_model`` is the tag a run records when a
        chat call was made. ``timezone`` (settings.timezone) says which day it is for
        the invoice checks (PRD 2 ML 6(j)): today is the clock's date there.
        ``customer_active(customer_id)`` (``PgDocuments.customer_active``) is asked
        at the start of every job; None (tests with fakes) asks nothing.

        PRD 5, all optional too (see the module notes): ``ocr_model`` is OCR_MODEL when
        the Gemini reader reads the scans (a page read by it is checked and counted as
        the model's); ``checker`` reads the boxes of those pages again, at most
        ``max_check_crops`` boxes with the vision model's, and ``checker_model`` is the
        name a run records when it did; ``page_cache`` is the page cache opened around
        each document's reading, and ``read_again`` says which pages to read harder.
        ``model_usage`` is the model client's tokens counted so far (G9,
        ``CountsTokens.usage``): a run adds what its job's calls added. ``map_budget``
        (CX2, CX3): the budget of the one ``chat`` call that writes the document's section
        summaries and topics; None makes no call, and the sections come from the headings."""
        ``CountsTokens.usage``): a run adds what its job's calls added."""
        at the start of every job; None (tests with fakes) asks nothing."""
        if runs is not None and (clock is None or timer is None):
            raise ValueError("runs need a clock and a timer")
        if max_check_crops < 0:
            raise ValueError("max_check_crops must be 0 or more")
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
        self._ocr_engines = ocr_engines
        self._clock = clock or _utc_now
        self._timer = timer or time.perf_counter
        self._max_check_crops = max_check_crops
        self._run_config = dict(run_config or {})
        self._vision_model = vision_model
        self._embed_model = embed_model
        self._chat = chat
        self._chat_model = chat_model
        self._model_budget = model_budget
        self._timezone = timezone
        self._customer_active = customer_active
        self._ocr_model = ocr_model
        self._checker = checker
        self._checker_model = checker_model
        self._page_cache = page_cache
        self._read_again = read_again
        self._model_usage = model_usage
        self._map_budget = map_budget

    def __call__(self, job: Job, heartbeat: Heartbeat) -> None:
        record = self._documents.get(job.customer_id, job.document_id)
        if record is None:
            # No record for this customer: nothing to mark, nothing read.
            raise JobFailed("document_missing", retryable=False)
        run = self._start_run(job)
        settled = False  # the run was finished, or is being, on a known path
        chosen = contextlib.ExitStack()  # the upload's reader, until the run is closed
        try:
            if self._customer_active is not None and not self._customer_active(job.customer_id):
                raise JobFailed("customer_disabled", retryable=False)  # marked failed below, never read
            self._choose_reader(job, record, run, chosen)
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
            code = _unavailable_code(error)
            if str(error) == _KEY_PROBLEM:
                # G6, Q7: not an outage. Documents wait and nothing falls back: the fix is a person's.
                _log.error(
                    "reader key rejected, documents wait",
                    extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": code},
                )
            self._clear_progress(job, run, code)  # while the run is still open
            self._close_run(job, run, "failed", code)
            self._clear_progress(job, run, _unavailable_code(error))  # while the run is still open
            self._close_run(job, run, "failed", _unavailable_code(error))
            raise
        finally:
            if not settled:
                # Anything else is a bug: it is not caught (no blanket except) and goes on
                # up to the worker, but its run ends failed rather than staying open.
                self._close_run(job, run, "failed", "internal_error")
            chosen.close()  # Auto again for the next job, whatever happened

    def _choose_reader(
        self, job: Job, record: DocumentRecord, run: _Run, chosen: contextlib.ExitStack[bool | None]
    ) -> None:
        """Read this job's scans with the reader its upload chose (NULL: Auto). A choice
        this worker doesn't have (not set up, or no OCR at all) is read with Auto."""
        choice = record.ocr_choice
        if choice is None:
            return  # Auto: exactly as before, and the run's config is unchanged
        honoured = self._ocr_engines is not None and chosen.enter_context(self._ocr_engines.choose(choice))
        run.ocr_choice = {"ocr_choice": choice, "ocr_choice_honoured": honoured}
        if not honoured:
            _log.warning(
                "ocr choice not available, read with auto",
                extra={
                    "customer_id": job.customer_id,
                    "document_id": job.document_id,
                    "code": "ocr_choice_unavailable",
                },
            )

    def _process(self, job: Job, record: DocumentRecord, heartbeat: Heartbeat, run: _Run) -> None:
        customer_id, document_id = job.customer_id, job.document_id
        self._progress(job, run, "scanning", 0, 1)
        data = self._scan(job, run)
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise JobFailed("content_changed", retryable=False)
        heartbeat()

        run.rates, run.preparing_started = self._rates(job), self._timer()
        pictured: set[int] = set()  # the pages whose picture is stored

        def on_picture(page_no: int, count: int, picture: Image.Image) -> None:
            self._page_images.put_page_image(customer_id, document_id, page_no, _stored_picture(picture))
            pictured.add(page_no)
            heartbeat()
            self._progress(job, run, "preparing", len(pictured), count)

        prepared = _or_fail(lambda: self._prepare_or_count(job, record.detected_type, data, on_picture))
        pages = [_page_record(info, prepared, pictured) for info in prepared.pages]

        run.pages_prepared = len(pictured)
        run.reading_started = run.moved = self._timer()
        self._progress(job, run, "reading", 0, len(pages))
        reads: list[RouteDecision | None] = []  # how each page was read, as the reader said
        on_page = self._reading_beat(job, run, heartbeat, len(pages), reads)
        try:
            with self._reading_document(job, run):  # closed before anything else, whatever happened
                spans = _or_fail(lambda: self._socket.read(record.detected_type, data, on_page=on_page))
        except NoReader:
            require_clean(self._statuses, job)
            pages = _with_routes(pages, reads, prepared.checks)  # a blank page is blank still
            pages = _with_results(pages, {}, Counter())
            run.routes = Counter(page.route for page in pages if page.route is not None)
            content = DocumentContent(customer_id, document_id, pages, [], [], [], reader="")
            self._store.save(content, run_id=run.run_id)
            self._documents.set_page_count(customer_id, document_id, len(pages))
            self._statuses.set_status(customer_id, document_id, DocumentStatus.NEEDS_READER)
            self._close_run(job, run, "needs_reader", None)
            return
        if self._ocr_meter is not None:
            run.page_reads = self._ocr_meter.take_page_reads()
        run.reading_ended = self._timer()

        if not pages:
            cells = [span for span in spans if isinstance(span.location, CellLocation)]
            pages = [PageRecord(info, False) for info in self._sheet_pages(cells)] if cells else []
        pages = _with_routes(pages, reads, prepared.checks)
        run.routes = Counter(page.route for page in pages if page.route is not None)
        self._fail_unreadable(run, _with_results(pages, run.page_reads, Counter()))  # F4, before any call

        self._progress(job, run, "understanding", 0, 2)  # read: the time left is now until ready
        second = self._read_second(job, record.detected_type, data)
        heartbeat()

        # The socket already refuses spans without a real location; this keeps the
        # rule here too, so no piece or value can be saved without one. A word mark (PRD 5
        # F3) is drawn on the page from the stored output, never a piece or a value.
        usable = [span for span in spans if _points_at(span.location) and not is_word_mark(span)]
        # The raw second reading is kept as read; only the labels leave pages out.
        compared = None if second is None else _without_unread_pages(second, pages)
        pieces = pieces_from_rows(customer_id, document_id, usable)
        # CX1: a page whose sentence goes on onto the next one, as its reader said.
        continued = {page_no for page_no, read in run.page_reads.items() if read.continues_on_next_page}
        pieces += pieces_from_lines(customer_id, document_id, usable, continues=continued)
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
        document_map = self._map(run, heartbeat, pieces)
        rule, quoted = understanding.rule_values, understanding.quoted_values
        # A card value, or a located value whose spot is unsure (a loose, ambiguous or
        # several-span quote), is never verified, so its box is never cropped (security A8).
        cropped = rule + [
            value for value in quoted if value.record_key != CARD_RECORD and not spot_unsure(value)
        ]
        marks, line_texts = _line_marks(spans)
        readings, checked = self._second_readings(
            job, cropped, prepared, pictured, heartbeat, run, line_texts
        )
        values = label_values(rule, compared, readings, also_located=quoted, checked=checked)
        values += label_quoted(quoted, compared, readings, also_located=rule, checked=checked)
        # The checker's disagreements (F2) are counted before the reading marks may give
        # those values another reason (V4, G5, Q7), so the page keeps readers_disagree.
        disputed = Counter(
            value.location.page
            for value in values
            if value.label_reason == "readers_disagree" and isinstance(value.location, BoxLocation)
        )
        local = {page_no for page_no, read in run.page_reads.items() if _handed_on(read)}
        values = label_reading_marks(values, marks, local)  # V4, G5, Q7
        pages = _with_results(pages, run.page_reads, disputed)
        if values:
            learned = self._store.load_learned(customer_id, {value.field for value in values})
            values = [_with_parsed(value) for value in apply_learned(values, learned)]
        values, invoice = self._invoice(understanding, facts, usable, values)
        links = document_links(facts, values)
        run.values_verified = sum(1 for value in values if value.label == "verified")
        run.values_needs_review = len(values) - run.values_verified

        reader, version = self._socket.reader_id(record.detected_type)
        run.version_marks = version_marks(version)
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
            sections=document_map.sections,
            topics=document_map.topics,
        )
        self._store.save(content, run_id=run.run_id)
        heartbeat()
        self._progress(job, run, "indexing", 0, 1)
        run.embedded = self._index(job, run, heartbeat)
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

    def _map(self, run: _Run, heartbeat: Heartbeat, pieces: list[Piece]) -> DocumentMap:
        """CX2, CX3: the document's sections (``sections_from_pieces``), with their summaries
        and the topics from the one map call when the processor has a chat model and its
        ``map_budget``, counted in the run's model calls. A call that failed leaves the
        sections from the headings and no topics, and its code on the run, like the card's."""
        sections = sections_from_pieces(pieces)
        if self._map_budget is None:
            return DocumentMap(sections, (), 0, None)
        found = map_document(self._chat, pieces, sections, self._map_budget)
        run.model_calls += found.model_calls
        run.model_error = run.model_error or found.error_code
        if found.model_calls:
            heartbeat()
        return found

    def _prepare_or_count(self, job: Job, detected_type: str, data: bytes, on_picture: OnPicture) -> Prepared:
        """``prepare``; a file with too many pages keeps its real page count before
        it fails, so its message can name it."""
        try:
            return self._prepare(detected_type, data, on_picture)
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

    def _index(self, job: Job, run: _Run, heartbeat: Heartbeat) -> bool:
        """Make the pieces' meaning vectors; True if it ran. After each batch is written,
        the heartbeat, then "indexing i of n". The progress write runs after every batch
        on purpose: it is fenced, so it is what stops a superseded run within one batch."""
        if self._embed is None:
            return False

        def on_batch(done: int, total: int) -> None:
            heartbeat()
            self._progress(job, run, "indexing", done, total)

        try:
            self._embed(job.customer_id, job.document_id, on_batch=on_batch)
        except self._model_errors as error:
            _log.warning(
                "pieces saved without meaning vectors",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": _code(error)},
            )
            return False
        return True

    def _second_readings(
        self,
        job: Job,
        values: list[ExtractedValue],
        prepared: Prepared,
        pictured: Collection[int],
        heartbeat: Heartbeat,
        run: _Run,
        line_texts: Mapping[BoxLocation, str],
    ) -> tuple[dict[Location, str], set[Location]]:
        """(each scanned value's box read again, by box; the boxes the checker read).

        A box on a page the reading model read is read by the checker (PRD 5 V1,
        ``_check_crop``: the box grown by CHECK_MARGIN, with the Devanagari recogniser
        when its line, ``line_texts``, has Devanagari); any other scanned box by the
        vision model, as before (security A8), until its first error. A heartbeat after
        each; at most ``max_check_crops`` boxes in all, and the values left stay single
        reader. One page picture at a time, drawn again (``Prepared.picture``): the
        pixels preparing drew, lossless. A reading of nothing is no reading."""
        checker, model = self._checker, self._ocr_model
        checker_pages = set()
        if checker is not None and model is not None:
            checker_pages = {page_no for page_no, read in run.page_reads.items() if read.read_by == model}
        wanted: dict[int, dict[BoxLocation, bool]] = {}  # page -> {box: Devanagari}, in order, once each
        for value in values:
            box = value.location
            if not isinstance(box, BoxLocation) or (
                value.confidence is None and value.origin in DIRECT_ORIGINS
            ):
                continue  # read directly, not scanned; no origin counts as scanned (fail closed)
            if box.page in pictured and (box.page in checker_pages or self._vision is not None):
                text = line_texts.get(box, value.value_text)
                wanted.setdefault(box.page, {})[box] = _DEVANAGARI.search(text) is not None
        readings: dict[Location, str] = {}
        checked: set[Location] = set()
        vision_down = False
        for page_no, boxes in wanted.items():
            with _or_fail(functools.partial(prepared.picture, page_no)) as page:
                for box, devanagari in boxes.items():
                    if run.vision_crops >= self._max_check_crops:
                        return readings, checked
                    if checker is not None and page_no in checker_pages:
                        run.vision_crops += 1
                        run.checker_crops += 1
                        with _check_crop(page, box) as crop:
                            text = checker.read_crop(crop, devanagari=devanagari)
                        checked.add(box)
                    elif self._vision is None or vision_down:
                        continue
                    else:
                        run.vision_crops += 1
                        try:
                            png = _crop(page, box)
                            text = self._vision.read_image(png=png, instruction=VISION_INSTRUCTION)
                        except self._model_errors as error:
                            _log.warning(
                                "vision reading skipped",
                                extra={
                                    "customer_id": job.customer_id,
                                    "document_id": job.document_id,
                                    "code": _code(error),
                                },
                            )
                            vision_down = True  # asked no more; the checker goes on
                            continue
                    heartbeat()
                    text = text.strip()
                    if text:
                        readings[box] = text
        return readings, checked

    def _reading_document(self, job: Job, run: _Run) -> contextlib.AbstractContextManager[None]:
        """The document's page cache, open around its reading (PRD 5 G7, F5), with the pages
        a user asked to read again (kept on the run, for its counters); nothing to open
        without a meter or a page cache. One short query for the pages."""
        meter, cache = self._ocr_meter, self._page_cache
        if meter is None or cache is None:
            return contextlib.nullcontext()
        if self._read_again is not None:
            run.read_again = frozenset(self._read_again.pages_to_read_again(job.customer_id, job.document_id))
        pages = DocumentPages(
            customer_id=job.customer_id, document_id=job.document_id, cache=cache, read_again=run.read_again
        )
        return meter.document(pages)

    def _fail_unreadable(self, run: _Run, pages: Sequence[PageRecord]) -> None:
        """F4: ``JobFailed("unreadable")`` when every page that is not blank was read and
        graded unreadable; the run keeps how many were. O(pages)."""
        run.pages_unreadable = sum(page.read_quality == "unreadable" for page in pages)
        read = [page for page in pages if page.route is not None and page.route != "blank"]
        if read and all(page.read_quality == "unreadable" for page in read):
            raise JobFailed("unreadable", retryable=False)

    # --- runs and progress (R6, X2, X3) -----------------------------------------

    def _start_run(self, job: Job) -> _Run:
        """A new run of this delivery, started now. The OCR meter starts from zero (AR11)."""
        if self._ocr_meter is not None:
            self._ocr_meter.take()
            self._ocr_meter.take_fallback_pages()
            self._ocr_meter.take_page_reads()
        run_id, now = None, self._clock()
        if self._runs is not None:
            run_id = self._runs.start(job.customer_id, job.document_id, now=now)
        return _Run(run_id, self._timer(), started_at=now, model_tokens_at_start=self._model_tokens())

    def _model_tokens(self) -> tuple[int, int]:
        """The model client's (input, output) tokens so far (G9); (0, 0) without a count."""
        if self._model_usage is None:
            return 0, 0
        usage = self._model_usage()
        return usage.input_tokens, usage.output_tokens
        run_id, now = None, self._clock()
        if self._runs is not None:
            run_id = self._runs.start(job.customer_id, job.document_id, now=now)
        return _Run(run_id, self._timer(), started_at=now)

    def _finished(self, run: _Run, status: str, error_code: str | None) -> FinishedRun | None:
        """The run as it ends now, with everything it counted; None without runs.
        The OCR meter is read and set back to zero either way (AR11). The reader counters
        (PRD 5) come from the pages read: those of a reading that ended part way too."""
        meter = self._ocr_meter
        pages_sent, ocr_seconds = meter.take() if meter is not None else (0, 0.0)
        fallback_pages = meter.take_fallback_pages() if meter is not None else 0
        page_reads = {**(meter.take_page_reads() if meter is not None else {}), **run.page_reads}
        if run.run_id is None:
            return None
        ended = self._timer()
        if run.checker_crops:
            checked_by = self._checker_model
        elif run.vision_crops:
            checked_by = self._vision_model
        else:
            checked_by = None
        counters = _reader_counters(page_reads, self._ocr_model, run.read_again)
        model_in, model_out = self._model_tokens()
        counters["input_tokens"] += max(0, model_in - run.model_tokens_at_start[0])
        counters["output_tokens"] += max(0, model_out - run.model_tokens_at_start[1])
        totals = RunTotals(
            status=status,
            error_code=error_code,
            ocr_engine=f"{meter.name} {meter.version}"[:100] if meter is not None and pages_sent else None,
            model_chat=self._chat_model if run.model_calls else None,
            model_vision=checked_by,
            model_embed=self._embed_model if run.embedded else None,
            pages_cells=run.routes["cells"],
            pages_text_layer=run.routes["text_layer"],
            pages_ocr=run.routes["ocr"],
            pages_blank=run.routes["blank"],
            vision_crops=run.vision_crops,
            model_calls=run.model_calls,
            dropped_quotes=run.dropped_quotes,
            quotes_returned=run.quotes_returned,
            seconds_total=max(0.0, ended - run.started),
            seconds_ocr=ocr_seconds if meter is not None else None,
            values_verified=run.values_verified,
            values_needs_review=run.values_needs_review,
            config={
                **self._run_config,
                **run.quote_config,
                **run.version_marks,
                **({"pages_ocr_fallback": fallback_pages} if fallback_pages else {}),
                **run.ocr_choice,
                **_step_seconds(run, ended),
            },
            **counters,
            pages_unreadable=run.pages_unreadable,
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
        fenced: the Superseded of a run no longer open goes on up. Before each write,
        ``reached`` takes the fraction the last estimate written shows now, so a new
        estimate (a slow page 1 that raises item_s) never takes the percent back. That
        estimate goes with the write as ``prior``: a screen still counts on from it
        until its next poll, so the API serves the larger of the two (kaagaz.eta);
        ``reached`` takes that prior's fraction too, so it is never lost."""
        if self._progress_store is None:
            return
        if run.started_at is not None:
            now, started = self._clock(), run.started_at
            for last_step, last in (shown for shown in (run.shown, run.prior) if shown is not None):
                floored = last_step in FLOORED_STEPS
                run.reached = max(
                    run.reached, fraction_at(last, started_at=started, floored=floored, now=now)
                )
        try:
            estimate = self._estimate(run, step, done, total)
            progress = Progress(step, done, total, estimate, run.started_at, run.reached, run.shown)
            self._progress_store.set_progress(job.customer_id, job.document_id, progress, run_id=run.run_id)
            if estimate is not None:
                run.prior, run.shown = run.shown, (step, estimate)  # once written: what the screen has
        except BackendUnavailable:
            _log.warning(
                "progress_not_saved",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": step},
            )

    def _clear_progress(self, job: Job, run: _Run, code: str) -> None:
        """Clear this run's progress before the job waits to come back. Best effort: a
        database that is down is logged with the run's error code."""
        if self._progress_store is None:
            return
        try:
            self._progress_store.clear_progress(job.customer_id, job.document_id, run_id=run.run_id)
        except BackendUnavailable:
            _log.warning(
                "progress_not_cleared",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": code},
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
            now, kind = self._timer(), "ocr" if read is not None and read.route == "ocr" else "fast"
            seconds, pages = run.measured.get(kind, _NOTHING)
            run.measured[kind], run.moved = (seconds + max(0.0, now - run.moved), pages + 1), now
            heartbeat()
            self._progress(job, run, "reading", min(done, total), total)

        return on_page

    def _rates(self, job: Job) -> Rates | None:
        """The time-left rates from this customer's own runs only (one query), or the
        constants alone when they can't be read; None when no progress is shown."""
        if self._progress_store is None:
            return None
        history: list[RunTiming] = []
        if self._runs is not None:
            try:
                history = self._runs.recent_timings(job.customer_id)
            except BackendUnavailable:
                _log.warning(
                    "run_history_not_read",
                    extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": "eta"},
                )
        return rates(history, None if self._ocr_meter is None else self._ocr_meter.name)

    def _estimate(self, run: _Run, step: Step, done: int, total: int) -> Estimate | None:
        """The time left written with this step: while preparing or reading, from the
        pages so far; from the end of reading, one estimate kept for every later step."""
        if run.rates is None or step == "scanning":
            return None
        if step in ("understanding", "indexing"):
            if run.after is None:
                run.after = after_reading(run.rates, self._clock())
            return run.after
        if step == "preparing":
            spent = 0.0 if run.preparing_started is None else max(0.0, self._timer() - run.preparing_started)
            return preparing(run.rates, self._clock(), done, total, (spent, done))
        ocr, fast = run.measured.get("ocr", _NOTHING), run.measured.get("fast", _NOTHING)
        return reading(run.rates, self._clock(), done, total, ocr, fast)


def version_marks(version: str) -> dict[str, int]:
    """The run config's numbers from a reader's version (``_VERSION_MARKS``). O(len)."""
    return {key: int(found.group(1)) for key, mark in _VERSION_MARKS if (found := mark.search(version))}


def _step_seconds(run: _Run, now: float) -> dict[str, float]:
    """The run's ``seconds_preparing`` (when a page was drawn), ``seconds_reading`` and
    ``seconds_after_reading`` for its config, from the timer's marks; none for a run that
    did not finish reading."""
    if run.reading_started is None or run.reading_ended is None:
        return {}
    reading, after = max(0.0, run.reading_ended - run.reading_started), max(0.0, now - run.reading_ended)
    timed = {"seconds_reading": round(reading, 3), "seconds_after_reading": round(after, 3)}
    if run.pages_prepared and run.preparing_started is not None:
        timed["seconds_preparing"] = round(max(0.0, run.reading_started - run.preparing_started), 3)
    return timed


def _or_fail[T](call: Callable[[], T]) -> T:
    """``call()``, with a ReadError turned into a failure that cannot be retried.
    Raised outside the except block, so no parser error (which can quote document
    content) is chained onto it."""
    try:
        return call()
    except ReadError as error:
        code = error.code
    raise JobFailed(code, retryable=False)


def _page_record(info: PageInfo, prepared: Prepared, pictured: Collection[int]) -> PageRecord:
    """The page as saved, with its effective DPI and low-resolution flag (R4)."""
    check = prepared.checks.get(info.page)
    record = PageRecord(info, info.page in pictured)
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


def _with_results(
    pages: list[PageRecord], page_reads: Mapping[int, PageRead], disputed: Mapping[int, int]
) -> list[PageRecord]:
    """Each page with its reading result (PRD 5 RT2, F1, F2): a text-layer or cell page is
    good, read by its route; an ocr page takes its ``PageRead``'s reader, issues and
    counts, plus low_resolution and readers_disagree (``disputed``: values per page), and
    is graded by ``page_quality.grade``; a blank page, or one not read, keeps F1's default
    with no reader. O(pages + lines)."""
    return [
        _with_result(page, page_reads.get(page.info.page), disputed.get(page.info.page, 0)) for page in pages
    ]


def _with_result(page: PageRecord, read: PageRead | None, disputed: int) -> PageRecord:
    if page.route in ("text_layer", "cells"):
        return replace(page, read_by=page.route)
    if page.route != "ocr":
        return page
    found = set(() if read is None else read.issues)
    if page.low_resolution:
        found.add(LOW_RESOLUTION)
    if disputed:
        found.add(READERS_DISAGREE)
    issues = tuple(code for code in ISSUES if code in found)  # the F1 table's order
    if read is None:  # a socket that keeps no page reads (tests' fakes)
        quality = grade(issues, model_quality=None, words=0, illegible_words=0, unsure_words=0)
        return replace(page, read_quality=quality, read_issues=issues)
    words = sum(len(line.text.split()) for line in read.lines)
    quality = grade(
        issues,
        model_quality=read.quality,
        words=words,
        illegible_words=read.illegible_words,
        unsure_words=read.unsure_words,
    )
    return replace(
        page,
        read_by=read.read_by,
        read_quality=quality,
        read_issues=issues,
        unsure_words=read.unsure_words,
        illegible_words=read.illegible_words,
        unpinned_lines=read.unpinned_lines,
        continues_on_next_page=read.continues_on_next_page,
    )


def _reader_counters(
    page_reads: Mapping[int, PageRead], ocr_model: str | None, read_again: Collection[int]
) -> dict[str, int]:
    """A run's reader counters (``kaagaz.db.runs.READER_COUNTERS``) but pages_unreadable,
    from its page reads: pages the reading model read and billed (not from the cache),
    pages the reading model handed to its local reader (``_handed_on``: a page read with
    no key, or by another engine, is not one), pages from the cache, pages read again on request by the model,
    and the tokens billed. With no ``ocr_model`` no page is the model's. O(pages)."""
    reads = list(page_reads.items())
    billed = [
        n for n, read in reads if ocr_model is not None and read.read_by == ocr_model and not read.cached
    ]
    return {
        "pages_gemini": len(billed),
        "pages_local": sum(_handed_on(read) for _, read in reads),
        "pages_cached": sum(read.cached for _, read in reads),
        "pages_read_again": sum(page_no in read_again for page_no in billed),
        "input_tokens": sum(read.input_tokens for _, read in reads),
        "output_tokens": sum(read.output_tokens for _, read in reads),
    }


HANDED_ON: frozenset[str] = frozenset({READ_LOCALLY, READER_REFUSED})
"""The issues of a page the reading model handed to its local reader (Q7, G6): read
while the model was down, or after it declined the page or sent a bad reply."""


def _handed_on(read: PageRead) -> bool:
    """The page was read by the backup reader for the reading model (``HANDED_ON``),
    whatever engine read it: never a page read with no key, nor by PaddleOCR-VL."""
    return not HANDED_ON.isdisjoint(read.issues)


def _line_marks(spans: Sequence[Span]) -> tuple[dict[BoxLocation, LineMarks], dict[BoxLocation, str]]:
    """(what the reader said about each OCR line, by its box; each OCR line's text, by its
    box). A word mark (F3) follows its line in the reader's output (``ocr_spans``), so its
    unsure word is that line's; a line with box_source "model" is unpinned (G5). Two lines
    with one box (G5: both kept it) share their marks. O(spans)."""
    unsure: dict[BoxLocation, list[str]] = {}
    unpinned: set[BoxLocation] = set()
    texts: dict[BoxLocation, str] = {}
    line: BoxLocation | None = None  # the box of the last OCR line
    for span in spans:
        box = span.location
        if span.origin != "ocr" or not isinstance(box, BoxLocation):
            line = None
            continue
        if is_word_mark(span):
            if line is not None and span.quality == "unsure" and box.page == line.page:
                unsure.setdefault(line, []).append(span.text)
            continue
        line = box
        texts[box] = f"{texts[box]} {span.text}" if box in texts else span.text
        unsure.setdefault(box, [])
        if span.box_source == "model":
            unpinned.add(box)
    marks = {
        box: LineMarks(tuple(words), box in unpinned)
        for box, words in unsure.items()
        if words or box in unpinned
    }
    return marks, texts


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


def _check_crop(page: Image.Image, box: BoxLocation) -> Image.Image:
    """The checker's crop (PRD 5 V1): ``box`` grown by CHECK_MARGIN of its own width and
    height on each side (question 9), within the page, never empty. O(crop pixels)."""
    width, height = page.size
    grow_x, grow_y = CHECK_MARGIN * (box.x1 - box.x0), CHECK_MARGIN * (box.y1 - box.y0)
    left = max(0, math.floor((box.x0 - grow_x) * width))
    top = max(0, math.floor((box.y0 - grow_y) * height))
    right = min(width, max(left + 1, math.ceil((box.x1 + grow_x) * width)))
    bottom = min(height, max(top + 1, math.ceil((box.y1 + grow_y) * height)))
    return page.crop((left, top, right, bottom))


def _stored_picture(page: Image.Image) -> bytes:
    """The page picture as stored: WebP, straight from the drawn picture, with no
    PNG in between. O(pixels), one page in memory.

    PROVISIONAL (plan 7.6): a side longer than WebP allows is shrunk for storage
    only. The page keeps its real size, and boxes are fractions of it, so they
    still fit the smaller picture.
    """
    picture = page
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
    from kaagaz.db.page_reads import PgPageReads
    from kaagaz.db.pipeline_store import PgPipelineStore
    from kaagaz.db.runs import PgRuns
    from kaagaz.preparing.pages import prepare_pages, sheet_pages
    from kaagaz.reading.ocr import GEMINI, MeteredOcrEngine
    from kaagaz.reading.rapid_pool import shared_pool
    from kaagaz.reading.wiring import build_ocr_engines, build_reader_socket, reader_config
    from kaagaz.reading.ocr import MeteredOcrEngine, build_ocr_engines
    from kaagaz.reading.wiring import build_reader_socket, reader_config
    from kaagaz.search.indexing import embed_pieces

    documents = PgDocuments(db)

    def prepare(detected_type: str, data: bytes, on_picture: OnPicture) -> Prepared:
        return prepare_pages(
            detected_type,
            data,
            on_picture,
            dpi=settings.page_image_dpi,
            max_pages=settings.max_pages,
            max_image_pixels=settings.max_image_pixels,
            blank_stddev=settings.blank_page_stddev,
            low_res_dpi=settings.low_res_dpi,
        )

    embed: Embedder | None = None
    if model is not None:
        embed = functools.partial(embed_pieces, db, model=model)
    # The Gemini reader (PRD 5 G1) from kaagaz.reading.wiring; the readers read pages on its
    # page pool (wiring.page_pool, G8) through the same meter.
    engines = build_ocr_engines(settings)
    meter = None if engines is None else MeteredOcrEngine(engines, timer=time.perf_counter)
    vision = model if model is not None and settings.model_vision else None
    # The checker (V1) reads the boxes of pages the reading model read, so only with it; it
    # shares the process's one RapidOCR pool with the box pinning (CPU_WORKERS).
    gemini = engines is not None and settings.ocr_engine == GEMINI

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
        ocr_engines=engines,
        clock=_utc_now,
        timer=time.perf_counter,
        max_check_crops=settings.max_check_crops,
        run_config={
            "build": _build(),
            **reader_config(settings),
            "max_check_crops": settings.max_check_crops,
            **(model_config(settings) if model is not None else {}),
            **({"map_prompt": MAP_PROMPT_HASH} if model is not None else {}),
        },
        vision_model=_vision_name(settings) if vision is not None else None,
        embed_model=settings.model_embed if embed is not None else None,
        chat=model,
        chat_model=settings.model_chat if model is not None else None,
        model_budget=ModelBudget.from_settings(settings) if model is not None else None,
        timezone=settings.timezone,
        customer_active=documents.customer_active,
        ocr_model=settings.ocr_model if gemini else None,
        checker=shared_pool(settings.cpu_workers) if gemini else None,
        checker_model=_checker_name() if gemini else None,
        page_cache=PgPageReads(db) if gemini else None,  # keyless: no query a job, as before
        read_again=documents,
        model_usage=model.usage if isinstance(model, CountsTokens) else None,
        map_budget=MapBudget.from_settings(settings) if model is not None else None,
    )


def _vision_name(settings: "Settings") -> str | None:
    """The model a run records for its vision crops (``model_vision``): OCR_MODEL when the
    chat model is on OpenRouter, whose ``read_image`` reads with it (G9), else MODEL_VISION."""
    if is_gemini_model(settings.model_chat):
        return settings.ocr_model
    return settings.model_vision


def _checker_name() -> str:
    """The checker's name a run records (``model_vision``, PRD 5 V1): RapidOCR and its
    installed version, read from its metadata without importing it."""
    try:
        return f"{LOCAL_READ_BY} {metadata.version('rapidocr')}"
    except metadata.PackageNotFoundError:
        return LOCAL_READ_BY


def _build() -> str:
    """The backend's version, as installed (ML 10's build). PROVISIONAL: a build's
    commit id needs the image to carry it, which is Vrushit's CI."""
    try:
        return metadata.version("kaagaz-backend")
    except metadata.PackageNotFoundError:
        return "unknown"
