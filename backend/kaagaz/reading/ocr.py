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
  timed under its own name.
* ``ocr_spans`` turns an engine's pixel boxes into spans with page fractions
  and origin "ocr". RapidOCR's boxes go through the same line rule as the PDF
  readers (``kaagaz/reading/lines.py``), so "INVOICE" and "1042" found as two
  boxes on one line become one span. PaddleOCR-VL's blocks are already in
  reading order, so each becomes one span, in the order given.

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
from pathlib import Path
from typing import Any, NoReturn, Protocol, TypeGuard, runtime_checkable

from PIL import Image

from kaagaz.chunking.script import ocr_script_suspect, ocr_suspect_count
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading.lines import PageFrame, WordBox, line_spans
from kaagaz.reading.page_quality import ILLEGIBLE_MARK
from kaagaz.reading.spans import BLOCK_KINDS, TABLE_ID, BoxLocation, ReadError, Span
from kaagaz.settings import Settings, SettingsError

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
# RapidOCR's Devanagari recogniser (PP-OCRv5 mobile, 7,940,361 bytes, Apache-2.0),
# for pages its default one (PP-OCRv6 small, no Devanagari) reads as other scripts.
DEVANAGARI_REC_MODEL = "devanagari_PP-OCRv5_rec_mobile"
# After PaddleOCR-VL timed out or ran out of memory, pages go straight to RapidOCR
# for this long, so each one does not first wait out OCR_TIMEOUT_SECONDS
# (PROVISIONAL, council 4 Oct 2026).
COOL_DOWN_SECONDS = 120.0
# The choice that means "the usual way" (stored as NULL); never an engine's id.
AUTO = "auto"
# Why no engine can be chosen with OCR_ENGINE=none.
_READING_OFF = "Scan reading is off on this server"

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
    * ``read_by``: who read it (RT2): the model ID in OCR_MODEL, or "rapidocr".
    * ``input_tokens``, ``output_tokens``: what this read was billed, from the
      reply's usage metadata (thinking tokens are output); 0 for a page taken
      from the page cache or read locally.
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
        """
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
                self._engine = _rapidocr({"Global.log_level": "error"})
            return self._engine

    def _devanagari_model(self) -> Any:
        """Built on the first suspect page, from the file on disk; ``OcrModelMissing`` without it."""
        with self._lock:
            if self._devanagari is None:
                path = devanagari_model_path()
                if not path.is_file():
                    raise OcrModelMissing(DEVANAGARI_REC_MODEL)
                self._devanagari = _rapidocr(_devanagari_params(path))
            return self._devanagari


def _rapidocr(params: dict[str, Any]) -> Any:
    from rapidocr import RapidOCR  # lazy: an optional extra

    return RapidOCR(params=params)


def _devanagari_params(path: Path) -> dict[str, Any]:
    """The second engine's settings. With ``Rec.model_path`` set, RapidOCR loads that
    file and never reaches its download step; the file holds its own letters. The
    other ``Rec`` values name the same model, as Enum values (RapidOCR refuses plain
    strings for them)."""
    from rapidocr import LangRec, ModelType, OCRVersion

    return {
        "Global.log_level": "error",
        "Rec.lang_type": LangRec.DEVANAGARI,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.MOBILE,
        "Rec.model_path": str(path),
    }


def devanagari_model_path() -> Path:
    """Where the image's build puts the Devanagari model: RapidOCR's own models folder,
    in its package (backend/Dockerfile checks the file's SHA-256 there)."""
    import rapidocr  # lazy: an optional extra

    return Path(str(rapidocr.__file__)).parent / "models" / f"{DEVANAGARI_REC_MODEL}.onnx"


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

    @property
    def version(self) -> str:
        return self._primary.version  # the fallback's pages are counted, not named here

    def take_fallback_pages(self) -> int:
        """Pages the fallback read since the last take, and start again from zero."""
        taken, self._fallback_pages = self._fallback_pages, 0
        return taken

    def read(self, image: Image.Image) -> list[OcrLine]:
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
                return lines
            return self._read_fallback(image, ReadError("ocr_no_lines"), kept=lines)
        # Outside the handler, so a failing fallback's error is chained onto the primary's once.
        lines = self._read_fallback(image, failure)
        if isinstance(failure, OcrOverloaded):
            self._cool_until = self._timer() + COOL_DOWN_SECONDS  # only once the fallback worked
        return lines

    def _read_fallback(
        self, image: Image.Image, failure: ReadError | OcrOverloaded, *, kept: list[OcrLine] | None = None
    ) -> list[OcrLine]:
        """The page as the fallback reads it. When the fallback cannot be built or fails,
        any cool-down ends, so the next page tries the primary, and ``kept`` (the
        primary's own lines) is returned or else ``failure`` (its own error) is raised.
        Logs codes and type names only, never the page."""
        try:
            if self._fallback is None:
                self._fallback = self._build_fallback()
            lines = self._fallback.read(image)
        except Exception as error:
            self._cool_until = None
            _log.warning("ocr fallback failed: %s", type(error).__name__, extra={"code": failure.code})
            if kept is not None:
                return kept
            raise failure from error
        self._fallback_pages += 1
        _log.warning("ocr page read by the fallback", extra={"code": failure.code})
        return lines


class MeteredOcrEngine:
    """An ``OcrEngine`` that passes each page to ``engine`` and counts the pages
    sent and the seconds they took on ``timer`` (``time.perf_counter`` in the
    product). A page whose read fails is counted too: it was sent. One job at a
    time uses it, so there is no lock. O(1) per page beyond the engine's own time.
    """

    def __init__(self, engine: OcrEngine, *, timer: Callable[[], float]) -> None:
        self._engine = engine
        self._timer = timer
        self._pages = 0
        self._seconds = 0.0

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
            self._pages += 1
            self._seconds += max(0.0, self._timer() - started)

    def take(self) -> tuple[int, float]:
        """(pages sent, seconds) since the last take, and start counting again from zero."""
        counted = (self._pages, self._seconds)
        self._pages, self._seconds = 0, 0.0
        return counted

    def take_fallback_pages(self) -> int:
        """Pages a ``FallbackOcrEngine`` (or every one ``OcrEngines`` built) read with its
        fallback since the last take; 0 for any other engine."""
        engine = self._engine
        return engine.take_fallback_pages() if isinstance(engine, FallbackOcrEngine | OcrEngines) else 0


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


def build_ocr_engine(settings: Settings) -> OcrEngine | None:
    if settings.ocr_engine == "none":
        return None
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
    (primary,) = (engine for engine in _ENGINES if _engine_id(engine) == settings.ocr_engine)
    if not _has_fallback(settings):
        return f"The usual reader here: {primary.title}"
    return f"The usual reader here: {primary.title}, then {RapidOcrEngine.title} for a page it can't read"


class OcrEngines:
    """The worker's engine (see the module notes): Auto, or for one job the chain
    ``choose`` picks. Engines and chains are built on first need, once each. One
    job at a time uses it, so there is no lock. O(1) per page beyond the engines'.

    ``engine(id)`` builds an engine (tests give fakes); ``timer`` is the cool-downs'.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        engine: Callable[[str], OcrEngine] | None = None,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        if settings.ocr_engine == "none":
            raise ValueError("OCR_ENGINE=none has no engine to choose")
        self._settings = settings
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


def build_ocr_engines(settings: Settings) -> OcrEngines | None:
    """The worker's ``OcrEngines``; None with OCR_ENGINE=none (no scan is read)."""
    return None if settings.ocr_engine == "none" else OcrEngines(settings)


def ocr_spans(lines: list[OcrLine], page_no: int, width_px: int, height_px: int) -> list[Span]:
    """Pixel boxes to spans with page fractions, clamped to 0..1, origin "ocr".

    Block mode, when any line has a kind (PaddleOCR-VL): one span per line, in
    the order given, never re-sorted, with its kind and table. Line mode
    (RapidOCR): through the shared line rule. A line with no text, a box with
    nothing inside the page, or a confidence that is not a number is left out
    (never guessed); a confidence is clamped to 0..1, and None stays None.
    O(n log n) for n lines.
    """
    if width_px <= 0 or height_px <= 0:
        return []
    usable = [line for line in lines if line.confidence is None or math.isfinite(line.confidence)]
    if any(line.kind is not None for line in lines):
        return [span for line in usable if (span := _block_span(line, page_no, width_px, height_px))]
    words = [
        WordBox(line.text, line.x0, line.y0, line.x1, line.y1, _clamped(line.confidence))
        for line in usable
        if line.text.strip()
    ]
    return line_spans(words, page_no, PageFrame(0.0, 0.0, float(width_px), float(height_px)), origin="ocr")


def _block_span(line: OcrLine, page_no: int, width: int, height: int) -> Span | None:
    text = " ".join(line.text.split())
    box = BoxLocation(
        page_no,
        min(1.0, max(0.0, line.x0 / width)),
        min(1.0, max(0.0, line.y0 / height)),
        min(1.0, max(0.0, line.x1 / width)),
        min(1.0, max(0.0, line.y1 / height)),
    )
    if not text or not box.is_valid():
        return None
    span = Span(text, line.text, box, _clamped(line.confidence), line.kind, "ocr")
    table = line.table
    if table is None:
        return span
    return dataclasses.replace(
        span, table_id=table.table_id, row=table.row, col=table.col, header=table.header
    )


def _clamped(confidence: float | None) -> float | None:
    return None if confidence is None else min(1.0, max(0.0, confidence))
