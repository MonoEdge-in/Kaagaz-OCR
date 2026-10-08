"""The Gemini client (PRD 5 G2, G3, G6, G9) against a fake Gemini API on 127.0.0.1.

Never the real API: ``tests/gemini_fake.py`` scripts every reply. A fake clock
(``Clock``) drives deadlines, backoff, the limiter, the key latch and the circuit
breaker, so no test sleeps for real except the ones about a slow reply.
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

from kaagaz.ai import gemini, replies
from kaagaz.ai.client import ModelOutputError, ModelUnavailable, OllamaClient, build_model_client
from kaagaz.ai.gemini import (
    BACKOFF_MAX_SECONDS,
    BREAKER_FAILURES,
    BREAKER_OPEN_SECONDS,
    CHAT_THINKING_TOKENS,
    KEY_LATCH_SECONDS,
    PAGE_MAX_OUTPUT_TOKENS,
    GeminiClient,
    PageReply,
    Usage,
)
from kaagaz.settings import Settings
from tests import gemini_fake
from tests.gemini_fake import (
    KEY,
    GeminiFake,
    Recorded,
    Reply,
    blocked_prompt,
    closed_port_url,
    embed_reply,
    error_reply,
    json_reply,
    oversized,
    rate_limited,
    redirect_to,
    running_fake,
    slow,
    text_reply,
    usage,
)
from tests.settings_factory import make_settings

PNG = b"\x89PNG made-up page"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "not_found"]},
        "lines": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "lines"],
}
GOOD = {"status": "answered", "lines": ["Total 5,000"]}
PRO = "gemini-3.1-pro-preview"


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
def fake() -> Iterator[GeminiFake]:
    with running_fake() as server:
        yield server


def _settings(url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "model_enabled": True,
        "model_chat": PRO,
        "model_embed": "gemini-embedding-2",
        "gemini_api_key": KEY,
        "gemini_base_url": url,
        "embedding_dim": 4,
        "gemini_rpm": 10**9,  # the limiter's spacing is a few nanoseconds unless a test sets it
        "model_timeout_seconds": 5.0,
    }
    return make_settings(**{**values, **overrides})


def _client(url: str, clock: Clock | None = None, *, workers: int = 1, **overrides: Any) -> GeminiClient:
    if clock is None:
        return GeminiClient(_settings(url, **overrides), workers=workers)
    return GeminiClient(
        _settings(url, **overrides),
        workers=workers,
        clock=clock,
        sleep=clock.sleep,
        random=lambda: 0.5,
        wall_clock=lambda: datetime(2026, 10, 8, 10, 0, 0, tzinfo=UTC),
    )


def _read(client: GeminiClient, *, deadline: float | None = None, **options: Any) -> PageReply:
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


def test_read_page_sends_the_instruction_then_the_image_with_its_own_resolution(fake: GeminiFake) -> None:
    fake.script("generateContent", json_reply(GOOD, counts=usage(1500, 300, 200)))

    page = _read(_client(fake.url, Clock()))

    assert page == PageReply(GOOD, 1500, 500, "STOP")
    (request,) = fake.requests
    assert (request.method, request.path) == ("POST", f"/v1beta/models/{PRO}:generateContent")
    assert request.headers["content-type"] == "application/json"
    assert request.body == {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "Copy the page."},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(PNG).decode("ascii"),
                        },
                        "media_resolution": {"level": "MEDIA_RESOLUTION_HIGH"},
                    },
                ],
            }
        ],
        "generationConfig": {
            "thinkingConfig": {"thinkingLevel": "LOW"},
            "maxOutputTokens": PAGE_MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
            "responseJsonSchema": SCHEMA,
        },
    }
    assert "temperature" not in json.dumps(request.body)  # Google's default (section 14)


def test_the_key_goes_in_its_header_and_never_in_the_url_or_body(fake: GeminiFake) -> None:
    fake.script("generateContent", json_reply(GOOD))
    fake.script("batchEmbedContents", embed_reply([[0.1, 0.2, 0.3, 0.4]]))
    client = _client(fake.url, Clock())
    _read(client)
    client.embed(["one"])
    fake.assert_key_in_header_only(KEY)
    assert all("key=" not in request.path for request in fake.requests)


@pytest.mark.parametrize(
    ("resolution", "thinking", "level", "thinking_level"),
    [
        ("ultra_high", "high", "MEDIA_RESOLUTION_ULTRA_HIGH", "HIGH"),
        ("low", "medium", "MEDIA_RESOLUTION_LOW", "MEDIUM"),
    ],
)
def test_the_retry_pair_and_other_levels_are_sent_as_the_apis_names(
    fake: GeminiFake, resolution: str, thinking: str, level: str, thinking_level: str
) -> None:
    """F5: "Read this page again" sends ultra_high and high."""
    fake.script("generateContent", json_reply(GOOD))
    _read(_client(fake.url, Clock()), media_resolution=resolution, thinking_level=thinking)
    body = fake.bodies()[0]
    assert body["contents"][0]["parts"][1]["media_resolution"] == {"level": level}
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": thinking_level}


@pytest.mark.parametrize(("option", "value"), [("media_resolution", "ultra"), ("thinking_level", "max")])
def test_an_unknown_level_is_refused_before_any_request(fake: GeminiFake, option: str, value: str) -> None:
    options: dict[str, Any] = {option: value}
    with pytest.raises(ValueError, match="unknown"):
        _read(_client(fake.url, Clock()), **options)
    assert fake.requests == []


def test_usage_counts_thinking_as_output_and_keeps_totals(fake: GeminiFake) -> None:
    """G9: input is promptTokenCount, output is candidates plus thoughts (billed as output)."""
    fake.script(
        "generateContent",
        json_reply(GOOD, counts=usage(1700, 400, 3600)),
        json_reply(GOOD, counts=usage(1200, 100, None)),  # no thinking reported: 0
    )
    client = _client(fake.url, Clock())
    first, second = _read(client), _read(client)
    assert (first.input_tokens, first.output_tokens) == (1700, 4000)
    assert (second.input_tokens, second.output_tokens) == (1200, 100)
    assert client.usage() == Usage(2900, 4100)


def test_thought_parts_are_left_out_of_the_reply(fake: GeminiFake) -> None:
    fake.script("generateContent", json_reply(GOOD, thought="thinking about the page"))
    assert _read(_client(fake.url, Clock())).value == GOOD


# Failures (G6).


def test_a_429_waits_for_retry_after_then_succeeds(fake: GeminiFake) -> None:
    fake.script("generateContent", rate_limited("7"), json_reply(GOOD))
    clock = Clock()
    assert _read(_client(fake.url, clock)).value == GOOD
    assert clock.backoffs() == [7.0]
    assert len(fake.requests) == 2


def test_retry_after_as_an_http_date_is_honoured(fake: GeminiFake) -> None:
    fake.script("generateContent", rate_limited("Thu, 08 Oct 2026 10:00:12 GMT"), json_reply(GOOD))
    clock = Clock()
    _read(_client(fake.url, clock))
    assert clock.backoffs() == [12.0]


def test_500_and_503_back_off_with_full_jitter_then_succeed(fake: GeminiFake) -> None:
    fake.script(
        "generateContent", error_reply(500, "INTERNAL"), error_reply(503, "UNAVAILABLE"), json_reply(GOOD)
    )
    clock = Clock()
    assert _read(_client(fake.url, clock)).value == GOOD
    assert clock.backoffs() == [0.5, 1.0]  # random 0.5 times 1 s, then 2 s


def test_backoff_never_waits_more_than_its_cap(fake: GeminiFake, monkeypatch: pytest.MonkeyPatch) -> None:
    assert BACKOFF_MAX_SECONDS == 30.0
    monkeypatch.setattr(gemini, "BACKOFF_MAX_SECONDS", 3.0)
    fake.script("generateContent", *[error_reply(503, "UNAVAILABLE")] * 4, json_reply(GOOD))
    clock = Clock()
    client = _client(fake.url, clock)
    client._random = lambda: 1.0
    _read(client, deadline=clock.now + 1000)
    assert clock.backoffs() == [1.0, 2.0, 3.0, 3.0]


@pytest.mark.parametrize(
    ("reply", "code"),
    [(rate_limited(), "rate_limited"), (error_reply(503, "UNAVAILABLE"), "server_error")],
)
def test_retries_stop_at_the_pages_deadline(fake: GeminiFake, reply: Reply, code: str) -> None:
    fake.responder = lambda request: reply
    clock = Clock()
    client = _client(fake.url, clock)
    client._random = lambda: 1.0
    deadline = clock.now + 10
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=deadline)
    assert caught.value.code == code
    assert str(caught.value) == code
    assert clock.now < deadline  # it gave up instead of sleeping past the deadline
    assert 1 < len(fake.requests) <= 4


def test_a_retry_after_past_the_deadline_gives_up_at_once(fake: GeminiFake) -> None:
    fake.script("generateContent", rate_limited("600"))
    clock = Clock()
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, clock), deadline=clock.now + 120)
    assert caught.value.code == "rate_limited"
    assert len(fake.requests) == 1
    assert clock.backoffs() == []


def test_a_400_is_a_bad_request_and_never_retried(fake: GeminiFake) -> None:
    fake.script("generateContent", error_reply(400, "INVALID_ARGUMENT"))
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert (caught.value.code, str(caught.value)) == ("bad_request", "bad_request")
    assert len(fake.requests) == 1


def test_a_404_names_the_model_problem(fake: GeminiFake) -> None:
    fake.script("generateContent", error_reply(404, "NOT_FOUND"))
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "model_not_found"


@pytest.mark.parametrize(("status", "word"), [(401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED")])
def test_a_rejected_key_latches_and_never_loops(
    fake: GeminiFake, caplog: pytest.LogCaptureFixture, status: int, word: str
) -> None:
    """G6: a wrong key or a spent cap is logged at error once and fails fast, without a
    request, for KEY_LATCH_SECONDS; then one call tries again."""
    fake.script("generateContent", error_reply(status, word), json_reply(GOOD))
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
    assert [getattr(record, "code", None) for record in errors] == ["key_rejected"]

    clock.now += KEY_LATCH_SECONDS
    assert _read(client).value == GOOD
    assert len(fake.requests) == 2
    assert client.status() == ("working", None)


@pytest.mark.parametrize(
    "finish", ["SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"]
)
def test_a_blocked_finish_reason_is_blocked_with_no_repair(fake: GeminiFake, finish: str) -> None:
    fake.script("generateContent", text_reply("", finish=finish))
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "blocked"
    assert len(fake.requests) == 1


def test_a_blocked_prompt_is_blocked(fake: GeminiFake) -> None:
    fake.script("generateContent", blocked_prompt("SAFETY"))
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "blocked"


def test_a_reply_cut_at_max_tokens_is_truncated_with_no_repair(fake: GeminiFake) -> None:
    fake.script("generateContent", text_reply('{"status": "answered", "lines": ["Total', finish="MAX_TOKENS"))
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "output_truncated"
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    "reply",
    [
        text_reply(json.dumps(GOOD), finish="OTHER"),
        text_reply(json.dumps(GOOD), finish="SOMETHING_NEW"),
        Reply(200, {"candidates": [], "usageMetadata": usage()}),
        Reply(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(GOOD)}]}}]}),  # no finish
        Reply(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": 5}]}}]}),
    ],
)
def test_any_other_ending_is_invalid_output(fake: GeminiFake, reply: Reply) -> None:
    fake.script("generateContent", reply)
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "invalid_output"


def test_bad_json_is_repaired_once_with_the_page_again(fake: GeminiFake) -> None:
    fake.script(
        "generateContent",
        text_reply("not json", counts=usage(1500, 10, 90)),
        json_reply(GOOD, counts=usage(1600, 300, 100)),
    )
    page = _read(_client(fake.url, Clock()))
    assert page == PageReply(GOOD, 3100, 500, "STOP")
    second = fake.bodies()[1]["contents"]
    assert [content["role"] for content in second] == ["user", "model", "user"]
    assert second[0]["parts"][1]["inline_data"]["data"] == base64.b64encode(PNG).decode("ascii")
    assert second[1]["parts"] == [{"text": "not json"}]
    assert "the reply is not JSON" in second[2]["parts"][0]["text"]


def test_two_bad_replies_are_invalid_output(fake: GeminiFake) -> None:
    fake.script("generateContent", text_reply("[1]"), json_reply({"status": "answered"}))
    with pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "invalid_output"
    assert len(fake.requests) == 2


def test_a_slow_reply_past_the_deadline_times_out(fake: GeminiFake) -> None:
    fake.script("generateContent", slow(json_reply(GOOD), 5))
    client = _client(fake.url)
    started = time.monotonic()
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=client.now() + 0.3)
    assert caught.value.code == "timeout"
    assert time.monotonic() - started < 3


class RealTimeClock(Clock):
    """``Clock`` that also moves with real time, for a refused connection: Windows takes
    about 2 seconds to refuse one on loopback, Linux none."""

    def __init__(self) -> None:
        super().__init__()
        self._started = time.monotonic()

    def __call__(self) -> float:
        return self.now + time.monotonic() - self._started


def test_a_closed_port_is_unreachable_after_retries_inside_the_deadline() -> None:
    """The exit test's outage: GEMINI_BASE_URL at a closed port. Each refusal is retried
    after a backoff, until the next wait would pass the page's deadline."""
    clock = RealTimeClock()
    client = _client(closed_port_url(), clock)
    deadline = clock() + 5
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=deadline)
    assert caught.value.code == "unreachable"
    assert clock.backoffs()[0] == 0.5  # it backed off and tried again instead of failing at once
    assert clock() < deadline


def test_a_redirect_is_refused_and_the_key_never_reaches_the_other_host(fake: GeminiFake) -> None:
    with running_fake() as other:
        fake.script("generateContent", redirect_to(other.url + f"/models/{PRO}:generateContent"))
        with pytest.raises(ModelUnavailable) as caught:
            _read(_client(fake.url, Clock()))
        assert caught.value.code == "redirect_refused"
        assert other.requests == []

        # Even an opener that follows redirects would not carry the key: it is unredirected.
        fake.script("generateContent", redirect_to(other.url + f"/models/{PRO}:generateContent"))
        other.responder = lambda request: json_reply(GOOD)
        following = _client(fake.url, Clock())
        following._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        _read(following)
        (forwarded,) = other.requests
        assert "x-goog-api-key" not in forwarded.headers


def test_proxy_variables_are_ignored(fake: GeminiFake, monkeypatch: pytest.MonkeyPatch) -> None:
    """Page images and the key must never reach a proxy from the environment."""
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    fake.script("generateContent", json_reply(GOOD))
    assert _read(_client(fake.url, Clock())).value == GOOD


def test_an_oversized_reply_is_refused(fake: GeminiFake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(replies, "MAX_RESPONSE_BYTES", 1000)
    fake.script("generateContent", oversized(5000))
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "response_too_large"


@pytest.mark.parametrize("body", [b"<html>", b"[1, 2]"])
def test_a_reply_that_is_not_a_json_object_is_bad(fake: GeminiFake, body: bytes) -> None:
    fake.script("generateContent", Reply(200, body))
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, Clock()))
    assert caught.value.code == "bad_response"


# The circuit breaker and the limiter (G9).


def test_the_breaker_opens_after_five_failures_then_lets_one_probe_through(fake: GeminiFake) -> None:
    fake.script("generateContent", *[error_reply(503, "UNAVAILABLE")] * BREAKER_FAILURES)
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
    fake.script("generateContent", json_reply(GOOD))
    assert _read(client).value == GOOD
    assert client.status() == ("working", None)


def test_a_failed_probe_opens_the_breaker_again(fake: GeminiFake) -> None:
    fake.script("generateContent", *[error_reply(500, "INTERNAL")] * (BREAKER_FAILURES + 1))
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


def test_only_one_probe_runs_while_the_breaker_is_half_open(fake: GeminiFake) -> None:
    fake.script("generateContent", *[error_reply(503, "UNAVAILABLE")] * BREAKER_FAILURES)
    clock = Clock()
    client = _client(fake.url, clock)
    with pytest.raises(ModelUnavailable):
        _read(client, deadline=clock.now + 1000)
    clock.now += BREAKER_OPEN_SECONDS
    fake.script("generateContent", slow(json_reply(GOOD), 1.0))
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


def test_failures_below_the_limit_are_failing_not_down(fake: GeminiFake) -> None:
    fake.script("generateContent", error_reply(503, "UNAVAILABLE"))
    clock = Clock()
    client = _client(fake.url, clock)
    with pytest.raises(ModelUnavailable):
        _read(client, deadline=clock.now + 0.4)  # one try, and no room for a second
    assert client.status() == ("failing", 1000.0)


@pytest.mark.parametrize(("workers", "interval"), [(1, 1.0), (3, 3.0)])
def test_the_limiter_spaces_calls_by_rpm_over_the_workers(
    fake: GeminiFake, workers: int, interval: float
) -> None:
    """GEMINI_RPM 60 shared by ``workers`` processes: one call every 60 * workers / 60 seconds."""
    fake.responder = lambda request: json_reply(GOOD)
    clock = Clock()
    client = _client(fake.url, clock, workers=workers, gemini_rpm=60)
    assert client.interval == interval
    for _ in range(3):
        _read(client)
    assert clock.sleeps == [interval, interval]


def test_a_limiter_slot_past_the_deadline_is_rate_limited_without_a_request(fake: GeminiFake) -> None:
    fake.responder = lambda request: json_reply(GOOD)
    clock = Clock()
    client = _client(fake.url, clock, gemini_rpm=1)  # one call a minute
    _read(client)
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, deadline=clock.now + 30)
    assert caught.value.code == "rate_limited"
    assert len(fake.requests) == 1


def test_pages_read_in_parallel_share_the_client_safely(fake: GeminiFake) -> None:
    """G8 reads 6 pages of a document at a time on threads: every read and the totals hold."""
    fake.responder = lambda request: slow(json_reply(GOOD, counts=usage(100, 10, 5)), 0.05)
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
    assert client.usage() == Usage(1200, 180)
    assert fake.max_in_flight > 1


# The ModelClient protocol, as the Ollama client's tests pin it.


def test_chat_json_sends_the_system_prompt_the_schema_and_no_budget(fake: GeminiFake) -> None:
    fake.script("generateContent", json_reply(GOOD))
    assert _client(fake.url, Clock()).chat_json(system="rules", user="question", schema=SCHEMA) == GOOD
    (request,) = fake.requests
    assert request.path == f"/v1beta/models/{PRO}:generateContent"
    assert request.body == {
        "systemInstruction": {"parts": [{"text": "rules"}]},
        "contents": [{"role": "user", "parts": [{"text": "question"}]}],
        "generationConfig": {
            "thinkingConfig": {"thinkingLevel": "LOW"},
            "responseMimeType": "application/json",
            "responseJsonSchema": SCHEMA,
        },
    }


def test_chat_json_holds_the_reply_to_the_callers_budget(fake: GeminiFake) -> None:
    """Thinking counts inside maxOutputTokens, so it gets its own room; the reply itself
    is held to max_tokens, and one over it is output_truncated with no repair."""
    fake.script(
        "generateContent",
        json_reply(GOOD, counts=usage(100, 512, 3000)),
        json_reply(GOOD, counts=usage(100, 513, 0)),
    )
    client = _client(fake.url, Clock())
    assert client.chat_json(system="s", user="u", schema=SCHEMA, max_tokens=512) == GOOD
    assert fake.bodies()[0]["generationConfig"]["maxOutputTokens"] == 512 + CHAT_THINKING_TOKENS
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
def test_chat_json_repairs_a_bad_reply_once(fake: GeminiFake, bad: str, problem: str) -> None:
    fake.script("generateContent", text_reply(bad), json_reply(GOOD))
    assert _client(fake.url, Clock()).chat_json(system="rules", user="question", schema=SCHEMA) == GOOD
    contents = fake.bodies()[1]["contents"]
    assert [content["role"] for content in contents] == ["user", "model", "user"]
    assert contents[1]["parts"] == [{"text": bad}]
    assert problem in contents[2]["parts"][0]["text"]


def test_chat_json_gives_up_after_two_bad_replies(fake: GeminiFake) -> None:
    fake.script("generateContent", text_reply("[1, 2]"), json_reply({"status": "answered"}))
    with pytest.raises(ModelOutputError) as caught:
        _client(fake.url, Clock()).chat_json(system="s", user="u", schema=SCHEMA)
    assert caught.value.code == "invalid_output"
    assert len(fake.requests) == 2


def test_the_worker_chat_timeout_is_used_for_chat_only(fake: GeminiFake) -> None:
    fake.script("generateContent", slow(json_reply(GOOD), 5))
    client = GeminiClient(_settings(fake.url, model_timeout_seconds=30.0), chat_timeout_seconds=0.2)
    with pytest.raises(ModelUnavailable) as caught:
        client.chat_json(system="s", user="u", schema=SCHEMA)
    assert caught.value.code == "timeout"


def test_embed_batches_by_32_at_1024_dimensions_and_keeps_order(fake: GeminiFake) -> None:
    fake.script(
        "batchEmbedContents",
        embed_reply([[float(i), 0.5, 0.25, 1.0] for i in range(32)]),
        embed_reply([[7.0, 0.5, 0.25, 1.0]]),
    )
    client = _client(fake.url, Clock())
    vectors = client.embed([f"piece {i}" for i in range(33)])
    assert len(vectors) == 33
    assert (vectors[0], vectors[31][0], vectors[32][0]) == ([0.0, 0.5, 0.25, 1.0], 31.0, 7.0)
    first, second = fake.bodies("batchEmbedContents")
    assert fake.requests[0].path == "/v1beta/models/gemini-embedding-2:batchEmbedContents"
    assert first["requests"][0] == {
        "model": "models/gemini-embedding-2",
        "content": {"parts": [{"text": "piece 0"}]},
        "outputDimensionality": 4,  # EMBEDDING_DIM (1024 in production)
    }
    assert len(first["requests"]) == 32
    assert [item["content"]["parts"][0]["text"] for item in second["requests"]] == ["piece 32"]


def test_embed_of_nothing_makes_no_request(fake: GeminiFake) -> None:
    assert _client(fake.url, Clock()).embed([]) == []
    assert fake.requests == []


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (embed_reply([[0.1, 0.2, 0.3]]), "embedding_dim"),
        (embed_reply([[0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3, 0.4]]), "embedding_count"),
        (Reply(200, {"embeddings": [{"values": [0.1, "x", 0.3, 0.4]}]}), "embedding_value"),
        (Reply(200, {"embeddings": [{"values": [0.1, True, 0.3, 0.4]}]}), "embedding_value"),
        (Reply(200, {"embeddings": [[0.1, 0.2, 0.3, 0.4]]}), "embedding_value"),
    ],
)
def test_embeddings_are_checked(fake: GeminiFake, reply: Reply, code: str) -> None:
    fake.script("batchEmbedContents", reply)
    with pytest.raises(ModelOutputError) as caught:
        _client(fake.url, Clock()).embed(["one"])
    assert caught.value.code == code


def test_an_embedding_reply_without_embeddings_is_unavailable(fake: GeminiFake) -> None:
    fake.script("batchEmbedContents", Reply(200, {"no": "embeddings"}))
    with pytest.raises(ModelUnavailable) as caught:
        _client(fake.url, Clock()).embed(["one"])
    assert caught.value.code == "bad_response"


def test_read_image_sends_the_crop_to_the_ocr_model_and_strips_the_text(fake: GeminiFake) -> None:
    fake.script("generateContent", text_reply("  1,23,456.00\n"))
    client = _client(fake.url, Clock(), ocr_model="gemini-3.8-flash", ocr_media_resolution="medium")
    assert client.read_image(png=PNG, instruction="Read the amount.") == "1,23,456.00"
    (request,) = fake.requests
    assert request.path == "/v1beta/models/gemini-3.8-flash:generateContent"
    parts = request.body["contents"][0]["parts"]
    assert parts[0] == {"text": "Read the amount."}
    assert parts[1]["media_resolution"] == {"level": "MEDIA_RESOLUTION_MEDIUM"}
    assert "responseJsonSchema" not in request.body["generationConfig"]
    assert "maxOutputTokens" not in request.body["generationConfig"]


# The key is a secret.


def test_the_key_is_never_in_a_log_an_error_or_a_repr(
    fake: GeminiFake, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fake.script(
        "generateContent",
        error_reply(400, "INVALID_ARGUMENT"),
        *[error_reply(503, "UNAVAILABLE")] * BREAKER_FAILURES,
        error_reply(403, "PERMISSION_DENIED"),
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
    assert "SECRET-ERROR-MESSAGE" not in caplog.text  # error bodies are never logged
    assert KEY not in repr(client) and KEY not in repr(client.status())
    assert KEY not in repr(_settings(fake.url))


def test_the_client_refuses_a_missing_key_a_plain_http_host_or_a_strange_model_id() -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiClient(_settings("https://generativelanguage.googleapis.com/v1beta", gemini_api_key=None))
    with pytest.raises(ValueError, match="https"):
        GeminiClient(_settings("http://example.com/v1beta"))
    with pytest.raises(ValueError, match="model ID"):
        GeminiClient(_settings("https://generativelanguage.googleapis.com/v1beta", ocr_model="../keys"))


# build_model_client (PRD 5 question 4).


def test_without_ai_there_is_no_client_and_no_gemini_call(fake: GeminiFake) -> None:
    assert build_model_client(make_settings(model_enabled=False)) is None
    assert build_model_client(_settings(fake.url, model_enabled=False)) is None
    assert isinstance(build_model_client(make_settings(model_enabled=True)), OllamaClient)
    assert fake.requests == []


def test_a_gemini_chat_model_reads_with_gemini_and_embeds_with_ollama(fake: GeminiFake) -> None:
    """Question 4: chat and reading go to Gemini in PR 1; embeddings stay on the
    existing model until M5 switches MODEL_EMBED in PR 3."""
    settings = _settings(fake.url, model_embed="qwen3-embedding:0.6b", model_base_url=closed_port_url())
    client = build_model_client(settings)
    assert client is not None
    assert (client.chat_model, client.embed_model) == (PRO, "qwen3-embedding:0.6b")
    fake.script("generateContent", json_reply(GOOD), text_reply("12"))
    assert client.chat_json(system="s", user="u", schema=SCHEMA) == GOOD
    assert client.read_image(png=PNG, instruction="Read.") == "12"
    with pytest.raises(ModelUnavailable) as caught:
        client.embed(["one"])
    assert caught.value.code == "unreachable"  # Ollama's closed port, not Gemini
    assert [request.path.rpartition(":")[2] for request in fake.requests] == ["generateContent"] * 2


def test_gemini_chat_and_embed_models_give_the_gemini_client(fake: GeminiFake) -> None:
    client = build_model_client(_settings(fake.url), chat_timeout_seconds=1.0)
    assert isinstance(client, GeminiClient)
    fake.script("batchEmbedContents", embed_reply([[0.1, 0.2, 0.3, 0.4]]))
    assert client.embed(["one"]) == [[0.1, 0.2, 0.3, 0.4]]


def test_an_ollama_chat_model_with_a_gemini_embed_model_splits_the_other_way(fake: GeminiFake) -> None:
    settings = _settings(fake.url, model_chat="qwen3:4b", model_base_url=closed_port_url())
    client = build_model_client(settings)
    assert client is not None
    fake.script("batchEmbedContents", embed_reply([[0.1, 0.2, 0.3, 0.4]]))
    assert client.embed(["one"]) == [[0.1, 0.2, 0.3, 0.4]]
    with pytest.raises(ModelUnavailable) as caught:
        client.chat_json(system="s", user="u", schema=SCHEMA)
    assert caught.value.code == "unreachable"


def test_the_fake_answers_only_what_a_test_scripted(fake: GeminiFake) -> None:
    clock = Clock()
    with pytest.raises(ModelUnavailable) as caught:
        _read(_client(fake.url, clock), deadline=clock.now + 0.4)
    assert caught.value.code == "server_error"  # 599: nothing scripted
    assert fake.unanswered >= 1
    assert isinstance(fake.requests[0], Recorded)
    assert gemini_fake.API_PATH in fake.url
