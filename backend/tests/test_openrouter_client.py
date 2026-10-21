"""The model client through OpenRouter (PRD 5 G2, G3, G6, G9; council 8 Oct 2026) against a
fake OpenRouter API on 127.0.0.1.

Never the real API: ``tests/openrouter_fake.py`` scripts every reply. A fake clock
(``Clock``) drives deadlines, backoff, the limiter, the key latch and the circuit breaker,
so no test sleeps for real except the ones about a slow reply.
"""

import base64
import json
import logging
import threading
import time
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from kaagaz.ai import openrouter, replies
from kaagaz.ai.client import ModelOutputError, ModelUnavailable, build_model_client
from kaagaz.ai.openrouter import (
    BACKOFF_MAX_SECONDS,
    BREAKER_FAILURES,
    BREAKER_OPEN_SECONDS,
    CHAT_THINKING_TOKENS,
    EMBED_PROVIDER,
    KEY_LATCH_SECONDS,
    LIMITER_WAIT,
    PAGE_MAX_OUTPUT_TOKENS,
    PROVIDER,
    STRICT_SCHEMA,
    ModelGate,
    OpenRouterClient,
    PageReply,
    Usage,
    page_request,
    process_gate,
)
from kaagaz.settings import NO_THINKING, Settings
from kaagaz.worker_main import worker_model
from tests import openrouter_fake
from tests.openrouter_fake import (
    KEY,
    SECRET,
    OpenRouterFake,
    Recorded,
    Reply,
    chat_reply,
    closed_port_url,
    content_filtered,
    embed_reply,
    endpoints_reply,
    error_reply,
    failed_part_way,
    json_reply,
    key_reply,
    moderated_403,
    no_credits,
    oversized,
    rate_limited,
    redirect_to,
    refused_403,
    running_fake,
    slow,
    trickled,
    usage,
)
from tests.settings_factory import make_settings

PNG = b"\x89PNG made-up page"
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "not_found"]},
        "lines": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "lines"],
}
GOOD = {"status": "answered", "lines": ["Total 5,000"]}
PRO = "google/gemini-3.1-pro-preview"
EMBED = "google/gemini-embedding-2"
THE_PROVIDER_BLOCK = {
    "only": ["google-vertex"],
    "allow_fallbacks": False,
    "require_parameters": True,
    "data_collection": "deny",
    "zdr": True,
}
THE_EMBED_PROVIDER_BLOCK = {**THE_PROVIDER_BLOCK, "require_parameters": False}


class Clock:
    """A fake monotonic clock: ``sleep`` moves it forward and is recorded."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def backoffs(self) -> list[float]:
        """The sleeps that are backoff, not the limiter's microsecond spacing."""
        return [seconds for seconds in self.sleeps if seconds >= 0.01]


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        yield server


def _settings(url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "model_enabled": True,
        "model_chat": PRO,
        "model_embed": EMBED,
        "openrouter_api_key": KEY,
        "openrouter_base_url": url,
        "embedding_dim": 4,
        "model_rpm": 10**9,  # the limiter's spacing is a few nanoseconds unless a test sets it
        "model_timeout_seconds": 5.0,
    }
    return make_settings(**{**values, **overrides})


def _client(url: str, clock: Clock | None = None, *, workers: int = 1, **overrides: Any) -> OpenRouterClient:
    settings = _settings(url, **overrides)
    if clock is None:
        return OpenRouterClient(settings, gate=ModelGate(settings, workers=workers))
    gate = ModelGate(
        settings,
        workers=workers,
        clock=clock,
        sleep=clock.sleep,
        random=lambda: 0.5,
        wall_clock=lambda: datetime(2026, 10, 8, 10, 0, 0, tzinfo=UTC),
    )
    return OpenRouterClient(settings, gate=gate)


def _read(client: OpenRouterClient, *, deadline: float | None = None, **options: Any) -> PageReply:
    values: dict[str, Any] = {
        "png": PNG,
        "instruction": "Copy the page.",
        "schema": SCHEMA,
        "media_resolution": "high",
        "thinking_level": "low",
        "deadline": client.now() + 120 if deadline is None else deadline,
    }
    return client.read_page(**{**values, **options})


# The request (G2, G3).


def test_read_page_sends_the_instruction_then_the_image_with_its_detail(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply(GOOD, counts=usage(1500, 500, 200)))

    page = _read(_client(fake.url, Clock()))

    assert page == PageReply(GOOD, 1500, 500, "STOP")
    (request,) = fake.requests
    assert (request.method, request.path) == ("POST", "/api/v1/chat/completions")
    assert request.headers["content-type"] == "application/json"
    assert request.body == {
        "model": PRO,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Copy the page."},
                    {"type": "image_url", "image_url": {"url": DATA_URL, "detail": "high"}},
                ],
            }
        ],
        "stream": False,
        "reasoning": {"effort": "low", "exclude": True},
        "provider": THE_PROVIDER_BLOCK,
        "max_completion_tokens": PAGE_MAX_OUTPUT_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "page", "strict": STRICT_SCHEMA, "schema": SCHEMA},
        },
    }
    assert "temperature" not in json.dumps(request.body)  # the default (section 14)


def test_the_key_goes_in_its_bearer_header_and_never_in_the_url_or_body(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply(GOOD))
    fake.script("embeddings", embed_reply([[0.1, 0.2, 0.3, 0.4]]))
    client = _client(fake.url, Clock())
    _read(client)
    client.embed(["one"])
    fake.assert_key_in_header_only(KEY)
    assert all("key=" not in request.path for request in fake.requests)


def test_no_attribution_header_is_sent(fake: OpenRouterFake) -> None:
    """Security (council 8 Oct 2026): no HTTP-Referer or X-Title tells OpenRouter who we are."""
    fake.script("completions", json_reply(GOOD), json_reply(GOOD))
    fake.script("embeddings", embed_reply([[0.1, 0.2, 0.3, 0.4]]))
    client = _client(fake.url, Clock())
    _read(client)
    client.chat_json(system="s", user="u", schema=SCHEMA)
    client.embed(["one"])
    for request in fake.requests:
        assert not {"http-referer", "referer", "x-title"} & set(request.headers)


def test_every_request_kind_carries_the_provider_block(fake: OpenRouterFake) -> None:
    """Google Vertex only, no fallback, every parameter honoured, no data collection and
    zero data retention: on read_page, its repair, chat_json and embed."""
    assert dict(PROVIDER) == THE_PROVIDER_BLOCK
    fake.script("completions", chat_reply("not json"), json_reply(GOOD), json_reply(GOOD))
    fake.script("embeddings", embed_reply([[0.1, 0.2, 0.3, 0.4]]))
    client = _client(fake.url, Clock())
    _read(client)
    client.chat_json(system="s", user="u", schema=SCHEMA)
    client.embed(["one"])
    assert [request.action for request in fake.requests] == ["completions"] * 3 + ["embeddings"]
    for request in fake.requests[:3]:
        assert request.body["provider"] == THE_PROVIDER_BLOCK
    assert fake.requests[3].body["provider"] == THE_EMBED_PROVIDER_BLOCK


def test_embeddings_keep_vertex_only_and_zero_retention_without_requiring_parameters() -> None:
    """OpenRouter lists no supported parameters for embedding endpoints, so only
    require_parameters is dropped there; every privacy rule stays."""
    assert dict(EMBED_PROVIDER) == THE_EMBED_PROVIDER_BLOCK
    for key in ("only", "allow_fallbacks", "data_collection", "zdr"):
        assert EMBED_PROVIDER[key] == PROVIDER[key]


def test_a_request_body_cannot_change_the_shared_provider_block(fake: OpenRouterFake) -> None:
    body = page_request(
        model=PRO,
        png=PNG,
        instruction="x",
        schema=None,
        media_resolution="low",
        thinking="low",
        max_output_tokens=None,
    )
    body["provider"]["only"].append("someone-else")
    assert dict(PROVIDER) == THE_PROVIDER_BLOCK


@pytest.mark.parametrize(
    ("resolution", "thinking"), [("low", "medium"), ("high", "high"), ("low", "minimal")]
)
def test_the_detail_and_the_reasoning_effort_are_the_settings(
    fake: OpenRouterFake, resolution: str, thinking: str
) -> None:
    """F5: "Read this page again" sends its own pair, as the settings say."""
    fake.script("completions", json_reply(GOOD))
    _read(_client(fake.url, Clock()), media_resolution=resolution, thinking_level=thinking)
    body = fake.bodies()[0]
    assert body["messages"][0]["content"][1]["image_url"]["detail"] == resolution
    assert body["reasoning"] == {"effort": thinking, "exclude": True}


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("media_resolution", "ultra", "unknown"),
        ("thinking_level", "max", "unknown"),
        ("media_resolution", "ultra_high", "cannot be sent"),
        ("media_resolution", "medium", "cannot be sent"),
    ],
)
def test_a_level_that_cannot_be_sent_is_refused_before_any_request(
    fake: OpenRouterFake, option: str, value: str, message: str
) -> None:
    """Never a quiet downgrade: ultra_high is not sent as high."""
    options: dict[str, Any] = {option: value}
    with pytest.raises(ValueError, match=message):
        _read(_client(fake.url, Clock()), **options)
    assert fake.requests == []


def test_a_page_request_may_leave_the_detail_out_for_verify() -> None:
    body = page_request(
        model=PRO,
        png=PNG,
        instruction="x",
        schema=SCHEMA,
        media_resolution=None,
        thinking="low",
        max_output_tokens=16,
        strict=False,
    )
    assert body["messages"][0]["content"][1] == {"type": "image_url", "image_url": {"url": DATA_URL}}
    assert body["response_format"]["json_schema"]["strict"] is False
    assert body["max_completion_tokens"] == 16


def test_a_model_with_no_thinking_levels_gets_no_reasoning_field_and_others_still_do() -> None:
    """Tushar, 18 Oct 2026: NO_THINKING sends no reasoning at all; any other level sends it."""
    options: dict[str, Any] = {"model": PRO, "png": PNG, "instruction": "x", "schema": SCHEMA}
    options |= {"media_resolution": "high", "max_output_tokens": 16}
    assert "reasoning" not in page_request(**options, thinking=NO_THINKING)
    assert page_request(**options, thinking="high")["reasoning"] == {"effort": "high", "exclude": True}


def test_chat_json_with_no_thinking_levels_sends_no_reasoning_and_no_room_to_think(
    fake: OpenRouterFake,
) -> None:
    fake.script("completions", json_reply(GOOD))
    client = _client(fake.url, Clock())
    assert client.chat_json(system="s", user="u", schema=SCHEMA, max_tokens=512, thinking=NO_THINKING) == GOOD
    (body,) = fake.bodies()
    assert "reasoning" not in body and body["max_completion_tokens"] == 512


def test_usage_counts_prompt_and_completion_tokens_and_sums_the_cost(fake: OpenRouterFake) -> None:
    """G9: input is prompt_tokens, output is completion_tokens (thinking inside it)."""
    fake.script(
        "completions",
        json_reply(GOOD, counts=usage(1700, 4000, 3600, cost=0.0125)),
        json_reply(GOOD, counts=usage(1200, 100, None, cost=0.0025)),  # no reasoning reported
    )
    client = _client(fake.url, Clock())
    first, second = _read(client), _read(client)
    assert (first.input_tokens, first.output_tokens) == (1700, 4000)
    assert (second.input_tokens, second.output_tokens) == (1200, 100)
    totals = client.usage()
    assert (totals.input_tokens, totals.output_tokens) == (2900, 4100)
    assert totals.cost == pytest.approx(0.015)


def test_usage_has_no_cost_when_no_reply_carries_one(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply(GOOD), json_reply(GOOD, counts={"prompt_tokens": 3}))
    client = _client(fake.url, Clock())
    _read(client)
    _read(client)
    assert client.usage() == Usage(1503, 500, None)


@pytest.mark.parametrize("cost", [-1.0, "0.01", True, float("inf")])
def test_a_cost_that_is_not_a_number_is_left_out(fake: OpenRouterFake, cost: object) -> None:
    counts: dict[str, Any] = {**usage(), "cost": cost}
    fake.script("completions", json_reply(GOOD, counts=counts))
    client = _client(fake.url, Clock())
    _read(client)
    assert client.usage().cost is None


def test_the_finish_reason_is_googles_native_one_when_given(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply(GOOD, native=None), json_reply(GOOD, native="STOP"))
    client = _client(fake.url, Clock())
    assert _read(client).finish_reason == "stop"
    assert _read(client).finish_reason == "STOP"


# Failures (G6).


def test_a_429_waits_for_retry_after_then_succeeds(fake: OpenRouterFake) -> None:
    fake.script("completions", rate_limited("7"), json_reply(GOOD))
    clock = Clock()
    assert _read(_client(fake.url, clock)).value == GOOD
    assert clock.backoffs() == [7.0]
    assert len(fake.requests) == 2


def test_retry_after_as_an_http_date_is_honoured(fake: OpenRouterFake) -> None:
    fake.script("completions", rate_limited("Thu, 08 Oct 2026 10:00:12 GMT"), json_reply(GOOD))
    clock = Clock()
    _read(_client(fake.url, clock))
    assert clock.backoffs() == [12.0]


@pytest.mark.parametrize("status", [500, 502, 503, 524, 529])
def test_server_errors_back_off_with_full_jitter_then_succeed(fake: OpenRouterFake, status: int) -> None:
    fake.script("completions", error_reply(status), error_reply(status), json_reply(GOOD))
    clock = Clock()
    assert _read(_client(fake.url, clock)).value == GOOD
    assert clock.backoffs() == [0.5, 1.0]  # random 0.5 times 1 s, then 2 s


def test_a_408_is_retried_then_succeeds(fake: OpenRouterFake) -> None:
    fake.script("completions", error_reply(408), json_reply(GOOD))
    clock = Clock()
    assert _read(_client(fake.url, clock)).value == GOOD
    assert clock.backoffs() == [0.5]


def test_a_reply_that_failed_part_way_is_retried_as_a_server_error(fake: OpenRouterFake) -> None:
    """finish_reason "error" on a 200: the retry path, counted by the breaker."""
    fake.script("completions", failed_part_way(), json_reply(GOOD))
    clock = Clock()
    assert _read(_client(fake.url, clock)).value == GOOD
    assert clock.backoffs() == [0.5]
    assert len(fake.requests) == 2


def test_backoff_never_waits_more_than_its_cap(fake: OpenRouterFake, monkeypatch: pytest.MonkeyPatch) -> None:
    assert BACKOFF_MAX_SECONDS == 30.0
    monkeypatch.setattr(openrouter, "BACKOFF_MAX_SECONDS", 3.0)
    fake.script("completions", *[error_reply(503)] * 4, json_reply(GOOD))
    clock = Clock()
    client = _client(fake.url, clock)
    client.gate.random = lambda: 1.0
    _read(client, deadline=clock.now + 1000)
    assert clock.backoffs() == [1.0, 2.0, 3.0, 3.0]


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (rate_limited(), "rate_limited"),
        (error_reply(503), "server_error"),
        (error_reply(529), "server_error"),
        (error_reply(408), "timeout"),
        (failed_part_way(), "server_error"),
    ],
)
def test_retries_stop_at_the_pages_deadline(fake: OpenRouterFake, reply: Reply, code: str) -> None:
    fake.responder = lambda request: reply
    clock = Clock()
    client = _client(fake.url, clock)
    client.gate.random = lambda: 1.0
    deadline = clock.now + 10
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=deadline)
    assert caught.value.code == code
    assert str(caught.value) == code
    assert clock.now < deadline  # it gave up instead of sleeping past the deadline
    assert 1 < len(fake.requests) <= 4


def test_a_retry_after_past_the_deadline_gives_up_at_once(fake: OpenRouterFake) -> None:
    fake.script("completions", rate_limited("600"))
    clock = Clock()
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, clock), deadline=clock.now + 120)
    assert caught.value.code == "rate_limited"
    assert len(fake.requests) == 1
    assert clock.backoffs() == []


@pytest.mark.parametrize("status", [400, 413, 422])
def test_a_bad_request_is_never_retried(fake: OpenRouterFake, status: int) -> None:
    fake.script("completions", error_reply(status, error_type="context_length_exceeded"))
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert (caught.value.code, str(caught.value)) == ("bad_request", "bad_request")
    assert len(fake.requests) == 1


def test_a_404_names_the_model_problem(fake: OpenRouterFake) -> None:
    fake.script("completions", error_reply(404))
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "model_not_found"


def test_any_other_status_is_an_http_error(fake: OpenRouterFake) -> None:
    fake.script("completions", error_reply(418))
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "http_error"


@pytest.mark.parametrize(
    ("reply", "logged"),
    [
        (error_reply(401, error_type="authentication"), "key_rejected"),
        (no_credits(), "no_credits"),
        (error_reply(403, error_type="permission_denied"), "key_rejected"),
        (error_reply(403), "key_rejected"),
    ],
)
def test_a_rejected_key_or_no_credits_latches_and_never_loops(
    fake: OpenRouterFake, caplog: pytest.LogCaptureFixture, reply: Reply, logged: str
) -> None:
    """G6: a wrong key or spent credit is logged at error once and fails fast, without a
    request, for KEY_LATCH_SECONDS; then one call tries again."""
    fake.script("completions", reply, json_reply(GOOD))
    clock = Clock()
    client = _client(fake.url, clock)
    caplog.set_level(logging.DEBUG)
    for _ in range(3):
        with pytest.raises(ModelUnavailable) as caught:
            _read(client)
        assert caught.value.code == "key_rejected"
    assert len(fake.requests) == 1
    assert client.status() == ("key_problem", 1000.0)
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert [getattr(record, "code", None) for record in errors] == [logged]

    clock.now += KEY_LATCH_SECONDS
    assert _read(client).value == GOOD
    assert len(fake.requests) == 2
    assert client.status() == ("working", None)


@pytest.mark.parametrize(
    "reply",
    [
        refused_403(),
        moderated_403(),
        error_reply(403, error_type="content_policy_violation"),
    ],
)
def test_a_403_refusal_or_moderation_is_blocked_and_never_latches(fake: OpenRouterFake, reply: Reply) -> None:
    fake.script("completions", reply, json_reply(GOOD))
    client = _client(fake.url, Clock())
    with pytest.raises(ModelOutputError) as caught:
        _read(client)
    assert caught.value.code == "blocked"
    assert client.status() == ("working", None)
    assert _read(client).value == GOOD  # the next page is read at once


@pytest.mark.parametrize(
    "reply",
    [
        content_filtered(),
        content_filtered(native=None),
        *[
            chat_reply("", finish="stop", native=native)
            for native in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY")
        ],
        chat_reply(None, refusal="I can't help with that."),
    ],
)
def test_a_blocked_reply_is_blocked_with_no_repair(fake: OpenRouterFake, reply: Reply) -> None:
    fake.script("completions", reply)
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "blocked"
    assert len(fake.requests) == 1


def test_a_reply_cut_at_the_length_limit_is_truncated_with_no_repair(fake: OpenRouterFake) -> None:
    fake.script(
        "completions",
        chat_reply('{"status": "answered", "lines": ["Total', finish="length", counts=usage(19, 23, 0)),
    )
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "output_truncated"
    assert (caught.value.input_tokens, caught.value.output_tokens) == (19, 23)
    assert len(fake.requests) == 1


def test_a_blocked_reply_keeps_its_billed_tokens(fake: OpenRouterFake) -> None:
    fake.script("completions", chat_reply(None, finish="content_filter", counts=usage(13, 17, 0)))
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert (caught.value.code, caught.value.input_tokens, caught.value.output_tokens) == ("blocked", 13, 17)


@pytest.mark.parametrize(
    "reply",
    [
        chat_reply(json.dumps(GOOD), finish="tool_calls"),
        chat_reply(json.dumps(GOOD), finish="something_new"),
        chat_reply(json.dumps(GOOD), finish=None, native=None),  # no finish reason
        chat_reply(None),  # stop with no content
        Reply(200, {"choices": [], "usage": usage()}),
        Reply(200, {"choices": [{"finish_reason": "stop", "message": {"content": 5}}]}),
        Reply(200, {"choices": [{"finish_reason": "stop", "message": "text"}]}),
        Reply(200, {"no": "choices"}),
    ],
)
def test_any_other_ending_is_invalid_output(fake: OpenRouterFake, reply: Reply) -> None:
    fake.script("completions", reply)
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "invalid_output"


def test_bad_json_is_repaired_once_with_the_page_again(fake: OpenRouterFake) -> None:
    fake.script(
        "completions",
        chat_reply("not json", counts=usage(1500, 100, 90)),
        json_reply(GOOD, counts=usage(1600, 400, 100)),
    )
    page = _read(_client(fake.url, Clock()))
    assert page == PageReply(GOOD, 3100, 500, "STOP")
    second = fake.bodies()[1]["messages"]
    assert [message["role"] for message in second] == ["user", "assistant", "user"]
    assert second[0]["content"][1]["image_url"]["url"] == DATA_URL
    assert second[1]["content"] == "not json"
    assert "the reply is not JSON" in second[2]["content"]


def _only_good(value: dict[str, Any]) -> str | None:
    return None if value == GOOD else "a line is not on the page"


def test_a_reply_the_callers_check_refuses_gets_the_one_repair(fake: OpenRouterFake) -> None:
    """R12: JSON that follows the schema but breaks the caller's own check is repaired
    once, with the check's words, like JSON that breaks the schema."""
    wrong = {**GOOD, "lines": ["Total 9,999"]}
    fake.script(
        "completions",
        json_reply(wrong, counts=usage(1500, 100, 90)),
        json_reply(GOOD, counts=usage(1600, 400, 100)),
    )
    page = _read(_client(fake.url, Clock()), validate=_only_good)
    assert page == PageReply(GOOD, 3100, 500, "STOP")
    second = fake.bodies()[1]["messages"]
    assert [message["role"] for message in second] == ["user", "assistant", "user"]
    assert "a line is not on the page" in second[2]["content"]


def test_still_refused_after_the_one_repair_is_invalid_output_with_both_calls_tokens(
    fake: OpenRouterFake,
) -> None:
    """R12: one repair at most, whichever check failed first."""
    wrong = {**GOOD, "lines": ["Total 9,999"]}
    fake.script(
        "completions",
        chat_reply("not json", counts=usage(1500, 100, 90)),
        json_reply(wrong, counts=usage(1600, 400, 100)),
    )
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()), validate=_only_good)
    assert (caught.value.code, caught.value.input_tokens, caught.value.output_tokens) == (
        "invalid_output",
        3100,
        500,
    )
    assert len(fake.requests) == 2


def test_two_bad_replies_are_invalid_output(fake: OpenRouterFake) -> None:
    fake.script("completions", chat_reply("[1]"), json_reply({"status": "answered"}))
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "invalid_output"
    assert len(fake.requests) == 2


def test_a_slow_reply_past_the_deadline_times_out(fake: OpenRouterFake) -> None:
    fake.script("completions", slow(json_reply(GOOD), 5))
    client = _client(fake.url)
    started = time.monotonic()
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=client.now() + 0.3)
    assert caught.value.code == "timeout"
    assert time.monotonic() - started < 3


def test_a_reply_that_keeps_trickling_in_still_times_out_at_the_deadline(fake: OpenRouterFake) -> None:
    """Stuck reading (16 Oct 2026): a space every 0.05 s keeps each socket read short, so
    only the whole reply's deadline can end it."""
    fake.script("completions", trickled(json_reply(GOOD), 5))
    client = _client(fake.url)
    started = time.monotonic()
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=client.now() + 0.5)
    assert caught.value.code == "timeout"
    assert time.monotonic() - started < 3


def test_a_page_out_of_its_own_time_never_opens_the_breaker(fake: OpenRouterFake) -> None:
    """Scan reliability (16 Oct 2026): a slow page at high thinking runs out of its own time;
    that is not the model failing, so five of them leave the model working and the next call
    goes out (it used to open the breaker and make every other page wait for its lease)."""
    fake.script("completions", *[slow(json_reply(GOOD), 2)] * BREAKER_FAILURES, json_reply(GOOD))
    client = _client(fake.url)
    for _ in range(BREAKER_FAILURES):
        with pytest.raises(ModelUnavailable) as caught:
            _read(client, deadline=client.now() + 0.2)
        assert caught.value.code == "timeout"
    assert client.status() == ("working", None)
    assert _read(client).value == GOOD


def test_a_call_that_cannot_start_before_its_deadline_is_a_timeout() -> None:
    """The caller's time ran out under the limiter: a timeout (read locally), not a rate limit
    (which makes the document wait as an outage)."""
    clock = Clock()
    gate = ModelGate(make_settings(model_rpm=1), workers=1, clock=lambda: clock.now)
    gate.take_slot(clock.now + 120, "m")  # books the next 60 s
    with pytest.raises(ModelUnavailable) as caught:
        gate.take_slot(clock.now + 30, "m")
    assert caught.value.code == "timeout"


def test_shared_database_limiter_failure_releases_its_probe() -> None:
    class BrokenDatabase:
        calls = 0

        def model_rate_slot(self, *_args: Any) -> float:
            self.calls += 1
            raise RuntimeError("database unavailable")

    database = BrokenDatabase()
    gate = ModelGate(make_settings(), database=database)
    gate._breakers["m"] = openrouter._Breaker(state=openrouter._HALF_OPEN, probing=True)

    with pytest.raises(ModelUnavailable) as caught:
        gate.take_slot(time.monotonic() + 1, "m")

    assert caught.value.code == "rate_limited"
    assert database.calls == 2  # one failed reservation and a best-effort ticket cleanup
    assert not gate._breakers["m"].probing


def test_a_trickled_reply_inside_the_deadline_is_read(fake: OpenRouterFake) -> None:
    fake.script("completions", trickled(json_reply(GOOD), 0.2))
    assert _read(_client(fake.url)).value == GOOD


class RealTimeClock(Clock):
    """``Clock`` that also moves with real time, for a refused connection: Windows takes
    about 2 seconds to refuse one on loopback, Linux none."""

    def __init__(self) -> None:
        super().__init__()
        self._started = time.monotonic()

    def __call__(self) -> float:
        return self.now + time.monotonic() - self._started


def test_a_closed_port_is_unreachable_after_retries_inside_the_deadline() -> None:
    """The exit test's outage: OPENROUTER_BASE_URL at a closed port. Each refusal is retried
    after a backoff, until the next wait would pass the page's deadline."""
    clock = RealTimeClock()
    client = _client(closed_port_url(), clock)
    deadline = clock() + 5
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=deadline)
    assert caught.value.code == "unreachable"
    assert clock.backoffs()[0] == 0.5  # it backed off and tried again instead of failing at once
    assert clock() < deadline


def test_a_redirect_is_refused_and_the_key_never_reaches_the_other_host(fake: OpenRouterFake) -> None:
    with running_fake() as other:
        fake.script("completions", redirect_to(other.url + "/chat/completions"))
        with pytest.raises(ModelUnavailable) as caught:
            _read(_client(fake.url, Clock()))
        assert caught.value.code == "redirect_refused"
        assert other.requests == []

        # Even an opener that follows redirects would not carry the key: it is unredirected.
        fake.script("completions", redirect_to(other.url + "/chat/completions"))
        other.responder = lambda request: json_reply(GOOD)
        following = _client(fake.url, Clock())
        following._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        _read(following)
        (forwarded,) = other.requests
        assert "authorization" not in forwarded.headers


def test_proxy_variables_are_ignored(fake: OpenRouterFake, monkeypatch: pytest.MonkeyPatch) -> None:
    """Page images and the key must never reach a proxy from the environment."""
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    fake.script("completions", json_reply(GOOD))
    assert _read(_client(fake.url, Clock())).value == GOOD


def test_an_oversized_reply_is_refused(fake: OpenRouterFake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(replies, "MAX_RESPONSE_BYTES", 1000)
    fake.script("completions", oversized(5000))
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "response_too_large"


@pytest.mark.parametrize("body", [b"<html>", b"[1, 2]"])
def test_a_reply_that_is_not_a_json_object_is_bad(fake: OpenRouterFake, body: bytes) -> None:
    fake.script("completions", Reply(200, body))
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "bad_response"


# The circuit breaker and the limiter (G9).


def test_the_breaker_opens_after_five_failures_then_lets_one_probe_through(fake: OpenRouterFake) -> None:
    fake.script("completions", *[error_reply(503)] * BREAKER_FAILURES)
    clock = Clock()
    client = _client(fake.url, clock)
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=clock.now + 1000)
    assert caught.value.code == "circuit_open"
    assert len(fake.requests) == BREAKER_FAILURES
    state, since = client.status()
    assert (state, since) == ("down", 1000.0)

    with pytest.raises(ModelUnavailable) as caught:  # open: no request at all
        _read(client)
    assert caught.value.code == "circuit_open"
    assert len(fake.requests) == BREAKER_FAILURES

    clock.now += BREAKER_OPEN_SECONDS
    fake.script("completions", json_reply(GOOD))
    assert _read(client).value == GOOD
    assert client.status() == ("working", None)


def test_mid_reply_errors_count_towards_the_breaker(fake: OpenRouterFake) -> None:
    fake.script("completions", *[failed_part_way()] * BREAKER_FAILURES)
    clock = Clock()
    client = _client(fake.url, clock)
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=clock.now + 1000)
    assert caught.value.code == "circuit_open"
    assert client.status()[0] == "down"


def test_a_failed_probe_opens_the_breaker_again(fake: OpenRouterFake) -> None:
    fake.script("completions", *[error_reply(500)] * (BREAKER_FAILURES + 1))
    clock = Clock()
    client = _client(fake.url, clock)
    with pytest.raises(ModelUnavailable):
        _read(client, deadline=clock.now + 1000)
    clock.now += BREAKER_OPEN_SECONDS
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=clock.now + 1000)
    assert caught.value.code == "circuit_open"
    assert len(fake.requests) == BREAKER_FAILURES + 1  # exactly one probe
    assert client.status()[0] == "down"


def test_only_one_probe_runs_while_the_breaker_is_half_open(fake: OpenRouterFake) -> None:
    fake.script("completions", *[error_reply(503)] * BREAKER_FAILURES)
    clock = Clock()
    client = _client(fake.url, clock)
    with pytest.raises(ModelUnavailable):
        _read(client, deadline=clock.now + 1000)
    clock.now += BREAKER_OPEN_SECONDS
    fake.script("completions", slow(json_reply(GOOD), 1.0))
    results: list[object] = []
    probe = threading.Thread(target=lambda: results.append(_read(client)))
    probe.start()
    while len(fake.requests) < BREAKER_FAILURES + 1:
        time.sleep(0.01)
    with pytest.raises(ModelUnavailable) as caught:
        _read(client)
    assert caught.value.code == "circuit_open"
    probe.join()
    assert results == [PageReply(GOOD, 1500, 500, "STOP")]


def test_failures_below_the_limit_are_failing_not_down(fake: OpenRouterFake) -> None:
    fake.script("completions", error_reply(503))
    clock = Clock()
    client = _client(fake.url, clock)
    with pytest.raises(ModelUnavailable):
        _read(client, deadline=clock.now + 0.4)  # one try, and no room for a second
    assert client.status() == ("failing", 1000.0)


@pytest.mark.parametrize(("workers", "interval"), [(1, 1.0), (3, 3.0)])
def test_the_limiter_spaces_calls_by_rpm_over_the_workers(
    fake: OpenRouterFake, workers: int, interval: float
) -> None:
    """MODEL_RPM 60 shared by ``workers`` processes: one call every 60 * workers / 60 seconds."""
    fake.responder = lambda request: json_reply(GOOD)
    clock = Clock()
    client = _client(fake.url, clock, workers=workers, model_rpm=60)
    assert client.interval == interval
    for _ in range(3):
        _read(client)
    assert clock.sleeps == [interval, interval]


def test_the_limiter_waits_add_up_only_where_a_caller_holds_the_counter(fake: OpenRouterFake) -> None:
    """Ask precision PR 1a (D): the answer timing line reads the limiter's waits."""
    fake.responder = lambda request: json_reply(GOOD)
    clock = Clock()
    client = _client(fake.url, clock, model_rpm=60)
    _read(client)
    _read(client)  # no counter held: the wait goes nowhere
    held = LIMITER_WAIT.set([0.0])
    try:
        _read(client)
        _read(client)
        assert LIMITER_WAIT.get() == [2.0]
    finally:
        LIMITER_WAIT.reset(held)
    assert clock.sleeps == [1.0, 1.0, 1.0]


def test_a_limiter_slot_past_the_deadline_is_a_timeout_without_a_request(fake: OpenRouterFake) -> None:
    # Changed on purpose (16 Oct 2026): the caller's time ran out, so the page is read locally
    # for review (a timeout), not held as an outage (rate_limited).
    fake.responder = lambda request: json_reply(GOOD)
    clock = Clock()
    client = _client(fake.url, clock, model_rpm=1)  # one call a minute
    _read(client)
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=clock.now + 30)
    assert caught.value.code == "timeout"
    assert len(fake.requests) == 1


def test_pages_read_in_parallel_share_the_client_safely(fake: OpenRouterFake) -> None:
    """G8 reads 6 pages of a document at a time on threads: every read and the totals hold."""
    fake.responder = lambda request: slow(json_reply(GOOD, counts=usage(100, 15, 5, cost=0.5)), 0.05)
    client = _client(fake.url)
    pages: list[PageReply] = []
    lock = threading.Lock()

    def read() -> None:
        page = _read(client)
        with lock:
            pages.append(page)

    threads = [threading.Thread(target=read) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert pages == [PageReply(GOOD, 100, 15, "STOP")] * 12
    assert client.usage() == Usage(1200, 180, 6.0)
    assert fake.max_in_flight > 1


# The ModelClient protocol: chat_json and embed.


def test_chat_json_sends_the_system_prompt_first_the_schema_and_default_budget(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply(GOOD))
    assert _client(fake.url, Clock()).chat_json(system="rules", user="question", schema=SCHEMA) == GOOD
    (request,) = fake.requests
    assert request.path == "/api/v1/chat/completions"
    assert request.body == {
        "model": PRO,
        "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "question"}],
        "stream": False,
        "reasoning": {"effort": "low", "exclude": True},
        # Changed on purpose (features 25-27, 15 Oct 2026): the room to think is per level now.
        "max_completion_tokens": 512 + CHAT_THINKING_TOKENS["low"],
        "provider": THE_PROVIDER_BLOCK,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "page", "strict": STRICT_SCHEMA, "schema": SCHEMA},
        },
    }


def test_chat_json_holds_the_reply_to_the_callers_budget(fake: OpenRouterFake) -> None:
    """Thinking gets its own room in max_completion_tokens; the reply itself (completion
    less reasoning) is held to max_tokens, and one over it is output_truncated with no
    repair."""
    fake.script(
        "completions",
        json_reply(GOOD, counts=usage(100, 3512, 3000)),
        json_reply(GOOD, counts=usage(100, 513, 0)),
    )
    client = _client(fake.url, Clock())
    assert client.chat_json(system="s", user="u", schema=SCHEMA, max_tokens=512) == GOOD
    # Changed on purpose (features 25-27, 15 Oct 2026): the room to think is per level now.
    assert fake.bodies()[0]["max_completion_tokens"] == 512 + CHAT_THINKING_TOKENS["low"]
    with pytest.raises(ModelOutputError) as caught:
        client.chat_json(system="s", user="u", schema=SCHEMA, max_tokens=512)
    assert caught.value.code == "output_truncated"
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        ("not json at all", "the reply is not JSON"),
        (json.dumps({"status": "maybe", "lines": []}), "$.status must be one of the allowed values"),
        ('{"status": "answered", "lines": ["Bill \\ud800"]}', "not valid text"),
    ],
)
def test_chat_json_repairs_a_bad_reply_once_as_the_assistant(
    fake: OpenRouterFake, bad: str, problem: str
) -> None:
    fake.script("completions", chat_reply(bad), json_reply(GOOD))
    assert _client(fake.url, Clock()).chat_json(system="rules", user="question", schema=SCHEMA) == GOOD
    messages = fake.bodies()[1]["messages"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert messages[2]["content"] == bad
    assert problem in messages[3]["content"]


def test_chat_json_gives_up_after_two_bad_replies(fake: OpenRouterFake) -> None:
    fake.script("completions", chat_reply("[1, 2]"), json_reply({"status": "answered"}))
    with pytest.raises(ModelOutputError) as caught:
        _client(fake.url, Clock()).chat_json(system="s", user="u", schema=SCHEMA)
    assert caught.value.code == "invalid_output"
    assert len(fake.requests) == 2


def test_chat_json_counts_the_bad_reply_and_the_blocked_repair(fake: OpenRouterFake) -> None:
    fake.script(
        "completions",
        chat_reply("not json", counts=usage(3, 5, 0)),
        chat_reply(None, finish="content_filter", counts=usage(7, 11, 0)),
    )
    with pytest.raises(ModelOutputError) as caught:
        _client(fake.url, Clock()).chat_json(system="s", user="u", schema=SCHEMA)
    assert (caught.value.code, caught.value.input_tokens, caught.value.output_tokens) == ("blocked", 10, 16)


def test_the_worker_chat_timeout_is_used_for_chat_only(fake: OpenRouterFake) -> None:
    fake.script("completions", slow(json_reply(GOOD), 5))
    client = OpenRouterClient(_settings(fake.url, model_timeout_seconds=30.0), chat_timeout_seconds=0.2)
    with pytest.raises(ModelUnavailable) as caught:
        client.chat_json(system="s", user="u", schema=SCHEMA)
    assert caught.value.code == "timeout"


def test_embed_sends_one_text_a_request_at_the_dimensions_in_order(fake: OpenRouterFake) -> None:
    """Google Vertex embeds one text per request (a batch is served only by AI Studio, which
    the provider block refuses), so each text is its own request; the vectors in order.
    Changed on purpose (fast scan, council 16 Oct 2026): the requests go at the same time,
    so each is answered by its text, not by turn."""
    fake.responder = lambda request: embed_reply([[float(request.body["input"][0][-1]), 0.5, 0.25, 1.0]])
    client = _client(fake.url, Clock())
    vectors = client.embed(["piece 0", "piece 1", "piece 2"])
    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]
    bodies = sorted(fake.bodies("embeddings"), key=lambda body: str(body["input"]))
    assert (fake.requests[0].method, fake.requests[0].path) == ("POST", "/api/v1/embeddings")
    assert [body["input"] for body in bodies] == [["piece 0"], ["piece 1"], ["piece 2"]]
    assert bodies[0] == {
        "model": EMBED,
        "input": ["piece 0"],
        "dimensions": 4,  # EMBEDDING_DIM (1024 in production)
        "encoding_format": "float",
        "provider": THE_EMBED_PROVIDER_BLOCK,
    }


def test_embed_of_nothing_makes_no_request(fake: OpenRouterFake) -> None:
    assert _client(fake.url, Clock()).embed([]) == []
    assert fake.requests == []


def _data(*items: dict[str, Any]) -> Reply:
    return Reply(200, {"data": list(items)})


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (embed_reply([[0.1, 0.2, 0.3]]), "embedding_dim"),
        (embed_reply([[0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3, 0.4]]), "embedding_count"),
        (_data(), "embedding_count"),
        (_data({"embedding": [0.1, 0.2, 0.3, 0.4], "index": 1}), "embedding_count"),
        (_data({"embedding": [0.1, 0.2, 0.3, 0.4]}), "embedding_count"),  # no index
        (_data({"embedding": [0.1, 0.2, 0.3, 0.4], "index": True}), "embedding_count"),
        (_data({"embedding": [0.1, "x", 0.3, 0.4], "index": 0}), "embedding_value"),
        (_data({"embedding": [0.1, True, 0.3, 0.4], "index": 0}), "embedding_value"),
        (_data({"embedding": "AAAA", "index": 0}), "embedding_value"),  # base64, not floats
        (Reply(200, b'{"data": [{"embedding": [0.1, NaN, 0.3, 0.4], "index": 0}]}'), "embedding_value"),
        (Reply(200, b'{"data": [{"embedding": [0.1, Infinity, 0.3, 0.4], "index": 0}]}'), "embedding_value"),
    ],
)
def test_embeddings_are_checked(fake: OpenRouterFake, reply: Reply, code: str) -> None:
    fake.script("embeddings", reply)
    with pytest.raises(ModelOutputError) as caught:
        _client(fake.url, Clock()).embed(["one"])
    assert caught.value.code == code


def test_two_vectors_with_the_same_index_are_refused(fake: OpenRouterFake) -> None:
    item = {"embedding": [0.1, 0.2, 0.3, 0.4], "index": 0}
    # Changed on purpose (fast scan, council 16 Oct 2026): both requests go at once, so both
    # get the bad reply (a script would answer whichever came first).
    fake.responder = lambda request: _data(item, item)
    with pytest.raises(ModelOutputError) as caught:
        _client(fake.url, Clock()).embed(["one", "two"])
    assert caught.value.code == "embedding_count"


def test_an_embedding_reply_without_data_is_unavailable(fake: OpenRouterFake) -> None:
    fake.script("embeddings", Reply(200, {"no": "data"}))
    with pytest.raises(ModelUnavailable) as caught:
        _client(fake.url, Clock()).embed(["one"])
    assert caught.value.code == "bad_response"


# The key and page text are secrets.


def test_the_key_is_never_in_a_log_an_error_or_a_repr(
    fake: OpenRouterFake, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fake.script(
        "completions",
        error_reply(400),
        *[error_reply(503)] * BREAKER_FAILURES,
        error_reply(403),
    )
    clock = Clock()
    client = _client(fake.url, clock)
    errors: list[BaseException] = []
    for _ in range(4):
        try:
            _read(client, deadline=clock.now + 1000)
        except (ModelUnavailable, ModelOutputError) as caught:
            errors.append(caught)
        clock.now += BREAKER_OPEN_SECONDS
    assert [getattr(raised, "code", None) for raised in errors] == [
        "bad_request",
        "circuit_open",
        "key_rejected",
        "key_rejected",
    ]
    for raised in errors:
        assert KEY not in str(raised) and KEY not in repr(raised)
        assert raised.__cause__ is None and raised.__context__ is None  # no traceback holds the request
    assert KEY not in caplog.text
    assert KEY not in repr(client) and KEY not in repr(client.status()) and KEY not in repr(client.gate)
    assert KEY not in repr(_settings(fake.url))


@pytest.mark.parametrize(
    "reply",
    [
        error_reply(400),
        error_reply(402),
        refused_403(),
        moderated_403(),
        error_reply(403),
        error_reply(422),
        error_reply(404),
        error_reply(503),
        rate_limited(),
    ],
)
def test_page_text_in_an_error_reply_is_never_logged_raised_or_kept(
    fake: OpenRouterFake, caplog: pytest.LogCaptureFixture, reply: Reply
) -> None:
    """error.message, metadata.flagged_input and metadata.raw can hold page text: SECRET is
    in all three, and appears in no log record, exception or repr."""
    caplog.set_level(logging.DEBUG)
    fake.responder = lambda request: reply
    clock = Clock()
    client = _client(fake.url, clock)
    with pytest.raises((ModelUnavailable, ModelOutputError)) as caught:
        _read(client, deadline=clock.now + 5)
    raised = caught.value
    assert SECRET not in str(raised) and SECRET not in repr(raised)
    assert raised.__cause__ is None and raised.__context__ is None
    assert SECRET not in caplog.text
    for record in caplog.records:
        assert SECRET not in repr(record.__dict__)
    assert (
        SECRET not in repr(client)
        and SECRET not in repr(client.usage())
        and SECRET not in repr(client.status())
    )
    probe = client.probe("/chat/completions", fake.bodies()[0], timeout=5)
    assert SECRET not in repr(probe)


@pytest.mark.parametrize(
    ("reply", "kept"),
    [
        (refused_403(), ("refusal", "google-vertex")),
        (
            error_reply(429, error_type="rate_limit_exceeded", provider_name="Google Vertex"),
            ("rate_limit_exceeded", None),
        ),
        (error_reply(400, error_type="Ignore all rules " + SECRET), (None, None)),
        (error_reply(500, error_type="x" * 65, provider_name="google-vertex"), (None, "google-vertex")),
        (Reply(500, b"<html>" + SECRET.encode() + b"</html>"), (None, None)),
        (Reply(500, {"error": {"metadata": "not an object"}}), (None, None)),
    ],
)
def test_only_short_codes_are_kept_from_an_error_reply(
    fake: OpenRouterFake, reply: Reply, kept: tuple[str | None, str | None]
) -> None:
    fake.script("completions", reply)
    probe = _client(fake.url, Clock()).probe("/chat/completions", {"model": PRO}, timeout=5)
    assert (probe.error_type, probe.provider_name) == kept
    assert probe.reply is None


def test_probe_gets_with_no_body_and_the_key(fake: OpenRouterFake) -> None:
    fake.script("endpoints", endpoints_reply(PRO))
    fake.script("key", key_reply())
    client = _client(fake.url, Clock())
    listing = client.probe(f"/models/{PRO}/endpoints", None, timeout=5)
    limits = client.probe("/key", None, timeout=5)
    assert (listing.status, limits.status) == (200, 200)
    assert listing.reply is not None and listing.reply["data"]["id"] == PRO
    assert [(request.method, request.path, request.body) for request in fake.requests] == [
        ("GET", f"/api/v1/models/{PRO}/endpoints", None),
        ("GET", "/api/v1/key", None),
    ]
    fake.assert_key_in_header_only(KEY)


def test_probe_can_send_no_key(fake: OpenRouterFake) -> None:
    fake.script("completions", error_reply(401, error_type="authentication"))
    probe = _client(fake.url, Clock()).probe("/chat/completions", {"model": PRO}, send_key=False, timeout=5)
    assert (probe.status, probe.error_type) == (401, "authentication")
    assert "authorization" not in fake.requests[0].headers


def test_the_client_refuses_a_missing_key_a_plain_http_host_or_a_strange_model_id() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouterClient(_settings("https://openrouter.ai/api/v1", openrouter_api_key=None))
    with pytest.raises(ValueError, match="https"):
        OpenRouterClient(_settings("http://example.com/api/v1"))
    for model in ("../keys", "gemini-3.1-pro-preview", "google/gemini-3.1-pro-preview:free"):
        with pytest.raises(ValueError, match="model ID"):
            OpenRouterClient(_settings("https://openrouter.ai/api/v1", ocr_model=model))
    with pytest.raises(ValueError, match="model ID"):
        OpenRouterClient(_settings("https://openrouter.ai/api/v1", model_chat="google/gemini-3:free"))


# build_model_client (PRD 5 G9, M1, M5).


def test_without_ai_there_is_no_client_and_no_model_call(fake: OpenRouterFake) -> None:
    assert build_model_client(make_settings(model_enabled=False)) is None
    assert build_model_client(_settings(fake.url, model_enabled=False)) is None
    assert fake.requests == []


def test_gemini_chat_and_embed_models_give_the_openrouter_client(fake: OpenRouterFake) -> None:
    client = build_model_client(_settings(fake.url), chat_timeout_seconds=1.0)
    assert isinstance(client, OpenRouterClient)
    fake.script("embeddings", embed_reply([[0.1, 0.2, 0.3, 0.4]]))
    assert client.embed(["one"]) == [[0.1, 0.2, 0.3, 0.4]]


def test_the_worker_client_uses_model_extract_timeout_seconds_for_chat(fake: OpenRouterFake) -> None:
    """The worker's client (worker_main.worker_model) times its chat calls out after
    MODEL_EXTRACT_TIMEOUT_SECONDS, not MODEL_TIMEOUT_SECONDS."""
    fake.script("completions", slow(json_reply(GOOD), 5))
    client = worker_model(_settings(fake.url, model_timeout_seconds=30.0, model_extract_timeout_seconds=0.2))
    assert client is not None
    with pytest.raises(ModelUnavailable) as caught:
        client.chat_json(system="s", user="u", schema=SCHEMA)
    assert caught.value.code == "timeout"


def test_the_fake_answers_only_what_a_test_scripted(fake: OpenRouterFake) -> None:
    clock = Clock()
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, clock), deadline=clock.now + 0.4)
    assert caught.value.code == "server_error"  # 599: nothing scripted
    assert fake.unanswered >= 1
    assert isinstance(fake.requests[0], Recorded)
    assert openrouter_fake.API_PATH in fake.url


def test_every_client_of_the_process_shares_one_gate(fake: OpenRouterFake) -> None:
    """G9 per process: the reader's client, chat and the translator share one limiter and
    one breaker, so their calls together stay under MODEL_RPM. PRD 5 V7: MODEL_RPM is
    shared by WORKER_REPLICAS worker copies and the API, so each process's calls are
    60 * (WORKER_REPLICAS + 1) / MODEL_RPM seconds apart."""
    settings = _settings(fake.url)
    chat = build_model_client(settings)
    translator = build_model_client(settings, chat_timeout_seconds=1.0)
    assert isinstance(chat, OpenRouterClient) and isinstance(translator, OpenRouterClient)
    assert chat.gate is translator.gate is process_gate(settings)
    assert chat.interval == pytest.approx(60.0 * (settings.worker_replicas + 1) / settings.model_rpm)
    assert process_gate(settings, workers=settings.worker_replicas + 1) is chat.gate
    assert process_gate(settings, workers=1) is not chat.gate
    assert process_gate(settings, workers=1).interval == pytest.approx(chat.interval / 4)
    with pytest.raises(ValueError, match="workers"):
        ModelGate(settings, workers=0)


def test_a_schema_goes_out_with_no_max_items_and_nothing_else_changed() -> None:
    """Live 11 Oct 2026: Google Vertex refused the card's schema with maxItems (400
    INVALID_ARGUMENT) and took it without; maxLength is kept."""
    from kaagaz.extraction.card import CARD_SCHEMA

    sent = openrouter.response_format(CARD_SCHEMA)["json_schema"]["schema"]
    assert '"maxItems"' in json.dumps(CARD_SCHEMA) and '"maxItems"' not in json.dumps(sent)
    assert '"maxLength"' in json.dumps(sent)

    def dropped(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: dropped(item) for key, item in value.items() if key != "maxItems"}
        return [dropped(item) for item in value] if isinstance(value, list) else value

    assert sent == dropped(CARD_SCHEMA)
