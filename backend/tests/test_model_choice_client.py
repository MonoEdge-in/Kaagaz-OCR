"""Model choice and AI spend in the model client (council 11 Oct 2026, MC2, MS5, SP1), against
the fake OpenRouter API: every page read carries the provider block of the provider
resolved for its model, a provider outside OPENROUTER_PROVIDERS is refused before anything
is sent, Anthropic's ``refusal`` is a block, each model has its own circuit breaker, and
every billed reply (blocked, cut off and repaired ones too) reaches the usage ledger."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from kaagaz.ai import usage
from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.ai.openrouter import (
    BLOCKED_FINISH_REASONS,
    BREAKER_FAILURES,
    BREAKER_OPEN_SECONDS,
    FAILURE_GAP_SECONDS,
    ModelGate,
    OpenRouterClient,
    PageReply,
    ProviderNotAllowed,
)
from kaagaz.db.engine import Database
from kaagaz.db.usage import UsageRow
from kaagaz.settings import Settings
from tests.openrouter_fake import (
    KEY,
    OpenRouterFake,
    Reply,
    chat_reply,
    cut_off,
    error_reply,
    failed_part_way,
    json_reply,
    running_fake,
)
from tests.openrouter_fake import usage as counts
from tests.settings_factory import make_settings

CUSTOMER = "00000000-0000-0000-0000-00000000000a"
USER = "00000000-0000-0000-0000-00000000000b"
DOCUMENT = "00000000-0000-0000-0000-00000000000c"
PRO = "google/gemini-3.1-pro-preview"
CLAUDE = "anthropic/claude-sonnet-5.5"
GPT = "openai/gpt-6-luna"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"],
    "additionalProperties": False,
}
GOOD = {"lines": ["Total 5,000"]}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        yield server


def _settings(url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "model_enabled": True,
        "openrouter_api_key": KEY,
        "openrouter_base_url": url,
        "model_rpm": 10**9,
        "model_timeout_seconds": 5.0,
    }
    return make_settings(**{**values, **overrides})


def _client(url: str, clock: Clock, **overrides: Any) -> OpenRouterClient:
    settings = _settings(url, **overrides)
    gate = ModelGate(
        settings,
        clock=clock,
        sleep=clock.sleep,
        random=lambda: 0.5,
        wall_clock=lambda: datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC),
    )
    return OpenRouterClient(settings, gate=gate)


def _read(client: OpenRouterClient, **options: Any) -> PageReply:
    values: dict[str, Any] = {
        "png": b"\x89PNG made-up page",
        "instruction": "Copy the page.",
        "schema": SCHEMA,
        "media_resolution": "high",
        "thinking_level": "low",
        "deadline": client.now() + 120,
    }
    return client.read_page(**{**values, **options})


class _Ledger:
    """A database whose usage saves are kept in memory: ``kaagaz.ai.usage`` saves through
    ``insert_usage``, which ``ledger`` below swaps for one that appends here."""

    def __init__(self) -> None:
        self.rows: list[UsageRow] = []

    @contextmanager
    def customer(self, customer_id: str) -> Iterator["_Ledger"]:
        yield self


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> _Ledger:
    def insert(conn: _Ledger, rows: list[UsageRow]) -> None:
        conn.rows.extend(rows)

    monkeypatch.setattr(usage, "insert_usage", insert)
    return _Ledger()


@contextmanager
def _scanning(ledger: _Ledger) -> Iterator[None]:
    with usage.usage_scope(
        cast(Database, ledger), customer_id=CUSTOMER, user_id=USER, kind="scan", document_id=DOCUMENT
    ):
        yield


# MC2, MS5: the provider block of the model's provider, under the ceiling.


def test_a_page_read_carries_the_provider_block_of_its_model_and_provider(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply(GOOD), json_reply(GOOD))
    client = _client(fake.url, Clock(), openrouter_providers=frozenset({"google-vertex", "azure"}))

    _read(client, model=CLAUDE, provider="google-vertex")
    _read(client, model=GPT, provider="azure")

    first, second = fake.bodies()
    assert (first["model"], first["provider"]["only"]) == (CLAUDE, ["google-vertex"])
    assert (second["model"], second["provider"]["only"]) == (GPT, ["azure"])
    for body in (first, second):
        assert body["provider"] | {"only": None} == {
            "only": None,
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
        }


def test_a_provider_outside_the_ceiling_is_refused_and_nothing_is_sent(fake: OpenRouterFake) -> None:
    client = _client(fake.url, Clock())  # OPENROUTER_PROVIDERS unset: google-vertex only
    with pytest.raises(ProviderNotAllowed, match="provider_not_allowed"):
        _read(client, model=GPT, provider="azure")
    assert fake.requests == []


def test_the_client_takes_any_openrouter_model_id_not_only_gemini(fake: OpenRouterFake) -> None:
    """The is_gemini_model gate is gone: a Claude or GPT chat model builds a client, and a
    model ID with a variant is still refused."""
    assert OpenRouterClient(_settings(fake.url, model_chat=CLAUDE, model_embed=GPT)).chat_model == CLAUDE
    with pytest.raises(ValueError, match="model ID"):
        OpenRouterClient(_settings(fake.url, model_chat="anthropic/claude-sonnet-5.5:free"))


# G6: Anthropic's refusal is a block.


def test_a_claude_refusal_is_blocked_and_still_billed(fake: OpenRouterFake, ledger: _Ledger) -> None:
    assert "refusal" in BLOCKED_FINISH_REASONS
    fake.script(
        "completions", chat_reply("", finish="stop", native="refusal", counts=counts(900, 40, 0, 0.003))
    )
    with _scanning(ledger), pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()), model=CLAUDE, provider="google-vertex")
    assert (caught.value.code, caught.value.input_tokens, caught.value.output_tokens) == ("blocked", 900, 40)
    (row,) = ledger.rows
    assert (row.model, row.calls, row.input_tokens, row.output_tokens, row.cost_usd) == (
        CLAUDE,
        1,
        900,
        40,
        0.003,
    )


# FINAL 2: one breaker per model.


def test_a_failing_model_opens_only_its_own_breaker(fake: OpenRouterFake) -> None:
    clock = Clock()
    client = _client(fake.url, clock)
    fake.script("completions", *[error_reply(503)] * BREAKER_FAILURES)
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, model=CLAUDE, provider="google-vertex", deadline=clock.now + 1000)
    assert caught.value.code == "circuit_open"
    assert client.status() == ("down", 1000.0)  # the worst model's state

    fake.script("completions", json_reply(GOOD))
    assert _read(client, model=PRO, provider="google-vertex").value == GOOD  # another model works
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, model=CLAUDE, provider="google-vertex")
    assert caught.value.code == "circuit_open"  # still open, no request sent
    assert len(fake.requests) == BREAKER_FAILURES + 1
    assert client.status()[0] == "down"


def test_status_is_failing_while_a_model_fails_and_working_when_none_does(fake: OpenRouterFake) -> None:
    clock = Clock()
    client = _client(fake.url, clock)
    fake.script("completions", error_reply(503), json_reply(GOOD))
    _read(client, model=CLAUDE, provider="google-vertex", deadline=clock.now + 1000)  # 503, then the retry
    assert client.status() == ("working", None)
    client.gate.record(CLAUDE, failed=True)
    client.gate.record(PRO, failed=False)
    assert client.status()[0] == "failing"


def test_a_model_no_longer_called_never_keeps_the_reader_down(fake: OpenRouterFake) -> None:
    """R2: five failures open a chosen model's breaker; 10,000 s later the reader works on
    another model, and the first one, idle since, does not say down (or failing)."""
    clock = Clock()
    client = _client(fake.url, clock)
    fake.script("completions", *[error_reply(503)] * BREAKER_FAILURES)
    with pytest.raises(ModelUnavailable):
        _read(client, model=CLAUDE, provider="google-vertex", deadline=clock.now + 1000)
    assert client.status()[0] == "down"

    clock.now += BREAKER_OPEN_SECONDS
    assert client.status()[0] == "failing"  # open past its time: the next call is let through
    clock.now += 10_000
    fake.responder = lambda request: json_reply(GOOD)
    for _ in range(3):
        assert _read(client, model=PRO, provider="google-vertex").value == GOOD
    assert client.status() == ("working", None)


def test_one_old_failure_does_not_keep_the_reader_failing(fake: OpenRouterFake) -> None:
    """R2: one failure, then 50 successes on another model over the next minutes: failing
    only until the failure is FAILURE_GAP_SECONDS old, never for ever."""
    clock = Clock()
    client = _client(fake.url, clock)
    client.gate.record(CLAUDE, failed=True)
    fake.responder = lambda request: json_reply(GOOD)
    for _ in range(50):
        _read(client, model=PRO, provider="google-vertex")
        clock.now += 20  # 1,000 s in all
    assert clock.now - 1000.0 > FAILURE_GAP_SECONDS
    assert client.status() == ("working", None)


def test_a_failure_long_after_the_last_starts_the_count_again(fake: OpenRouterFake) -> None:
    """R2: failures more than FAILURE_GAP_SECONDS apart never add up to an open breaker."""
    clock = Clock()
    client = _client(fake.url, clock)
    for count in range(BREAKER_FAILURES):
        clock.now += FAILURE_GAP_SECONDS + 1 if count else 0
        client.gate.record(CLAUDE, failed=True)
    client.gate.admit(CLAUDE)  # still closed: no circuit_open
    assert client.status() == ("failing", clock.now)  # since the last failure alone


def test_a_rejected_key_stops_every_model(fake: OpenRouterFake) -> None:
    """The key latch stays the process's: one key."""
    clock = Clock()
    client = _client(fake.url, clock)
    fake.script("completions", error_reply(401))
    with pytest.raises(ModelUnavailable):
        _read(client, model=CLAUDE, provider="google-vertex")
    with pytest.raises(ModelUnavailable) as caught:
        _read(client, model=PRO, provider="google-vertex")
    assert caught.value.code == "key_rejected"
    assert len(fake.requests) == 1
    assert client.status()[0] == "key_problem"


# SP1: every billed reply reaches the ledger.


def test_a_read_page_is_one_ledger_row_with_its_tokens_cost_and_provider(
    fake: OpenRouterFake, ledger: _Ledger
) -> None:
    fake.script("completions", json_reply(GOOD, counts=counts(1500, 500, 200, 0.012)))
    with _scanning(ledger):
        _read(_client(fake.url, Clock()), model=CLAUDE, provider="google-vertex")
    assert ledger.rows == [
        UsageRow(
            customer_id=CUSTOMER,
            user_id=USER,
            document_id=DOCUMENT,
            conversation_id=None,
            kind="scan",
            model=CLAUDE,
            provider_name="Google",  # the reply's provider
            cached=False,
            calls=1,
            input_tokens=1500,
            output_tokens=500,
            reasoning_tokens=200,
            cost_usd=0.012,
        )
    ]


def test_a_repaired_read_bills_both_calls(fake: OpenRouterFake, ledger: _Ledger) -> None:
    fake.script(
        "completions",
        chat_reply("not json", counts=counts(1000, 10, 5, 0.001)),
        json_reply(GOOD, counts=counts(1100, 20, 6, 0.002)),
    )
    with _scanning(ledger):
        _read(_client(fake.url, Clock()), model=PRO, provider="google-vertex")
    (row,) = ledger.rows
    assert (row.calls, row.input_tokens, row.output_tokens, row.reasoning_tokens) == (2, 2100, 30, 11)
    assert row.cost_usd == pytest.approx(0.003)


@pytest.mark.parametrize(
    ("reply", "code"),
    [(cut_off(), "output_truncated"), (chat_reply(None, finish="content_filter", native=None), "blocked")],
)
def test_a_cut_off_or_blocked_reply_is_still_a_ledger_row(
    fake: OpenRouterFake, ledger: _Ledger, reply: Reply, code: str
) -> None:
    fake.script("completions", reply)
    with _scanning(ledger), pytest.raises(ModelOutputError) as caught:
        _read(_client(fake.url, Clock()), model=PRO, provider="google-vertex")
    assert caught.value.code == code
    (row,) = ledger.rows
    assert (row.model, row.calls, row.input_tokens) == (PRO, 1, 1500)


def test_a_reply_that_failed_part_way_is_billed_before_its_retry(
    fake: OpenRouterFake, ledger: _Ledger
) -> None:
    fake.script("completions", failed_part_way(), json_reply(GOOD))
    with _scanning(ledger):
        _read(_client(fake.url, Clock()), model=PRO, provider="google-vertex")
    (row,) = ledger.rows
    assert row.calls == 2


def test_a_provider_name_that_is_not_a_name_is_left_out(fake: OpenRouterFake, ledger: _Ledger) -> None:
    reply = json_reply(GOOD)
    assert isinstance(reply.body, dict)
    reply.body["provider"] = "Google; DROP TABLE"
    fake.script("completions", reply)
    with _scanning(ledger):
        _read(_client(fake.url, Clock()), model=PRO, provider="google-vertex")
    assert ledger.rows[0].provider_name is None


def test_an_unbilled_error_and_a_call_with_no_scope_make_no_row(
    fake: OpenRouterFake, ledger: _Ledger
) -> None:
    fake.script("completions", error_reply(404), json_reply(GOOD))
    client = _client(fake.url, Clock())
    with _scanning(ledger), pytest.raises(ModelUnavailable):
        _read(client, model=CLAUDE, provider="google-vertex")
    _read(client, model=PRO, provider="google-vertex")  # no scope open: not recorded
    assert ledger.rows == []
    assert client.usage().input_tokens == 1500  # the client's own totals still count it
