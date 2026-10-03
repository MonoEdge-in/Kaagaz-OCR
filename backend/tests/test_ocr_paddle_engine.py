"""PaddleVlEngine against a scripted fake of the /read contract: a stdlib HTTP
server on 127.0.0.1 (the pattern of tests/test_ai_client.py). Nothing waits on
a sleep: a reply that must be late waits on a threading.Event.
"""

import gc
import io
import json
import logging
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from PIL import Image

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading import ocr
from kaagaz.reading.ocr import OcrLine, OcrTable
from kaagaz.reading.spans import ReadError

VERSION = (
    "paddleocr=3.7.0;paddlex=3.7.2;paddle=3.3.1;vl=PaddleOCR-VL-1.6@c5630ab;layout=PP-DocLayoutV3@241f8bd"
)
CELL = {"kind": "table_cell", "text": "7,000.00", "order": 14, "box": [0.5, 0.25, 0.75, 0.5]}
TABLE = {"id": "t1", "row": 3, "col": 4, "header": False}
HEADING = {
    "kind": "heading",
    "text": "Tax Invoice",
    "order": 0,
    "box": [0.125, 0.0625, 0.5, 0.125],
    "table": None,
}


def good(**changes: Any) -> dict[str, Any]:
    reply = {"engine": "paddleocr-vl", "version": VERSION, "width": 200, "height": 100}
    return reply | {"blocks": [CELL | {"table": TABLE}, HEADING]} | changes


@dataclass
class Reply:
    status: int = 200
    body: object = field(default_factory=good)
    headers: dict[str, str] = field(default_factory=dict)
    hold: bool = False  # wait for the test before answering at all
    cut: bool = False  # say the full length, send half, wait for the test, then close
    trickle: str = ""  # "headers" or "body": one byte every 0.1 s, at most 60, then close


class FakeOcrServer:
    def __init__(self) -> None:
        self.url = ""
        self.replies: list[Reply] = []
        self.requests: list[tuple[str, str | None, bytes]] = []  # path, content type, body
        self.let_go = threading.Event()


@pytest.fixture
def fake() -> Iterator[FakeOcrServer]:
    server_fake = FakeOcrServer()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            server_fake.requests.append((self.path, self.headers["Content-Type"], body))
            reply = server_fake.replies.pop(0)
            if reply.hold:
                server_fake.let_go.wait(10)
            payload = reply.body if isinstance(reply.body, bytes) else json.dumps(reply.body).encode()
            try:
                if reply.trickle:
                    self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                    if reply.trickle == "body":
                        self.wfile.write(b"Content-Length: 1000\r\n\r\n")
                    for _ in range(60):  # paced by the Event, which the test's end sets
                        if server_fake.let_go.wait(0.1):
                            return
                        self.wfile.write(b"a")
                    return
                self.send_response(reply.status)
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if reply.cut:
                    self.wfile.write(payload[: len(payload) // 2])
                    self.wfile.flush()
                    server_fake.let_go.wait(10)
                else:
                    self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # the engine gave up first

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    server_fake.url = f"http://127.0.0.1:{server.server_port}"
    yield server_fake
    server_fake.let_go.set()
    server.shutdown()
    server.server_close()
    thread.join()


def page(width: int = 200, height: int = 100) -> Image.Image:
    return Image.new("RGB", (width, height), "white")


def engine(fake: FakeOcrServer, timeout: float = 5.0) -> "ocr.PaddleVlEngine":
    return ocr.PaddleVlEngine(fake.url, timeout_seconds=timeout)


def read_error(fake: FakeOcrServer, reply: Reply) -> str:
    fake.replies.append(reply)
    with pytest.raises(ReadError) as caught:
        engine(fake).read(page())
    assert caught.value.__context__ is None  # nothing chained that could quote the reply
    return caught.value.code


def test_blocks_come_back_in_order_as_pixels_with_kind_and_table(fake: FakeOcrServer) -> None:
    fake.replies.append(Reply())
    paddle = engine(fake)
    assert paddle.version == "unknown"

    lines = paddle.read(page(200, 100))

    assert lines == [
        OcrLine("Tax Invoice", 25.0, 6.25, 100.0, 12.5, None, "heading"),
        OcrLine("7,000.00", 100.0, 25.0, 150.0, 50.0, None, "table_cell", OcrTable("t1", 3, 4, False)),
    ]
    assert (paddle.name, paddle.version) == ("paddleocr-vl", VERSION)
    ((path, content_type, body),) = fake.requests
    assert (path, content_type) == ("/read", "image/png")
    with Image.open(io.BytesIO(body)) as sent:
        assert (sent.format, sent.size) == ("PNG", (200, 100))


def test_a_confidence_in_the_reply_is_never_used(fake: FakeOcrServer) -> None:
    fake.replies.append(Reply(body=good(blocks=[HEADING | {"confidence": 0.99}])))
    (line,) = engine(fake).read(page())
    assert line.confidence is None


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (500, "ocr_failed"),  # read_failed: the model raised on this page, so asking again fails again
        (422, "corrupt_file"),
        (413, "image_too_large"),
        (400, "invalid_reader_output"),
        (404, "invalid_reader_output"),
    ],
)
def test_refusals_fail_the_document_with_a_code(fake: FakeOcrServer, status: int, code: str) -> None:
    error = "read_failed" if status == 500 else "any"
    assert read_error(fake, Reply(status, {"error": error})) == code
    assert len(fake.requests) == 1  # never asked again


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (503, "not_ready"),
        (503, "out_of_memory"),  # the GPU ran out: this page is fine, asked again later
        (500, "internal_error"),
        (500, "not json"),
        (502, "bad_gateway"),
        (504, "gateway_timeout"),
        (421, "misdirected"),
    ],  # 421: set-up, not the file
)
def test_a_server_that_cannot_read_now_is_unavailable(fake: FakeOcrServer, status: int, error: str) -> None:
    fake.replies.append(Reply(status, b"not json" if error == "not json" else {"error": error}))
    with pytest.raises(BackendUnavailable):
        engine(fake).read(page())
    assert len(fake.requests) == 1  # the job's own retry asks again, later


def test_a_busy_server_is_asked_again_within_the_deadline(
    fake: FakeOcrServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Busy: still on a page a worker gave up on. This page is fine, so no attempt is used up."""
    monkeypatch.setattr(ocr, "_BUSY_WAIT_SECONDS", 0.05)
    fake.replies += [Reply(503, {"error": "busy"}), Reply(503, {"error": "busy"}), Reply()]
    assert len(engine(fake).read(page())) == 2
    assert len(fake.requests) == 3


def test_a_server_busy_past_the_deadline_is_unavailable(
    fake: FakeOcrServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr, "_BUSY_WAIT_SECONDS", 0.05)
    fake.replies += [Reply(503, {"error": "busy"}) for _ in range(20)]
    started = time.monotonic()
    with pytest.raises(BackendUnavailable):
        engine(fake, timeout=0.5).read(page())
    assert time.monotonic() - started < 0.5 + 1.0  # one deadline for the whole call
    assert 2 <= len(fake.requests) < 20  # asked again, backing off


def test_a_redirect_is_refused_and_not_followed(fake: FakeOcrServer) -> None:
    moved = Reply(302, b"", {"Location": fake.url + "/elsewhere"})
    assert read_error(fake, moved) == "invalid_reader_output"
    assert len(fake.requests) == 1


def test_an_ocr_service_that_is_not_there_is_unavailable() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(BackendUnavailable):
        ocr.PaddleVlEngine(f"http://127.0.0.1:{port}", timeout_seconds=5).read(page())


@pytest.mark.parametrize("reply", [Reply(hold=True), Reply(cut=True)], ids=["no reply", "reply stalls"])
def test_a_reply_past_the_deadline_is_unavailable(fake: FakeOcrServer, reply: Reply) -> None:
    fake.replies.append(reply)
    with pytest.raises(BackendUnavailable):
        engine(fake, timeout=0.3).read(page())


@pytest.mark.parametrize("where", ["headers", "body"])
def test_a_reply_that_trickles_in_is_cut_at_the_deadline(fake: FakeOcrServer, where: str) -> None:
    """A byte every 0.1 s never trips a per-read timeout: only one deadline for the
    whole call stops it, within the timeout and a small margin."""
    fake.replies.append(Reply(trickle=where))
    started = time.monotonic()
    with pytest.raises(BackendUnavailable):
        engine(fake, timeout=0.5).read(page())
    assert time.monotonic() - started < 0.5 + 1.0


def test_a_reply_cut_off_mid_body_is_unavailable(fake: FakeOcrServer) -> None:
    fake.replies.append(Reply(cut=True))
    fake.let_go.set()  # the server closes the connection after half the body
    with pytest.raises(BackendUnavailable):
        engine(fake).read(page())


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        [],
        good(width=201),
        good(height="100"),
        good(engine=None),
        good(version="3.7<script>"),
        good(version="v" * 201),
        good(blocks={}),
        good(blocks=["x"]),
        good(blocks=[HEADING | {"kind": "banner"}]),
        good(blocks=[HEADING | {"text": ""}]),
        good(blocks=[HEADING | {"text": 7}]),
        good(blocks=[HEADING | {"order": True}]),
        good(blocks=[HEADING | {"box": [0.1, 0.1, 0.5]}]),
        good(blocks=[HEADING | {"box": [0.1, 0.1, 1.5, 0.5]}]),
        good(blocks=[HEADING | {"box": [0.5, 0.1, 0.5, 0.2]}]),
        b'{"engine": "paddleocr-vl", "version": "1", "width": 200, "height": 100, "blocks":'
        b' [{"kind": "heading", "text": "x", "order": 0, "box": [NaN, 0.1, 0.5, 0.2], "table": null}]}',
        good(blocks=[CELL | {"table": TABLE | {"id": "t 1"}}]),
        good(blocks=[CELL | {"table": TABLE | {"row": -1}}]),
        good(blocks=[CELL | {"table": TABLE | {"header": "no"}}]),
        good(blocks=[CELL | {"table": [3, 4]}]),
        # A crafted reply must never crash the worker (council): every field's type is checked.
        good(engine=7),
        good(blocks=[HEADING | {"kind": ["heading"]}]),  # unhashable: not even a set lookup
        good(blocks=[HEADING | {"kind": {"heading": 1}}]),
        good(blocks=[HEADING | {"kind": 7}]),
        good(blocks=[HEADING | {"text": "Total \ud800 500"}]),  # a lone surrogate cannot be stored
        good(blocks=[HEADING | {"text": "\udfff"}]),
        good(blocks=[HEADING | {"order": 1.5}]),
        good(blocks=[HEADING | {"order": "0"}]),
        good(blocks=[HEADING | {"box": {"x0": 0.1}}]),
        good(blocks=[HEADING | {"box": ["0.1", 0.1, 0.5, 0.2]}]),
        good(blocks=[HEADING | {"box": [True, 0.1, 0.5, 0.2]}]),
        good(blocks=[HEADING | {"box": [0.1, 0.1, float("inf"), 0.2]}]),
        good(blocks=[HEADING | {"box": [0.1, 0.1, 10**400, 0.2]}]),  # too big for a float
        good(blocks=[CELL | {"table": TABLE | {"id": ["t1"]}}]),
        good(blocks=[CELL | {"table": TABLE | {"id": ""}}]),
        good(blocks=[CELL | {"table": TABLE | {"row": 1.0}}]),
        good(blocks=[CELL | {"table": TABLE | {"col": "2"}}]),
        good(blocks=[CELL | {"table": TABLE | {"row": True}}]),
        good(blocks=[CELL | {"table": TABLE | {"header": None}}]),
    ],
)
def test_a_malformed_reply_is_invalid_output(fake: FakeOcrServer, body: object) -> None:
    assert read_error(fake, Reply(body=body)) == "invalid_reader_output"


def test_a_reply_over_the_cap_is_invalid_output(fake: FakeOcrServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "MAX_OCR_REPLY_BYTES", 100)
    assert read_error(fake, Reply()) == "invalid_reader_output"


def test_over_limit_items_are_dropped_and_counted(
    fake: FakeOcrServer, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(ocr, "MAX_BLOCK_CHARS", 11)
    monkeypatch.setattr(ocr, "MAX_BLOCKS_PER_PAGE", 4)
    blocks = [
        HEADING,
        HEADING | {"text": "Twelve chars"},  # over MAX_BLOCK_CHARS
        CELL | {"table": TABLE | {"row": 1000}},  # rows and columns stay below 1000
        HEADING | {"text": "‮\x07"},  # nothing left once control characters go
        HEADING | {"text": "fifth"},  # past MAX_BLOCKS_PER_PAGE
    ]
    fake.replies.append(Reply(body=good(blocks=blocks)))
    assert [line.text for line in engine(fake).read(page())] == ["Tax Invoice"]
    (record,) = [r for r in caplog.records if r.name == "kaagaz.reading.ocr"]
    assert (record.getMessage(), record.__dict__["code"]) == (
        "ocr reply items dropped: 4",
        "ocr_reply_capped",
    )
    assert "Twelve" not in caplog.text


def test_control_and_bidi_characters_are_stripped(fake: FakeOcrServer) -> None:
    fake.replies.append(Reply(body=good(blocks=[HEADING | {"text": "Total‮ 500\x07\x85"}])))
    assert [line.text for line in engine(fake).read(page())] == ["Total 500"]


def test_a_bogus_proxy_is_ignored(fake: FakeOcrServer, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    fake.replies.append(Reply())
    assert len(engine(fake).read(page())) == 2


def test_http_errors_are_closed(fake: FakeOcrServer) -> None:
    """Every failing reply closes its connection: under filterwarnings=error, a
    leaked socket's ResourceWarning at collection fails this test."""
    for reply in (Reply(422), Reply(413), Reply(503), Reply(302, b""), Reply(400), Reply(body=b"[")):
        fake.replies.append(reply)
        with pytest.raises((ReadError, BackendUnavailable)):
            engine(fake).read(page())
    gc.collect()


def test_no_page_text_is_logged(fake: FakeOcrServer, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fake.replies += [Reply(), Reply(body=good(width=1))]
    engine(fake).read(page())
    with pytest.raises(ReadError):
        engine(fake).read(page())
    assert "Tax Invoice" not in caplog.text
    assert "7,000.00" not in caplog.text


def test_the_url_must_be_http() -> None:
    with pytest.raises(ValueError, match="http"):
        ocr.PaddleVlEngine("file:///etc/passwd", timeout_seconds=5)
