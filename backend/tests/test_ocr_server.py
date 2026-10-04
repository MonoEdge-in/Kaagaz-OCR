"""The OCR server app (kaagaz/ocr_server/app.py) with a fake page model.

PaddleOCR-VL itself never runs here: CI has no Paddle, and one page takes
minutes on a CPU. ``FakePageModel`` hands back scripted blocks, so these tests
pin the HTTP contract, the request checks, the label table and the table
parser. ``tests/fixtures/paddle_*.json`` are real PaddleOCR-VL-1.6 pages from
the scratch run on 30 Sep (FINDINGS), trimmed: no input path, polygons or
layout scores.
"""

import io
import json
import logging
import os
import struct
import subprocess
import sys
import threading
import types
import zlib
from collections.abc import Callable, Iterator
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kaagaz.ocr_server import app as server
from kaagaz.ocr_server.app import (
    PADDLE_LABELS,
    PaddleModel,
    PageResult,
    RawBlock,
    blocks_json,
    create_app,
    ocr_device,
    paddle_page,
)
from kaagaz.ocr_server.app import table_cells as cells
from kaagaz.reading import ocr

FIXTURES = Path(__file__).resolve().parent / "fixtures"
URL = "http://ocr:8080"
PNG = {"Content-Type": "image/png"}
VERSION = (
    "paddleocr=3.7.0;paddlex=3.7.2;paddle=3.3.1;vl=PaddleOCR-VL-1.6@c5630ab;layout=PP-DocLayoutV3@241f8bd"
)
TABLE = "<table><tr><td>Item</td><td>Amount</td></tr><tr><td>Seeds</td><td>7,000.00</td></tr></table>"

# FINDINGS.md, "The 25 labels" and its mapping table.
FINDINGS_LABELS = {
    "doc_title": "title",
    "paragraph_title": "heading",
    **dict.fromkeys(
        (
            "text",
            "content",
            "abstract",
            "reference",
            "reference_content",
            "vertical_text",
            "number",
            "aside_text",
            "figure_title",
            "vision_footnote",
            "footnote",
        ),
        "paragraph",
    ),
    "table": "table_cell",
    **dict.fromkeys(["header", "header_image"], "header"),
    **dict.fromkeys(["footer", "footer_image"], "footer"),
    "seal": "stamp",
    **dict.fromkeys(["image", "chart"], "image"),
    **dict.fromkeys(["display_formula", "inline_formula", "formula_number"], "formula"),
    "algorithm": "other",
}


class FakePageModel:
    """Scripted blocks. ``load`` waits for ``may_load``; ``predict`` records the image
    it got, then waits for ``release`` (both set unless a test clears them)."""

    def __init__(self, blocks: list[RawBlock] | None = None, *, loaded: bool = True) -> None:
        self.blocks = blocks or [RawBlock("text", "Total 500", (20, 10, 120, 30), 0)]
        self.may_load, self.loaded, self.started, self.release = (threading.Event() for _ in range(4))
        if loaded:
            self.may_load.set()
        self.release.set()
        self.images: list[tuple[tuple[int, int], str]] = []
        self.failure: Exception | None = None
        self.load_failure: Exception | None = None
        self.released = 0

    def load(self) -> None:
        self.may_load.wait(10)
        if self.load_failure is not None:
            raise self.load_failure
        self.loaded.set()

    def ready(self) -> bool:
        return self.loaded.is_set()

    def predict(self, image: Image.Image) -> PageResult:
        self.images.append((image.size, image.mode))
        self.started.set()
        self.release.wait(10)
        if self.failure is not None:
            raise self.failure
        return PageResult(image.width, image.height, self.blocks)

    def version(self) -> str:
        return VERSION

    def release_memory(self) -> None:
        self.released += 1


def png(width: int = 200, height: int = 100, **options: Any) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG", **options)
    return buffer.getvalue()


def fixture(name: str) -> dict[str, Any]:
    page: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return page


@pytest.fixture
def model() -> FakePageModel:
    return FakePageModel()


@pytest.fixture
def client(model: FakePageModel) -> Iterator[TestClient]:
    with TestClient(create_app(model), base_url=URL) as test_client:
        assert model.loaded.wait(5)
        yield test_client


def test_each_of_the_25_labels_maps_as_in_findings() -> None:
    assert len(FINDINGS_LABELS) == 25
    assert PADDLE_LABELS == FINDINGS_LABELS
    (block,) = blocks_json(PageResult(100, 100, [RawBlock("a_new_label", "x", (0, 0, 50, 50), 0)]))
    assert block["kind"] == "other"


def test_health_is_503_while_loading_then_200() -> None:
    model = FakePageModel(loaded=False)
    with TestClient(create_app(model), base_url=URL) as client:
        health = client.get("/health")
        assert (health.status_code, health.json()) == (503, {"ready": False})
        reply = client.post("/read", content=png(), headers=PNG)
        assert (reply.status_code, reply.json()) == (503, {"error": "not_ready"})
        model.may_load.set()
        assert model.loaded.wait(5)
        health = client.get("/health")
        assert (health.status_code, health.json()) == (200, {"ready": True})
    assert model.images == []


def test_read_gives_the_contract_json(client: TestClient, model: FakePageModel) -> None:
    model.blocks = [
        RawBlock("paragraph_title", "Tax Invoice", (20, 5, 100, 15), 0),
        RawBlock("table", TABLE, (20, 40, 180, 80), 1),
        RawBlock("footer", "Page 1", (20, 90, 60, 98), 2),
        RawBlock("image", "", (0, 0, 10, 10), 3),  # no text: left out
    ]
    reply = client.post("/read", content=png(200, 100), headers=PNG)

    assert reply.status_code == 200
    table_box = [0.1, 0.4, 0.9, 0.8]  # each cell gets its table's box: Paddle gives none per cell
    assert reply.json() == {
        "engine": "paddleocr-vl",
        "version": VERSION,
        "width": 200,
        "height": 100,
        "blocks": [
            {
                "kind": "heading",
                "text": "Tax Invoice",
                "order": 0,
                "box": [0.1, 0.05, 0.5, 0.15],
                "table": None,
            },
            *(
                {"kind": "table_cell", "text": text, "order": 1, "box": table_box}
                | {"table": {"id": "t1", "row": row, "col": col, "header": False}}
                for row, col, text in [(0, 0, "Item"), (0, 1, "Amount"), (1, 0, "Seeds"), (1, 1, "7,000.00")]
            ),
            {"kind": "footer", "text": "Page 1", "order": 2, "box": [0.1, 0.9, 0.3, 0.98], "table": None},
        ],
    }
    assert model.images == [((200, 100), "RGB")]


def test_boxes_are_fractions_of_the_size_the_model_saw() -> None:
    (block,) = blocks_json(PageResult(400, 200, [RawBlock("text", "x", (40, 10, 200, 30), 5)]))
    assert (block["box"], block["order"]) == ([0.1, 0.05, 0.5, 0.15], 5)
    assert blocks_json(PageResult(400, 200, [RawBlock("text", "x", (500, 10, 600, 30), 0)])) == []


def test_null_block_order_keeps_list_order() -> None:
    page = fixture("paddle_en_invoice")
    assert page["parsing_res_list"][9]["block_order"] is None  # the table
    blocks = blocks_json(paddle_page(page))

    assert [block["order"] for block in blocks] == [*range(9), *[9] * 16, *range(10, 14)]
    assert blocks[0] == {
        "kind": "heading",
        "text": "TAX INVOICE",
        "order": 0,
        "box": [round(194 / 2480, 6), round(203 / 3508, 6), round(771 / 2480, 6), round(295 / 3508, 6)],
        "table": None,
    }
    table = [(b["table"]["row"], b["table"]["col"], b["text"]) for b in blocks if b["table"] is not None]
    assert table[:4] == [(0, 0, "Item"), (0, 1, "Qty"), (0, 2, "Rate"), (0, 3, "Amount")]
    assert table[-1] == (3, 3, "950.00")


@pytest.mark.parametrize("name", ["paddle_en_invoice", "paddle_hi_para", "paddle_mr_para"])
def test_real_paddle_pages_keep_every_text_block_in_list_order(name: str) -> None:
    page = fixture(name)
    blocks = blocks_json(paddle_page(page))
    texts = [b["block_content"] for b in page["parsing_res_list"] if b["block_label"] != "table"]
    assert [block["text"] for block in blocks if block["table"] is None] == texts
    for block in blocks:
        x0, y0, x1, y1 = block["box"]
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1


def test_one_inference_at_a_time(client: TestClient, model: FakePageModel) -> None:
    model.release.clear()
    first: list[int] = []
    thread = threading.Thread(
        target=lambda: first.append(client.post("/read", content=png(), headers=PNG).status_code)
    )
    thread.start()
    assert model.started.wait(5)

    second = client.post("/read", content=png(), headers=PNG)
    model.release.set()
    thread.join(10)

    assert (second.status_code, second.json()) == (503, {"error": "busy"})
    assert first == [200]
    assert len(model.images) == 1  # the model was called once
    assert client.post("/read", content=png(), headers=PNG).status_code == 200  # the lock was let go


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({"Host": "evil.example:8080"}, 421, "misdirected"),
        ({"Host": "ocr"}, 421, "misdirected"),
        ({"Origin": "http://ocr:8080"}, 403, "forbidden"),
        ({"Content-Type": "image/jpeg"}, 415, "unsupported_media_type"),
        ({"Content-Type": "image/png; charset=x"}, 415, "unsupported_media_type"),
        ({"Content-Length": str(64 * 1024 * 1024 + 1)}, 413, "too_large"),
        ({"Content-Length": "9" * 40}, 413, "too_large"),
        ({"Content-Length": "2"}, 413, "too_large"),  # more body than it said: counted as it streams
    ],
)
def test_requests_are_checked_before_the_model(
    client: TestClient, model: FakePageModel, headers: dict[str, str], status: int, code: str
) -> None:
    reply = client.post("/read", content=png(), headers={**PNG, **headers})
    assert (reply.status_code, reply.json()) == (status, {"error": code})
    assert model.images == []


def test_a_body_without_a_length_is_refused(client: TestClient, model: FakePageModel) -> None:
    reply = client.post("/read", content=iter([png()]), headers=PNG)  # chunked
    assert (reply.status_code, reply.json()) == (411, {"error": "length_required"})
    assert model.images == []


def _png_header_only(width: int, height: int) -> bytes:
    """A PNG signature and header chunk claiming this size, with no pixels at all."""
    header = b"IHDR" + struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", 13) + header + struct.pack(">I", zlib.crc32(header))
    return b"\x89PNG\r\n\x1a\n" + chunk


def test_unreadable_images_are_422(
    client: TestClient, model: FakePageModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    frames = [Image.new("RGB", (20, 10), colour) for colour in ("white", "black")]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="PNG", save_all=True, append_images=frames[1:])
    jpeg = io.BytesIO()
    frames[0].save(jpeg, format="JPEG")
    bodies = {
        "not an image": b"not a picture",
        "a jpeg": jpeg.getvalue(),
        "cut short": png()[:60],
        "two frames": buffer.getvalue(),
        "a bomb header": _png_header_only(20_000, 20_000),  # refused from its size, before decoding
    }
    for name, body in bodies.items():
        reply = client.post("/read", content=body, headers=PNG)
        assert (name, reply.status_code, reply.json()) == (name, 422, {"error": "unreadable_image"})
    monkeypatch.setattr(server, "MAX_PIXELS", 199 * 100)
    assert client.post("/read", content=png(200, 100), headers=PNG).status_code == 422
    assert model.images == []


def test_the_server_needs_no_backend_settings_database_storage_or_paddle() -> None:
    """The ocr image installs our package with --no-deps (security A4), so importing
    the app must pull in none of these. A fresh interpreter: this one has them loaded."""
    code = (
        "import sys, kaagaz.ocr_server.app; "
        "names = ('kaagaz.settings', 'kaagaz.db', 'sqlalchemy', 'psycopg', 'boto3', 'paddle'); "
        "print(sorted(m for m in sys.modules if m.startswith(names)))"
    )
    backend = FIXTURES.parent.parent
    environ = {name: value for name, value in os.environ.items() if name != "OCR_DEVICE"}
    result = subprocess.run(  # noqa: S603 - fixed arguments, this interpreter
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        cwd=backend,
        env=environ,
    )
    assert result.stdout.strip() == "[]"


def test_the_worker_never_draws_a_page_too_big_for_the_body_cap() -> None:
    # A PNG that cannot compress is 3 bytes a pixel, plus a filter byte a row and
    # zlib's framing: well under 1% more.
    assert ocr.MAX_OCR_PIXELS * 3 * 1.01 < server.MAX_BODY_BYTES


def test_the_docs_routes_are_off(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_a_page_the_model_fails_on_is_read_failed_logged_by_its_type_only(
    client: TestClient, model: FakePageModel, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    model.failure = ValueError("Sunil Patil 27AABCS1234K1Z5")
    reply = client.post("/read", content=png(), headers=PNG)
    assert (reply.status_code, reply.json()) == (500, {"error": "read_failed"})  # the worker fails the page
    assert "ValueError" in caplog.text
    assert "Sunil" not in caplog.text
    model.failure = None
    assert client.post("/read", content=png(), headers=PNG).status_code == 200  # the lock was let go
    assert "Total 500" not in caplog.text  # nothing read is ever logged


def test_an_error_outside_the_model_is_internal_error(
    client: TestClient,
    model: FakePageModel,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> str:
        raise KeyError("Sunil Patil")

    monkeypatch.setattr(model, "version", broken)
    reply = client.post("/read", content=png(), headers=PNG)
    assert (reply.status_code, reply.json()) == (500, {"error": "internal_error"})  # retried by the worker
    assert "KeyError" in caplog.text
    assert "Sunil" not in caplog.text


def test_a_model_that_fails_to_load_ends_the_process(caplog: pytest.LogCaptureFixture) -> None:
    """So compose's restart policy starts it again, instead of a server that stays up and
    never ready while every scan uses up its tries. The exit is a fake here."""
    model = FakePageModel()
    model.load_failure = OSError("/opt/models/PaddleOCR-VL-1.6 Sunil Patil")
    codes: list[int] = []
    exited = threading.Event()

    def exit_process(code: int) -> None:
        codes.append(code)
        exited.set()

    with TestClient(create_app(model, exit_process=exit_process), base_url=URL) as client:
        assert exited.wait(5)
        assert client.get("/health").status_code == 503
    assert codes == [1]
    assert "model load failed: OSError" in caplog.text
    assert "Sunil" not in caplog.text


def test_table_cells_headers_spans_and_empty_cells() -> None:
    html = (
        "<table><thead><tr><th>Item</th><td>Qty</td></tr></thead>"
        '<tr><td rowspan="2">Seeds</td><td colspan="2"> 4 </td><td></td><td>x &amp; y</td></tr>'
        "<tr><td>5</td></tr></table>"
    )
    assert cells(html) == [
        (0, 0, True, "Item"),
        (0, 1, True, "Qty"),
        (1, 0, False, "Seeds"),
        (1, 1, False, "4"),
        (1, 4, False, "x & y"),
        (2, 1, False, "5"),  # column 0 is still Seeds's
    ]


def test_table_spans_are_clamped_and_odd_html_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cells('<table><tr><td colspan="100000">a</td><td>b</td></tr></table>') == [
        (0, 0, False, "a"),
        (0, 100, False, "b"),
    ]
    assert cells(
        '<table><tr><td colspan="0">a</td><td colspan="x">b</td><td rowspan="-3">c</td></tr></table>'
    ) == [
        (0, 0, False, "a"),
        (0, 1, False, "b"),
        (0, 2, False, "c"),
    ]
    nested = "<table><tr><td>outer <table><tr><td>inner</td></tr></table> end</td><td>next</td></tr></table>"
    assert cells(nested) == [(0, 0, False, "outer inner end"), (0, 1, False, "next")]
    assert cells("<table><tr><td>a<td>b<tr><td>c") == [
        (0, 0, False, "a"),
        (0, 1, False, "b"),
        (1, 0, False, "c"),
    ]
    assert cells("<b>no table here</b>") == []
    monkeypatch.setattr(server, "MAX_TABLE_CELLS", 3)
    assert len(cells("<table><tr>" + "<td>v</td>" * 5 + "</tr></table>")) == 3


def test_a_table_over_1_mib_is_dropped_before_parsing(caplog: pytest.LogCaptureFixture) -> None:
    big = "<table><tr><td>" + "9" * (1024 * 1024) + "</td></tr></table>"
    raw = [RawBlock("table", big, (0, 0, 50, 50), 0), RawBlock("text", "kept", (0, 60, 50, 70), 1)]
    assert [block["text"] for block in blocks_json(PageResult(100, 100, raw))] == ["kept"]
    assert "table dropped" in caplog.text
    assert "999" not in caplog.text


def test_the_device_is_the_cpu_unless_ocr_device_says_otherwise() -> None:
    assert ocr_device({}) == "cpu"
    assert ocr_device({"OTHER": "gpu:0"}) == "cpu"
    for value in ("cpu", "gpu:0", "gpu:1", "gpu:7", "gpu:15", "gpu:99"):
        assert ocr_device({"OCR_DEVICE": value}) == value


@pytest.mark.parametrize(
    "value",
    ["cuda", "", " cpu", "cpu ", "cpu\n", "CPU", "GPU:0", "gpu", "gpu:", "gpu:\u0967", "gpu:999", "gpu:0,1"],
)
def test_any_other_device_is_refused_by_name(value: str) -> None:
    """Exactly cpu or gpu:N, N one or two ASCII digits: no spaces, no capitals, and no
    Devanagari digit one (U+0967), which a \\d pattern would let through."""
    with pytest.raises(ValueError, match="OCR_DEVICE") as refused:
        ocr_device({"OCR_DEVICE": value})
    assert repr(value) in str(refused.value)
    assert "cpu or gpu:N" in str(refused.value)
    with pytest.raises(ValueError, match="OCR_DEVICE"):
        PaddleModel(value)


def test_paddle_model_reads_ocr_device_when_none_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCR_DEVICE", raising=False)
    assert PaddleModel().device == "cpu"
    monkeypatch.setenv("OCR_DEVICE", "gpu:0")
    assert PaddleModel().device == "gpu:0"
    assert PaddleModel("cpu").device == "cpu"  # a device given wins
    monkeypatch.setenv("OCR_DEVICE", "cuda")
    with pytest.raises(ValueError, match="OCR_DEVICE"):
        PaddleModel()


def test_a_bad_ocr_device_stops_the_server_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """uvicorn imports the module first, and the module builds its model at import
    (``app = create_app(PaddleModel())``), so a bad OCR_DEVICE fails that import."""
    source = Path(server.__file__).read_text(encoding="utf-8")
    assert "app = create_app(PaddleModel())" in source.splitlines()
    monkeypatch.setenv("OCR_DEVICE", "cuda")
    with pytest.raises(ValueError, match="OCR_DEVICE='cuda'"):
        PaddleModel()
    monkeypatch.setenv("OCR_DEVICE", "gpu:0")
    assert PaddleModel().device == "gpu:0"


def test_the_device_reaches_paddleocr_vl(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[dict[str, Any]] = []

    class FakePaddleOCRVL:
        def __init__(self, **options: Any) -> None:
            built.append(options)

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCRVL=FakePaddleOCRVL))
    monkeypatch.setitem(sys.modules, "paddle", types.SimpleNamespace(is_compiled_with_cuda=lambda: True))
    model = PaddleModel("gpu:0")
    model.load()
    assert model.ready()
    (options,) = built
    assert options["device"] == "gpu:0"
    assert options["use_doc_unwarping"] is False  # the other settings are as before


def test_a_gpu_device_needs_a_paddle_built_for_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    """The image build can't import the GPU paddle (no driver), so load checks it."""
    built: list[dict[str, Any]] = []

    class FakePaddleOCRVL:
        def __init__(self, **options: Any) -> None:
            built.append(options)

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCRVL=FakePaddleOCRVL))
    monkeypatch.setitem(sys.modules, "paddle", types.SimpleNamespace(is_compiled_with_cuda=lambda: False))
    model = PaddleModel("gpu:0")
    with pytest.raises(RuntimeError, match="not built for CUDA"):
        model.load()
    assert not model.ready()
    assert built == []
    cpu = PaddleModel("cpu")  # the CPU device never asks paddle about CUDA
    cpu.load()
    assert cpu.ready()


def installed(packages: dict[str, str]) -> Callable[[str], str]:
    """A stand-in for ``importlib.metadata.version`` that knows only ``packages``."""

    def version(name: str) -> str:
        if name not in packages:
            raise metadata.PackageNotFoundError(name)
        return packages[name]

    return version


GPU_PACKAGES = {"paddleocr": "3.7.0", "paddlex": "3.7.2", "paddlepaddle-gpu": "3.3.1"}
MODELS = "vl=PaddleOCR-VL-1.6@c5630ab;layout=PP-DocLayoutV3@241f8bd"


def test_version_names_the_gpu_build_and_the_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """The GPU image has paddlepaddle-gpu and no paddlepaddle: asking for paddlepaddle
    only would fail every GPU page (council, 3 Oct)."""
    monkeypatch.setattr(metadata, "version", installed(GPU_PACKAGES))
    gpu = PaddleModel("gpu:0").version()
    assert gpu == f"paddleocr=3.7.0;paddlex=3.7.2;paddle-gpu=3.3.1;{MODELS};device=gpu:0"
    cpu_packages = {"paddleocr": "3.7.0", "paddlex": "3.7.2", "paddlepaddle": "3.3.1"}
    monkeypatch.setattr(metadata, "version", installed(cpu_packages))
    assert PaddleModel("cpu").version() == f"{VERSION};device=cpu"


def test_the_worker_takes_the_gpu_servers_version(
    client: TestClient, model: FakePageModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Server to worker: a GPU server's real version passes the worker's check. Its ":"
    was refused there, which would fail every GPU page (council, 3 Oct)."""
    monkeypatch.setattr(metadata, "version", installed(GPU_PACKAGES))
    gpu_version = PaddleModel("gpu:0").version()
    monkeypatch.setattr(model, "version", lambda: gpu_version)
    reply = client.post("/read", content=png(200, 100), headers=PNG)
    assert reply.status_code == 200
    version, lines = ocr._reply_lines(reply.content, 200, 100)
    assert version == gpu_version
    assert [line.text for line in lines] == ["Total 500"]


class ResourceExhaustedError(Exception):
    """Named as Paddle's own out-of-memory error, for when it comes through as is."""


@pytest.mark.parametrize(
    "failure",
    [
        MemoryError("Sunil Patil"),
        ResourceExhaustedError("Sunil Patil"),
        RuntimeError("(ResourceExhausted) Out of memory error on GPU 0, Sunil Patil"),
        OSError("CUDA error(2), out of memory. Sunil Patil"),
        RuntimeError("ALLOC_FAILED Sunil Patil"),
    ],
)
def test_out_of_memory_is_a_503_the_worker_retries(
    client: TestClient, model: FakePageModel, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    """Not this page's fault (the GPU is shared with Ollama): a 503 the worker asks again
    later, never read_failed, which would fail the document."""
    caplog.set_level(logging.DEBUG)
    model.failure = failure
    reply = client.post("/read", content=png(), headers=PNG)
    assert (reply.status_code, reply.json()) == (503, {"error": "out_of_memory"})
    assert type(failure).__name__ in caplog.text
    assert "Sunil" not in caplog.text
    assert model.released == 1  # Paddle's cached GPU memory is handed back
    model.failure = None
    assert client.post("/read", content=png(), headers=PNG).status_code == 200  # the lock was let go


def _deep_failure() -> PageResult:
    """A model error with a cause, as Paddle's own errors come, both quoting the page."""
    try:
        raise ValueError("SENTINEL-4417 Sunil Patil")
    except ValueError as error:
        raise RuntimeError("SENTINEL-4417 Exception from the 'vlm' worker") from error


def test_a_read_failure_logs_its_frames_never_its_message(
    client: TestClient,
    model: FakePageModel,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A RuntimeError that is not out of memory is this page's own failure; the log says
    where it was raised (file, line, function) and the types along its cause chain."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(model, "predict", lambda image: _deep_failure())
    reply = client.post("/read", content=png(), headers=PNG)
    assert (reply.status_code, reply.json()) == (500, {"error": "read_failed"})
    (line,) = [record.getMessage() for record in caplog.records if "read failed" in record.getMessage()]
    assert line.startswith("read failed: RuntimeError < ValueError at app.py:")
    assert line.endswith(":_deep_failure")
    assert "test_ocr_server.py:" in line
    assert "SENTINEL" not in caplog.text  # never the message
    assert "raise RuntimeError" not in caplog.text  # never a source line
    assert model.released == 1


def test_paddles_gpu_memory_is_handed_back_after_every_page(client: TestClient, model: FakePageModel) -> None:
    assert client.post("/read", content=png(), headers=PNG).status_code == 200
    model.failure = ValueError("Sunil Patil")
    assert client.post("/read", content=png(), headers=PNG).status_code == 500
    assert model.released == 2


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (MemoryError(), True),
        (RuntimeError("Out of memory error on GPU 0"), True),
        (OSError("RESOURCEEXHAUSTED"), True),
        (RuntimeError("Exception from the 'vlm' worker: shape mismatch"), False),
        (ValueError("out of memory"), False),  # only a RuntimeError's or OSError's text counts
    ],
)
def test_out_of_memory_is_known_by_its_type_or_its_text(failure: Exception, expected: bool) -> None:
    assert server._out_of_memory(failure) is expected


def test_the_vl_limits_are_read_strictly(monkeypatch: pytest.MonkeyPatch) -> None:
    assert server.vl_limits({}) == {}
    assert server.vl_limits({"OCR_VL_MAX_PIXELS": "", "OCR_VL_MAX_NEW_TOKENS": ""}) == {}
    both = {"OCR_VL_MAX_PIXELS": "112896", "OCR_VL_MAX_NEW_TOKENS": "8192"}
    assert server.vl_limits(both) == {"max_pixels": 112_896, "max_new_tokens": 8192}
    for name, value in [
        ("OCR_VL_MAX_PIXELS", "112895"),
        ("OCR_VL_MAX_PIXELS", "1003521"),
        ("OCR_VL_MAX_PIXELS", " 501760"),
        ("OCR_VL_MAX_PIXELS", "5e5"),
        ("OCR_VL_MAX_PIXELS", "\u0665\u0660\u0661\u0667\u0666\u0660"),  # Arabic-Indic digits
        ("OCR_VL_MAX_NEW_TOKENS", "255"),
        ("OCR_VL_MAX_NEW_TOKENS", "8193"),
    ]:
        with pytest.raises(ValueError, match=name):
            server.vl_limits({name: value})
    monkeypatch.setenv("OCR_VL_MAX_PIXELS", "big")  # read at import, so a bad value stops uvicorn
    with pytest.raises(ValueError, match="OCR_VL_MAX_PIXELS='big'"):
        PaddleModel()


def test_predict_turns_queues_off_and_passes_the_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakePaddleOCRVL:
        def __init__(self, **options: Any) -> None:
            calls.append(("built", options))

        def predict(self, path: str, **options: Any) -> list[Any]:
            calls.append(("predict", options))
            page = {"width": 200, "height": 100, "parsing_res_list": []}
            return [types.SimpleNamespace(json={"res": page})]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCRVL=FakePaddleOCRVL))
    model = PaddleModel("cpu", {"OCR_VL_MAX_PIXELS": "501760", "OCR_VL_MAX_NEW_TOKENS": "2048"})
    model.load()
    assert model.predict(Image.new("RGB", (200, 100), "white")) == PageResult(200, 100, [])
    (_, built), (_, asked) = calls
    assert built["use_queues"] is False and asked["use_queues"] is False
    assert (asked["max_pixels"], asked["max_new_tokens"]) == (501_760, 2048)
    assert "repetition_penalty" not in asked  # the local VL model ignores it
    unset = PaddleModel("cpu", {})
    unset.load()
    unset.predict(Image.new("RGB", (200, 100), "white"))
    assert not {"max_pixels", "max_new_tokens"} & calls[-1][1].keys()  # the model's own values


def test_the_version_names_the_limits_and_the_worker_takes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata, "version", installed(GPU_PACKAGES))
    limits = {"OCR_VL_MAX_PIXELS": "1003520", "OCR_VL_MAX_NEW_TOKENS": "8192"}
    version = PaddleModel("gpu:0", limits).version()
    assert version.endswith(";device=gpu:0;max_pixels=1003520;max_new_tokens=8192")
    assert ocr._VERSION.fullmatch(version)  # the worker's check of a reply's version
    assert PaddleModel("gpu:0", {}).version().endswith(";device=gpu:0")


def test_release_memory_empties_the_gpu_cache_and_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    emptied: list[int] = []
    cuda = types.SimpleNamespace(empty_cache=lambda: emptied.append(1))
    paddle = types.SimpleNamespace(
        is_compiled_with_cuda=lambda: True, device=types.SimpleNamespace(cuda=cuda)
    )
    monkeypatch.setitem(sys.modules, "paddle", paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCRVL=lambda **options: None))
    gpu = PaddleModel("gpu:0", {})
    gpu.release_memory()  # not loaded: nothing to hand back
    gpu.load()
    gpu.release_memory()
    cpu = PaddleModel("cpu", {})
    cpu.load()
    cpu.release_memory()  # the CPU has no GPU cache
    assert emptied == [1]

    def broken() -> None:
        raise RuntimeError("Sunil Patil")

    cuda.empty_cache = broken
    gpu.release_memory()
    assert "release memory failed: RuntimeError" in caplog.text
    assert "Sunil" not in caplog.text
