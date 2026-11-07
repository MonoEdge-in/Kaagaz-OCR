"""Ask's streamed draft through OpenRouter (#535): ``chat_json_stream`` against the fake API.

The same request as ``chat_json`` with ``stream: true``; each piece of text handed out as it
comes; the reply put back together, counted on the ledger from the last chunk's usage; the
limiter, breaker and deadline as ever; no repair turn, and nothing sent twice once text went out.
"""

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest

from kaagaz.ai import openrouter
from kaagaz.ai import usage as ledger
from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.ai.openrouter import CHAT_THINKING_TOKENS, STRICT_SCHEMA, Usage
from tests.openrouter_fake import (
    OpenRouterFake,
    error_reply,
    json_stream,
    running_fake,
    stream_reply,
    usage,
)
from tests.test_openrouter_client import GOOD, PRO, SCHEMA, THE_PROVIDER_BLOCK, Clock, _client


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        yield server


def _stream(client: openrouter.OpenRouterClient, heard: list[str], **options: Any) -> dict[str, Any]:
    return client.chat_json_stream(
        system="rules", user="question", schema=SCHEMA, on_text=heard.append, **options
    )


def test_the_stream_asks_as_chat_json_does_with_stream_on_and_hands_out_every_piece(
    fake: OpenRouterFake,
) -> None:
    fake.script("completions", json_stream(GOOD, size=5, counts=usage(1500, 500, 200, cost=0.25)))
    client = _client(fake.url, Clock())
    heard: list[str] = []

    assert _stream(client, heard) == GOOD

    assert "".join(heard) == json.dumps(GOOD)
    assert len([piece for piece in heard if piece]) > 3  # piece by piece, not all at the end
    (request,) = fake.requests
    assert request.body == {
        "model": PRO,
        "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "question"}],
        "stream": True,
        "usage": {"include": True},  # the tokens and cost in the last chunk, asked for explicitly
        "reasoning": {"effort": "low", "exclude": True},
        "max_completion_tokens": 512 + CHAT_THINKING_TOKENS["low"],
        "provider": THE_PROVIDER_BLOCK,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "page", "strict": STRICT_SCHEMA, "schema": SCHEMA},
        },
    }
    assert client.usage() == Usage(1500, 500, 0.25)  # the ledger, from the last chunk's usage


def test_the_stream_is_billed_on_the_ledger_with_its_provider(
    fake: OpenRouterFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ledger, "record_call", lambda *args, **kwargs: calls.append(args))
    fake.script("completions", json_stream(GOOD, counts=usage(10, 20, 5, cost=0.5)))

    _stream(_client(fake.url, Clock()), [])

    assert calls == [(PRO, "Google", 10, 20, 5, 0.5)]


def test_a_stream_without_usage_is_counted_at_its_worst_case_never_as_nothing(
    fake: OpenRouterFake, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ledger, "record_call", lambda *args, **kwargs: calls.append(args))
    fake.script("completions", json_stream(GOOD, with_usage=False))

    assert _stream(_client(fake.url, Clock()), []) == GOOD  # the reply itself is fine

    body = fake.bodies()[0]
    said = len(json.dumps(body["messages"], ensure_ascii=False).encode())
    text = len(json.dumps(GOOD).encode())
    limit = body["max_completion_tokens"]
    # No cost: the ledger prices these tokens at the catalogue's and marks it estimated (db/usage.py).
    assert calls == [(PRO, "Google", said, limit, limit - text, None)]
    assert said > 0 and limit > 0
    assert any(getattr(r, "code", None) == "stream_usage_missing" for r in caplog.records)


def test_a_reply_that_is_not_the_schema_is_invalid_with_no_repair_turn(fake: OpenRouterFake) -> None:
    fake.script("completions", stream_reply(['{"status": "answered", ', '"lines": [1, 2]}']))
    with pytest.raises(ModelOutputError) as caught:
        _stream(_client(fake.url, Clock()), [])
    assert caught.value.code == "invalid_output"
    assert len(fake.requests) == 1  # the caller drafts again without streaming


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (stream_reply(['{"status": "ans'], finish="length", native="MAX_TOKENS"), "output_truncated"),
        (stream_reply([""], finish="content_filter", native="SAFETY"), "blocked"),
        (stream_reply(['{"status": "ans'], finish=None), "invalid_output"),  # cut off, no finish
    ],
)
def test_a_cut_blocked_or_unfinished_stream_fails_as_the_same_reply_unstreamed(
    fake: OpenRouterFake, reply: Any, code: str
) -> None:
    fake.script("completions", reply)
    with pytest.raises(ModelOutputError) as caught:
        _stream(_client(fake.url, Clock()), [])
    assert caught.value.code == code


def test_a_failure_before_any_text_is_retried_as_ever(fake: OpenRouterFake) -> None:
    fake.script("completions", error_reply(503), json_stream(GOOD))
    clock = Clock()
    assert _stream(_client(fake.url, clock), []) == GOOD
    assert len(fake.requests) == 2 and clock.backoffs()


def test_a_failure_after_text_went_out_is_never_sent_again(fake: OpenRouterFake) -> None:
    fake.script("completions", stream_reply(['{"status": "answered", "li'], error=True), json_stream(GOOD))
    heard: list[str] = []
    with pytest.raises(ModelUnavailable) as caught:
        _stream(_client(fake.url, Clock()), heard)
    assert caught.value.code == "server_error"
    assert len(fake.requests) == 1  # the text already handed out would be handed out twice
    assert "".join(heard) == '{"status": "answered", "li'


def test_the_caller_stops_the_stream_by_raising_and_the_rest_is_never_read(fake: OpenRouterFake) -> None:
    reply = json_stream(GOOD, size=2, every=0.05)
    fake.script("completions", reply)

    class Stop(Exception):
        pass

    def heard(text: str) -> None:
        if text:
            raise Stop

    client = _client(fake.url, Clock())
    with pytest.raises(Stop):
        client.chat_json_stream(system="s", user="u", schema=SCHEMA, on_text=heard)
    assert reply.stream is not None and reply.sent < len(reply.stream)
    fake.script("completions", json_stream(GOOD))
    assert _stream(client, []) == GOOD  # the breaker and the limiter are as they were


def test_a_stream_that_outlasts_the_deadline_is_a_timeout(fake: OpenRouterFake) -> None:
    fake.script("completions", json_stream(GOOD, size=1, every=0.05))
    client = _client(fake.url)
    with pytest.raises(ModelUnavailable) as caught:
        _stream(client, [], deadline=time.monotonic() + 0.3)
    assert caught.value.code == "timeout"
