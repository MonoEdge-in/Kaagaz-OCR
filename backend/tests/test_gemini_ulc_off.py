"""Ultra low cost switched off (#488): every request body, page cache key and reading is
byte-for-byte what it was before Ultra low cost existed.

The fingerprints below were taken on the code before #488 (commit 4a9aa1a1), through the real
OpenRouter client against the loopback fake: a first read, a read again and a cached read of one
page, with a table cell, unsure words, two blocks and a cut-off reply read in halves. The image
is checked to be ``png_of`` the page and left out of the fingerprint, so a Pillow upgrade can't
change it; everything else in each body is in it."""

import base64
import hashlib
import io
import json
from typing import Any

from PIL import Image

from kaagaz.ai.openrouter import ModelGate, OpenRouterClient
from kaagaz.reading.gemini_ocr import GeminiEngine, png_of
from kaagaz.reading.ocr import DocumentPages, as_page_engine
from tests.openrouter_fake import (
    KEY,
    OpenRouterFake,
    Recorded,
    Reply,
    chat_reply,
    json_reply,
    running_fake,
    usage,
)
from tests.settings_factory import make_settings
from tests.test_dynamic_reading import ledger
from tests.test_gemini_ocr import CUSTOMER, DOCUMENT, FakeCache, FakeFinder, FakeRapidOcr, line, value

MODEL = "google/gemini-3.1-pro-preview"
# sha256 of the scenario's bodies, keys, cache rows and reads, on the code before #488.
BEFORE_ULC = "7667e045e3d3dfaf541e0f8919f8003633c6b9311f193de7564399beb0a182d6"
# #488 review (R10): sha256 of a repair, a retry after a reply that failed part way, and their
# ledger rows, on the code before #488 (commit 4a9aa1a1).
BEFORE_ULC_REPAIR_AND_RETRY = "5c6cbf1bec977d5ae5a3434fdeef151de0e345bb0ac057668e524fd53842bd5b"


def _reply_value() -> dict[str, Any]:
    return value(
        line("Survey No. 42", (100, 100, 120, 500), order=0, kind="title", block=0),
        line(
            "Area",
            (130, 100, 150, 300),
            order=1,
            kind="table_cell",
            block=1,
            table={"id": "t1", "row": 0, "col": 0, "header": True},
        ),
        line(
            "2.5 ha",
            (130, 300, 150, 500),
            order=2,
            kind="table_cell",
            block=1,
            unsure=("2.5",),
            table={"id": "t1", "row": 0, "col": 1, "header": False},
        ),
        scripts=("latin", "devanagari"),
        issues=("faint",),
    )


def fingerprint() -> tuple[str, int]:
    """(the scenario's sha256, how many requests it sent)."""
    with running_fake() as fake:
        settings = make_settings(
            openrouter_api_key=KEY,
            openrouter_base_url=fake.url,
            ocr_model=MODEL,
            ocr_timeout_seconds=5.0,
            model_timeout_seconds=5.0,
            model_rpm=10**9,
        )
        local = as_page_engine(FakeRapidOcr())
        client = OpenRouterClient(settings, gate=ModelGate(settings, workers=1))
        engine = GeminiEngine(
            settings, client=client, pool=FakeFinder((100, 200, 500, 240)), local=lambda: local
        )
        cut = chat_reply("cut off", finish="length", native="MAX_TOKENS", counts=usage(10, 20, 0))
        fake.script(
            "completions",
            json_reply(_reply_value(), counts=usage(1700, 900, 300, 0.001)),
            json_reply(_reply_value(), counts=usage(1800, 950, 310)),
            cut,
        )

        def by_half(request: Recorded) -> Reply:  # the halves are asked at once: answered by what each shows
            url = request.body["messages"][0]["content"][1]["image_url"]["url"]
            with Image.open(io.BytesIO(base64.b64decode(url.partition(",")[2]))) as half:
                top = half.convert("L").getpixel((0, 0)) == 0
            if top:
                return json_reply(value(line("top")), counts=usage(5, 6, 0))
            return json_reply(value(line("bottom")), counts=usage(7, 8, 0))

        fake.responder = by_half
        image = Image.new("RGB", (1000, 2000), "white")
        cache = FakeCache()
        reads = []
        first = DocumentPages(customer_id=CUSTOMER, document_id=DOCUMENT, cache=cache, page_count=3)
        with engine.document(first):
            reads.append(engine.read_page(image, page_no=1))
            reads.append(engine.read_page(image, page_no=1))  # cached
        again = DocumentPages(
            customer_id=CUSTOMER, document_id=DOCUMENT, cache=cache, read_again=frozenset({1}), page_count=3
        )
        with engine.document(again):
            reads.append(engine.read_page(image, page_no=1))
        other = Image.new("RGB", (1000, 2000), "white")
        other.paste((0, 0, 0), (0, 0, other.width, 100))
        with engine.document(first):
            reads.append(engine.read_page(other, page_no=2))  # cut off: halves
        bodies = fake.bodies()
        whole = "data:image/png;base64," + base64.b64encode(png_of(image)).decode("ascii")
        assert bodies[0]["messages"][0]["content"][1]["image_url"]["url"] == whole
        for body in bodies:
            for message in body["messages"]:
                if isinstance(message["content"], list):
                    message["content"][1]["image_url"]["url"] = "<png>"
        scenario = {
            "bodies": bodies,
            "gets": [repr(key) for key in cache.gets],
            "puts": [repr(key) for key in cache.puts],
            "rows": [[repr(key), row] for key, row in cache.rows.items()],
            "reads": [repr(read) for read in reads],
            "version": engine.version,
        }
        text = json.dumps(scenario, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(bodies)


def _bodies_without_png(fake: OpenRouterFake) -> list[Any]:
    bodies = fake.bodies()
    for body in bodies:
        for message in body["messages"]:
            if isinstance(message["content"], list):
                message["content"][1]["image_url"]["url"] = "<png>"
    return bodies


def fingerprint_repair_and_retry() -> tuple[str, int]:
    """#488 review (R10): (the sha256 of a repaired page and a page whose first reply failed part
    way, retried: their bodies, reads and ledger rows; how many requests were sent)."""
    with running_fake() as fake:
        settings = make_settings(
            openrouter_api_key=KEY,
            openrouter_base_url=fake.url,
            ocr_model=MODEL,
            ocr_timeout_seconds=5.0,
            model_timeout_seconds=5.0,
            model_rpm=10**9,
        )
        local = as_page_engine(FakeRapidOcr())
        gate = ModelGate(settings, workers=1, sleep=lambda _seconds: None, random=lambda: 0.0)
        engine = GeminiEngine(
            settings,
            client=OpenRouterClient(settings, gate=gate),
            pool=FakeFinder((100, 200, 500, 240)),
            local=lambda: local,
        )
        fake.script(
            "completions",
            chat_reply("not JSON", counts=usage(1700, 40, 10, 0.0004)),  # repaired
            json_reply(_reply_value(), counts=usage(1750, 900, 300, 0.001)),
            chat_reply("", finish="error", native=None, counts=usage(1700, 100, 50)),  # billed, retried
            json_reply(_reply_value(), counts=usage(1700, 950, 310, 0.0011)),
        )
        image = Image.new("RGB", (1000, 2000), "white")
        other = Image.new("RGB", (1000, 2000), "white")
        other.paste((0, 0, 0), (0, 0, other.width, 100))
        reads = []
        with ledger() as kept:
            pages = DocumentPages(customer_id=CUSTOMER, document_id=DOCUMENT, cache=FakeCache(), page_count=2)
            with engine.document(pages):
                reads.append(engine.read_page(image, page_no=1))
                reads.append(engine.read_page(other, page_no=2))
        bodies = _bodies_without_png(fake)
        scenario = {
            "bodies": bodies,
            "reads": [repr(read) for read in reads],
            "ledger": [repr(row) for row in kept.rows],
            "live": kept.live,
        }
        text = json.dumps(scenario, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(bodies)


def test_488_ulc_off_bodies_keys_and_readings_are_byte_identical_to_before() -> None:
    digest, sent = fingerprint()
    assert sent == 5
    assert digest == BEFORE_ULC


def test_488_ulc_off_a_repair_a_retry_and_the_ledger_are_byte_identical_to_before() -> None:
    digest, sent = fingerprint_repair_and_retry()
    assert sent == 4
    assert digest == BEFORE_ULC_REPAIR_AND_RETRY
