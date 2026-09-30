"""The scan reader engine (OCR), behind one small interface.

The scan reader is PaddleOCR-VL (decided 29 Sep, docs/architecture.md), in its
own container; RapidOCR (Apache-2.0) stays as the fallback for weak laptops. No
page ever leaves the machine and nothing is downloaded at run time.

* ``OcrEngine`` is what the page readers depend on, so tests use a fake and a
  different engine means one new class here.
* ``PaddleVlEngine`` posts each page, as a PNG it encoded itself, to the ocr
  container's ``POST /read`` (``kaagaz/ocr_server/app.py``, PRD 2 P2) and
  checks every field of the reply before anything becomes a line (security
  A7). It gives no confidence (PaddleOCR-VL has none), so a scan is known by
  its spans' origin "ocr", never by a made-up number. Failures (P5):

  * down, refused, a 5xx (``not_ready``, ``busy``), cut off, or past
    ``OCR_TIMEOUT_SECONDS``: ``BackendUnavailable("ocr")``, retried later;
  * 422: ``corrupt_file``; 413: ``image_too_large``; a 3xx (never followed),
    another 4xx, a malformed reply or one over ``MAX_OCR_REPLY_BYTES``:
    ``invalid_reader_output``. The document fails; these are not retried.

  It uses ``http.client``, not urllib's opener: it follows no redirect, reads
  no proxy variable (page images never reach a proxy), and one monotonic
  deadline covers the whole call, because each socket read gets only the time
  that is left. Over-limit items in a reply are dropped and counted.
* ``RapidOcrEngine`` imports ``rapidocr`` only on its first page, because it
  is an optional extra (``pip install .[ocr]``): without it, the rest of the
  backend still starts, and ``OCR_ENGINE=none`` marks scans needs_reader.
  The model is loaded once, under a lock, and reused for every page.
* ``ocr_spans`` turns an engine's pixel boxes into spans with page fractions
  and origin "ocr". RapidOCR's boxes go through the same line rule as the PDF
  readers (``kaagaz/reading/lines.py``), so "INVOICE" and "1042" found as two
  boxes on one line become one span. PaddleOCR-VL's blocks are already in
  reading order, so each becomes one span, in the order given.

An engine never logs what it read: RapidOCR's own logger is set to errors
only, and its messages hold no page text.
"""

import dataclasses
import io
import json
import logging
import math
import re
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException, HTTPResponse, HTTPSConnection, IncompleteRead
from importlib import metadata
from typing import Any, NoReturn, Protocol, TypeGuard

from PIL import Image

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading.lines import PageFrame, WordBox, line_spans
from kaagaz.reading.spans import BLOCK_KINDS, TABLE_ID, BoxLocation, ReadError, Span
from kaagaz.settings import Settings, SettingsError

# Caps on a /read reply (security A7). A reply over MAX_OCR_REPLY_BYTES is
# refused; blocks past MAX_BLOCKS_PER_PAGE, longer than MAX_BLOCK_CHARS, or in a
# table row or column from MAX_TABLE_INDEX on are dropped and counted.
MAX_OCR_REPLY_BYTES = 16 * 1024 * 1024
MAX_BLOCKS_PER_PAGE = 5000
MAX_BLOCK_CHARS = 20_000
MAX_TABLE_INDEX = 1000
_READ_CHUNK = 64 * 1024
_REFUSALS = {422: "corrupt_file", 413: "image_too_large"}
_VERSION = re.compile(r"[A-Za-z0-9._+;=@ -]{1,200}")
# C0 and C1 controls and the bidirectional overrides and isolates, as
# kaagaz/answering/sanitize.py strips them.
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f‪-‮⁦-⁩]")

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
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float | None
    kind: str | None = None
    table: OcrTable | None = None


class OcrEngine(Protocol):
    name: str

    @property
    def version(self) -> str: ...  # read-only: PaddleOCR-VL's comes from its last reply

    def read(self, image: Image.Image) -> list[OcrLine]: ...


class PaddleVlEngine:
    """``OcrEngine`` over the ocr container's ``POST /read`` (see the module notes)."""

    name = "paddleocr-vl"

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
        """One request per page; the time is the server's (minutes on a CPU)."""
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")  # our own PNG, never the uploaded bytes (security A5)
        status, body = self._post(buffer.getvalue())
        if status >= 500:
            raise BackendUnavailable("ocr")  # down, still loading, or busy: retried later
        if status != 200:
            raise ReadError(_REFUSALS.get(status, "invalid_reader_output"))
        self._version, lines = _reply_lines(body, image.width, image.height)
        return lines

    def _post(self, png: bytes) -> tuple[int, bytes]:
        deadline = time.monotonic() + self._timeout
        connection = self._connection(self._host, self._port, timeout=self._timeout)
        response: HTTPResponse | None = None
        reply: tuple[int, bytes] | None = None
        try:
            connection.request("POST", self._path, body=png, headers={"Content-Type": "image/png"})
            sock = connection.sock  # kept: getresponse drops the connection's own when the reply closes it
            _time_left(sock, deadline)
            response = connection.getresponse()
            reply = response.status, _body(sock, response, deadline)
        except (OSError, HTTPException):
            reply = None  # refused, unreachable, cut off or too slow: raised below, nothing chained
        finally:
            if response is not None:
                response.close()
            connection.close()
        if reply is None:
            raise BackendUnavailable("ocr")
        return reply


def _time_left(sock: socket.socket, deadline: float) -> None:
    """Give the next socket read only the time left before the deadline."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("ocr deadline")
    sock.settimeout(left)


def _body(sock: socket.socket, response: HTTPResponse, deadline: float) -> bytes:
    """At most MAX_OCR_REPLY_BYTES + 1 bytes; a body cut short of its length raises."""
    expected = response.length
    chunks: list[bytes] = []
    size = 0
    while size <= MAX_OCR_REPLY_BYTES and not response.isclosed():  # closed: all of it was read
        _time_left(sock, deadline)
        chunk = response.read1(_READ_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if expected is not None and size < expected and size <= MAX_OCR_REPLY_BYTES:
        raise IncompleteRead(b"", expected - size)
    return b"".join(chunks)


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
    if not (kind in BLOCK_KINDS and isinstance(text, str) and text and _is_whole(order) and _is_box(box)):
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
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in value):
        return False
    x0, y0, x1, y1 = value
    return bool(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)


def _invalid() -> NoReturn:
    raise ReadError("invalid_reader_output")


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self) -> None:
        version = _installed_version("rapidocr")
        if version is None:
            raise SettingsError("OCR_ENGINE=rapidocr needs the ocr extra installed")
        self.version = version
        self._engine: Any = None
        self._lock = threading.Lock()

    def read(self, image: Image.Image) -> list[OcrLine]:
        """Time is dominated by the model, roughly linear in the image's pixels."""
        result = self._model()(image.convert("RGB"))
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

    def _model(self) -> Any:
        with self._lock:
            if self._engine is None:
                from rapidocr import RapidOCR  # lazy: an optional extra

                self._engine = RapidOCR(params={"Global.log_level": "error"})
            return self._engine


def _installed_version(package: str) -> str | None:
    """The package's version, read from its metadata without importing it."""
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def build_ocr_engine(settings: Settings) -> OcrEngine | None:
    if settings.ocr_engine == "none":
        return None
    if settings.ocr_engine == "paddleocr_vl":
        return PaddleVlEngine(settings.ocr_url, timeout_seconds=settings.ocr_timeout_seconds)
    return RapidOcrEngine()


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
