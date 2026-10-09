"""The scan reader engine (OCR), behind one small interface.

The scan reader is PaddleOCR-VL (decided 29 Sep, docs/architecture.md), in its
own container; RapidOCR (Apache-2.0) stays as the fallback, for weak laptops and
for a page PaddleOCR-VL cannot read (``FallbackOcrEngine``). No page ever leaves
the machine and nothing is downloaded at run time.

* ``OcrEngine`` is what the page readers depend on, so tests use a fake and a
  different engine means one new class here.
* ``PaddleVlEngine`` posts each page, as a PNG it encoded itself, to the ocr
  container's ``POST /read`` (``kaagaz/ocr_server/app.py``, PRD 2 P2) and
  checks every field of the reply before anything becomes a line (security
  A7). It gives no confidence (PaddleOCR-VL has none), so a scan is known by
  its spans' origin "ocr", never by a made-up number. Failures (P5):

  * 503 ``busy``: the server is still on another page (one it cannot stop
    after a worker gave up on it), so the call waits and asks again, backing
    off, until its deadline; only then is it ``OcrOverloaded("ocr_timeout")``;
  * 503 ``out_of_memory``: ``OcrOverloaded("ocr_out_of_memory")``; past
    ``OCR_TIMEOUT_SECONDS``: ``OcrOverloaded("ocr_timeout")``. Both are a
    ``BackendUnavailable("ocr")``, retried later, that ``FallbackOcrEngine``
    reads with RapidOCR instead;
  * down, refused, another 5xx (``not_ready``, ``internal_error``), a 421, or
    cut off: ``BackendUnavailable("ocr")``, retried later, never read by the
    fallback, so a fault in the set-up is not hidden behind RapidOCR;
  * 500 ``read_failed`` (the model raised on this page): ``ocr_failed``, read
    by the fallback; 422: ``corrupt_file``; 413: ``image_too_large``; a 3xx
    (never followed), another 4xx, a malformed reply or one over
    ``MAX_OCR_REPLY_BYTES``: ``invalid_reader_output``. Unless the fallback
    reads the page, the document fails; these are not retried.

  It uses ``http.client``, not urllib's opener: it follows no redirect, reads
  no proxy variable (page images never reach a proxy), and one monotonic
  deadline covers the whole call (a timer shuts the socket down at it).
  Over-limit items in a reply are dropped and counted.
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
* ``FallbackOcrEngine`` (the product's, for ``paddleocr_vl`` when RapidOCR is
  installed) reads each page with PaddleOCR-VL and, on ``ocr_failed`` or
  ``OcrOverloaded``, reads that page with RapidOCR (built on first need, once).
  After an ``OcrOverloaded``, pages go straight to RapidOCR for
  ``COOL_DOWN_SECONDS``, so each does not wait out the deadline again; then
  PaddleOCR-VL is tried again. Any other error is raised as before, and when
  RapidOCR fails too, the primary's own error is raised. Each fallback page is
  one warning with its code only. Its name and version are PaddleOCR-VL's (the
  ETA keys on the name; the run's ``ocr_engine`` is cut at 100 characters);
  the pages RapidOCR read are counted (``take_fallback_pages``) into the
  run's config as ``pages_ocr_fallback``. A RapidOCR page has lines with no
  kind and a real confidence, so ``ocr_spans`` (which decides per page) puts
  it through the line rule, and a value from it is still needs review.
* The scan reader an upload chose (PRD 4 #17, council 4 Oct 2026, PROVISIONAL):
  ``ocr_choices`` lists Auto and every engine here, each with its own
  ``title`` and ``hint``, available or with its reason, from the settings and
  what is installed only, so the api and the worker agree. ``OcrEngines`` is
  the worker's engine: Auto (``build_ocr_engine``'s chain) unless ``choose``
  picks another for one job. Each engine is built once and shared. A chosen
  engine falls back to Auto, through Auto's one instance and cool-down, on its
  failures above and also when it finds no line: the readers only send a page
  with ink on it (R3, ``is_blank``). A choice that is Auto's first engine is
  Auto itself.
* ``MeteredOcrEngine`` wraps any engine and counts the pages sent to it and
  the seconds they took (PRD 2 R6: the run's ``seconds_ocr``; ``cli measure``).
  Its name is the engine's at each moment, so a chosen engine's pages are
  timed under its own name. It also keeps each numbered page's ``PageRead``
  for the run (``take_page_reads``, PRD 5 G7, F1). Pages of one document are
  read on several threads at once (G8), so its counts are kept under a lock;
  the seconds are the pages' own, summed, so they can pass the wall time.
* ``as_page_engine`` gives any engine ``read_page`` (PRD 5): a ``PageEngine``
  (the Gemini reader) as it is, and any other engine through ``read``, as a
  page with no grade of its own, ``read_by`` the id of the engine that read it
  ("rapidocr", "paddleocr_vl"; a ``FallbackOcrEngine`` says which of its two).
* The Gemini reader (PRD 5 G1, OCR_ENGINE=gemini) lives in its own module,
  ``kaagaz/reading/gemini_ocr.py``, which this module never imports: a
  ``GeminiEngineFactory`` given to ``build_ocr_engine`` builds it, with the
  process's ``RapidOcrPool``. ``kaagaz.reading.wiring`` gives the product's
  (``build_gemini_engine``, which imports it by its path only for
  OCR_ENGINE=gemini), so OCR_ENGINE=rapidocr never loads it.
* The page cache (PRD 5 G7, F5): the pipeline opens ``reading_document(engine,
  DocumentPages(...))`` around one document's reading. An engine with a page cache
  (``DocumentEngine``, the Gemini reader) then keys each page by that document; the
  meter and ``OcrEngines`` pass it on; any other engine has nothing to open.
* No reader choice at upload (PRD 5 RT3): ``ocr_choices`` offers one reader,
  Auto, so the "Scan with" select hides itself.
  timed under its own name.
* ``ocr_spans`` turns an engine's pixel boxes into spans with page fractions
  and origin "ocr". RapidOCR's boxes go through the same line rule as the PDF
  readers (``kaagaz/reading/lines.py``), so "INVOICE" and "1042" found as two
  boxes on one line become one span. PaddleOCR-VL's blocks are already in
  reading order, so each becomes one span, in the order given. A line's
  ``box_source`` is copied to its span, and its word marks (PRD 5 F3) become
  spans of their own (``is_word_mark``).

An engine never logs what it read: RapidOCR's own logger is set to errors
only, and its messages hold no page text.
"""

import contextlib
import dataclasses
import io
import json
import logging
import math
import re
import socket
import statistics
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException, HTTPResponse, HTTPSConnection, IncompleteRead
from importlib import metadata
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, TypeGuard, runtime_checkable

from PIL import Image

from kaagaz.chunking.script import ocr_script_suspect, ocr_suspect_count
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading.lines import PageFrame, WordBox, line_spans
from kaagaz.reading.page_quality import ILLEGIBLE_MARK, NO_TEXT
from kaagaz.reading.rapid_pool import (
    DEVANAGARI_REC_MODEL,
    PixelBox,
    RapidOcrPool,
    build_rapidocr,
    devanagari_model_path,
    devanagari_params,
    shared_pool,
)
from kaagaz.reading.spans import BLOCK_KINDS, BOX_SOURCES, TABLE_ID, BoxLocation, ReadError, Span
from kaagaz.settings import Settings, SettingsError

if TYPE_CHECKING:
    from kaagaz.db.page_reads import PageKey

# Caps on a /read reply (security A7). A reply over MAX_OCR_REPLY_BYTES is
# refused; blocks past MAX_BLOCKS_PER_PAGE, longer than MAX_BLOCK_CHARS, or in a
# table row or column from MAX_TABLE_INDEX on are dropped and counted.
MAX_OCR_REPLY_BYTES = 16 * 1024 * 1024
MAX_BLOCKS_PER_PAGE = 5000
MAX_BLOCK_CHARS = 20_000
MAX_TABLE_INDEX = 1000
# The most pixels a page is drawn at for OCR. A PNG that cannot compress is 3 bytes
# a pixel, so any page stays under the ocr server's 64 MiB body cap (MAX_BODY_BYTES
# in kaagaz/ocr_server/app.py). Readers lower the DPI to fit it, never refuse.
MAX_OCR_PIXELS = 21_000_000
_READ_CHUNK = 64 * 1024
_REFUSALS = {422: "corrupt_file", 413: "image_too_large"}
# The first wait after a busy reply, doubled after each one up to the most.
_BUSY_WAIT_SECONDS = 1.0
_BUSY_WAIT_MAX_SECONDS = 15.0
# ":" is in the device the ocr server names (";device=gpu:0").
_VERSION = re.compile(r"[A-Za-z0-9._+;=@: -]{1,200}")
# C0 and C1 controls and the bidirectional overrides and isolates, as
# kaagaz/answering/sanitize.py strips them.
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
# Lone surrogates: JSON can carry them, but no database or UTF-8 file can hold them.
_SURROGATE = re.compile("[\ud800-\udfff]")
# After PaddleOCR-VL timed out or ran out of memory, pages go straight to RapidOCR
# for this long, so each one does not first wait out OCR_TIMEOUT_SECONDS
# (PROVISIONAL, council 4 Oct 2026).
COOL_DOWN_SECONDS = 120.0
# The choice that means "the usual way" (stored as NULL); never an engine's id.
AUTO = "auto"
# Why no engine can be chosen with OCR_ENGINE=none.
_READING_OFF = "Scan reading is off on this server"
# OCR_ENGINE's value for the Gemini reader (PRD 5 G1).
GEMINI = "gemini"
# PRD 5 RT2: pages.read_by for a page the local reader read.
LOCAL_READ_BY = "rapidocr"
# PRD 5 F3: the span qualities that mark one word of a line, not the line.
WORD_MARK_QUALITIES = frozenset({"unsure", "illegible"})

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

    A layout engine (PaddleOCR-VL) gives one line per block, with its kind
    (``spans.BLOCK_KINDS``) and, for a table cell, its table. It gives no
    confidence, so that is None: never a made-up number.

    The Gemini reader (PRD 5) also gives ``unsure``: each word in this line it
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
    def version(self) -> str: ...  # read-only: PaddleOCR-VL's comes from its last reply

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
      that read it ("rapidocr", "paddleocr_vl"). A page the Gemini reader handed to the
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

    @property
    def unsure_words(self) -> int:
        return sum(len(line.unsure) for line in self.lines)

    @property
    def illegible_words(self) -> int:
        return sum(line.text.count(ILLEGIBLE_MARK) for line in self.lines)

    @property
    def unpinned_lines(self) -> int:
        return sum(line.box_source == "model" for line in self.lines)


@runtime_checkable
class PageEngine(OcrEngine, Protocol):
    """An ``OcrEngine`` that also reads one numbered page at a time with everything
    the page cache, the flags and the pinning need (PRD 5 G1, G7): ``read_page``.
    ``page_no`` keys the page cache, so a retry reuses every page already read.
    The ``Reader`` protocol does not change: the readers hand pages to it."""

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead: ...


class OcrOverloaded(BackendUnavailable):
    """The scan reader was up but did not read this page: it ran out of memory, or the
    page took past OCR_TIMEOUT_SECONDS. Still ``BackendUnavailable("ocr")`` (retried
    later); ``FallbackOcrEngine`` reads the page with its fallback instead. ``code``
    is short and safe to log."""

    def __init__(self, code: str) -> None:
        super().__init__("ocr")
        self.code = code


class PaddleVlEngine:
    """``OcrEngine`` over the ocr container's ``POST /read`` (see the module notes)."""

    name = "paddleocr-vl"
    title = "PaddleOCR-VL"  # what the upload screen offers (ocr_choices)
    hint = "Best for handwriting and complex pages (slower)"

    @staticmethod
    def unavailable(settings: Settings) -> str | None:
        """Why this server can't read with it, or None: only OCR_ENGINE=paddleocr_vl sets it up."""
        return None if settings.ocr_engine == "paddleocr_vl" else "Not set up on this server"

    @classmethod
    def from_settings(cls, settings: Settings) -> "PaddleVlEngine":
        return cls(settings.ocr_url, timeout_seconds=settings.ocr_timeout_seconds)

    def __init__(self, url: str, *, timeout_seconds: float) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("the OCR URL must be an http or https URL")
        self._connection = HTTPSConnection if parts.scheme == "https" else HTTPConnection
        self._host, self._port = parts.hostname, parts.port
        self._path = parts.path.rstrip("/") + "/read"
        self._timeout = timeout_seconds
        self._version = "unknown"

    @property
    def version(self) -> str:
        """The last reply's version; "unknown" before the first."""
        return self._version

    def read(self, image: Image.Image) -> list[OcrLine]:
        """One request per page, asked again while the server is busy, all within one
        deadline; the time is the server's (minutes on a CPU)."""
        buffer = io.BytesIO()
        # Our own PNG, never the uploaded bytes (security A5). compress_level 1 (council 5 Oct
        # 2026): lossless like any level, a little bigger, and much faster to encode.
        image.save(buffer, format="PNG", compress_level=1)
        deadline = time.monotonic() + self._timeout
        wait = _BUSY_WAIT_SECONDS
        status, body = self._post(buffer.getvalue(), deadline)
        while status == 503 and _error_code(body) == "busy":
            # Still on another page, often one a worker gave up on: nothing is wrong with
            # this one, so it waits here rather than use up one of the job's attempts.
            if time.monotonic() + wait >= deadline:
                raise OcrOverloaded("ocr_timeout")
            time.sleep(wait)
            wait = min(2 * wait, _BUSY_WAIT_MAX_SECONDS)
            status, body = self._post(buffer.getvalue(), deadline)
        if status == 500 and _error_code(body) == "read_failed":
            raise ReadError("ocr_failed")  # the model raised on this page: asking again fails again
        if status == 503 and _error_code(body) == "out_of_memory":
            raise OcrOverloaded("ocr_out_of_memory")  # the page is fine; the GPU memory is shared
        if status >= 500 or status == 421:
            # Down, still loading, or an error outside the model; or 421, a Host the server
            # refuses, which is this set-up's fault, not the file's. Retried later either way.
            raise BackendUnavailable("ocr")
        if status != 200:
            raise ReadError(_REFUSALS.get(status, "invalid_reader_output"))
        self._version, lines = _reply_lines(body, image.width, image.height)
        return lines

    def _post(self, png: bytes, deadline: float) -> tuple[int, bytes]:
        """One deadline for the whole call: connecting waits at most the time left, and a
        timer shuts the socket down at the deadline, so even a server that sends its
        headers a byte at a time cannot hold the worker past OCR_TIMEOUT_SECONDS."""
        connection = self._connection(self._host, self._port, timeout=self._timeout)
        expired = threading.Event()
        timer: threading.Timer | None = None
        response: HTTPResponse | None = None
        reply: tuple[int, bytes] | None = None
        timed_out = False
        try:
            connection.timeout = _time_left(deadline)
            connection.connect()
            timer = threading.Timer(_time_left(deadline), _cut, (connection.sock, expired))
            timer.daemon = True
            timer.start()
            connection.request("POST", self._path, body=png, headers={"Content-Type": "image/png"})
            response = connection.getresponse()
            reply = response.status, _body(response)
        except TimeoutError:
            timed_out = True  # past the deadline before the timer cut it: raised below
        except (OSError, HTTPException):
            reply = None  # refused, unreachable or cut off: raised below, nothing chained
        finally:
            if timer is not None:
                timer.cancel()
            if response is not None:
                response.close()
            connection.close()
        if timed_out or expired.is_set():  # what arrived before the cut is not the reply
            raise OcrOverloaded("ocr_timeout")
        if reply is None:
            raise BackendUnavailable("ocr")
        return reply


def _time_left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("ocr deadline")
    return left


def _cut(sock: socket.socket, expired: threading.Event) -> None:
    """The timer, at the deadline: stop every read and write on the socket."""
    expired.set()
    with contextlib.suppress(OSError):  # already closed: the call ended first
        sock.shutdown(socket.SHUT_RDWR)


def _body(response: HTTPResponse) -> bytes:
    """At most MAX_OCR_REPLY_BYTES + 1 bytes; a body cut short of its length raises."""
    expected = response.length
    chunks: list[bytes] = []
    size = 0
    while size <= MAX_OCR_REPLY_BYTES and not response.isclosed():  # closed: all of it was read
        chunk = response.read1(_READ_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if expected is not None and size < expected and size <= MAX_OCR_REPLY_BYTES:
        raise IncompleteRead(b"", expected - size)
    return b"".join(chunks)


def _error_code(body: bytes) -> object:
    """The ``error`` of a refusal's ``{"error": code}`` body; None for any other body."""
    reply = _json(body)
    return reply.get("error") if isinstance(reply, dict) else None


def _reply_lines(body: bytes, width: int, height: int) -> tuple[str, list[OcrLine]]:
    """The reply's version and its lines in pixels, sorted by ``order``. O(b log b) for b blocks."""
    reply = _json(body) if len(body) <= MAX_OCR_REPLY_BYTES else None
    if not isinstance(reply, dict):
        _invalid()
    version, blocks = reply.get("version"), reply.get("blocks")
    if not (
        isinstance(reply.get("engine"), str)
        and isinstance(version, str)
        and _VERSION.fullmatch(version)
        and _is_whole(reply.get("width"))
        and reply["width"] == width
        and _is_whole(reply.get("height"))
        and reply["height"] == height
        and isinstance(blocks, list)
    ):
        _invalid()
    kept = [line for block in blocks[:MAX_BLOCKS_PER_PAGE] if (line := _line(block, width, height))]
    if len(kept) < len(blocks):
        _log.warning(
            "ocr reply items dropped: %d", len(blocks) - len(kept), extra={"code": "ocr_reply_capped"}
        )
    kept.sort(key=lambda item: item[0])  # stable: a table's cells share its order and keep theirs
    return version, [line for _, line in kept]


def _line(block: object, width: int, height: int) -> tuple[int, OcrLine] | None:
    """(order, the line in pixels); None when it is over a limit (dropped)."""
    if not isinstance(block, dict):
        _invalid()
    kind, text, order, box = block.get("kind"), block.get("text"), block.get("order"), block.get("box")
    if not (isinstance(kind, str) and kind in BLOCK_KINDS and isinstance(text, str) and text):
        _invalid()  # kind is checked to be a str first: a list or dict is not even hashable
    if _SURROGATE.search(text) or not (_is_whole(order) and _is_box(box)):
        _invalid()
    table = _table(block.get("table"))
    if len(text) > MAX_BLOCK_CHARS or (table is not None and max(table.row, table.col) >= MAX_TABLE_INDEX):
        return None
    text = _CONTROL.sub("", text)
    if not text.strip():
        return None
    x0, y0, x1, y1 = box
    return order, OcrLine(text, x0 * width, y0 * height, x1 * width, y1 * height, None, kind, table)


def _table(value: object) -> OcrTable | None:
    if value is None:
        return None
    if not (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and TABLE_ID.fullmatch(value["id"])
        and _is_index(value.get("row"))
        and _is_index(value.get("col"))
        and isinstance(value.get("header"), bool)
    ):
        _invalid()
    return OcrTable(value["id"], value["row"], value["col"], value["header"])


def _json(body: bytes) -> object:
    try:
        return json.loads(body)
    except (ValueError, RecursionError):  # not JSON (UnicodeDecodeError is a ValueError), or nested too deep
        return None


def _is_whole(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_box(value: object) -> TypeGuard[list[float]]:
    """[x0, y0, x1, y1] as page fractions: 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 (NaN fails)."""
    if not (isinstance(value, list) and len(value) == 4):
        return False
    if not all(_is_finite(v) for v in value):
        return False
    x0, y0, x1, y1 = value
    return bool(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)


def _is_finite(value: object) -> bool:
    """A whole number, or a finite float (bool refused). A huge int stays exact."""
    return _is_whole(value) or (isinstance(value, float) and math.isfinite(value))


def _invalid() -> NoReturn:
    raise ReadError("invalid_reader_output")


class OcrModelMissing(Exception):
    """A model file the engine loads from disk is not there. ``code`` is short and
    safe to log; the file is never downloaded at run time."""

    code = "ocr_model_missing"


class RapidOcrEngine:
    name = "rapidocr"
    title = "RapidOCR"  # what the upload screen offers (ocr_choices)
    hint = "Fast, for clean printed pages"

    @staticmethod
    def unavailable(settings: Settings) -> str | None:
        """Why this server can't read with it, or None: it needs the ocr extra."""
        return None if _installed_version("rapidocr") is not None else "Not installed on this server"

    @classmethod
    def from_settings(cls, settings: Settings) -> "RapidOcrEngine":
        return cls()

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


class FallbackOcrEngine:
    """An ``OcrEngine`` that reads a page with ``primary`` and, when it cannot, with the
    engine ``fallback`` builds (see the module notes). ``timer`` is ``time.monotonic``
    in the product. With ``empty_fails`` (a chosen engine, ``OcrEngines``), a page
    the primary finds no line on is read by the fallback too; when the fallback
    then fails, the page stays as the primary read it. One job at a time uses it;
    its pages may come from several page threads (PRD 5 G8), so the fallback is built
    and its pages counted under a lock. O(1) per page beyond the engines' own time.
    then fails, the page stays as the primary read it. One job at a time uses it,
    so there is no lock. O(1) per page beyond the engines' own time.
    """

    def __init__(
        self,
        primary: OcrEngine,
        fallback: Callable[[], OcrEngine],
        *,
        timer: Callable[[], float] = time.monotonic,
        empty_fails: bool = False,
    ) -> None:
        self._primary = primary
        self._build_fallback = fallback
        self._fallback: OcrEngine | None = None
        self._timer = timer
        self._empty_fails = empty_fails
        self.name = primary.name  # the ETA's rates key on the name
        self._cool_until: float | None = None
        self._fallback_pages = 0
        self._lock = threading.Lock()

    @property
    def version(self) -> str:
        return self._primary.version  # the fallback's pages are counted, not named here

    def take_fallback_pages(self) -> int:
        """Pages the fallback read since the last take, and start again from zero."""
        with self._lock:
            taken, self._fallback_pages = self._fallback_pages, 0
        return taken

    def read(self, image: Image.Image) -> list[OcrLine]:
        return self.read_with_reader(image)[0]

    def read_with_reader(self, image: Image.Image) -> tuple[list[OcrLine], str]:
        """The page's lines, and the name of the engine that read them: the primary's, or
        the fallback's (followed through a fallback that is itself a chain), for
        ``PageRead.read_by`` (PRD 5 RT2)."""
        if self._cool_until is not None and self._timer() < self._cool_until:
            return self._read_fallback(image, OcrOverloaded("ocr_cooling_down"))
        self._cool_until = None
        failure: ReadError | OcrOverloaded
        try:
            lines = self._primary.read(image)
        except OcrOverloaded as error:
            failure = error
        except ReadError as error:
            if error.code != "ocr_failed":
                raise  # the file's own failure: the fallback would not read it either
            failure = error
        else:
            if lines or not self._empty_fails:
                return lines, self._primary.name
                return lines
            return self._read_fallback(image, ReadError("ocr_no_lines"), kept=lines)
        # Outside the handler, so a failing fallback's error is chained onto the primary's once.
        read = self._read_fallback(image, failure)
        if isinstance(failure, OcrOverloaded):
            self._cool_until = self._timer() + COOL_DOWN_SECONDS  # only once the fallback worked
        return read

    def _read_fallback(
        self, image: Image.Image, failure: ReadError | OcrOverloaded, *, kept: list[OcrLine] | None = None
    ) -> tuple[list[OcrLine], str]:
        """The page as the fallback reads it, with the name of the engine that read it.
        When the fallback cannot be built or fails, any cool-down ends, so the next page
        tries the primary, and ``kept`` (the primary's own lines) is returned or else
        ``failure`` (its own error) is raised. Logs codes and type names only, never the page."""
        return lines

    def _read_fallback(
        self, image: Image.Image, failure: ReadError | OcrOverloaded, *, kept: list[OcrLine] | None = None
    ) -> list[OcrLine]:
        """The page as the fallback reads it. When the fallback cannot be built or fails,
        any cool-down ends, so the next page tries the primary, and ``kept`` (the
        primary's own lines) is returned or else ``failure`` (its own error) is raised.
        Logs codes and type names only, never the page."""
        try:
            read = lines_and_reader(self._fallback_engine(), image)
        except Exception as error:
            self._cool_until = None
            _log.warning("ocr fallback failed: %s", type(error).__name__, extra={"code": failure.code})
            if kept is not None:
                return kept, self._primary.name
                return kept
            raise failure from error
        with self._lock:
            self._fallback_pages += 1
        _log.warning("ocr page read by the fallback", extra={"code": failure.code})
        return read

    def _fallback_engine(self) -> OcrEngine:
        """The fallback, built on its first need, once."""
        with self._lock:
            if self._fallback is None:
                self._fallback = self._build_fallback()
            return self._fallback


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
        R3). ``read_by`` (RT2) is the id of the engine that read it (``read_by_id``):
        "rapidocr", "paddleocr_vl", or for a ``FallbackOcrEngine`` the one of its two
        that read this page. Being read by RapidOCR is not, by itself, being read
        locally: the Gemini reader adds ``read_locally`` or ``reader_refused`` to a page
        it hands to RapidOCR (Q7), and those issues are what say "the backup reader"."""
        lines, reader = lines_and_reader(self._engine, image)
        return PageRead(
            page_no=page_no,
            lines=tuple(lines),
            quality=None,
            handwritten=False,
            scripts=(),
            issues=() if lines else (NO_TEXT,),
            read_by=read_by_id(reader),
        )


def lines_and_reader(engine: OcrEngine, image: Image.Image) -> tuple[list[OcrLine], str]:
    """``engine``'s lines for ``image`` and the name of the engine that read them: a
    ``FallbackOcrEngine`` says which of its two did; any other engine is itself. O(1)
    beyond the reading."""
    if isinstance(engine, FallbackOcrEngine):
        return engine.read_with_reader(image)
    return engine.read(image), engine.name


def read_by_id(name: str) -> str:
    """``PageRead.read_by`` for an engine's name (RT2): its OCR_ENGINE id, the name with
    "_" for "-" ("rapidocr", "paddleocr_vl")."""
    return name.replace("-", "_")


def as_page_engine(engine: OcrEngine) -> PageEngine:
    """``engine`` with ``read_page`` (PRD 5 G7, G8): itself when it is a ``PageEngine`` (the
    Gemini reader, ``MeteredOcrEngine``, ``OcrEngines``), else its ``read`` as a page read
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
    read harder and never taken from the cache."""

    customer_id: str
    document_id: str
    cache: PageCache
    read_again: frozenset[int] = frozenset()


@runtime_checkable
class DocumentEngine(Protocol):
    """An engine that reads one document's pages with its page cache (the Gemini reader),
    or passes the document on to one that does (``MeteredOcrEngine``, ``OcrEngines``)."""

    def document(self, pages: DocumentPages) -> contextlib.AbstractContextManager[None]: ...


def reading_document(engine: OcrEngine, pages: DocumentPages) -> contextlib.AbstractContextManager[None]:
    """``engine.document(pages)`` when the engine keeps a page cache, else nothing to enter
    (RapidOCR, PaddleOCR-VL, a test fake): the pipeline opens it around one document's
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
        return self._engine.name  # asked each time: ``OcrEngines`` changes engine per job

    @property
    def name(self) -> str:
        return self._engine.name  # asked each time: ``OcrEngines`` changes engine per job

    @property
    def version(self) -> str:
        return self._engine.version  # asked for late: PaddleOCR-VL's comes from its reply

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

    def take_fallback_pages(self) -> int:
        """Pages a ``FallbackOcrEngine`` (or every one ``OcrEngines`` built) read with its
        fallback since the last take; 0 for any other engine."""
        engine = self._engine
        return engine.take_fallback_pages() if isinstance(engine, FallbackOcrEngine | OcrEngines) else 0


class GeminiEngineFactory(Protocol):
    """Builds the Gemini reader (PRD 5 G1) for OCR_ENGINE=gemini. In the product it is
    ``wiring.build_gemini_engine``, which imports ``kaagaz.reading.gemini_ocr`` only then;
    tests give a fake. ``pool`` is the process's ``RapidOcrPool`` (``shared_pool``), for
    pinning boxes (G5) and reading a page locally (Q7). The engine it returns is a
    ``PageEngine`` whose ``read_page`` may be called from OCR_PAGE_CONCURRENCY threads
    at once (G8)."""

    def __call__(self, settings: Settings, *, pool: RapidOcrPool) -> PageEngine: ...


# Every engine here, in the order the upload screen offered them.
# Every engine here, in the order the upload screen offers them.
_ENGINES: tuple[type[PaddleVlEngine] | type[RapidOcrEngine], ...] = (PaddleVlEngine, RapidOcrEngine)


def _engine_id(engine: type[PaddleVlEngine] | type[RapidOcrEngine]) -> str:
    """An engine's id: its OCR_ENGINE value, which is its name with "_" for "-"."""
    return engine.name.replace("-", "_")


def _new_engine(settings: Settings, engine_id: str) -> OcrEngine:
    """A new engine for an id ``ocr_choices`` offers."""
    (engine,) = (engine for engine in _ENGINES if _engine_id(engine) == engine_id)
    return engine.from_settings(settings)


def _has_fallback(settings: Settings) -> bool:
    """Whether Auto reads a page PaddleOCR-VL can't with RapidOCR: only with the ocr extra."""
    return settings.ocr_engine == _engine_id(PaddleVlEngine) and _installed_version("rapidocr") is not None


def _auto_engine(
    settings: Settings, engine: Callable[[str], OcrEngine], timer: Callable[[], float] = time.monotonic
) -> OcrEngine:
    """The usual chain for OCR_ENGINE (not "none"), its engines from ``engine(id)``."""
    primary = engine(settings.ocr_engine)
    if not _has_fallback(settings):
        return primary  # RapidOCR, or PaddleOCR-VL with no ocr extra: no fallback, as before
    rapidocr = _engine_id(RapidOcrEngine)
    return FallbackOcrEngine(primary, lambda: engine(rapidocr), timer=timer)  # RapidOCR built on first need


def _new_engine(settings: Settings, engine_id: str, gemini: GeminiEngineFactory | None = None) -> OcrEngine:
    """A new engine for OCR_ENGINE or an id ``_engine_choices`` offers; the Gemini reader
    from ``gemini``, with the process's RapidOCR pool. Without ``gemini`` there is no
    Gemini reader: build the engines through ``kaagaz.reading.wiring``."""
    if engine_id == GEMINI:
        if gemini is None:
            raise SettingsError("OCR_ENGINE=gemini: build the engines with kaagaz.reading.wiring")
        return gemini(settings, pool=shared_pool(settings.cpu_workers))
    (engine,) = (engine for engine in _ENGINES if _engine_id(engine) == engine_id)
    return engine.from_settings(settings)


def _has_fallback(settings: Settings) -> bool:
    """Whether Auto reads a page PaddleOCR-VL can't with RapidOCR: only with the ocr extra."""
    return settings.ocr_engine == _engine_id(PaddleVlEngine) and _installed_version("rapidocr") is not None


def _auto_engine(
    settings: Settings, engine: Callable[[str], OcrEngine], timer: Callable[[], float] = time.monotonic
) -> OcrEngine:
    """The usual chain for OCR_ENGINE (not "none"), its engines from ``engine(id)``."""
    primary = engine(settings.ocr_engine)
    if not _has_fallback(settings):
        return primary  # Gemini, RapidOCR, or PaddleOCR-VL with no ocr extra: no fallback here
    rapidocr = _engine_id(RapidOcrEngine)
    return FallbackOcrEngine(primary, lambda: engine(rapidocr), timer=timer)  # RapidOCR built on first need


def build_ocr_engine(settings: Settings, *, gemini: GeminiEngineFactory | None = None) -> OcrEngine | None:
    """The engine for OCR_ENGINE, None for "none". The Gemini reader is built by ``gemini``,
    only for OCR_ENGINE=gemini (``wiring.build_ocr_engine`` gives the product's)."""
    if settings.ocr_engine == "none":
        return None
    return _auto_engine(settings, lambda engine_id: _new_engine(settings, engine_id, gemini))


def ocr_choices(settings: Settings) -> list[dict[str, str | bool | None]]:
    """The readers an upload may choose (PRD 5 RT3: no reader choice at upload): one,
    Auto, with ``id``, ``title``, ``hint``, ``available`` and ``reason``, so the "Scan
    with" select hides itself. Pure: from the settings and what is installed, nothing
    built or called, so the api and the worker agree. O(1)."""
    return _engine_choices(settings)[:1]


def _engine_choices(settings: Settings) -> list[dict[str, str | bool | None]]:
    """Auto first, then every engine here, available or with its ``reason``: what a stored
    choice may still pick in ``OcrEngines.choose``. O(engines)."""
    return _auto_engine(settings, lambda engine_id: _new_engine(settings, engine_id))


def ocr_choices(settings: Settings) -> list[dict[str, str | bool | None]]:
    """The readers an upload may choose, Auto first, then every engine here: ``id``,
    ``title``, ``hint``, ``available``, and ``reason`` when it is not. Pure: from the
    settings and what is installed, nothing built or called, so the api and the
    worker agree. O(engines)."""
    choices: list[dict[str, str | bool | None]] = [
        {"id": AUTO, "title": "Auto", "hint": _auto_hint(settings), "available": True, "reason": None}
    ]
    for engine in _ENGINES:
        reason = _READING_OFF if settings.ocr_engine == "none" else engine.unavailable(settings)
        choices.append(
            {
                "id": _engine_id(engine),
                "title": engine.title,
                "hint": engine.hint,
                "available": reason is None,
                "reason": reason,
            }
        )
    return choices


def _auto_hint(settings: Settings) -> str:
    """What Auto does on this server, in the engines' own titles."""
    if settings.ocr_engine == "none":
        return "Scans wait until this server has a scan reader"
    if settings.ocr_engine == GEMINI:
        return "The usual reader here: Gemini"
    (primary,) = (engine for engine in _ENGINES if _engine_id(engine) == settings.ocr_engine)
    if not _has_fallback(settings):
        return f"The usual reader here: {primary.title}"
    return f"The usual reader here: {primary.title}, then {RapidOcrEngine.title} for a page it can't read"


class OcrEngines:
    """The worker's engine (see the module notes): Auto, or for one job the chain
    ``choose`` picks. Engines and chains are built on first need, once each. One
    job at a time uses it; ``choose`` is entered before its pages are read, so the
    page threads (PRD 5 G8) only read ``_active``. O(1) per page beyond the engines'.

    ``engine(id)`` builds an engine (tests give fakes); ``timer`` is the cool-downs';
    ``gemini`` builds the Gemini reader (``build_ocr_engine``).
    job at a time uses it, so there is no lock. O(1) per page beyond the engines'.

    ``engine(id)`` builds an engine (tests give fakes); ``timer`` is the cool-downs'.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        engine: Callable[[str], OcrEngine] | None = None,
        timer: Callable[[], float] = time.monotonic,
        gemini: GeminiEngineFactory | None = None,
    ) -> None:
        if settings.ocr_engine == "none":
            raise ValueError("OCR_ENGINE=none has no engine to choose")
        self._settings = settings
        self._new = engine or (lambda engine_id: _new_engine(settings, engine_id, gemini))
        self._new = engine or (lambda engine_id: _new_engine(settings, engine_id))
        self._timer = timer
        self._built: dict[str, OcrEngine] = {}
        self._chains: dict[str, FallbackOcrEngine] = {}
        self._auto = _auto_engine(settings, self._engine, timer)
        self._active = self._auto

    @property
    def name(self) -> str:
        return self._active.name

    @property
    def version(self) -> str:
        return self._active.version

    def read(self, image: Image.Image) -> list[OcrLine]:
        return self._active.read(image)

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        """The engine in use's ``read_page`` (``as_page_engine``)."""
        return as_page_engine(self._active).read_page(image, page_no=page_no)

    def document(self, pages: DocumentPages) -> contextlib.AbstractContextManager[None]:
        """Auto's document context (``reading_document``): a chosen chain falls back to
        Auto, so Auto is the engine that keeps the page cache."""
        return reading_document(self._auto, pages)

    @contextlib.contextmanager
    def choose(self, choice: str | None) -> Iterator[bool]:
        """Read with ``choice``'s chain until the block ends, then with Auto again, even
        when it raises. Yields False when ``choice`` is not available here (Auto then)."""
        self._active, honoured = self._chain(choice)
        try:
            yield honoured
        finally:
            self._active = self._auto

    def take_fallback_pages(self) -> int:
        """Pages any of the chains read with its fallback since the last take, summed."""
        chains = [self._auto, *self._chains.values()]
        return sum(chain.take_fallback_pages() for chain in chains if isinstance(chain, FallbackOcrEngine))

    def _engine(self, engine_id: str) -> OcrEngine:
        if engine_id not in self._built:
            self._built[engine_id] = self._new(engine_id)
        return self._built[engine_id]

    def _chain(self, choice: str | None) -> tuple[OcrEngine, bool]:
        """(the engine for ``choice``, honoured). Auto's first engine is Auto itself. Uploads
        no longer choose (RT3), but a stored choice of an engine this server has is still
        read with it."""
        if choice in (None, AUTO, self._settings.ocr_engine):
            return self._auto, True
        offered = {item["id"] for item in _engine_choices(self._settings) if item["available"]}
        """(the engine for ``choice``, honoured). Auto's first engine is Auto itself."""
        if choice in (None, AUTO, self._settings.ocr_engine):
            return self._auto, True
        offered = {item["id"] for item in ocr_choices(self._settings) if item["available"]}
        if choice not in offered:
            return self._auto, False
        if choice not in self._chains:
            chosen = self._engine(choice)
            self._chains[choice] = FallbackOcrEngine(
                chosen, lambda: self._auto, timer=self._timer, empty_fails=True
            )
        return self._chains[choice], True


def build_ocr_engines(settings: Settings, *, gemini: GeminiEngineFactory | None = None) -> OcrEngines | None:
    """The worker's ``OcrEngines``; None with OCR_ENGINE=none (no scan is read). ``gemini``
    as in ``build_ocr_engine``."""
    return None if settings.ocr_engine == "none" else OcrEngines(settings, gemini=gemini)
def build_ocr_engines(settings: Settings) -> OcrEngines | None:
    """The worker's ``OcrEngines``; None with OCR_ENGINE=none (no scan is read)."""
    return None if settings.ocr_engine == "none" else OcrEngines(settings)


def ocr_spans(lines: list[OcrLine], page_no: int, width_px: int, height_px: int) -> list[Span]:
    """Pixel boxes to spans with page fractions, clamped to 0..1, origin "ocr".

    Block mode, when any line has a kind (PaddleOCR-VL, the Gemini reader): one
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
    ``ocr_spans`` makes them with no kind; a line from a layout reader always has one."""
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
