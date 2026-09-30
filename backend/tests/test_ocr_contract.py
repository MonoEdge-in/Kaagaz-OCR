"""The worker's engine against the real server app, over real HTTP.

A stdlib HTTP server on 127.0.0.1 hands every request to the OCR server app
(through FastAPI's TestClient, with FakePageModel in place of PaddleOCR-VL) and
writes its reply back. So PaddleVlEngine, the readers and the server's own code
meet exactly as in Docker, minus the model.
"""

import io
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from kaagaz.ingestion import sniff
from kaagaz.ocr_server.app import RawBlock, create_app
from kaagaz.reading.ocr import OcrLine, OcrTable, PaddleVlEngine
from kaagaz.reading.wiring import build_reader_socket
from tests.pdf_builder import Page, Picture, make_pdf
from tests.picture_support import text_like_picture
from tests.settings_factory import make_settings
from tests.test_ocr_server import TABLE, VERSION, FakePageModel


@dataclass
class Bridge:
    model: FakePageModel
    url: str = ""
    bodies: list[bytes] = field(default_factory=list)  # what the engine sent, as it arrived


@pytest.fixture
def bridge() -> Iterator[Bridge]:
    link = Bridge(FakePageModel())
    with TestClient(create_app(link.model), base_url="http://ocr:8080") as client:
        assert link.model.loaded.wait(5)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                link.bodies.append(body)
                reply = client.post(
                    self.path, content=body, headers={"Content-Type": self.headers["Content-Type"]}
                )
                self.send_response(reply.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply.content)))
                self.end_headers()
                self.wfile.write(reply.content)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        link.url = f"http://127.0.0.1:{server.server_port}"
        yield link
        server.shutdown()
        server.server_close()
        thread.join()


def test_engine_reads_the_real_server_app(bridge: Bridge) -> None:
    bridge.model.blocks = [
        RawBlock("paragraph_title", "Tax Invoice", (20, 5, 100, 15), 0),
        RawBlock("table", TABLE, (20, 40, 180, 80), 1),
    ]
    paddle = PaddleVlEngine(bridge.url, timeout_seconds=5)

    lines = paddle.read(Image.new("RGB", (200, 100), "white"))

    cell_box = (20.0, 40.0, 180.0, 80.0)
    assert lines == [
        OcrLine("Tax Invoice", 20.0, 5.0, 100.0, 15.0, None, "heading"),
        *(
            OcrLine(text, *cell_box, None, "table_cell", OcrTable("t1", row, col, False))
            for row, col, text in [(0, 0, "Item"), (0, 1, "Amount"), (1, 0, "Seeds"), (1, 1, "7,000.00")]
        ),
    ]
    assert paddle.version == VERSION


def test_the_request_is_a_png_of_the_page_at_ocr_dpi(bridge: Bridge) -> None:
    settings = make_settings(ocr_engine="paddleocr_vl", ocr_url=bridge.url, ocr_dpi=300, page_image_dpi=150)
    socket = build_reader_socket(settings, PaddleVlEngine(settings.ocr_url, timeout_seconds=5))

    scan = Page(picture=Picture(text_like_picture(153, 198), 0, 0, 612, 792))
    spans = socket.read(sniff.PDF, make_pdf([scan]))  # a US letter page, 612 x 792 points, no text layer

    # 612 and 792 points x 300 / 72 = 2550 x 3300 (not PAGE_IMAGE_DPI's 1275 x 1650); pdfium
    # rounds 792 x (300 / 72) = 3300.0000000000005 up, so one pixel either way is its rounding.
    ((size, mode),) = bridge.model.images
    assert mode == "RGB"
    assert abs(size[0] - 2550) <= 1 and abs(size[1] - 3300) <= 1
    assert [(span.text, span.origin, span.kind) for span in spans] == [("Total 500", "ocr", "paragraph")]
    assert socket.reader_id(sniff.PDF)[1].endswith(f"+paddleocr-vl-{VERSION}")  # named after the reply


def test_the_worker_sends_only_a_png_it_encoded_itself(bridge: Bridge) -> None:
    """Security A5.8: never the uploaded bytes, whatever they carry."""
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "kaagaz-marker-from-the-upload")
    upload = io.BytesIO()
    text_like_picture(120, 80).save(upload, format="PNG", pnginfo=info)
    settings = make_settings(ocr_engine="paddleocr_vl", ocr_url=bridge.url)
    socket = build_reader_socket(settings, PaddleVlEngine(settings.ocr_url, timeout_seconds=5))

    socket.read(sniff.PNG, upload.getvalue())

    (sent,) = bridge.bodies
    assert sent != upload.getvalue()
    assert b"kaagaz-marker" not in sent
    with Image.open(io.BytesIO(sent)) as picture:
        assert (picture.format, picture.size, picture.mode) == ("PNG", (120, 80), "RGB")
