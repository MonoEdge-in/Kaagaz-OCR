"""The OCR server: PaddleOCR-VL behind our own small HTTP contract (PRD 2 P1, P2).

It runs only in the ``ocr`` container (``backend/Dockerfile.ocr``), started as
``uvicorn kaagaz.ocr_server.app:app``, and never imports ``kaagaz.settings``:
it needs no database, storage or queue settings, and has none.

It reads on the CPU, or on a GPU on a GPU server (``docker-compose.gpu.yml``).
``OCR_DEVICE`` names the device: ``cpu`` (the default, when unset) or
``gpu:N``. Any other value stops the server while this module is imported, so
uvicorn never starts with a device it would only fail on later.

* ``GET /health``: 200 ``{"ready": true}`` once the model is loaded, 503
  ``{"ready": false}`` while it loads (minutes), so compose's check waits.
* ``POST /read``, the body one page as PNG: 200 ``{"engine": "paddleocr-vl",
  "version", "width", "height", "blocks": [{"kind", "text", "order", "box",
  "table"}]}``. ``order`` is PaddleOCR-VL's ``block_id``, its reading order
  (``block_order`` is null for tables, headers and footers, so it is not
  used). ``box`` is ``[x0, y0, x1, y1]`` as fractions of the size the model
  saw, top left origin. A table becomes one ``table_cell`` block per non-empty
  cell, each with the table's box, because PaddleOCR-VL gives no box per cell
  (VERIFIED), and ``{"id": "t1", "row", "col", "header"}``. No confidence:
  PaddleOCR-VL has none (VERIFIED).

Refusals, each ``{"error": code}``: 421 ``misdirected`` (a Host other than
this service), 403 ``forbidden`` (any Origin: a browser never calls this),
415 ``unsupported_media_type``, 411 ``length_required`` (no Content-Length, or
chunked), 413 ``too_large``, 422 ``unreadable_image`` (not a PNG, damaged,
over ``MAX_PIXELS`` from its header, or more than one frame), 503
``not_ready``, and 503 ``busy``: one page is read at a time, and the lock is
taken without waiting before the body is read, so a second caller hears
``busy`` at once and asks again, within its own deadline, instead of queueing
(AR2). One ``ocr`` container serves one worker.

Failures: the model raising on a page is a 500 ``read_failed`` (that page's
own failure, so the worker does not retry it), except running out of memory
(``MemoryError``, which Paddle raises when the GPU's memory runs out): that is
a 503 ``out_of_memory``, which the worker retries later, because the page is
fine and the memory is shared (Ollama on the same GPU). Any other error nothing else
handled is a 500 ``internal_error``. Each is one log line with the error's
type name only. A model that fails to load ends the process (exit code 1), so
compose's ``restart: unless-stopped`` starts it again rather than leaving it
up and never ready.

Nothing read is ever logged, and ``res.save_to_*`` is never called.
Inference cannot be cut off inside the server; a reply the worker gave up on
is thrown away.

Time: one model call per page (minutes on a CPU); the rest is O(blocks +
table HTML). Memory: one page, plus the model (GBs, docs/running-locally.md).
"""

import html.parser
import importlib
import io
import logging
import os
import re
import struct
import tempfile
import threading
import warnings
import zlib
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 64 * 1024 * 1024  # PROVISIONAL: a 300 DPI A3 page as PNG fits
MAX_PIXELS = 40_000_000  # PROVISIONAL: the worker's MAX_IMAGE_PIXELS on the laptop
MAX_TABLE_HTML_BYTES = 1024 * 1024  # security A7: a bigger table block is dropped before parsing
MAX_TABLE_CELLS = 5000  # PROVISIONAL
MAX_CELL_SPAN = 100  # security A7: colspan and rowspan are clamped to 1..100
MAX_TABLE_INDEX = 1000  # the worker drops rows and columns from here on, so none are made
ALLOWED_HOSTS = frozenset({"ocr:8080", "localhost:8080", "127.0.0.1:8080"})
# Read-only, baked in by Dockerfile.ocr at pinned revisions (FINDINGS).
MODELS = Path("/opt/models")
VL_MODEL = "PaddleOCR-VL-1.6@c5630ab"
LAYOUT_MODEL = "PP-DocLayoutV3@241f8bd"
# All off (security A6, ML 7). VERIFIED names in paddleocr 3.7.0 (the constructor
# and predict). Unwarping would move the boxes off the page as it was sent.
PADDLE_FLAGS = {
    "use_doc_orientation_classify": False,
    "use_doc_unwarping": False,
    "use_chart_recognition": False,
    "use_seal_recognition": False,
}
# Paddle's device names: the CPU, or one GPU by its number (docker-compose.gpu.yml).
DEVICE = re.compile(r"cpu|gpu:[0-9]{1,2}")
# Errors that mean the memory ran out, not that the page is bad. Paddle raises
# MemoryError for its ResourceExhausted errors, GPU memory included (INFERRED from
# Paddle's C++ to Python error mapping; VERIFY on g4dn). The names cover a library
# that raises its own type instead.
_OUT_OF_MEMORY_NAMES = frozenset({"ResourceExhaustedError", "OutOfMemoryError"})

# The 25 layout labels of PaddleOCR-VL-1.6 (PP-DocLayoutV3's inference.yml,
# VERIFIED in FINDINGS), as our block kinds. An unknown label becomes "other".
# There is no list or signature label, so those kinds never come from here.
PADDLE_LABELS: dict[str, str] = {
    "doc_title": "title",
    "paragraph_title": "heading",
    "text": "paragraph",
    "content": "paragraph",
    "abstract": "paragraph",
    "reference": "paragraph",
    "reference_content": "paragraph",
    "vertical_text": "paragraph",
    "number": "paragraph",
    "aside_text": "paragraph",
    "figure_title": "paragraph",
    "vision_footnote": "paragraph",
    "footnote": "paragraph",
    "table": "table_cell",
    "header": "header",
    "header_image": "header",
    "footer": "footer",
    "footer_image": "footer",
    "seal": "stamp",
    "image": "image",
    "chart": "image",
    "display_formula": "formula",
    "inline_formula": "formula",
    "formula_number": "formula",
    "algorithm": "other",
}

# Errors Pillow raises for a damaged or refused image (as kaagaz/preparing/pages.py).
_UNREADABLE = (
    OSError,
    ValueError,
    SyntaxError,
    EOFError,
    struct.error,
    zlib.error,
    Image.DecompressionBombError,
    Image.DecompressionBombWarning,
)

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RawBlock:
    """One block as PaddleOCR-VL gives it (``parsing_res_list``)."""

    label: str
    text: str  # an HTML table for "table"
    box: tuple[float, float, float, float]  # pixels of the image the model saw, top left origin
    order: int  # block_id: its place in reading order, from 0


@dataclass(frozen=True, slots=True)
class PageResult:
    width: int  # the size of the image the model saw: its boxes are in these pixels
    height: int
    blocks: list[RawBlock]


class PageModel(Protocol):
    def load(self) -> None: ...
    def ready(self) -> bool: ...
    def predict(self, image: Image.Image) -> PageResult: ...
    def version(self) -> str: ...


def ocr_device(env: Mapping[str, str]) -> str:
    """``OCR_DEVICE`` from ``env``, ``cpu`` when unset: exactly ``cpu`` or ``gpu:N``, N one
    or two ASCII digits. Anything else (spaces and capitals included) is a ValueError
    that names the variable, the value and the forms allowed."""
    value = env.get("OCR_DEVICE", "cpu")
    if not DEVICE.fullmatch(value):
        raise ValueError(f"OCR_DEVICE={value!r} is not allowed: use cpu or gpu:N, such as gpu:0")
    return value


def _out_of_memory(error: Exception) -> bool:
    return isinstance(error, MemoryError) or type(error).__name__ in _OUT_OF_MEMORY_NAMES


class PaddleModel:
    """PaddleOCR-VL on the CPU or a GPU. ``paddleocr`` is imported inside ``load`` only,
    so this module and its tests never need it. VERIFIED in a scratch venv (paddleocr
    3.7.0, on the CPU): the class, the model folders, ``predict`` on a PNG path, and
    ``json["res"]``. ``device`` is checked here; None reads ``OCR_DEVICE``."""

    def __init__(self, device: str | None = None) -> None:
        self.device = ocr_device(os.environ if device is None else {"OCR_DEVICE": device})
        self._pipeline: Any = None
        self._loaded = threading.Event()

    def load(self) -> None:
        from paddleocr import PaddleOCRVL  # lazy: only the ocr image has it

        for name in ("paddle", "paddlex", "paddleocr"):
            logging.getLogger(name).setLevel(logging.ERROR)
        if self.device != "cpu" and not importlib.import_module("paddle").is_compiled_with_cuda():
            # The image build can't check this: importing the GPU paddle needs the driver.
            raise RuntimeError(f"OCR_DEVICE={self.device} but this paddle is not built for CUDA")
        self._pipeline = PaddleOCRVL(
            device=self.device,
            vl_rec_model_dir=str(MODELS / "PaddleOCR-VL-1.6"),
            layout_detection_model_dir=str(MODELS / "PP-DocLayoutV3"),
            **PADDLE_FLAGS,
        )
        self._loaded.set()

    def ready(self) -> bool:
        return self._loaded.is_set()

    def predict(self, image: Image.Image) -> PageResult:
        with tempfile.TemporaryDirectory() as folder:  # /tmp is a tmpfs in the container
            path = Path(folder) / "page.png"
            image.save(path, format="PNG")
            (result,) = self._pipeline.predict(str(path), **PADDLE_FLAGS)
        return paddle_page(result.json["res"])

    def version(self) -> str:
        """The packages, the model revisions and the device, so each run records how it was
        read. The GPU image has ``paddlepaddle-gpu`` and no ``paddlepaddle``, so that one is
        asked for first, and the short name says which build it was."""
        packages = (("paddleocr", "paddleocr"), ("paddlex", "paddlex"), _paddle_package())
        installed = ";".join(f"{short}={metadata.version(name)}" for short, name in packages)
        return f"{installed};vl={VL_MODEL};layout={LAYOUT_MODEL};device={self.device}"


def _paddle_package() -> tuple[str, str]:
    """(short name, package) of the Paddle build installed: the GPU one, else the CPU one."""
    try:
        metadata.version("paddlepaddle-gpu")
    except metadata.PackageNotFoundError:
        return "paddle", "paddlepaddle"
    return "paddle-gpu", "paddlepaddle-gpu"


def paddle_page(page: Mapping[str, Any]) -> PageResult:
    """PaddleOCR-VL's page JSON (``result.json["res"]``), its blocks in list order."""
    blocks = []
    for block in page["parsing_res_list"]:
        x0, y0, x1, y1 = (float(value) for value in block["block_bbox"])
        blocks.append(
            RawBlock(
                str(block["block_label"]),
                str(block["block_content"] or ""),  # "" for a block with no text (VERIFIED)
                (x0, y0, x1, y1),
                int(block["block_id"]),
            )
        )
    return PageResult(int(page["width"]), int(page["height"]), blocks)


def blocks_json(result: PageResult) -> list[dict[str, Any]]:
    """The contract's blocks, in reading order. A block with no text, or with no
    part of its box inside the page, is left out."""
    blocks: list[dict[str, Any]] = []
    tables = 0
    for raw in result.blocks:
        box = _fractions(raw.box, result.width, result.height)
        kind = PADDLE_LABELS.get(raw.label, "other")
        if box is None or not raw.text.strip():
            continue
        if kind != "table_cell":
            blocks.append({"kind": kind, "text": raw.text, "order": raw.order, "box": box, "table": None})
            continue
        cells = table_cells(raw.text) if len(raw.text.encode("utf-8")) <= MAX_TABLE_HTML_BYTES else []
        if not cells:
            _log.warning("table dropped", extra={"code": "table_unreadable"})
            continue
        tables += 1
        for row, col, header, text in cells:
            table = {"id": f"t{tables}", "row": row, "col": col, "header": header}
            blocks.append({"kind": kind, "text": text, "order": raw.order, "box": box, "table": table})
    return blocks


def _fractions(box: tuple[float, float, float, float], width: int, height: int) -> list[float] | None:
    if width < 1 or height < 1:
        return None
    sizes = (width, height, width, height)
    x0, y0, x1, y1 = (
        round(min(1.0, max(0.0, value / size)), 6) for value, size in zip(box, sizes, strict=True)
    )
    return [x0, y0, x1, y1] if x0 < x1 and y0 < y1 else None


class _TableParser(html.parser.HTMLParser):
    """The cells of the outermost table. A nested table's text joins the cell it is in."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[tuple[int, int, bool, str]] = []
        self._depth = 0  # tables deep
        self._in_head = False
        self._row, self._col = -1, 0
        self._taken: dict[int, int] = {}  # column -> the first row a rowspan above leaves free
        self._cell: tuple[int, int, bool] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._depth += 1
        if self._depth != 1:
            return
        if tag == "thead":
            self._in_head = True
        elif tag == "tr":
            self._end_cell()
            self._row, self._col = self._row + 1, 0
        elif tag in {"td", "th"}:
            self._end_cell()
            self._row = max(self._row, 0)
            while self._taken.get(self._col, 0) > self._row:
                self._col += 1
            rows, cols = _span(attrs, "rowspan"), _span(attrs, "colspan")
            for col in range(self._col, min(self._col + cols, MAX_TABLE_INDEX)):
                self._taken[col] = self._row + rows
            if self._row < MAX_TABLE_INDEX and self._col < MAX_TABLE_INDEX:
                self._cell = (self._row, self._col, tag == "th" or self._in_head)
            self._col += cols

    def handle_endtag(self, tag: str) -> None:
        if self._depth == 1 and tag in {"td", "th", "tr", "table"}:
            self._end_cell()
        if self._depth == 1 and tag == "thead":
            self._in_head = False
        if tag == "table":
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._text.append(data)

    def close(self) -> None:
        super().close()
        self._end_cell()

    def _end_cell(self) -> None:
        text = " ".join("".join(self._text).split())
        if self._cell is not None and text and len(self.cells) < MAX_TABLE_CELLS:
            self.cells.append((*self._cell, text))
        self._cell, self._text = None, []


def _span(attrs: list[tuple[str, str | None]], name: str) -> int:
    value = (dict(attrs).get(name) or "").strip()
    if not value.isdecimal():
        return 1
    return MAX_CELL_SPAN if len(value) > 4 else min(MAX_CELL_SPAN, max(1, int(value)))


def table_cells(table_html: str) -> list[tuple[int, int, bool, str]]:
    """(row, col, header, text) for each non-empty cell, rows and columns from 0.

    The standard ``html.parser`` only: tags and character references, nothing
    fetched or run. ``th`` and ``thead`` cells are headers; PaddleOCR-VL writes
    neither (VERIFIED), so its header rows are for the pieces step to find.
    """
    parser = _TableParser()
    parser.feed(table_html)
    parser.close()
    return parser.cells


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def _refusal(request: Request) -> JSONResponse | None:
    """Security A5: the checks that need no body, in order."""
    headers = request.headers
    length = headers.get("content-length", "")
    if headers.get("host") not in ALLOWED_HOSTS:
        return _error(421, "misdirected")
    if "origin" in headers:
        return _error(403, "forbidden")
    if headers.get("content-type") != "image/png":
        return _error(415, "unsupported_media_type")
    if "transfer-encoding" in headers or not length.isdecimal():
        return _error(411, "length_required")
    if len(length) > 12 or int(length) > MAX_BODY_BYTES:
        return _error(413, "too_large")
    return None


async def _body(request: Request, length: int) -> bytes | None:
    """The body, counted as it arrives; None once it passes its declared length."""
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > length:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _decoded(body: bytes) -> Image.Image | None:
    """The PNG as RGB, or None. Its size is checked from the header before any pixel
    is decoded, and Pillow's bomb warning counts as a refusal."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(body), formats=["PNG"]) as image:
                if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) > 1:
                    return None
                image.load()
                return image.convert("RGB")
        except _UNREADABLE:
            return None


def _answer(model: PageModel, body: bytes) -> JSONResponse:
    image = _decoded(body)
    if image is None:
        return _error(422, "unreadable_image")
    with image:
        try:
            blocks = blocks_json(model.predict(image))
        except Exception as error:  # noqa: BLE001 - this page's own failure, see the module notes
            if _out_of_memory(error):  # not this page's fault: the worker asks again later
                _log.error("read out of memory: %s", type(error).__name__)
                return _error(503, "out_of_memory")
            _log.error("read failed: %s", type(error).__name__)
            return _error(500, "read_failed")
        reply = {"engine": "paddleocr-vl", "version": model.version(), "width": image.width}
        reply |= {"height": image.height, "blocks": blocks}
    return JSONResponse(reply)


class _ErrorBoundary:
    """The outermost of the app's own middleware: whatever the app lets out ends here
    as one log line with the error's type name, and a 500. Never its message or
    traceback, which can quote page text."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = False

        async def send_watched(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self._app(scope, receive, send_watched)
        except Exception as error:  # noqa: BLE001 - the ASGI boundary, see the docstring
            _log.error("error: %s", type(error).__name__)
            if not started:
                await _error(500, "internal_error")(scope, receive, send)


def create_app(model: PageModel, *, exit_process: Callable[[int], None] = os._exit) -> FastAPI:
    """``exit_process`` ends the process when the model fails to load; tests pass a fake."""
    lock = threading.Lock()

    def load() -> None:
        try:
            model.load()
        except Exception as error:  # noqa: BLE001 - the load thread's boundary: the process ends
            _log.error("model load failed: %s", type(error).__name__)
            exit_process(1)  # os._exit: sys.exit here would end only this thread

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        threading.Thread(target=load, name="ocr-model-load", daemon=True).start()
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/health")
    def health() -> JSONResponse:
        ready = model.ready()
        return JSONResponse({"ready": ready}, status_code=200 if ready else 503)

    @app.post("/read")
    async def read(request: Request) -> JSONResponse:
        refusal = _refusal(request)
        if refusal is not None:
            return refusal
        if not model.ready():
            return _error(503, "not_ready")
        if not lock.acquire(blocking=False):
            return _error(503, "busy")
        try:
            body = await _body(request, int(request.headers["content-length"]))
            if body is None:
                return _error(413, "too_large")
            # Decoding and the model are slow and blocking: off the event loop.
            return await run_in_threadpool(_answer, model, body)
        finally:
            lock.release()

    app.add_middleware(_ErrorBoundary)
    return app


# The uvicorn target; the model loads in the lifespan, on a thread. PaddleModel() checks
# OCR_DEVICE here, at import, so a bad value stops uvicorn before it serves anything.
app = create_app(PaddleModel())
