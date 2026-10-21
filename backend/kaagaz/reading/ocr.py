"""The scan reader engines (OCR), behind one small interface.

The main scan reader is the Gemini reader (PRD 5, OCR_ENGINE=gemini). RapidOCR
(Apache-2.0) is the local reader (OCR_ENGINE=rapidocr: laptops and CI), and the
Gemini reader's box finder, checker and backup. Nothing is downloaded at run time.

* ``OcrEngine`` is what the page readers depend on, so tests use a fake and a
  different engine means one new class here.
* ``RapidOcrEngine`` imports ``rapidocr`` only on its first page, because it
  is an optional extra (``pip install .[ocr]``): without it, the rest of the
  backend still starts, and ``OCR_ENGINE=none`` marks scans needs_reader.
  The model is loaded once, under a lock, and reused for every page. Its
  default recogniser has no Devanagari, so a page whose reading is suspect
  (``ocr_script_suspect``: letters from other scripts, such as Chinese) is
  read again with the Devanagari one, and the better reading is kept
  (``_better``); the version then names that model. Its file is fetched when
  the image is built (``backend/Dockerfile``) and only ever loaded from disk:
  when it is missing (``ocr_model_missing``), the page stays as first read.
* ``MeteredOcrEngine`` wraps any engine and counts the pages sent to it and
  the seconds they took (PRD 2 R6: the run's ``seconds_ocr``; ``cli measure``).
  It also keeps each numbered page's ``PageRead``
  for the run (``take_page_reads``, PRD 5 G7, F1). Pages of one document are
  read on several threads at once (G8), so its counts are kept under a lock;
  the seconds are the pages' own, summed, so they can pass the wall time.
* ``as_page_engine`` gives any engine ``read_page`` (PRD 5): a ``PageEngine``
  (the Gemini reader) as it is, and any other engine through ``read``, as a
  page with no grade of its own, ``read_by`` the id of the engine that read it
  ("rapidocr").
* The Gemini reader (PRD 5 G1, OCR_ENGINE=gemini) lives in its own module,
  ``kaagaz/reading/gemini_ocr.py``, which this module never imports: a
  ``GeminiEngineFactory`` given to ``build_ocr_engine`` builds it, with the
  process's ``RapidOcrPool``. ``kaagaz.reading.wiring`` gives the product's
  (``build_gemini_engine``, which imports it by its path only for
  OCR_ENGINE=gemini), so OCR_ENGINE=rapidocr never loads it.
* The page cache (PRD 5 G7, F5): the pipeline opens ``reading_document(engine,
  DocumentPages(...))`` around one document's reading. An engine with a page cache
  (``DocumentEngine``, the Gemini reader) then keys each page by that document; the
  meter passes it on; any other engine has nothing to open.
* No reader choice at upload (PRD 5 RT3): ``ocr_choices`` offers one reader,
  Auto, so the "Scan with" select hides itself. A choice sent or stored before is
  ignored: every scan is read with OCR_ENGINE's engine.
* ``ocr_spans`` turns an engine's pixel boxes into spans with page fractions
  and origin "ocr". RapidOCR's boxes go through the same line rule as the PDF
  readers (``kaagaz/reading/lines.py``), so "INVOICE" and "1042" found as two
  boxes on one line become one span. The Gemini reader's lines are already in
  reading order, so each becomes one span, in the order given. A line's
  ``box_source`` is copied to its span, and its word marks (PRD 5 F3) become
  spans of their own (``is_word_mark``).

An engine never logs what it read: RapidOCR's own logger is set to errors
only, and its messages hold no page text.
"""

import contextlib
import dataclasses
import logging
import math
import re
import statistics
import threading
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, runtime_checkable

from PIL import Image

from kaagaz.chunking.script import ocr_script_suspect, ocr_suspect_count
from kaagaz.reading.lines import PageFrame, WordBox, line_spans
from kaagaz.reading.page_quality import ILLEGIBLE_MARK, ILLEGIBLE_WORDS, NO_TEXT
from kaagaz.reading.rapid_pool import (
    DEVANAGARI_REC_MODEL,
    PixelBox,
    RapidOcrPool,
    build_rapidocr,
    devanagari_model_path,
    devanagari_params,
    shared_pool,
)
from kaagaz.reading.spans import BOX_SOURCES, BoxLocation, ReadError, Span
from kaagaz.settings import Settings, SettingsError

if TYPE_CHECKING:
    from kaagaz.db.page_reads import PageKey

# Caps on one page's reply from the reader (security A7, kaagaz/reading/gemini_ocr.py):
# lines past MAX_BLOCKS_PER_PAGE, longer than MAX_BLOCK_CHARS, or in a table row or
# column from MAX_TABLE_INDEX on are dropped and counted.
MAX_BLOCKS_PER_PAGE = 5000
MAX_BLOCK_CHARS = 20_000
MAX_TABLE_INDEX = 1000
# The most pixels a page is drawn at for OCR. A PNG that cannot compress is 3 bytes a
# pixel, so one page's PNG stays under 64 MiB. Readers lower the DPI to fit it, never refuse.
MAX_OCR_PIXELS = 21_000_000
# The one reader an upload is offered (PRD 5 RT3): "the usual way".
AUTO = "auto"
# OCR_ENGINE's value for the Gemini reader (PRD 5 G1).
GEMINI = "gemini"
# G1, G7: the Gemini reader's prompt version (kaagaz/reading/gemini_ocr.py): changes whenever
# its INSTRUCTION or PAGE_SCHEMA changes, so a page read with the old ones is never taken
# from the page cache and processing_runs shows the change. Here, not in that module, so the
# pipeline can ask which models are verified for it (model choice, council 11 Oct 2026)
# without loading the reader, and the API may import it. g2: the strict-portable PAGE_SCHEMA.
READER_PROMPT_VERSION = "g2"
# PRD 5 RT2: pages.read_by for a page the local reader read.
LOCAL_READ_BY = "rapidocr"
# PRD 5 F3: the span qualities that mark one word of a line, not the line.
WORD_MARK_QUALITIES = frozenset({"unsure", "illegible"})
# Features 26 and 27 (council 15 Oct 2026, C5, C6): at most this many pages of a document are
# read again with Careful (Dynamic), worst first; a page of at least CROWDED_LINES lines is
# crowded; the other readers of Ensemble read the pages numbered up to ENSEMBLE_MAX_PAGES.
DYNAMIC_MAX_PAGES = 25
CROWDED_LINES = 60  # ponytail: fixed threshold, tune on the golden set
ENSEMBLE_MAX_PAGES = 50
# C5: the page issues that make a page worth a careful read. cut_off, low_resolution and
# tilted never do: more care can't read what is not on the picture.
CAREFUL_ISSUES = frozenset(
    {
        "blurry",
        "faint",
        "glare",
        "shadow",
        "stamp_over_text",
        "overwritten",
        "handwriting_hard",
        ILLEGIBLE_WORDS,
        NO_TEXT,
    }
)
# Cost saving (Tushar, 16 Oct 2026: the super admin's name for Dynamic, whose ids stay
# "dynamic"; "Automatic" to organisations). A page worth more care is read again in one of
# two ways, named last in its reasons (``careful_plan``): PATH_WORDS, only its hard lines (an
# unsure or [?] word), cut out and read in one call, when they are at most HARD_LINES_MAX and
# at most HARD_SHARE of its lines and every one of them is pinned to a found box; else
# PATH_PAGE, the whole page, as before. PAGE_WIDE are the reasons that always read the whole
# page: the picture itself is hard to see, or no text was found to cut out. HARD_LINES_MAX
# and HARD_SHARE are the defaults of COST_SAVING_HARD_LINES_MAX and COST_SAVING_HARD_SHARE
# (settings), which the reader and the pipeline pass in; scripts/tune_cost_saving.py tunes them.
HARD_LINES_MAX = 12
HARD_SHARE = 0.4
PATH_WORDS, PATH_PAGE = "path_words", "path_page"
# Handwriting stays page wide (Tushar's accuracy first, 16 Oct 2026): a quick read of a
# handwritten page can be sure of every word and still be wrong (7/12 extracts).
PAGE_WIDE = frozenset({*(CAREFUL_ISSUES - {ILLEGIBLE_WORDS}), "quality_poor", "handwritten"})

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OcrTable:
    """Where a table cell sits: its table on the page, and its row and column from 0."""

    table_id: str
    row: int
    col: int
    header: bool


@dataclass(frozen=True, slots=True)
class OcrLine:
    """One piece of text an engine found, in pixels of the image it was given.

    The Gemini reader gives one line per block, with its kind
    (``spans.BLOCK_KINDS``) and, for a table cell, its table. It gives no
    confidence, so that is None: never a made-up number.

    It also gives ``unsure`` (PRD 5): each word in this line it
    is not sure of, copied exactly (G3; a part it can't read at all is
    ``page_quality.ILLEGIBLE_MARK`` in the text). ``box_source`` says where the
    box came from (``spans.BOX_SOURCES``, G5): "found", the union of the boxes
    RapidOCR's text finder found under the line, or "model", the model's own
    box when none matched. None for an engine that finds its own boxes.
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float | None
    kind: str | None = None
    table: OcrTable | None = None
    unsure: tuple[str, ...] = ()
    box_source: str | None = None


class OcrEngine(Protocol):
    @property
    def name(self) -> str: ...  # read-only: a metered engine's follows the engine in use

    @property
    def version(self) -> str: ...  # read-only

    def read(self, image: Image.Image) -> list[OcrLine]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class PageRead:
    """One page as the main reader read it (PRD 5 G1 to G7, F1), for ``PageEngine``.

    * ``page_no``: the page, from 1.
    * ``lines``: in reading order, in pixels of the image given, with their unsure
      words and box source (G3, G5). Header and footer lines keep their kind, so
      they are left out of pieces by the existing rule.
    * ``quality``, ``handwritten``, ``scripts``: the reader's own words about the
      page (G3): ``quality`` is one of ``page_quality.MODEL_QUALITIES`` (good, fair,
      poor), or None when the reader gives none (RapidOCR); ``scripts`` are from
      ``page_quality.MODEL_SCRIPTS``.
    * ``issues``: codes from the F1 table (``page_quality.ISSUE_CODES``) known when
      the page is read: the model's own (G3), and illegible_words, unpinned,
      reader_refused, read_locally and no_text. The pipeline adds low_resolution
      and readers_disagree, which need the page's DPI and the checker.
    * ``read_by``: who read it (RT2): the model ID in OCR_MODEL, or the id of the engine
      that read it ("rapidocr"). A page the Gemini reader handed to the
      local reader (Q7) is "rapidocr" with the issue read_locally or reader_refused.
    * ``input_tokens``, ``output_tokens``: what this read was billed, from the
      reply's usage metadata (thinking tokens are output), every call for the page
      summed: a cut-off whole page before its halves, and a reply that was blocked,
      cut off or invalid before the page was read locally, are billed too. 0 for a
      page taken from the page cache, read locally after an outage, or read by an
      engine with no model. The meter sums them over every read of a page.
    * ``cached``: taken from the page cache (G7), so no call was made or billed.
    * ``continues_on_next_page``: the last line's flag (G3, CX1): its sentence
      goes on at the top of the next page.

    * ``others`` (Ensemble, F27): the page as each other chosen model read it, for the
      vote on the values that need review; never stored.

    The counts F1 stores are worked out from the lines, so they always agree with
    them: ``unsure_words`` (every word listed as unsure), ``illegible_words``
    (every ``[?]``) and ``unpinned_lines`` (lines with box_source "model").
    """

    page_no: int
    lines: tuple[OcrLine, ...]
    quality: str | None
    handwritten: bool
    scripts: tuple[str, ...]
    issues: tuple[str, ...]
    read_by: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    continues_on_next_page: bool = False
    others: tuple["OtherRead", ...] = ()

    @property
    def unsure_words(self) -> int:
        return sum(len(line.unsure) for line in self.lines)

    @property
    def illegible_words(self) -> int:
        return sum(line.text.count(ILLEGIBLE_MARK) for line in self.lines)

    @property
    def unpinned_lines(self) -> int:
        return sum(line.box_source == "model" for line in self.lines)


@dataclass(frozen=True, slots=True)
class OtherRead:
    """Ensemble (F27, Tushar 15 Oct 2026): one other chosen model's read of a page, as
    spans on the page (``ocr_spans``, word marks included), or None when it could not
    read the page (an outage, a refusal: it is left out of the vote, never counted)."""

    model: str
    spans: tuple[Span, ...] | None


def careful_pages(
    reads: Mapping[int, PageRead],
    *,
    lead: str,
    read_again: Collection[int] = (),
    lines_max: int = HARD_LINES_MAX,
    share: float = HARD_SHARE,
) -> tuple[list[dict[str, Any]], int]:
    """Dynamic (F26, C5): ([{"page", "reasons"}] to read again with Careful, how many more
    were left out by the cap). A page counts when ``careful_plan`` gives it reasons, the
    last of them how it is read again. Worst first (most reasons, then page order), at most
    DYNAMIC_MAX_PAGES: the same reads give the same pages, whatever order the threads
    read them in. ``lines_max`` and ``share``: ``careful_plan``'s. O(p log p + text)."""
    found = {
        page_no: reasons
        for page_no, read in reads.items()
        if (
            reasons := careful_plan(
                read, lead=lead, again=page_no in read_again, lines_max=lines_max, share=share
            )
        )
    }
    return worst_first(found)


def careful_plan(
    read: PageRead,
    *,
    lead: str,
    again: bool = False,
    lines_max: int = HARD_LINES_MAX,
    share: float = HARD_SHARE,
) -> list[str]:
    """Cost saving (16 Oct 2026): ``careful_reasons`` with how the page is read again last,
    PATH_WORDS (at most ``lines_max`` hard lines and at most ``share`` of its lines, see
    HARD_LINES_MAX) or PATH_PAGE; none when it keeps its first read: no
    reason, or only reasons that are not PAGE_WIDE (a fair grade, crowded) on a
    page whose every word the reader was sure of. O(issues + the page's text)."""
    reasons = careful_reasons(read, lead=lead, again=again)
    if not reasons:
        return []
    if not PAGE_WIDE.isdisjoint(reasons):
        return [*reasons, PATH_PAGE]
    hard = hard_lines(read)
    if not hard:
        return []
    few = len(hard) <= lines_max and len(hard) <= share * len(read.lines)
    pinned = all(read.lines[index].box_source == "found" for index in hard)
    return [*reasons, PATH_WORDS if few and pinned else PATH_PAGE]


def hard_lines(read: PageRead) -> list[int]:
    """The indexes of the page's hard lines, in page order: a word the reader was unsure of,
    or a part it could not read (ILLEGIBLE_MARK). The checker's disagreement comes after the
    reading, so it is not known here. O(the page's text)."""
    return [index for index, line in enumerate(read.lines) if line.unsure or ILLEGIBLE_MARK in line.text]


def careful_reasons(read: PageRead, *, lead: str, again: bool = False) -> list[str]:
    """Why one page may be worth a careful read (``careful_plan`` decides, for ``careful_pages``
    and page by page while a Dynamic document is read, fast scan, council 16 Oct 2026): an
    issue in CAREFUL_ISSUES, handwriting, a fair or poor grade, unsure words, or at least
    CROWDED_LINES lines; none when ``lead`` did not read it or it was asked to be read again
    (``again``: F5 reads it harder already). O(issues)."""
    if read.read_by != lead or again:
        return []
    reasons = sorted(CAREFUL_ISSUES.intersection(read.issues))
    if read.handwritten:
        reasons.append("handwritten")
    if read.quality in ("fair", "poor"):
        reasons.append(f"quality_{read.quality}")
    if read.unsure_words:
        reasons.append("unsure_words")
    if len(read.lines) >= CROWDED_LINES:
        reasons.append("crowded")
    return reasons


def worst_first(found: Mapping[int, list[str]]) -> tuple[list[dict[str, Any]], int]:
    """([{"page", "reasons"}], how many past the cap): most reasons first, then page order,
    at most DYNAMIC_MAX_PAGES (C5). O(p log p)."""
    ordered = sorted(found.items(), key=lambda page: (-len(page[1]), page[0]))
    chosen = [{"page": page_no, "reasons": reasons} for page_no, reasons in ordered[:DYNAMIC_MAX_PAGES]]
    return chosen, max(0, len(ordered) - DYNAMIC_MAX_PAGES)


def cache_prompt_version(thinking: str, env_thinking: str) -> str:
    """The page cache's prompt version (C4): READER_PROMPT_VERSION, with "+<level>" when a
    page is read at another thinking level than OCR_THINKING_LEVEL, so a read at one level
    is never taken for another; the reads cached before stay valid."""
    return READER_PROMPT_VERSION if thinking == env_thinking else f"{READER_PROMPT_VERSION}+{thinking}"


@runtime_checkable
class PageEngine(OcrEngine, Protocol):
    """An ``OcrEngine`` that also reads one numbered page at a time with everything
    the page cache, the flags and the pinning need (PRD 5 G1, G7): ``read_page``.
    ``page_no`` keys the page cache, so a retry reuses every page already read.
    The ``Reader`` protocol does not change: the readers hand pages to it."""

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead: ...


def _invalid() -> NoReturn:
    raise ReadError("invalid_reader_output")


class OcrModelMissing(Exception):
    """A model file the engine loads from disk is not there. ``code`` is short and
    safe to log; the file is never downloaded at run time."""

    code = "ocr_model_missing"


class RapidOcrEngine:
    name = "rapidocr"
    title = "Backup reader"  # what Auto's hint calls it (ocr_choices); never an engine's name (U8)

    def __init__(self) -> None:
        version = _installed_version("rapidocr")
        if version is None:
            raise SettingsError("OCR_ENGINE=rapidocr needs the ocr extra installed")
        self._installed = version
        self._engine: Any = None
        self._devanagari: Any = None
        self._lock = threading.Lock()
        # One page at a time: a RapidOCR instance keeps state between calls, and the Gemini
        # reader may read pages here from several page threads at once (PRD 5 G8, Q7).
        self._reading = threading.Lock()
        self._kept_devanagari = False  # on a page read since the version was last asked for
        self._version_asked = False

    @property
    def version(self) -> str:
        """The installed version, plus ``;rec=`` and the Devanagari model when its reading
        was kept on a page read since this was last asked for. The readers ask after each
        document, so it says how that document was read."""
        self._version_asked = True
        return f"{self._installed};rec={DEVANAGARI_REC_MODEL}" if self._kept_devanagari else self._installed

    def read(self, image: Image.Image) -> list[OcrLine]:
        """Time is dominated by the model, roughly linear in the image's pixels.

        ponytail: a suspect page is read twice, so it takes about twice as long, and
        the second engine stays in memory once built (its own detector and the
        Devanagari recogniser, about 18 MB of model files). A clean page is read once.
        Pages wait for each other here (``_reading``).
        """
        with self._reading:
            if self._version_asked:  # the first page of the next document
                self._version_asked = self._kept_devanagari = False
            picture = image.convert("RGB")
            lines = _rapid_lines(self._model()(picture))
            if not ocr_script_suspect(_text(lines)):
                return lines
            try:
                devanagari = self._devanagari_model()
            except OcrModelMissing as error:
                _log.warning("ocr model missing, page kept as first read", extra={"code": error.code})
                return lines
            again = _rapid_lines(devanagari(picture))
            if not _better(again, lines):
                return lines
            self._kept_devanagari = True
            return again

    def _model(self) -> Any:
        with self._lock:
            if self._engine is None:
                self._engine = build_rapidocr({"Global.log_level": "error"})
            return self._engine

    def _devanagari_model(self) -> Any:
        """Built on the first suspect page, from the file on disk; ``OcrModelMissing`` without it."""
        with self._lock:
            if self._devanagari is None:
                path = devanagari_model_path()
                if not path.is_file():
                    raise OcrModelMissing(DEVANAGARI_REC_MODEL)
                self._devanagari = build_rapidocr(devanagari_params(path))
            return self._devanagari


def _rapid_lines(result: Any) -> list[OcrLine]:
    # A result with boxes but no text comes back as a detection-only
    # object without ``txts``, hence getattr.
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or texts is None or scores is None:
        return []  # nothing found on the page
    lines = []
    for box, text, score in zip(boxes, texts, scores, strict=True):
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        lines.append(OcrLine(str(text), min(xs), min(ys), max(xs), max(ys), float(score)))
    return lines


def _better(again: list[OcrLine], first: list[OcrLine]) -> bool:
    """Whether a page's second reading beats its first: it found text, it has fewer
    suspect letters (``ocr_suspect_count``), and its mean confidence is no lower. O(n)."""
    if not again:
        return False
    fewer = ocr_suspect_count(_text(again)) < ocr_suspect_count(_text(first))
    return fewer and _mean_confidence(again) >= _mean_confidence(first)


def _text(lines: list[OcrLine]) -> str:
    return " ".join(line.text for line in lines)


def _mean_confidence(lines: list[OcrLine]) -> float:
    scores = [line.confidence for line in lines if line.confidence is not None]
    return statistics.fmean(scores) if scores else 0.0


def _installed_version(package: str) -> str | None:
    """The package's version, read from its metadata without importing it."""
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


class _LinesAsPages:
    """A ``PageEngine`` over an engine with only ``read`` (``as_page_engine``)."""

    def __init__(self, engine: OcrEngine) -> None:
        self._engine = engine

    @property
    def name(self) -> str:
        return self._engine.name

    @property
    def version(self) -> str:
        return self._engine.version

    def read(self, image: Image.Image) -> list[OcrLine]:
        return self._engine.read(image)

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        """The engine's lines as a page with no grade, scripts or unsure words of its own,
        and ``no_text`` when it found no line (the readers only send a page with ink on it,
        R3). ``read_by`` (RT2) is the id of the engine that read it (``read_by_id``),
        such as "rapidocr". Being read by RapidOCR is not, by itself, being read
        locally: the Gemini reader adds ``read_locally`` or ``reader_refused`` to a page
        it hands to RapidOCR (Q7), and those issues are what say "the backup reader"."""
        lines = self._engine.read(image)
        return PageRead(
            page_no=page_no,
            lines=tuple(lines),
            quality=None,
            handwritten=False,
            scripts=(),
            issues=() if lines else (NO_TEXT,),
            read_by=read_by_id(self._engine.name),
        )


def read_by_id(name: str) -> str:
    """``PageRead.read_by`` for an engine's name (RT2): its OCR_ENGINE id, the name with
    "_" for "-" ("rapidocr")."""
    return name.replace("-", "_")


def as_page_engine(engine: OcrEngine) -> PageEngine:
    """``engine`` with ``read_page`` (PRD 5 G7, G8): itself when it is a ``PageEngine`` (the
    Gemini reader, ``MeteredOcrEngine``), else its ``read`` as a page read
    by that engine (``read_by`` its id, ``quality`` None). O(1)."""
    return engine if isinstance(engine, PageEngine) else _LinesAsPages(engine)


class PageCache(Protocol):
    """The page cache (PRD 5 G7): ``kaagaz.db.page_reads.PgPageReads`` in the product."""

    def get(self, key: "PageKey") -> tuple[dict[str, Any], int, int] | None: ...

    def put(self, key: "PageKey", result: dict[str, Any], input_tokens: int, output_tokens: int) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentPages:
    """The one document whose pages an engine is reading (PRD 5 G7, F5): its customer and
    id, which key the page cache with each page's number and image, the cache itself, and
    the pages a user asked to read again (``pages.read_again_requested_at``), which are
    read harder and never taken from the cache. ``model`` and ``provider`` are the model
    this document is read with and the OpenRouter provider it is called through
    (``kaagaz.db.models.resolve``; model choice, council 11 Oct 2026); None for both reads
    with OCR_MODEL through the default provider, as before model choice. A page read again
    keeps the document's model.

    Features 25-27 (council 15 Oct 2026, ``kaagaz.db.models.Reading``): ``thinking`` the
    scan level (None: OCR_THINKING_LEVEL); ``others`` Ensemble's other (model, provider)
    pairs, which read each page numbered up to ENSEMBLE_MAX_PAGES after the main model, in
    its page thread; ``careful`` Dynamic's Careful (model, provider), ``escalate`` the
    pages it reads again (``careful_pages``) and ``memo`` the first reading's pages by
    (page, pixels), so the second reading of the document calls no model for the rest.

    Fast scan (council 16 Oct 2026): with ``inline_careful`` (a Dynamic document of at most
    DYNAMIC_MAX_PAGES pages, ``page_count``) there is no second reading: each page is read
    with Careful on its own thread right after its first read when ``careful_reasons`` says
    so, and its reasons are kept in ``escalated`` for run_config.reading.

    Never failed (16 Oct 2026): a page read above low thinking gets a low read alongside once
    it is slow, on the same 60 s page deadline (``careful_read`` reads do not: their first read
    stays)."""

    customer_id: str
    document_id: str
    cache: PageCache
    read_again: frozenset[int] = frozenset()
    model: str | None = None
    provider: str | None = None
    thinking: str | None = None
    others: tuple[tuple[str, str], ...] = ()
    careful: tuple[str, str] | None = None
    escalate: frozenset[int] = frozenset()
    memo: dict[tuple[int, str], PageRead] | None = None
    inline_careful: bool = False
    page_count: int = 0
    escalated: dict[int, list[str]] | None = None
    careful_read: bool = False  # Careful's own read: a timeout is not read again at low
    local: bool = False  # AI settings None (0038): every page by the local reader, no model call
    # The models among these with no thinking levels (Tushar, 18 Oct 2026): every call to one
    # is sent with no reasoning field, whatever level the read asks for.
    plain: frozenset[str] = frozenset()


@runtime_checkable
class DocumentEngine(Protocol):
    """An engine that reads one document's pages with its page cache (the Gemini reader),
    or passes the document on to one that does (``MeteredOcrEngine``)."""

    def document(self, pages: DocumentPages) -> contextlib.AbstractContextManager[None]: ...


def reading_document(engine: OcrEngine, pages: DocumentPages) -> contextlib.AbstractContextManager[None]:
    """``engine.document(pages)`` when the engine keeps a page cache, else nothing to enter
    (RapidOCR, a test fake): the pipeline opens it around one document's
    reading, so a page read inside it is looked up and saved under that document. O(1)."""
    return engine.document(pages) if isinstance(engine, DocumentEngine) else contextlib.nullcontext()


class MeteredOcrEngine:
    """An ``OcrEngine`` that passes each page to ``engine`` and counts the pages
    sent and the seconds they took on ``timer`` (``time.perf_counter`` in the
    product). A page whose read fails is counted too: it was sent. Each numbered
    page's ``PageRead`` is kept for the run (``take_page_reads``). Pages of one
    document may be read on several threads at once (PRD 5 G8), so the counts are
    kept under a lock. O(1) per page beyond the engine's own time.

    A numbered page is kept only in the run it was started in: entering or leaving
    ``document`` and every ``take_page_reads`` start a new generation, and a page that
    ends in a later generation than the one it started in is dropped, neither kept nor
    counted. ``PagesInOrder`` already waits for every page it drops; this makes sure no
    page read for one document can reach the next one's run, even so.
    """

    def __init__(self, engine: OcrEngine, *, timer: Callable[[], float]) -> None:
        self._engine = engine
        self._pages_engine = as_page_engine(engine)
        self._timer = timer
        self._lock = threading.Lock()
        self._pages = 0
        self._seconds = 0.0
        self._page_reads: dict[int, PageRead] = {}
        self._generation = 0

    @property
    def name(self) -> str:
        return self._engine.name

    @property
    def version(self) -> str:
        return self._engine.version  # asked for late: RapidOCR's names the models a document used

    def read(self, image: Image.Image) -> list[OcrLine]:
        started = self._timer()
        try:
            return self._engine.read(image)
        finally:
            self._count(started)

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        """The engine's own ``read_page`` when it is a ``PageEngine``, else its ``read``
        (``as_page_engine``); counted like ``read``, and kept for ``take_page_reads``
        unless its generation ended while it was being read (see the class notes)."""
        with self._lock:
            generation = self._generation
        started = self._timer()
        read: PageRead | None = None
        try:
            read = self._pages_engine.read_page(image, page_no=page_no)
            return read
        finally:
            self._count(started, page_no, read, generation)

    @contextlib.contextmanager
    def document(self, pages: DocumentPages) -> Iterator[None]:
        """The engine's own document context (``reading_document``), a generation of its
        own: a page started before it or ending after it is not this document's."""
        self._next_generation()
        try:
            with reading_document(self._engine, pages):
                yield
        finally:
            self._next_generation()

    def _next_generation(self) -> None:
        with self._lock:
            self._generation += 1

    def take(self) -> tuple[int, float]:
        """(pages sent, seconds) since the last take, and start counting again from zero."""
        with self._lock:
            counted = (self._pages, self._seconds)
            self._pages, self._seconds = 0, 0.0
        return counted

    def take_page_reads(self) -> dict[int, PageRead]:
        """Each page read through ``read_page`` since the last take, by page number, and
        start again from none, in a new generation (a page still being read is not kept).
        A page read twice (again at the DPI ceiling) keeps its last read, the one its spans
        came from, with the tokens of every read of it summed: each was billed (G9)."""
        with self._lock:
            taken, self._page_reads = self._page_reads, {}
            self._generation += 1
        return taken

    def _count(
        self, started: float, page_no: int = 0, read: PageRead | None = None, generation: int | None = None
    ) -> None:
        """One page sent, since ``started``; with ``read``, kept as page ``page_no``. A
        numbered page from an ended ``generation`` is dropped (see the class notes)."""
        seconds = max(0.0, self._timer() - started)
        with self._lock:
            if generation is not None and generation != self._generation:
                return
            self._pages += 1
            self._seconds += seconds
            if read is None:
                return
            earlier = self._page_reads.get(page_no)
            if earlier is not None:
                read = dataclasses.replace(
                    read,
                    input_tokens=earlier.input_tokens + read.input_tokens,
                    output_tokens=earlier.output_tokens + read.output_tokens,
                )
            self._page_reads[page_no] = read


class GeminiEngineFactory(Protocol):
    """Builds the Gemini reader (PRD 5 G1) for OCR_ENGINE=gemini. In the product it is
    ``wiring.build_gemini_engine``, which imports ``kaagaz.reading.gemini_ocr`` only then;
    tests give a fake. ``pool`` is the process's ``RapidOcrPool`` (``shared_pool``), for
    pinning boxes (G5) and reading a page locally (Q7). The engine it returns is a
    ``PageEngine`` whose ``read_page`` may be called from OCR_PAGE_CONCURRENCY threads
    at once (G8)."""

    def __call__(self, settings: Settings, *, pool: RapidOcrPool) -> PageEngine: ...


def build_ocr_engine(settings: Settings, *, gemini: GeminiEngineFactory | None = None) -> OcrEngine | None:
    """The engine for OCR_ENGINE, None for "none": RapidOCR, or the Gemini reader from
    ``gemini`` with the process's RapidOCR pool. Without ``gemini`` there is no Gemini
    reader: build it through ``kaagaz.reading.wiring``."""
    if settings.ocr_engine == "none":
        return None
    if settings.ocr_engine != GEMINI:
        return RapidOcrEngine()
    if gemini is None:
        raise SettingsError("OCR_ENGINE=gemini: build the engines with kaagaz.reading.wiring")
    return gemini(settings, pool=shared_pool(settings.cpu_workers))


def ocr_choices(settings: Settings) -> list[dict[str, str | bool | None]]:
    """The readers an upload may choose (PRD 5 RT3: no reader choice at upload): one,
    Auto, with ``id``, ``title``, ``hint``, ``available`` and ``reason``, so the "Scan
    with" select hides itself. Pure: from the settings only, nothing built or called,
    so the api and the worker agree. O(1)."""
    return [{"id": AUTO, "title": "Auto", "hint": _auto_hint(settings), "available": True, "reason": None}]


def _auto_hint(settings: Settings) -> str:
    """What Auto does on this server, in words people know, never an engine's name
    (PRD 5 U8)."""
    if settings.ocr_engine == "none":
        return "Scans wait until this server has a scan reader"
    if settings.ocr_engine == GEMINI:
        return "The usual reader here: the main reader"
    return f"The usual reader here: the {RapidOcrEngine.title.lower()}"


def ocr_spans(lines: list[OcrLine], page_no: int, width_px: int, height_px: int) -> list[Span]:
    """Pixel boxes to spans with page fractions, clamped to 0..1, origin "ocr".

    Block mode, when any line has a kind (the Gemini reader): one
    span per line, in the order given, never re-sorted, with its kind, table and
    box source (PRD 5 G5). Line mode (RapidOCR): through the shared line rule. A
    line with no text, a box with nothing inside the page, or a confidence that is
    not a number is left out (never guessed); a confidence is clamped to 0..1, and
    None stays None.

    Word marks (PRD 5 F3): each unsure word of a line, and each ``[?]`` in it,
    becomes a span of its own after the line's, with quality "unsure" or
    "illegible", the line's box source, no kind, and a sub-box: the line's box
    split in proportion to the word's character position (approximate, so the
    viewer draws it dashed). ``is_word_mark`` tells them apart, so they are kept
    out of pieces and values in one place. An unsure word not found in its line
    has no spot, so it gets no mark.

    A box source or unsure list an engine should never give is
    ``ReadError("invalid_reader_output")``. O(n log n + c) for n lines of c
    characters in all.
    """
    if width_px <= 0 or height_px <= 0:
        return []
    for line in lines:
        if not _has_valid_marks(line):
            _invalid()
    usable = [line for line in lines if line.confidence is None or math.isfinite(line.confidence)]
    if any(line.kind is not None for line in lines):
        spans: list[Span] = []
        for line in usable:
            span = _block_span(line, page_no, width_px, height_px)
            if span is not None:
                spans.append(span)
                spans.extend(_word_marks(line, page_no, width_px, height_px))
        return spans
    words = [
        WordBox(line.text, line.x0, line.y0, line.x1, line.y1, _clamped(line.confidence))
        for line in usable
        if line.text.strip()
    ]
    spans = line_spans(words, page_no, PageFrame(0.0, 0.0, float(width_px), float(height_px)), origin="ocr")
    return spans + [mark for line in usable for mark in _word_marks(line, page_no, width_px, height_px)]


def is_word_mark(span: Span) -> bool:
    """Whether ``span`` is a word mark (PRD 5 F3: an unsure or illegible word of a line,
    with its own sub-box), not a line: drawn on the page, never a piece or a value.
    ``ocr_spans`` makes them with no kind; a line from the Gemini reader always has one."""
    return span.origin == "ocr" and span.kind is None and span.quality in WORD_MARK_QUALITIES


def _has_valid_marks(line: OcrLine) -> bool:
    """A known box source or None, and the unsure words as a tuple of str (G3, G5)."""
    source = line.box_source
    source_ok = source is None or (isinstance(source, str) and source in BOX_SOURCES)
    return source_ok and isinstance(line.unsure, tuple) and all(isinstance(word, str) for word in line.unsure)


def _block_span(line: OcrLine, page_no: int, width: int, height: int) -> Span | None:
    text = " ".join(line.text.split())
    box = _page_box(page_no, (line.x0, line.y0, line.x1, line.y1), width, height)
    if not text or not box.is_valid():
        return None
    span = Span(text, line.text, box, _clamped(line.confidence), line.kind, "ocr", box_source=line.box_source)
    table = line.table
    if table is None:
        return span
    return dataclasses.replace(
        span, table_id=table.table_id, row=table.row, col=table.col, header=table.header
    )


def _page_box(page_no: int, pixels: PixelBox, width: int, height: int) -> BoxLocation:
    """A pixel box (x0, y0, x1, y1) as page fractions, clamped to 0..1."""
    x0, y0, x1, y1 = pixels
    return BoxLocation(
        page_no,
        min(1.0, max(0.0, x0 / width)),
        min(1.0, max(0.0, y0 / height)),
        min(1.0, max(0.0, x1 / width)),
        min(1.0, max(0.0, y1 / height)),
    )


def _word_marks(line: OcrLine, page_no: int, width: int, height: int) -> list[Span]:
    """The line's word marks (F3), in the order they sit in the line. O(c x u) for c
    characters and u unsure words."""
    text = line.text
    found: list[tuple[int, int, str]] = []
    cursor = 0
    for word in line.unsure:
        word = word.strip()
        if not word or word == ILLEGIBLE_MARK:
            continue  # [?] is marked below, as illegible
        start = _find_word(text, word, cursor)
        if start is None:
            continue  # no spot to point at: nothing is guessed
        found.append((start, start + len(word), "unsure"))
        cursor = start + len(word)
    start = text.find(ILLEGIBLE_MARK)
    while start >= 0:
        found.append((start, start + len(ILLEGIBLE_MARK), "illegible"))
        start = text.find(ILLEGIBLE_MARK, start + len(ILLEGIBLE_MARK))
    marks: list[Span] = []
    step = (line.x1 - line.x0) / len(text) if text else 0.0
    for start, end, quality in sorted(found):
        raw = text[start:end]
        pixels = (line.x0 + step * start, line.y0, line.x0 + step * end, line.y1)
        box = _page_box(page_no, pixels, width, height)
        if box.is_valid():
            marks.append(
                Span(
                    " ".join(raw.split()),
                    raw,
                    box,
                    _clamped(line.confidence),
                    origin="ocr",
                    quality=quality,
                    box_source=line.box_source,
                )
            )
    return marks


def _find_word(text: str, word: str, cursor: int) -> int | None:
    """Where ``word`` starts in ``text``: as a whole word (between spaces or the ends)
    from ``cursor``, then from the start, then anywhere inside a word the same way
    (a word next to punctuation, such as "1,180."); None when it is not there."""
    whole = re.compile(rf"(?<!\S){re.escape(word)}(?!\S)")
    for start in (cursor, 0):
        match = whole.search(text, start)
        if match is not None:
            return match.start()
    for start in (cursor, 0):
        found = text.find(word, start)
        if found >= 0:
            return found
    return None


def _clamped(confidence: float | None) -> float | None:
    return None if confidence is None else min(1.0, max(0.0, confidence))
