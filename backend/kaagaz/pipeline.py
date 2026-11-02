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
   purge remove whole, and a later run writes the same keys again. The
   checker's crop draws its page again (``Prepared.picture``), lossless.
   Spreadsheets get one page per sheet after reading.
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
   (and PR 6's invoice windows, or PRD 6's land windows), with the heartbeat and progress
   "understanding i of n" after each call and after its searches. The card
   gives the document's type, languages, summary, date and card JSON; with the
   model off or down there is none, the languages come from the pieces (L2),
   and the document is saved exactly as with no model (the run keeps the
   error code, ``model_<code>``). Each piece gets its context header (with the
   card's type and date, C4), and a Latin spelling when it is Devanagari. Then
   the document's map (PRD 5 CX2, CX3, ``chunking.sections``): its sections from
   the headings, with their summaries and the topics from one more chat call when
   the processor has its ``map_budget``. For scans the reading model read, the
   checker reads each value's box again as the second reading. The rule values are
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
8. The page count, and the document is ready, in one transaction that also
   makes it its family's current version unless a newer version is ready
   already (PRD 1 F6). A version that fails never gets here, so the one before
   it stays current. Then auto-filing (it reads the card's type, saved in 7).

   Ready first (Tushar, 16 Oct 2026): with a chat model and a readable page, 7 and 8
   come twice. First the rule values (checked and labelled exactly as with the model
   off) are saved and the document made ready with its run still open (``open_run``),
   before any model call; the last progress says when (``ready_at``), so the list shows
   "Understanding the document" while it is usable (search, open, ask). Then the card,
   its invoice windows and the map are asked, the checker reads the boxes they located,
   and everything is saved again (``_understand_after_ready``), fenced by the same run:
   a newer run, or a read again asked meanwhile, makes it Superseded and nothing is
   written; the save writes each person's word change again. The model down saves the
   rule values again with the run's ``model_<code>``. The run ends ready after it, with
   ``seconds_to_ready`` and ``seconds_after_ready``; auto-filing and the vectors follow.
9. Meaning vectors for the pieces and the section summaries, if a model is set, AFTER
   the document is ready (fast scan, council 16 Oct 2026), with the heartbeat after each
   batch is written (a 20k-row sheet is about 626 batches, longer than one lease) and a
   check that the document is still ready (a newer run stops it). Nothing that fails
   here moves the document off ready: word and typo search work without vectors, and the
   worker's EmbedMissing fills them in later. The job indexes for MODEL_TIMEOUT_SECONDS at
   most (``index_seconds``, never stuck, 17 Oct 2026), then leaves the rest to EmbedMissing;
   understanding, filing and indexing each log one line with their outcome (``_step``).

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
* The scan reader an upload chose before PRD 5 RT3 (``documents.ocr_choice``) is
  ignored: every scan is read with the one engine the processor is given.

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
* F4 (changed 16 Oct 2026, never failed): a document whose every page that is not blank
  was read unreadable reaches ready too, each such page flagged for review; it no longer
  fails with ``unreadable``.
* The checker (V1): on a page the reading model read (``read_by`` is the job's model,
  ``run.model``),
  each value's box, grown by ``CHECK_MARGIN`` of its own size on each side, is read
  again by RapidOCR's recogniser (``checker``), with the Devanagari model when its
  line has Devanagari; never on a page RapidOCR read itself (it would only agree
  with itself). Other scanned values have no second reading. At most
  ``max_check_crops`` (MAX_CHECK_CROPS) boxes of one document; the run's
  ``vision_crops`` and ``model_vision`` hold the checker's crop count and name.
  Then the labels (V2, V3) and ``label_reading_marks`` (V4, G5, Q7: unsure_text,
  box_unanchored, local_reader). local_reader and pages_local key on the page's
  issues (``HANDED_ON``: read_locally or reader_refused), never on ``read_by``: a page
  read with no key is read by RapidOCR too, and is not "local".
* The run's reader counters (``kaagaz.db.runs.READER_COUNTERS``) come from the page
  reads: pages read by the model and not cached, handed to the local reader, taken
  from the cache, read again on request, unreadable, and the tokens billed. The
  run's tokens (G9) add the model client's own calls in the job (the card, the
  invoice windows, embeddings): ``model_usage`` read when the run
  starts and when it ends, the difference. A worker runs one job at a time
  (worker_main.run_until_stopped; its sweeps run between jobs), and the page reader
  has its own client, so the difference is this job's calls and no page twice.
* ``BackendUnavailable("gemini")`` (an outage) and ``BackendUnavailable("gemini_key")``
  (a wrong key or a spent cap, logged at error) come out of the reading as any
  backend that is down: the document waits for the next try, nothing falls back
  here (the reader does that itself after READER_OUTAGE_FALLBACK_MINUTES, Q7). #473: a
  document on Mistral OCR (``Reading.scan_reader``, passed on in ``DocumentPages`` with its
  model) waits the same way on ``("mistral")``, ``("mistral_key")`` and ``("mistral_setup")``
  (the last two logged at error), asked first (``mistral_ready``, review 2 Nov 2026) so a
  document waits before its pages are drawn; its run's model is "mistral/<id>"
  (``run.model``), and it never gets Dynamic's Careful or memo (Careful is Gemini).
* With OCR_ENGINE=rapidocr and no model there is no checker, no page counted as
  the model's and no model call: everything is as before.

Model choice and AI spend (council 11 Oct 2026), when the processor is given them:

* ``job_reading`` at the start of each job: the user who asked for the reading
  (``documents.read_requested_by``) and, with the Gemini reader, the model that reads its
  scans and its provider (``kaagaz.db.models.resolve``: the upload's allowed choice, else
  the organisation's default, else OCR_MODEL). The model goes in ``DocumentPages``, on the
  run (``run.model``, what the checker and the counters look for) and in its config
  (``read_model``).
* ``usage``: the job runs in a usage scope (``kaagaz.ai.usage``, SP1) billed to that user
  as kind scan, or upgrade when no user asked (the upgrade sweep); the card, the invoice
  windows, the map and the embeddings each run in their own nested scope of that kind.
  The page threads keep the scope (``reading.socket`` copies the context).
* Keyless (OCR_ENGINE=rapidocr, MODEL_ENABLED=false): neither, no query and no ledger.

Dynamic and Ensemble reading (features 26 and 27, council 15 Oct 2026): ``job_reading``
gives the document's whole ``Reading`` (``kaagaz.db.models.resolve_scan``, checked again
at job time, S2). The checker and the counters look at every model of it (``run.models``).

* Dynamic: after the first reading, the pages worth a careful read (``careful_pages``) are
  read with Careful in a second reading through the readers (``_read_carefully``); the
  other pages come back from the engine's memo with no call.
* Ensemble runs only after the scan (Tushar, 16 Oct 2026; ``kaagaz/jobs/ensemble.py``):
  a scan never changes a value by a vote.
* ``run_config.reading``: the reading, the escalated pages and cap hits (``_reading_config``).

Memory: the file's bytes, its spans and one decoded page picture at a time
(the checker). Time is linear in the spans apart from the
sorts in the chunkers and extractors (O(n log n)).
"""

import contextlib
import contextvars
import functools
import hashlib
import io
import logging
import math
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from importlib import metadata
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from PIL import Image

from kaagaz.chunking.context import with_context
from kaagaz.chunking.layout import Layout, page_layout
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
from kaagaz.extraction.land import value_number
from kaagaz.extraction.learned import LearnedCorrection, apply_learned
from kaagaz.extraction.parsing import parse_date
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
from kaagaz.ingestion import sniff
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobStorage, DocumentRecord, Job
from kaagaz.jobs.ports import LeaseLost
from kaagaz.jobs.worker import KEY_PROBLEMS, Heartbeat, JobFailed, Superseded
from kaagaz.preparing.page_checks import PageCheck
from kaagaz.reading.mistral_ocr import (
    DEFAULT_MISTRAL_MODEL,
    MISTRAL,
    MISTRAL_KEY,
    MISTRAL_SETUP,
    OPENROUTER,
    read_by,
)
from kaagaz.reading.ocr import (
    DYNAMIC_MAX_PAGES,
    HARD_LINES_MAX,
    HARD_SHARE,
    LOCAL_READ_BY,
    DocumentPages,
    PageCache,
    PageRead,
    Region,
    careful_pages,
    careful_plan,
    careful_reasons,
    is_word_mark,
    worst_first,
)
from kaagaz.reading.page_quality import (
    ISSUES,
    LOW_RESOLUTION,
    READ_LOCALLY,
    READER_REFUSED,
    READERS_DISAGREE,
    grade,
)
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import NoReader, OnPage, pages_at_once, pages_in_flight, waiting
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
    from kaagaz.db.models import Reading
    from kaagaz.settings import Settings

_log = logging.getLogger(__name__)

# The scan's own verdicts: recorded as the reason a document was rejected.
_VERDICT_CODES = REJECTION_CODES - {"rejected"}

# A document whose reading has ended. A delivery for one is a duplicate (the queue is
# at least once: a delete that failed hands the job out again) and is acked unread.
# Read again, one page again and the upgrade sweep set it to received before queueing
# it, so they are read. Rejected is not here: the scan gate refuses it for good.
_SETTLED = frozenset({DocumentStatus.READY, DocumentStatus.FAILED, DocumentStatus.NEEDS_READER})
# The detected types whose every page is a picture read by OCR (the time left, fast scan).
_PHOTO_TYPES = frozenset({sniff.PNG, sniff.JPEG, sniff.TIFF})

# Stored page pictures are for people to look at (PRD 1, S1), so they are WebP.
# Method 2 (R2 finding 2): several times faster to encode than 4, for under 1 dB
# of PSNR and a few % more bytes. Viewer-only: OCR and the checker draw their own.
_WEBP_QUALITY = 80
_WEBP_METHOD = 2
_WEBP_MAX_SIDE = 16383  # the longest side a WebP picture can have
PICTURE_WORKERS = 4  # page pictures encoded and stored at once (fast scan)

# Security A8, PRD 5 V1 (MAX_CHECK_CROPS, renamed from MAX_VISION_CROPS): at most this many
# value boxes of one document are read again by the checker; the values
# past it stay single_reader. The product's comes from the setting of that name.
MAX_CHECK_CROPS = 200
# V1, question 9: the checker's crop is the value's box grown by this share of its own width
# and height on each side.
CHECK_MARGIN = 0.04
# PRD 5 Q6: the run codes of a reader that is down (Gemini, Mistral (#473), or the OCR server) or
# has a key problem: the document shows "Reading is paused for now" until its job returns, or,
# for a key problem, that reading is stopped until it is fixed (kaagaz/api/views.py, issue 407).
_READER_WAITS = frozenset(
    {
        "gemini_unavailable",
        "gemini_key_unavailable",
        "ocr_unavailable",
        *(f"{name}_unavailable" for name in (MISTRAL, MISTRAL_KEY, MISTRAL_SETUP)),
    }
)
# V1: a line with any Devanagari character is read by the Devanagari recogniser.
_DEVANAGARI = re.compile("[ऀ-ॿ]")
# "ocr", "storage", "gemini_key", ...: a run's <backend>_unavailable (PRD 5 G6: a wrong key or a
# spent cap is BackendUnavailable("gemini_key")).
_BACKEND_NAME = re.compile(r"[a-z][a-z_]{0,31}")
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
    # #463: the thinking level and picture detail of the page's first model read (#463 review:
    # whichever pass produced its text), that pass (first or again) and the first read's output
    # tokens.
    read_thinking: str | None = None
    read_resolution: str | None = None
    read_pass: str | None = None
    read_output_tokens: int | None = None
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
    layout: Layout = field(default_factory=Layout)  # #383, #382: the pages' blocks and table cells


# --- runs and progress (R6, X2, X3) -------------------------------------------

Step = Literal["scanning", "preparing", "reading", "rereading", "understanding", "indexing"]


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
    # Ready first (16 Oct 2026): the last progress before Ready says when, so the API shows the
    # ready document as still being understood until the run ends (``mark_ready`` clears it).
    ready_at: datetime | None = field(default=None, compare=False)

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
        if self.ready_at is not None:
            shown["ready_at"] = self.ready_at.isoformat()
        return shown if self.estimate is None else {**shown, **self.estimate.to_json()}


@dataclass(frozen=True, slots=True)
class RunTotals:
    status: str  # "ready", "failed", "needs_reader", "rejected" or "superseded"
    error_code: str | None
    ocr_engine: str | None  # "<name> <version>" when any page went to OCR
    model_chat: str | None  # the model tag, when a chat call was made
    model_vision: str | None  # the checker, when a crop was read
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
        self,
        customer_id: str,
        document_id: str,
        page_count: int,
        *,
        run: FinishedRun | None = None,
        open_run: str | None = None,
    ) -> None:
        """Ready with its page count, and current unless a newer version is ready.
        Never moves a document out of rejected. With ``run``, finish that run in the
        same transaction, or raise Superseded when it is no longer open. With ``open_run``
        (Ready first), the same fence on that run, which stays open while the document is
        understood, and its progress is kept; otherwise the progress is cleared."""
        ...

    def file_document(self, customer_id: str, document_id: str) -> None:
        """Auto-filing (feature 28): file a ready document's family by its type, in its own
        transaction, when its upload went in no folder and the organisation's switch is on."""
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

    def clear_progress(
        self, customer_id: str, document_id: str, *, run_id: str | None = None, waiting: str | None = None
    ) -> None:
        """No progress shown (``waiting="reader"``: ``{"step": "waiting"}``, PRD 5 Q6). With
        ``run_id``, change nothing unless that run is still open."""
        ...


class OcrMeter(Protocol):
    """kaagaz.reading.ocr.MeteredOcrEngine in production: the engine the readers use."""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def take(self) -> tuple[int, float]:
        """(pages sent to OCR, seconds) since the last take, then zero again."""
        ...

    def take_page_reads(self) -> dict[int, PageRead]:
        """Each page read since the last take, by page number, then none again (PRD 5)."""
        ...

    def document(self, pages: DocumentPages) -> contextlib.AbstractContextManager[None]:
        """Read one document's pages with its page cache inside the block (PRD 5 G7)."""
        ...


class ReadAgainStore(Protocol):
    """kaagaz.db.documents.PgDocuments in production: the pages a user asked to read
    again, harder (PRD 5 F5), and the regions asked for on some of them (#385)."""

    def pages_to_read_again(self, customer_id: str, document_id: str) -> tuple[int, ...]: ...

    def regions_to_read_again(self, customer_id: str, document_id: str) -> dict[int, tuple[Region, ...]]: ...


class Checker(Protocol):
    """kaagaz.reading.rapid_pool.RapidOcrPool in production: RapidOCR's recogniser alone
    (PRD 5 V1), "" when it reads nothing."""

    def read_crop(self, image: Image.Image, *, devanagari: bool) -> str: ...


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


@dataclass(frozen=True, slots=True)
class JobReading:
    """Who a job's model calls are billed to, and which model reads its scans (model choice
    and AI spend, council 11 Oct 2026, SP1, MS5): ``user_id`` is ``documents.read_requested_by``
    (None: no user asked, the upgrade sweep); ``model`` and ``provider`` are
    ``kaagaz.db.models.resolve``'s, None when no model reads the scans (RapidOCR).
    ``reading`` is ``kaagaz.db.models.resolve_scan``'s whole answer (features 25-27: the
    mode, Careful, Ensemble's other models, the thinking level and why anything fell back);
    None with no model, or from a caller that knows the model only. ``pages_at_once`` is the
    super admin's pages of one document read at once (package S, 0053), read at the job's
    start; None: OCR_PAGE_CONCURRENCY."""

    user_id: str | None
    model: str | None = None
    provider: str | None = None
    reading: "Reading | None" = None
    pages_at_once: int | None = None


class UsageScopes(Protocol):
    """``kaagaz.ai.usage.usage_scope`` on the database (SP1): the model calls made inside
    are billed to this customer, user, kind and document, saved when it ends."""

    def __call__(
        self, *, customer_id: str, user_id: str | None, kind: str, document_id: str | None = None
    ) -> contextlib.AbstractContextManager[object]: ...


class ProcessorStorage(BlobStorage, Quarantine, PageImageStore, Protocol):
    """What build_processor needs from file storage (kaagaz.adapters.storage.S3Storage)."""


# --- the processor ------------------------------------------------------------


class _IndexLater(Exception):
    """The job's indexing ran past ``index_seconds``: the vectors left are EmbedMissing's."""


@dataclass(frozen=True, slots=True)
class _Read:
    """What a run read, kept for both saves of Ready first (``DocumentProcessor._content``):
    the pages with their routes, the spans that point at a page, the second reading as the
    labels compare it, the pieces before their context headers, the reading marks and line
    texts (V4), the reader outputs and the first reader's name, and the prepared pages."""

    name: str
    pages: list[PageRecord]
    usable: list[Span]
    compared: list[Span] | None
    pieces: list[Piece]
    marks: dict[BoxLocation, LineMarks]
    line_texts: dict[BoxLocation, str]
    outputs: list[ReaderOutput]
    reader: str
    prepared: Prepared
    pictured: Collection[int]


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
    # PRD 5: each page's read, the pages asked to be read again, the checker's crops (also
    # counted in vision_crops) and the pages graded unreadable.
    page_reads: dict[int, PageRead] = field(default_factory=dict)
    read_again: frozenset[int] = frozenset()
    read_again_regions: dict[int, tuple[Region, ...]] = field(default_factory=dict)  # #385
    checker_crops: int = 0
    pages_unreadable: int = 0
    model_tokens_at_start: tuple[int, int] = (0, 0)  # the model client's (input, output) then
    # Model choice (council 11 Oct 2026): the model reading this job's scans and its provider
    # (None: no model reads them), and the user its model calls are billed to (SP1).
    model: str | None = None
    provider: str | None = None
    user_id: str | None = None
    # Features 25-27 (council 15 Oct 2026): the document's reading (JobReading.reading), every
    # model that may read one of its pages (the main one, Careful, Ensemble's others: the
    # checker reads the values of a page any of them read, C5), Dynamic's first reads by
    # (page, pixels), and what the run adds to run_config.reading (escalated, cap_hit, ...).
    reading: "Reading | None" = None
    models: frozenset[str] = frozenset()
    memo: dict[tuple[int, str], PageRead] | None = None
    reading_notes: dict[str, Any] = field(default_factory=dict)
    # Fast scan (council 16 Oct 2026): each page Careful read inline, with its reasons (None:
    # not read inline), and the seconds of the steps after reading (``_timed``).
    escalated: dict[int, list[str]] | None = None
    # #384: each page's (route, reason) when the document's pages are routed (None: not routed).
    routed: dict[int, tuple[str, str]] | None = None
    steps: dict[str, float] = field(default_factory=dict)
    ready_at: float | None = None  # Ready first: the timer when the document was ready


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
        model_errors: tuple[type[Exception], ...] = (),
        runs: RunStore | None = None,
        progress: ProgressStore | None = None,
        ocr_meter: OcrMeter | None = None,
        clock: Callable[[], datetime] | None = None,
        timer: Callable[[], float] | None = None,
        max_check_crops: int = MAX_CHECK_CROPS,
        run_config: Mapping[str, Any] | None = None,
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
        job_reading: Callable[[str, str], JobReading] | None = None,
        usage: UsageScopes | None = None,
        ocr_page_concurrency: int = 1,
        cost_saving: tuple[int, float] = (HARD_LINES_MAX, HARD_SHARE),
        index_seconds: float | None = None,
        land_on: Callable[[str], bool] | None = None,
        mistral_ready: Callable[[], None] | None = None,
    ) -> None:
        """``embed(customer_id, document_id, on_batch=...)`` gives the saved pieces their vectors;
        failing with one of ``model_errors`` is logged and skipped, never a failed document.

        The rest are for PRD 2 and all optional: ``runs`` keeps one run per delivery
        (it needs ``clock`` for its times and ``timer`` for its seconds);
        ``progress`` shows how far a document has got; ``ocr_meter`` is the OCR
        engine the readers use, counted; ``run_config`` is the run's plain JSON set-up
        (ML 10); ``embed_model`` is the tag a run records when it used it. ``chat`` is
        the model stage's chat model (PRD 2 D1, E2), with its ``model_budget``;
        ``chat_model`` is the tag a run records when a chat call was made. ``timezone``
        (settings.timezone) says which day it is for the invoice checks (PRD 2 ML 6(j)):
        today is the clock's date there.
        ``customer_active(customer_id)`` (``PgDocuments.customer_active``) is asked
        at the start of every job; None (tests with fakes) asks nothing.

        PRD 5, all optional too (see the module notes): ``ocr_model`` is OCR_MODEL when
        the Gemini reader reads the scans (a page read by it is checked and counted as
        the model's); ``checker`` reads the boxes of those pages again, at most
        ``max_check_crops`` boxes, and ``checker_model`` is the
        name a run records when it did; ``page_cache`` is the page cache opened around
        each document's reading, and ``read_again`` says which pages to read harder.
        ``model_usage`` is the model client's tokens counted so far (G9,
        ``CountsTokens.usage``): a run adds what its job's calls added. ``map_budget``
        (CX2, CX3): the budget of the one ``chat`` call that writes the document's section
        summaries and topics; None makes no call, and the sections come from the headings.

        Model choice and AI spend (council 11 Oct 2026), both optional (None: keyless, no
        query and no ledger): ``job_reading(customer_id, document_id)`` is asked at the start
        of every job for its ``JobReading``; its model reads the scans and is the one the
        checker and the counters look for (in place of ``ocr_model``). ``usage`` bills the
        job's model calls (SP1): kind scan to the user who asked for the reading, or upgrade
        with no user, and card, invoice, map and embed for those stages.
        ``ocr_page_concurrency`` (OCR_PAGE_CONCURRENCY): the pages read at once, for the time
        left (fast scan, council 16 Oct 2026). ``cost_saving``: Cost saving's hard-lines limits
        (COST_SAVING_HARD_LINES_MAX, COST_SAVING_HARD_SHARE) for ``careful_pages``.
        ``index_seconds`` (MODEL_TIMEOUT_SECONDS): how long the job's own indexing may go on
        before it hands the vectors left to the worker's EmbedMissing (never stuck, 17 Oct
        2026); None, until done. ``land_on(customer_id)``: the ``land_facts`` switch (PRD 6,
        0052), asked once a job that has a chat model; None (tests with fakes) asks nothing.
        ``mistral_ready()`` (#473, ``GeminiEngine.mistral_ready``) is asked at the start of a job
        whose ``Reading`` is on Mistral, and raises BackendUnavailable while Mistral can't read
        here, before anything is drawn; None asks nothing."""
        if runs is not None and (clock is None or timer is None):
            raise ValueError("runs need a clock and a timer")
        if max_check_crops < 0:
            raise ValueError("max_check_crops must be 0 or more")
        if chat is not None and model_budget is None:
            raise ValueError("a chat model needs its model_budget")
        self._gate = gate
        self._statuses = statuses
        self._land_on = land_on
        self._documents = documents
        self._socket = socket
        self._prepare = prepare
        self._sheet_pages = sheet_pages
        self._page_images = page_images
        self._store = store
        self._embed = embed
        self._model_errors = model_errors
        self._runs = runs
        self._progress_store = progress
        self._ocr_meter = ocr_meter
        self._clock = clock or _utc_now
        self._timer = timer or time.perf_counter
        self._max_check_crops = max_check_crops
        self._run_config = dict(run_config or {})
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
        self._job_reading = job_reading
        self._usage = usage
        self._ocr_page_concurrency = ocr_page_concurrency
        self._cost_saving = cost_saving
        self._index_seconds = index_seconds
        self._mistral_ready = mistral_ready

    def __call__(self, job: Job, heartbeat: Heartbeat) -> None:
        record = self._documents.get(job.customer_id, job.document_id)
        if record is None:
            # No record for this customer: nothing to mark, nothing read.
            raise JobFailed("document_missing", retryable=False)
        if self._statuses.get_status(job.customer_id, job.document_id) in _SETTLED:
            _log.info(
                "duplicate delivery skipped",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": "settled"},
            )
            return  # acked: no run, nothing read again
        run = self._start_run(job)
        settled = False  # the run was finished, or is being, on a known path
        try:
            if self._customer_active is not None and not self._customer_active(job.customer_id):
                raise JobFailed("customer_disabled", retryable=False)  # marked failed below, never read
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
            if failure.retryable:  # tried again soon: the row says so, not a frozen step
                self._clear_progress(job, run, failure.code)
            status = "rejected" if failure.code in REJECTION_CODES else "failed"
            self._close_run(job, run, status, failure.code)
            raise
        except BackendUnavailable as error:
            settled = True
            code = _unavailable_code(error)
            if str(error) in KEY_PROBLEMS:
                # G6, Q7: not an outage. Documents wait and nothing falls back: the fix is a person's.
                _log.error(
                    "reader key rejected, documents wait",
                    extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": code},
                )
            self._clear_progress(job, run, code)  # while the run is still open
            self._close_run(job, run, "failed", code)
            raise
        finally:
            if not settled:
                # Anything else is a crash: it is not caught here and goes on up to the
                # worker, which hands the job back counted (council 17 Oct 2026). Its run ends
                # failed rather than staying open, and its progress is cleared first, as for a
                # retryable JobFailed, so the row says it waits to try again, not "Starting…".
                self._clear_progress(job, run, "internal_error")
                self._close_run(job, run, "failed", "internal_error")

    def _process(self, job: Job, record: DocumentRecord, heartbeat: Heartbeat, run: _Run) -> None:
        """The job's model and who it is billed to (``job_reading``), then everything else
        inside its usage scope: scan, or upgrade when no user asked (SP1)."""
        run.model = self._ocr_model
        at_once = None
        if self._job_reading is not None:
            reading = self._job_reading(job.customer_id, job.document_id)
            at_once = reading.pages_at_once
            run.user_id = reading.user_id
            if reading.model is not None:
                run.model, run.provider = reading.model, reading.provider
            run.reading = reading.reading
        if run.reading is not None and run.reading.scan_reader == MISTRAL:  # #473: Mistral reads it
            if self._mistral_ready is not None:
                self._mistral_ready()  # review, 2 Nov 2026: wait now, before any page is drawn
            run.model = read_by(run.reading.mistral_model)
        extra = () if run.reading is None else (run.reading.careful, *run.reading.others)
        run.models = frozenset(
            model for model in (run.model, *(agent.model for agent in extra if agent is not None)) if model
        )
        with self._billed(job, run, "scan" if run.user_id is not None else "upgrade"), pages_at_once(at_once):
            self._read_and_save(job, record, heartbeat, run)

    def _read_and_save(self, job: Job, record: DocumentRecord, heartbeat: Heartbeat, run: _Run) -> None:
        customer_id, document_id = job.customer_id, job.document_id
        self._progress(job, run, "scanning", 0, 1)
        data = self._scan(job, run)
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise JobFailed("content_changed", retryable=False)
        heartbeat()

        run.rates, run.preparing_started = self._rates(job, record.detected_type), self._timer()
        pictured: set[int] = set()  # the pages whose picture is stored
        # Fast scan: encoding a page's WebP takes ~90% of preparing, so pages are encoded and
        # stored PICTURE_WORKERS at a time (Pillow lets go of the GIL), and at most that many
        # drawn pictures wait in memory. All are stored before reading starts.
        slots = threading.BoundedSemaphore(PICTURE_WORKERS)
        storing: list[Future[None]] = []

        def store(page_no: int, picture: Image.Image) -> None:
            try:
                self._page_images.put_page_image(customer_id, document_id, page_no, _stored_picture(picture))
                pictured.add(page_no)
            finally:
                slots.release()

        with ThreadPoolExecutor(PICTURE_WORKERS) as pool:

            def on_picture(page_no: int, count: int, picture: Image.Image) -> None:
                slots.acquire()
                storing.append(pool.submit(store, page_no, picture))
                heartbeat()
                self._progress(job, run, "preparing", page_no, count)

            prepared = _or_fail(lambda: self._prepare_or_count(job, record.detected_type, data, on_picture))
            for stored in storing:
                stored.result()  # the first error, as when they were stored one by one
        pages = [_page_record(info, prepared, pictured) for info in prepared.pages]

        run.pages_prepared = len(pictured)
        run.reading_started = run.moved = self._timer()
        self._progress(job, run, "reading", 0, len(pages))
        reads: list[RouteDecision | None] = []  # how each page was read, as the reader said
        on_page = self._reading_beat(job, run, heartbeat, len(pages), reads)
        try:
            # Closed before anything else, whatever happened; the heartbeat beats while a page
            # is being read (fast scan: a page may make several slow calls in a row).
            with self._reading_document(job, run, page_count=len(pages)), waiting(heartbeat):
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
            spans = self._read_carefully(job, record, data, heartbeat, run, spans)
        run.reading_ended = self._timer()

        if not pages:
            cells = [span for span in spans if isinstance(span.location, CellLocation)]
            pages = [PageRecord(info, False) for info in self._sheet_pages(cells)] if cells else []
        pages = _with_routes(pages, reads, prepared.checks)
        run.routes = Counter(page.route for page in pages if page.route is not None)
        unreadable = self._count_unreadable(run, _with_results(pages, run.page_reads, Counter()))

        self._progress(job, run, "understanding", 0, 2)  # read: the time left is now until ready
        second = self._read_second(job, record.detected_type, data)
        heartbeat()

        # The socket already refuses spans without a real location; this keeps the
        # rule here too, so no piece or value can be saved without one. A word mark (PRD 5
        # F3) is drawn on the page from the stored output, never a piece or a value.
        usable = [span for span in spans if _points_at(span.location) and not is_word_mark(span)]
        pieces = pieces_from_rows(customer_id, document_id, usable)
        # CX1: a page whose sentence goes on onto the next one, as its reader said.
        continued = {page_no for page_no, read in run.page_reads.items() if read.continues_on_next_page}
        pieces += pieces_from_lines(customer_id, document_id, usable, continues=continued)
        reader, version = self._socket.reader_id(record.detected_type)
        run.version_marks = version_marks(version)
        outputs = [ReaderOutput("first", reader, version, tuple(spans))]
        if second is not None:
            second_reader, second_version = self._socket.reader_id(record.detected_type, second=True)
            outputs.append(ReaderOutput("second", second_reader, second_version, tuple(second)))
        marks, line_texts = _line_marks(spans)
        read = _Read(
            name=record.display_name,
            pages=pages,
            usable=usable,
            # The raw second reading is kept as read; only the labels leave pages out.
            compared=None if second is None else _without_unread_pages(second, pages),
            pieces=pieces,
            marks=marks,
            line_texts=line_texts,
            outputs=outputs,
            reader=reader,
            prepared=prepared,
            pictured=pictured,
        )
        # Council 16 Oct 2026: the card (type, date, summary, links) is asked only on readable
        # pages, never on a local reader's text of an unreadable one; with none, no card or map.
        readable = [span for span in usable if getattr(span.location, "page", None) not in unreadable]
        rule = values_from_cells(usable) + values_from_lines(usable)

        # Ready first (Tushar, 16 Oct 2026): saved and ready with the rule values, checked and
        # labelled as with the model off, before the card and the map are asked (Ready waits
        # on no model call); then understood and saved again, filed, and indexed.
        with self._timed(run, "checker", when=self._checker is not None):
            checks = self._second_readings(rule, prepared, pictured, heartbeat, run, line_texts)
        without_model = Understanding(DocumentFacts(), rule, [], None, 0, None)
        headings = DocumentMap(sections_from_pieces(pieces), (), 0, None)
        content = self._content(job, run, read, without_model, headings, checks)
        require_clean(self._statuses, job)  # still clean just before writing
        self._store.save(content, run_id=run.run_id)
        heartbeat()
        run.embedded = self._embed is not None
        if readable and (self._chat is not None or self._map_budget is not None):
            # The last progress before Ready says the understanding goes on (``ready_at``, for
            # the API); the run stays open, so the second save is fenced like the first.
            self._progress(job, run, "understanding", 0, 2, ready_at=self._clock())
            self._documents.mark_ready(customer_id, document_id, len(content.pages), open_run=run.run_id)
            run.ready_at = self._timer()
            self._understand_after_ready(job, run, read, readable, rule, checks, heartbeat)
        # Fast scan (council 16 Oct 2026): the meaning vectors follow (``_index``) once the run
        # ends here, so the embeddings' tokens are in the usage ledger only.
        finished = self._finished(run, "ready", run.model_error)
        self._documents.mark_ready(customer_id, document_id, len(content.pages), run=finished)
        self._file(job)  # reads the card's type, never a vector
        self._index(job, run, heartbeat)

    def _understand_after_ready(
        self,
        job: Job,
        run: _Run,
        read: "_Read",
        readable: list[Span],
        rule: list[ExtractedValue],
        checks: tuple[dict[Location, str], set[Location]],
        heartbeat: Heartbeat,
    ) -> None:
        """Ready first, its second part: the card with its invoice windows (``_understand``)
        and, on a thread of its own, the map (fast scan: it reads only the pieces' text,
        sections and pages, never the card's context header); then the checker on the boxes
        the card and the windows located (``checks`` holds the rule values' already), and
        everything saved again (``_content``). That save is fenced like the first: a newer
        run (a read again) makes it raise Superseded and write nothing; and it writes each
        person's word change again (``word_review.reapply``). The model down or its reply
        unusable saves the rule values again, with the run's code (``model_<code>``)."""
        with ThreadPoolExecutor(1) as mapper:
            mapping = mapper.submit(contextvars.copy_context().run, self._map, job, run, read.pieces)
            with self._timed(run, "card", when=self._chat is not None):
                understanding = self._understand(job, run, heartbeat, readable, rule)
            document_map = mapping.result()
        # After the card, which sets the run's calls and error: the map adds to them.
        run.model_calls += document_map.model_calls
        run.model_error = run.model_error or document_map.error_code
        if document_map.model_calls:
            heartbeat()
        # A card value, or a located value whose spot is unsure (a loose, ambiguous or
        # several-span quote), is never verified, so its box is never cropped (security A8).
        quoted = [
            value
            for value in understanding.quoted_values
            if value.record_key != CARD_RECORD and not spot_unsure(value)
        ]
        readings, checked = checks
        with self._timed(run, "checker", when=self._checker is not None and bool(quoted)):
            more, more_checked = self._second_readings(
                quoted, read.prepared, read.pictured, heartbeat, run, read.line_texts, already=checked
            )
        content = self._content(
            job, run, read, understanding, document_map, ({**readings, **more}, checked | more_checked)
        )
        self._store.save(content, run_id=run.run_id)
        heartbeat()
        failed = run.model_error is not None  # saved with the rule values; a read again asks again
        self._step(job, "understanding", run.ready_at, "failed" if failed else "done", run.model_error)

    def _step(
        self,
        job: Job,
        step: str,
        started: float | None,
        outcome: str,
        code: str | None = None,
        message: str = "",
        sqlstate: str | None = None,
    ) -> None:
        """One log line per post-Ready step outcome (never stuck, 17 Oct 2026, rule 7): the
        step, ``outcome`` (done, pending_retry or failed), its seconds since ``started`` on the
        timer and its code; ids only. Done is INFO, the others WARNING."""
        seconds = None if started is None else round(max(0.0, self._timer() - started), 3)
        extra: dict[str, object] = {
            "customer_id": job.customer_id,
            "document_id": job.document_id,
            "step": step,
        }
        extra |= {"outcome": outcome, "seconds": seconds, "code": code, "sqlstate": sqlstate}
        _log.log(
            logging.INFO if outcome == "done" else logging.WARNING, message or f"step {outcome}", extra=extra
        )

    def _content(
        self,
        job: Job,
        run: _Run,
        read: "_Read",
        understanding: Understanding,
        document_map: DocumentMap,
        checks: tuple[dict[Location, str], set[Location]],
    ) -> DocumentContent:
        """Everything one save keeps, from the reading and an ``understanding`` (both saves
        of Ready first build it): the card's facts, the pieces with their context headers
        (C4: the card's type and date), the values labelled with the checker's ``checks``,
        the remembered corrections, the invoice row and the links; the run's label counts."""
        customer_id, document_id = job.customer_id, job.document_id
        readings, checked = checks
        facts = understanding.facts
        # L2: the card says the languages; with no card (the model off or down) the pieces do.
        facts = replace(facts, languages=facts.languages or languages_without_model(read.pieces))
        pieces = with_context(
            read.pieces,
            document_name=read.name,
            doc_type=facts.doc_type,
            document_date=facts.document_date,
        )
        rule, quoted = understanding.rule_values, understanding.quoted_values
        values = label_values(rule, read.compared, readings, also_located=quoted, checked=checked)
        values += label_quoted(quoted, read.compared, readings, also_located=rule, checked=checked)
        # The checker's disagreements (F2) are counted before the reading marks may give
        # those values another reason (V4, G5, Q7), so the page keeps readers_disagree.
        disputed = Counter(
            value.location.page
            for value in values
            if value.label_reason == "readers_disagree" and isinstance(value.location, BoxLocation)
        )
        local = {page_no for page_no, page_read in run.page_reads.items() if _handed_on(page_read)}
        values = label_reading_marks(values, read.marks, local)  # V4, G5, Q7
        pages = _with_results(read.pages, run.page_reads, disputed)
        if values:
            learned = self._store.load_learned(customer_id, {value.field for value in values})
            values = [_with_parsed(value) for value in apply_learned(values, learned)]
        values, invoice = self._invoice(understanding, facts, read.usable, values)
        run.values_verified = sum(1 for value in values if value.label == "verified")
        run.values_needs_review = len(values) - run.values_verified
        return DocumentContent(
            customer_id,
            document_id,
            pages,
            read.outputs,
            pieces,
            values,
            read.reader,
            facts=facts,
            invoice=invoice,
            links=document_links(facts, values),
            sections=document_map.sections,
            topics=document_map.topics,
            layout=page_layout(read.usable),
        )

    def _file(self, job: Job) -> None:
        """Auto-filing (feature 28, R4), after the document is ready. It never fails the
        reading: an error is logged with its class, its SQLSTATE and the ids, never its
        message, and the family keeps its state."""
        started = self._timer()
        try:
            self._documents.file_document(job.customer_id, job.document_id)
        except Exception as error:  # noqa: BLE001 - R4: the document is read; filing is extra
            sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
            self._step(job, "filing", started, "failed", type(error).__name__, "autofile failed", sqlstate)
        else:
            self._step(job, "filing", started, "done")

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
        With no chat model it is today's result: no card, and the rule values as they are.
        Its calls are billed as card until ``understand`` starts its invoice or land windows,
        then as invoice or land (SP1)."""
        stage = contextlib.ExitStack()

        def windows(kind: str) -> None:
            stage.close()
            stage.enter_context(self._billed(job, run, kind))

        def beat(done: int, total: int) -> None:
            heartbeat()
            self._progress(job, run, "understanding", done, total)

        budget = self._model_budget
        chat = self._chat if budget is not None else None
        with stage:
            stage.enter_context(self._billed(job, run, "card"))
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
                on_windows=windows,
                with_land=chat is not None and (self._land_on is None or self._land_on(job.customer_id)),
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
        if understanding.land is not None:  # PRD 6 D2: text past the last land window, unread
            run.quote_config["land_truncated"] = int(understanding.land.truncated)
        return understanding

    def _map(self, job: Job, run: _Run, pieces: list[Piece], *, ask: bool = True) -> DocumentMap:
        """CX2, CX3: the document's sections (``sections_from_pieces``), with their summaries
        and the topics from the one map call when the processor has a chat model and its
        ``map_budget``. A call that failed leaves the sections from the headings and no
        topics, and its code, like the card's; the caller counts both on the run. The call
        is billed as map (SP1). It runs on its own thread, alongside the card. No call unless
        ``ask`` (a document with no readable page keeps its sections, with no summaries)."""
        sections = sections_from_pieces(pieces)
        if self._map_budget is None or not ask:
            return DocumentMap(sections, (), 0, None)
        with self._timed(run, "map"), self._billed(job, run, "map"):
            return map_document(self._chat, pieces, sections, self._map_budget)

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

    def _index(self, job: Job, run: _Run, heartbeat: Heartbeat) -> None:
        """Make the pieces' meaning vectors, after the document is ready (fast scan, council
        16 Oct 2026). After each batch is written: the heartbeat, then a check that the
        document is still ready; one that is not (a "read again" started a newer run) stops
        here quietly, within one batch. It never writes a status or progress: any other
        failure is logged with its class (or the model's code) and the ids, never a message,
        and the document stays ready; the worker's EmbedMissing fills the vectors left. A
        lost lease goes on up, as before (the run already ended ready).
        Never stuck (17 Oct 2026): past ``index_seconds`` it stops after the batch in hand
        (pending_retry), so a long document never holds the worker; EmbedMissing does the rest.
        ponytail: checked between batches, so it can run one batch (32 one-text requests) over."""
        if self._embed is None:
            return
        started = self._timer()

        def on_batch(done: int, total: int) -> None:
            heartbeat()
            if self._statuses.get_status(job.customer_id, job.document_id) is not DocumentStatus.READY:
                raise Superseded(run.run_id)
            if (
                self._index_seconds is not None
                and done < total
                and self._timer() - started >= self._index_seconds
            ):
                raise _IndexLater

        try:
            with self._billed(job, run, "embed"):
                self._embed(job.customer_id, job.document_id, on_batch=on_batch)
        except Superseded:
            return
        except LeaseLost:
            raise
        except _IndexLater:
            self._step(
                job, "indexing", started, "pending_retry", "deadline", "pieces saved without meaning vectors"
            )
        except Exception as error:  # noqa: BLE001 - the document is ready; vectors come later
            model = isinstance(error, self._model_errors)
            code = _code(error) if model else type(error).__name__
            outcome = "pending_retry" if model else "failed"  # EmbedMissing tries both again
            self._step(job, "indexing", started, outcome, code, "pieces saved without meaning vectors")
        else:
            self._step(job, "indexing", started, "done")

    def _second_readings(
        self,
        values: list[ExtractedValue],
        prepared: Prepared,
        pictured: Collection[int],
        heartbeat: Heartbeat,
        run: _Run,
        line_texts: Mapping[BoxLocation, str],
        *,
        already: Collection[Location] = (),
    ) -> tuple[dict[Location, str], set[Location]]:
        """(each scanned value's box read again, by box; the boxes the checker read), leaving
        out the boxes ``already`` read (Ready first: the rule values' before Ready).

        A box on a page the reading model read is read by the checker (PRD 5 V1,
        ``_check_crop``: the box grown by CHECK_MARGIN, with the Devanagari recogniser
        when its line, ``line_texts``, has Devanagari); no other box is read again. A
        heartbeat after each (security A8); at most ``max_check_crops`` boxes in all, and
        the values left stay single reader. One page picture at a time, drawn again
        (``Prepared.picture``): the pixels preparing drew, lossless. A reading of nothing
        is no reading."""
        checker = self._checker
        if checker is None or not run.models:
            return {}, set()
        # C5: a page Careful or another Ensemble model read is checked like the main model's.
        checker_pages = {page_no for page_no, read in run.page_reads.items() if read.read_by in run.models}
        wanted: dict[int, dict[BoxLocation, bool]] = {}  # page -> {box: Devanagari}, in order, once each
        for value in values:
            box = value.location
            if not isinstance(box, BoxLocation) or (
                value.confidence is None and value.origin in DIRECT_ORIGINS
            ):
                continue  # read directly, not scanned; no origin counts as scanned (fail closed)
            if box.page in pictured and box.page in checker_pages and box not in already:
                text = line_texts.get(box, value.value_text)
                wanted.setdefault(box.page, {})[box] = _DEVANAGARI.search(text) is not None
        readings: dict[Location, str] = {}
        checked: set[Location] = set()
        for page_no, boxes in wanted.items():
            with _or_fail(functools.partial(prepared.picture, page_no)) as page:
                for box, devanagari in boxes.items():
                    if run.vision_crops >= self._max_check_crops:
                        return readings, checked
                    run.vision_crops += 1
                    run.checker_crops += 1
                    with _check_crop(page, box) as crop:
                        text = checker.read_crop(crop, devanagari=devanagari)
                    checked.add(box)
                    heartbeat()
                    text = text.strip()
                    if text:
                        readings[box] = text
        return readings, checked

    def _reading_document(
        self, job: Job, run: _Run, *, escalate: frozenset[int] = frozenset(), page_count: int = 0
    ) -> contextlib.AbstractContextManager[None]:
        """The document's page cache, open around its reading (PRD 5 G7, F5), with the pages
        a user asked to read again (kept on the run, for its counters) and the job's model
        and provider; nothing to open without a meter or a page cache. One short query for
        the pages. With the document's ``Reading`` (features 25-27): its thinking level,
        Ensemble's other models, or Dynamic's Careful with its memo of first reads and the
        pages to read again with it (``escalate``, the second reading). Fast scan (council 16
        Oct 2026): a Dynamic document of at most DYNAMIC_MAX_PAGES pages (``page_count``) is
        read once, Careful page by page (``inline_careful``), its reasons kept on the run.
        #384: with ``Reading.routes`` each page is routed by its local read, each route kept
        on the run (``routed``); #385: one more short query for the regions asked for when a
        page is to be read again."""
        meter, cache = self._ocr_meter, self._page_cache
        if meter is None or cache is None:
            return contextlib.nullcontext()
        reading = run.reading
        if self._read_again is not None:
            run.read_again = frozenset(self._read_again.pages_to_read_again(job.customer_id, job.document_id))
            if run.read_again:  # #385: a second short query only when a page was asked for
                run.read_again_regions = self._read_again.regions_to_read_again(
                    job.customer_id, job.document_id
                )
        routes = reading is not None and reading.routes
        if routes and run.routed is None:
            run.routed = {}
        careful = None if reading is None or reading.mode != "dynamic" else reading.careful
        if reading is not None and reading.scan_reader == MISTRAL:
            careful = None  # #473: Careful is Gemini; Mistral's first read is its only one
        if careful is not None and run.memo is None:
            run.memo = {}
        inline = careful is not None and 0 < page_count <= DYNAMIC_MAX_PAGES
        if inline:
            run.escalated = {}
        pages = DocumentPages(
            customer_id=job.customer_id,
            document_id=job.document_id,
            cache=cache,
            read_again=run.read_again,
            model=run.model,
            provider=run.provider,
            thinking=None if reading is None else reading.thinking,
            resolution=None if reading is None else reading.resolution,  # #463: the organisation's
            careful_thinking=None if reading is None else reading.careful_thinking,
            careful_resolution=None if reading is None else reading.careful_resolution,
            max_output_tokens=None if reading is None else reading.page_max_output_tokens,
            others=()
            if reading is None
            else tuple((agent.model, agent.provider) for agent in reading.others),
            careful=None if careful is None else (careful.model, careful.provider),
            escalate=escalate,
            memo=None if careful is None else run.memo,
            inline_careful=inline,
            page_count=page_count,
            escalated=run.escalated if inline else None,
            local=reading is not None and reading.mode == "local",
            route_pages=routes,
            routed=run.routed if routes else None,
            read_again_regions=run.read_again_regions,
            plain=frozenset()
            if reading is None
            else frozenset(
                agent.model
                for agent in (reading.lead, reading.careful, *reading.others)
                if agent is not None and not agent.thinks
            ),
            # #463 review: a Careful or read-again level its model does not take is not sent.
            levels={} if reading is None else reading.accepted(),
            scan_reader=OPENROUTER if reading is None else reading.scan_reader,  # #473: who reads
            mistral_model=DEFAULT_MISTRAL_MODEL if reading is None else reading.mistral_model,
        )
        return meter.document(pages)

    def _read_carefully(
        self,
        job: Job,
        record: DocumentRecord,
        data: bytes,
        heartbeat: Heartbeat,
        run: _Run,
        spans: list[Span],
    ) -> list[Span]:
        """Dynamic (F26, C5), after the first reading: the pages worth a careful read
        (``careful_pages``: worst first, at most DYNAMIC_MAX_PAGES), noted for
        run_config.reading, and when there are any, the document read a second time through
        the readers, where each page's first read comes back from the memo with no call and
        those pages are read with Careful (the engine's ``_dynamic``). The second reading's
        spans and page reads are the document's. ``spans`` unchanged otherwise."""
        reading, meter = run.reading, self._ocr_meter
        lines_max, share = self._cost_saving
        if run.escalated is not None:  # read with Careful page by page already (fast scan)
            chosen, cap_hit = worst_first(run.escalated)
            run.reading_notes.update(escalated=chosen, cap_hit=cap_hit)
            _note_tokens(run)
            return spans
        if meter is None or run.memo is None or reading is None or run.model is None:
            return spans
        chosen, cap_hit = careful_pages(
            run.page_reads, lead=run.model, read_again=run.read_again, lines_max=lines_max, share=share
        )
        run.reading_notes.update(escalated=chosen, cap_hit=cap_hit)
        if not chosen:
            _note_tokens(run)
            return spans
        # Its own step, so the screen says what is happening; every page read is a move.
        self._progress(job, run, "rereading", 0, len(chosen))

        def beat(_read: RouteDecision | None) -> None:
            heartbeat()
            self._progress(job, run, "rereading", 0, len(chosen))

        escalate = frozenset(page["page"] for page in chosen)
        reading_again = self._reading_document(job, run, escalate=escalate)
        with self._timed(run, "careful"), reading_again, waiting(heartbeat):
            spans = _or_fail(lambda: self._socket.read(record.detected_type, data, on_page=beat))
        run.page_reads = {**run.page_reads, **meter.take_page_reads()}
        _note_tokens(run)
        return spans

    def _count_unreadable(self, run: _Run, pages: Sequence[PageRecord]) -> frozenset[int]:
        """The pages graded unreadable, counted on the run. Never failed (Tushar, 16 Oct 2026):
        a document whose every page is unreadable is ready all the same, each such page
        flagged for review with its fix ("Read it again, or upload a better scan"), in
        place of F4's ``unreadable`` failure. O(pages)."""
        unreadable = frozenset(page.info.page for page in pages if page.read_quality == "unreadable")
        run.pages_unreadable = len(unreadable)
        return unreadable

    @contextlib.contextmanager
    def _timed(self, run: _Run, step: str, *, when: bool = True) -> Iterator[None]:
        """The block's seconds on the run as ``seconds_<step>`` (its config: ``cli scan-times``),
        only ``when`` the step has work (a model, a checker); a step timed twice (the checker,
        before and after Ready) adds both."""
        started = self._timer()
        try:
            yield
        finally:
            if when:
                key = f"seconds_{step}"
                run.steps[key] = round(run.steps.get(key, 0.0) + max(0.0, self._timer() - started), 3)

    def _billed(self, job: Job, run: _Run, kind: str) -> contextlib.AbstractContextManager[object]:
        """SP1: the model calls made inside are billed as ``kind`` to the user who asked for
        the reading (``run.user_id``) and this document; nothing without a ledger."""
        if self._usage is None:
            return contextlib.nullcontext()
        return self._usage(
            customer_id=job.customer_id, user_id=run.user_id, kind=kind, document_id=job.document_id
        )

    # --- runs and progress (R6, X2, X3) -----------------------------------------

    def _start_run(self, job: Job) -> _Run:
        """A new run of this delivery, started now. The OCR meter starts from zero (AR11)."""
        if self._ocr_meter is not None:
            self._ocr_meter.take()
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

    def _finished(self, run: _Run, status: str, error_code: str | None) -> FinishedRun | None:
        """The run as it ends now, with everything it counted; None without runs.
        The OCR meter is read and set back to zero either way (AR11). The reader counters
        (PRD 5) come from the pages read: those of a reading that ended part way too."""
        meter = self._ocr_meter
        pages_sent, ocr_seconds = meter.take() if meter is not None else (0, 0.0)
        page_reads = {**(meter.take_page_reads() if meter is not None else {}), **run.page_reads}
        if run.run_id is None:
            return None
        ended = self._timer()
        checked_by = self._checker_model if run.checker_crops else None
        counters = _reader_counters(page_reads, run.models, run.read_again)
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
                **_step_seconds(run, ended),
                **({"read_model": run.model} if run.model is not None else {}),
                **({"reading": _reading_config(run)} if run.reading is not None else {}),
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

    def _progress(
        self, job: Job, run: _Run, step: Step, done: int, total: int, *, ready_at: datetime | None = None
    ) -> None:
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
            progress = Progress(step, done, total, estimate, run.started_at, run.reached, run.shown, ready_at)
            self._progress_store.set_progress(job.customer_id, job.document_id, progress, run_id=run.run_id)
            if estimate is not None:
                run.prior, run.shown = run.shown, (step, estimate)  # once written: what the screen has
        except BackendUnavailable:
            _log.warning(
                "progress_not_saved",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": step},
            )

    def _clear_progress(self, job: Job, run: _Run, code: str) -> None:
        """Clear this run's progress before the job waits to come back; when the reader
        stopped it (``code`` in _READER_WAITS), mark it waiting for the reader instead
        (PRD 5 Q6). Best effort: a database that is down is logged with the run's error
        code."""
        if self._progress_store is None:
            return
        waiting = "reader" if code in _READER_WAITS else None
        try:
            self._progress_store.clear_progress(
                job.customer_id, job.document_id, run_id=run.run_id, waiting=waiting
            )
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

    def _rates(self, job: Job, detected_type: str) -> Rates | None:
        """The time-left rates from this customer's own runs only (one query), or the
        constants alone when they can't be read; None when no progress is shown. A photo
        or scan file (``_PHOTO_TYPES``) is all OCR pages."""
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
        return rates(
            history,
            None if self._ocr_meter is None else self._ocr_meter.name,
            pages_at_once=pages_in_flight(self._ocr_page_concurrency),  # the job's own (package S)
            scanned=detected_type in _PHOTO_TYPES,
        )

    def _estimate(self, run: _Run, step: Step, done: int, total: int) -> Estimate | None:
        """The time left written with this step: while preparing or reading, from the
        pages so far; from the end of reading, one finish kept for every later step, but
        moved now at each beat (a card, a map, a batch embedded), so it is never stalled
        while work is done (progress fix, 16 Oct 2026)."""
        if run.rates is None or step == "scanning":
            return None
        if step in ("rereading", "understanding", "indexing"):
            now = self._clock()
            if run.after is None:
                run.after = after_reading(run.rates, now)
            return replace(run.after, moved_at=now, finish_at=max(run.after.finish_at, now))
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
    ``seconds_after_reading`` (to Ready: the time left counts to it) for its config, from the
    timer's marks; none for a run that did not finish reading. Ready first: also
    ``seconds_to_ready`` (from the run's start) and ``seconds_after_ready`` (understanding)."""
    if run.reading_started is None or run.reading_ended is None:
        return {}
    ready = now if run.ready_at is None else run.ready_at
    reading, after = max(0.0, run.reading_ended - run.reading_started), max(0.0, ready - run.reading_ended)
    timed = {"seconds_reading": round(reading, 3), "seconds_after_reading": round(after, 3), **run.steps}
    if run.ready_at is not None:
        timed["seconds_to_ready"] = round(max(0.0, run.ready_at - run.started), 3)
        timed["seconds_after_ready"] = round(max(0.0, now - run.ready_at), 3)
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
        read_thinking=read.read_thinking,
        read_resolution=read.read_resolution,
        read_pass=read.read_pass,
        read_output_tokens=read.read_output_tokens,
    )


def _reader_counters(
    page_reads: Mapping[int, PageRead], models: Collection[str], read_again: Collection[int]
) -> dict[str, int]:
    """A run's reader counters (``kaagaz.db.runs.READER_COUNTERS``) but pages_unreadable,
    from its page reads: pages a reading model read and billed (not from the cache),
    pages the reading model handed to its local reader (``_handed_on``: a page read with
    no key, or by another engine, is not one), pages from the cache, pages read again on request by the model,
    and the tokens billed. ``models``: the run's reading models (the main one, Careful,
    Ensemble's others); with none no page is a model's. O(pages)."""
    reads = list(page_reads.items())
    billed = [n for n, read in reads if read.read_by in models and not read.cached]
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
    whatever engine read it: never a page read with no key."""
    return not HANDED_ON.isdisjoint(read.issues)


def _note_tokens(run: _Run) -> None:
    """Cost saving's measurement (``cli cost-saving``, 16 Oct 2026), on run_config.reading:
    each escalated page's tokens, ``first_in``/``first_out`` its first read's and
    ``tokens_in``/``tokens_out`` Careful's (its last read's less the first's), and
    ``kept_quick``, each page that kept its first read though ``careful_reasons`` gives it
    reasons (before Cost saving it was read whole), with the same tokens (Careful's: 0, or a
    dropped early read). First reads come from the memo, a page read twice (the DPI ceiling)
    with both reads' tokens, as the meter sums them. Runs before this have neither. O(pages)."""
    if run.memo is None or run.model is None:
        return
    first: dict[int, tuple[int, int]] = {}
    reads: dict[int, PageRead] = {}
    for (page_no, _sha), read in run.memo.items():
        tokens_in, tokens_out = first.get(page_no, (0, 0))
        first[page_no] = (tokens_in + read.input_tokens, tokens_out + read.output_tokens)
        reads[page_no] = read

    def tokens(page_no: int) -> dict[str, int]:
        first_in, first_out = first.get(page_no, (0, 0))
        last = run.page_reads.get(page_no)
        last_in, last_out = (first_in, first_out) if last is None else (last.input_tokens, last.output_tokens)
        return {
            "first_in": first_in,
            "first_out": first_out,
            "tokens_in": max(0, last_in - first_in),
            "tokens_out": max(0, last_out - first_out),
        }

    escalated = run.reading_notes.get("escalated") or []
    for entry in escalated:
        entry.update(tokens(entry["page"]))
    chosen = {entry["page"] for entry in escalated}
    kept = []
    for page_no, read in sorted(reads.items()):
        again = page_no in run.read_again
        # careful_plan is empty whatever the limits: no page-wide reason and no hard line.
        reasons = careful_reasons(read, lead=run.model, again=again)
        if page_no not in chosen and reasons and not careful_plan(read, lead=run.model, again=again):
            kept.append({"page": page_no, "reasons": reasons, **tokens(page_no)})
    run.reading_notes["kept_quick"] = kept


def _reading_config(run: _Run) -> dict[str, Any]:
    """``processing_runs.config.reading`` (S5a, C5, M7): the document's ``Reading`` (ids,
    providers, mode, thinking, notes) and what the run found (Dynamic's escalated pages
    with their reasons and cap_hit)."""
    reading = run.reading
    config = {} if reading is None else reading.config()
    config.update(run.reading_notes)
    if run.routed:  # #384: how each scanned page was routed, for measuring the saving
        config["routed"] = [
            {"page": page_no, "route": route, "reason": reason}
            for page_no, (route, reason) in sorted(run.routed.items())
        ]
    return config


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
    """Its number (a land share's only when a printed percentage, ``land.value_number``) and date."""
    text = value.value_text
    return replace(value, value_number=value_number(value.field, text), value_date=parse_date(text))


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
    from sqlalchemy import text

    from kaagaz.ai.client import ModelOutputError, ModelUnavailable
    from kaagaz.ai.usage import usage_scope
    from kaagaz.db import ai_settings, models
    from kaagaz.db.documents import PgDocuments
    from kaagaz.db.engine import unavailable
    from kaagaz.db.page_reads import PgPageReads
    from kaagaz.db.pipeline_store import PgPipelineStore
    from kaagaz.db.runs import PgRuns
    from kaagaz.preparing.pages import prepare_pages, sheet_pages
    from kaagaz.reading.ocr import GEMINI, MeteredOcrEngine
    from kaagaz.reading.rapid_pool import shared_pool
    from kaagaz.reading.wiring import build_ocr_engine, build_reader_socket, reader_config
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
    engine = build_ocr_engine(settings, db=db)
    meter = None if engine is None else MeteredOcrEngine(engine, timer=time.perf_counter)
    # The checker (V1) reads the boxes of pages the reading model read, so only with it; it
    # shares the process's one RapidOCR pool with the box pinning (CPU_WORKERS).
    gemini = engine is not None and settings.ocr_engine == GEMINI
    # Model choice and AI spend (council 11 Oct 2026): with a model in use, each job asks who
    # it is billed to and, with the Gemini reader, which model reads it (the worker's own
    # check of the upload's choice, MS5). Keyless: no query a job and no ledger, as before.
    job_reading: Callable[[str, str], JobReading] | None = None
    if gemini or model is not None:
        requested_by = text("SELECT read_requested_by FROM documents WHERE customer_id = :c AND id = :d")
        pages_at_once_sql = text("SELECT pages_at_once FROM platform_ai_settings")

        def read_job(customer_id: str, document_id: str) -> JobReading:
            with unavailable(), db.customer(customer_id) as conn:
                user = conn.execute(requested_by, {"c": customer_id, "d": document_id}).scalar_one_or_none()
                user_id = None if user is None else str(user)
                if not gemini:
                    return JobReading(user_id)
                # Features 25-27 (S2): the worker's own check of the mode and every model, now.
                scan = models.resolve_scan(conn, customer_id, document_id, settings=settings)
                at_once = conn.execute(pages_at_once_sql).scalar_one()  # package S, 0053
            return JobReading(
                user_id, scan.lead.model, scan.lead.provider, reading=scan, pages_at_once=at_once
            )

        job_reading = read_job

    def land_on(customer_id: str) -> bool:
        with unavailable(), db.customer(customer_id) as conn:
            return ai_settings.switch_on(conn, "land_facts", customer_id)

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
        model_errors=(ModelUnavailable, ModelOutputError),
        runs=PgRuns(db),
        progress=documents,
        ocr_meter=meter,
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
        job_reading=job_reading,
        usage=None if job_reading is None else functools.partial(usage_scope, db),
        ocr_page_concurrency=settings.ocr_page_concurrency,
        cost_saving=(settings.cost_saving_hard_lines_max, settings.cost_saving_hard_share),
        index_seconds=settings.model_timeout_seconds,
        # #473: the Gemini reader's own check that Mistral can read (RapidOCR has none).
        mistral_ready=getattr(engine, "mistral_ready", None),
        land_on=land_on if model is not None else None,
    )


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
