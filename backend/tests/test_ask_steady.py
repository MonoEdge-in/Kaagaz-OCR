"""Steady answers (Tushar, 16 Oct 2026: "Answer is inconsistent between models"). The answer
depends on the documents, not the model: every model is given the same evidence in the same
order, the same prompt, schema and reply budget, and verify judges each the same way; the
same question searches the same way every run, its rewrite (which steers the search) asked
for at temperature 0 from Google models. The draft keeps the model's default temperature
(Google's guidance for Gemini 3), and Anthropic models never get one. The search's own
order on ties is pinned on PostgreSQL in tests/test_ask_counts_db.py.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest

from kaagaz.ai.openrouter import OpenRouterClient
from kaagaz.answering.draft import model_draft
from kaagaz.answering.rewrite import REWRITE_TEMPERATURE, Turn, rewrite
from kaagaz.answering.rewrite import SYSTEM_PROMPT as REWRITE_PROMPT
from kaagaz.answering.translate import translate
from kaagaz.db.models import Agent, ChatChoice
from tests.openrouter_fake import OpenRouterFake, json_reply, running_fake
from tests.test_answering_golden import INVOICE, QUESTION, TERMS, A, FakeModel, honest, service
from tests.test_openrouter_client import PRO as MODEL_CHAT
from tests.test_openrouter_client import SCHEMA, _settings

GEMINI = Agent("google/gemini-3.8-flash", "google-vertex", "Gemini 3.8 Flash")
SONNET = Agent("anthropic/claude-sonnet-5.5", "google-vertex", "Sonnet 5.5")
PRO = Agent("google/gemini-3.1-pro-preview", "google-vertex", "Gemini 3.1 Pro")
TEAM_KEYS = {"agents", "agent_ids", "agents_asked", "agreement", "thinking"}
HISTORY = [Turn("Show me bill-1042.pdf", None)]


class Recorder(FakeModel):
    """Rewrites to the question as asked (deterministic), answers honestly, and keeps
    everything each call was given."""

    def __init__(self) -> None:
        super().__init__()
        self.given: list[tuple[str | None, float | None, tuple[str, str, str, int | None]]] = []

    def chat_json(self, *, system: str, user: str, schema: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        prompt = (system, user, json.dumps(schema), kwargs.get("max_tokens"))
        self.given.append((kwargs.get("model"), kwargs.get("temperature"), prompt))
        if system == REWRITE_PROMPT:
            return {"question": QUESTION, "aspects": []}
        return honest(system, user)

    def drafts(self) -> list[tuple[str | None, float | None, tuple[str, str, str, int | None]]]:
        return [call for call in self.given if call[2][0] != REWRITE_PROMPT]


def test_every_model_is_given_the_same_evidence_prompt_schema_and_budget() -> None:
    recorder = Recorder()
    answers = []
    for agent in (GEMINI, SONNET, PRO):
        answer_service, _, _ = service(model=recorder, pieces=[TERMS, INVOICE])
        answers.append(answer_service.answer(A, QUESTION, choice=ChatChoice([agent], "low")))
    drafts = recorder.drafts()
    assert [model for model, _, _ in drafts] == [GEMINI.model, SONNET.model, PRO.model]
    assert len({prompt for _, _, prompt in drafts}) == 1  # one prompt, evidence order included
    assert {temperature for _, temperature, _ in drafts} == {None}  # the draft keeps the default
    shapes = [{k: v for k, v in answer.to_json().items() if k not in TEAM_KEYS} for answer in answers]
    assert shapes[0] == shapes[1] == shapes[2]  # the same sources, numbered the same, the same label
    assert [c.ref_id for c in answers[0].citations] == [INVOICE.piece_id]


def test_the_same_follow_up_searches_the_same_way_every_run() -> None:
    runs = []
    for _ in range(3):
        recorder = Recorder()
        answer_service, retriever, _ = service(model=recorder, pieces=[TERMS, INVOICE])
        answer = answer_service.answer(A, QUESTION, history=HISTORY)
        runs.append((retriever.calls, recorder.given, answer.to_json()))
    assert runs[0] == runs[1] == runs[2]
    rewrites = [call for call in runs[0][1] if call[2][0] == REWRITE_PROMPT]
    assert [temperature for _, temperature, _ in rewrites] == [REWRITE_TEMPERATURE] == [0.0]


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        yield server


def _client(fake: OpenRouterFake) -> OpenRouterClient:
    return OpenRouterClient(_settings(fake.url))


def test_the_rewrite_and_translation_send_temperature_zero_to_google_models(fake: OpenRouterFake) -> None:
    fake.script(
        "completions",
        json_reply({"question": "What is the grand total of bill-1042.pdf?", "aspects": []}),
        json_reply({"queries": [{"script": "Devanagari", "text": "एकूण रक्कम"}]}),
    )
    client = _client(fake)
    rewrite(client, "And its grand total?", HISTORY)
    translate(client, "What is the grand total?", ["DEVANAGARI"])
    bodies = fake.bodies()
    assert MODEL_CHAT.startswith("google/")
    assert [body["temperature"] for body in bodies] == [0.0, 0.0]


def test_the_draft_keeps_the_default_temperature(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply({"status": "not_found", "sentences": []}))
    model_draft(_client(fake), "What is the grand total?", [], max_tokens=512, chat_model=GEMINI.model)
    assert "temperature" not in fake.bodies()[-1]


def test_anthropic_models_never_get_a_temperature(fake: OpenRouterFake) -> None:
    """Anthropic refuses a temperature other than 1 while thinking."""
    fake.script("completions", json_reply({"status": "answered", "lines": []}))
    _client(fake).chat_json(system="s", user="u", schema=SCHEMA, model=SONNET.model, temperature=0.0)
    assert "temperature" not in fake.bodies()[-1]
