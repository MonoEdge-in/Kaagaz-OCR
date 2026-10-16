"""The Gemini reader over the real OpenRouter client against the loopback fake only."""

import base64
import io
from collections.abc import Iterator

import pytest
from PIL import Image

from kaagaz.ai.openrouter import ModelGate, OpenRouterClient
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading.gemini_ocr import GeminiEngine
from kaagaz.reading.ocr import as_page_engine
from kaagaz.reading.page_quality import READER_REFUSED
from tests.openrouter_fake import (
    KEY,
    OpenRouterFake,
    Recorded,
    Reply,
    chat_reply,
    error_reply,
    json_reply,
    no_credits,
    refused_403,
    running_fake,
    usage,
)
from tests.settings_factory import make_settings
from tests.test_gemini_ocr import FakeFinder, FakeRapidOcr, line, page, value

MODEL = "google/gemini-3.1-pro-preview"


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        yield server


def engine(fake: OpenRouterFake) -> GeminiEngine:
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
    return GeminiEngine(settings, client=client, pool=FakeFinder(), local=lambda: local)


def test_g1_openrouter_success_reads_a_page_through_the_gemini_engine(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply(value(line("Invoice total 5000"))))
    read = engine(fake).read_page(page(), page_no=1)
    assert [item.text for item in read.lines] == ["Invoice total 5000"]
    assert read.read_by == MODEL
    assert len(fake.requests) == 1
    assert fake.unanswered == 0


def test_q7_openrouter_key_failure_waits_without_using_the_local_reader(fake: OpenRouterFake) -> None:
    fake.script("completions", no_credits())
    reader = engine(fake)
    with pytest.raises(BackendUnavailable, match="gemini_key"):
        reader.read_page(page(), page_no=1)
    assert len(fake.requests) == 1


@pytest.mark.parametrize("reply", [refused_403(), error_reply(400)])
def test_g6_openrouter_refusal_or_bad_request_is_read_locally_and_flagged(
    fake: OpenRouterFake, reply: Reply
) -> None:
    fake.script("completions", reply)
    read = engine(fake).read_page(page(), page_no=1)
    assert read.read_by == "rapidocr"
    assert read.issues[0] == READER_REFUSED


def test_g6_openrouter_truncation_reads_halves_and_joins_them(fake: OpenRouterFake) -> None:
    # Changed on purpose (fast scan, council 16 Oct 2026): the halves are asked at the same
    # time, so each is answered by what it shows (the page's top rows are black), not by turn.
    cut = chat_reply("cut off", finish="length", native="MAX_TOKENS", counts=usage(10, 20, 0))
    fake.script("completions", cut)

    def by_half(request: Recorded) -> Reply:
        url = request.body["messages"][0]["content"][1]["image_url"]["url"]
        with Image.open(io.BytesIO(base64.b64decode(url.partition(",")[2]))) as half:
            top = half.convert("L").getpixel((0, 0)) == 0
        return json_reply(value(line("top half" if top else "bottom half")))

    fake.responder = by_half
    picture = page()
    picture.paste((0, 0, 0), (0, 0, picture.width, 100))
    read = engine(fake).read_page(picture, page_no=1)
    assert [item.text for item in read.lines] == ["top half", "bottom half"]
    assert len(fake.requests) == 3
    assert read.read_by == MODEL


def test_g3_invalid_json_after_repair_is_read_locally_with_both_calls_billed(
    fake: OpenRouterFake,
) -> None:
    fake.script(
        "completions",
        chat_reply("not JSON", counts=usage(11, 13, 0)),
        chat_reply("still not JSON", counts=usage(17, 19, 0)),
    )
    read = engine(fake).read_page(page(), page_no=1)
    assert read.read_by == "rapidocr"
    assert read.issues[0] == READER_REFUSED
    assert (read.input_tokens, read.output_tokens) == (28, 32)
    assert len(fake.requests) == 2
