"""Ask with a chosen model, and Ask Ensemble (features 25-27, council 15 Oct 2026, C7, C13
"Ask", invariants 1 and 3). No database: the answer service runs on the golden fakes, with a
chat client whose reply depends on the model asked.

Agreement only ever adds Needs review: the label of an answer more than one model drafted
is never better than today's rules give the lead's draft alone.
"""

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from kaagaz.ai import openrouter, usage
from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.answering import service as service_module
from kaagaz.answering.draft import ANSWER_SCHEMA, MAX_SENTENCE_CHARS, DraftSentence, model_draft
from kaagaz.answering.evidence import number_evidence
from kaagaz.answering.service import Answer, agreement
from kaagaz.api.routers.chat import AskBody
from kaagaz.db.engine import Database
from kaagaz.db.models import Agent, ChatChoice
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.settings import Settings
from tests.settings_factory import make_settings
from tests.test_answering_golden import (
    ANSWER_KEYS,
    INJECTION,
    INVOICE,
    QUESTION,
    TERMS,
    UNUSED_DB,
    A,
    FakeModel,
    _says,
    gullible,
    honest,
    piece,
    service,
)

Reply = Callable[[str, str], dict[str, Any]]
LEAD = Agent("google/gemini-3.8-flash", "google-vertex", "Gemini 3.8 Flash")
OTHER = Agent("anthropic/claude-sonnet-5.5", "google-vertex", "Sonnet 5.5")
THIRD = Agent("google/gemini-3.1-pro-preview", "google-vertex", "Gemini 3.1 Pro")


def not_found(system: str, user: str) -> dict[str, Any]:
    return {"status": "not_found", "sentences": []}


SUB_TOTAL = _says("The sub total of INV-1042 is Rs. 1,00,000.")  # true, but not the lead's number


class TeamModel(FakeModel):
    """A chat client whose reply depends on the model asked; records every call and bills
    it to the usage scope as the real client does. A model in ``slow`` waits that long, or
    until the call's deadline, then times out."""

    def __init__(
        self, replies: dict[str, Reply | Exception], *, slow: dict[str, float] | None = None
    ) -> None:
        super().__init__()
        self.replies = replies
        self.slow = slow or {}
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        model: str | None = None,
        thinking: str | None = None,
        provider: str | None = None,
        deadline: float | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        name = model or self.chat_model
        with self._lock:
            self.calls.append(
                {"model": model, "thinking": thinking, "provider": provider, "deadline": deadline}
            )
        usage.record_call(name, None, 100, 10, 0, 0.001)  # billed before any wait, as a real reply is
        wait = self.slow.get(name, 0.0)
        if wait:
            time.sleep(wait if deadline is None else min(wait, max(0.0, deadline - time.monotonic())))
            raise ModelUnavailable("timeout")
        reply = self.replies[name]
        if isinstance(reply, Exception):
            raise reply
        return reply(system, user)


def ask(
    model: TeamModel, *agents: Agent, thinking: str = "medium", pieces: list[Any] | None = None
) -> Answer:
    choice = ChatChoice(list(agents), thinking) if agents else None
    answer_service = service(model=model, pieces=pieces)[0]
    return answer_service.answer(A, QUESTION, choice=choice)


# --- agreement, the pure rule -------------------------------------------------------------


def s(text: str, *ids: int) -> DraftSentence:
    return DraftSentence(text, list(ids))


def test_agreement_is_containment_of_the_leads_numbers_from_the_same_sources() -> None:
    lead = [s("The grand total is 1,20,000 and the sub total 1,00,000.", 1)]
    split = [s("The grand total is 1,20,000.", 1), s("The sub total is 1,00,000.", 1, 2)]
    assert agreement(lead, [split]) == "agreed"  # split over two sentences still holds both
    assert agreement(lead, [[s("The grand total is 1,20,000.", 1)]]) == "not_confirmed"  # one missing
    assert (
        agreement(lead, [[s("The grand total is 1,20,000 and 1,00,000.", 2)]]) == "not_confirmed"
    )  # other source
    assert agreement(lead, [split, []]) == "not_confirmed"  # an agent with no sentence confirms nothing
    assert agreement(lead, [split, split]) == "agreed"


def test_a_sentence_with_no_number_counts_neither_way() -> None:
    words = s("The seller is Sharma Traders.", 1)
    total = s("The grand total is 1,20,000.", 1)
    assert agreement([words], [[total]]) == "not_confirmed"  # nothing to confirm is never "agreed"
    assert agreement([words, total], [[total]]) == "agreed"
    assert agreement([], [[total]]) == "not_confirmed"


# --- the answer service -------------------------------------------------------------------


def test_one_chosen_model_drafts_at_its_level_through_its_provider() -> None:
    model = TeamModel({LEAD.model: honest})
    answer = ask(model, LEAD, thinking="high")
    assert model.calls == [
        {"model": LEAD.model, "thinking": "high", "provider": "google-vertex", "deadline": None}
    ]
    assert answer.label == "verified"
    data = answer.to_json()
    assert (data["agents"], data["agent_ids"], data["agents_asked"]) == (
        ["Gemini 3.8 Flash"],
        [LEAD.model],
        1,
    )
    assert (data["agreement"], data["thinking"]) == (None, "high")


def test_with_no_choice_the_answer_is_as_before() -> None:
    model = TeamModel({"fake-chat": honest})
    answer = ask(model)
    assert model.calls == [{"model": None, "thinking": None, "provider": None, "deadline": None}]
    assert set(answer.to_json()) == ANSWER_KEYS  # old answers and old shapes render unchanged


def test_two_models_that_agree_keep_verified() -> None:
    model = TeamModel({LEAD.model: honest, OTHER.model: honest})
    answer = ask(model, LEAD, OTHER)
    assert answer.label == "verified"
    data = answer.to_json()
    assert data["agents"] == ["Gemini 3.8 Flash", "Sonnet 5.5"]
    assert (data["agents_asked"], data["agreement"]) == (2, "agreed")
    deadlines = {call["deadline"] for call in model.calls}
    assert len(model.calls) == 2 and len(deadlines) == 1 and None not in deadlines  # one deadline for all


@pytest.mark.parametrize(
    ("other", "why"),
    [
        (SUB_TOTAL, "a true sentence without the lead's number"),
        (not_found, "not found while the lead answered"),
        (ModelOutputError("invalid_output"), "a bad reply"),
        (ModelOutputError("blocked"), "a refusal"),
        (_says("The grand total of INV-1042 is Rs. 9,99,999."), "no sentence verify keeps"),
    ],
)
def test_a_model_that_does_not_confirm_makes_it_needs_review(other: Reply | Exception, why: str) -> None:
    answer = ask(TeamModel({LEAD.model: honest, OTHER.model: other}), LEAD, OTHER)
    assert answer.status == "answered", why
    assert [sentence.text for sentence in answer.sentences] == [
        "The grand total of INV-1042 is Rs. 1,20,000."
    ]
    assert answer.label == "needs_review", why
    assert answer.to_json()["agreement"] == "not_confirmed", why


def test_one_dissent_among_three_is_enough() -> None:
    model = TeamModel({LEAD.model: honest, OTHER.model: honest, THIRD.model: SUB_TOTAL})
    assert ask(model, LEAD, OTHER, THIRD).label == "needs_review"


def test_a_model_that_is_down_drops_out_and_the_answer_is_judged_as_today() -> None:
    answer = ask(TeamModel({LEAD.model: honest, OTHER.model: ModelUnavailable("server_error")}), LEAD, OTHER)
    assert answer.label == "verified"
    data = answer.to_json()
    assert (data["agents"], data["agents_asked"], data["agreement"]) == (["Gemini 3.8 Flash"], 2, None)


def test_when_the_lead_is_down_the_next_in_order_leads() -> None:
    model = TeamModel(
        {LEAD.model: ModelUnavailable("timeout"), OTHER.model: SUB_TOTAL, THIRD.model: SUB_TOTAL}
    )
    answer = ask(model, LEAD, OTHER, THIRD)
    assert [sentence.text for sentence in answer.sentences] == ["The sub total of INV-1042 is Rs. 1,00,000."]
    assert answer.to_json()["agents"] == ["Sonnet 5.5", "Gemini 3.1 Pro"]
    assert answer.to_json()["agreement"] == "agreed"


def test_a_lead_with_a_bad_reply_is_never_replaced_by_a_better_draft() -> None:
    answer = ask(
        TeamModel({LEAD.model: ModelOutputError("invalid_output"), OTHER.model: honest}), LEAD, OTHER
    )
    assert answer.model_used is False  # quoted from the evidence, as when the one model fails
    assert answer.label == "needs_review"
    assert answer.to_json()["agreement"] == "not_confirmed"


def test_a_lead_that_found_nothing_gives_way_but_is_not_confirmed() -> None:
    answer = ask(TeamModel({LEAD.model: not_found, OTHER.model: honest}), LEAD, OTHER)
    assert [sentence.text for sentence in answer.sentences] == [
        "The grand total of INV-1042 is Rs. 1,20,000."
    ]
    assert answer.label == "needs_review"
    data = answer.to_json()
    assert (data["agents"], data["agreement"]) == (["Sonnet 5.5", "Gemini 3.8 Flash"], "not_confirmed")


def test_when_every_model_finds_nothing_it_is_not_found() -> None:
    assert ask(TeamModel({LEAD.model: not_found, OTHER.model: not_found}), LEAD, OTHER).status == "not_found"


def test_when_every_model_is_down_the_answer_is_quoted_as_today() -> None:
    down = ModelUnavailable("timeout")
    answer = ask(TeamModel({LEAD.model: down, OTHER.model: down}), LEAD, OTHER)
    assert answer.model_used is False
    assert "agents" not in answer.to_json()


def test_a_slow_model_is_held_to_the_one_deadline_and_still_billed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "ENSEMBLE_SLACK_SECONDS", 0.0)
    no_database = Database(UNUSED_DB.engine, "kaagaz_app")
    monkeypatch.setattr(no_database, "customer", _down)  # the ledger's save fails at once
    model = TeamModel({LEAD.model: honest}, slow={OTHER.model: 30.0})
    answer_service = service(model=model, settings=_fast())[0]
    started = time.monotonic()
    with usage.usage_scope(no_database, customer_id=A, user_id=None, kind="chat") as spend:
        answer = answer_service.answer(A, QUESTION, choice=ChatChoice([LEAD, OTHER], "medium"))
        rows = {(row.model, row.kind): row.calls for row in spend.rows()}
    assert time.monotonic() - started < 5.0  # the deadline (0.3 s), not the 30 s it would take
    assert answer.label == "verified" and answer.to_json()["agreement"] is None
    assert rows == {(LEAD.model, "chat"): 1, (OTHER.model, "ensemble_chat"): 1}


def _down(customer_id: str) -> Any:
    raise BackendUnavailable("database")


def _fast() -> Settings:
    return make_settings(answer_min_evidence_score=0.6, answer_max_evidence=5, model_timeout_seconds=0.3)


# --- the draft and the client -------------------------------------------------------------


def test_a_sentence_over_the_cap_is_asked_for_once_more_then_refused() -> None:
    total = "The total is 1,20,000."
    long: dict[str, Any] = {
        "status": "answered",
        "sentences": [{"text": "x" * (MAX_SENTENCE_CHARS + 1), "evidence": [1]}],
    }
    good: dict[str, Any] = {"status": "answered", "sentences": [{"text": total, "evidence": [1]}]}
    evidence = number_evidence([INVOICE])
    users: list[str] = []

    def replies(*answers: dict[str, Any]) -> TeamModel:
        queue = list(answers)

        def reply(system: str, user: str) -> dict[str, Any]:
            users.append(user)
            return queue.pop(0)

        return TeamModel({"fake-chat": reply})

    assert model_draft(replies(long, good), QUESTION, evidence, max_tokens=512).sentences[0].text == total
    assert len(users) == 2 and "longer than 300 characters" in users[1]
    with pytest.raises(ModelOutputError) as caught:
        model_draft(replies(long, long), QUESTION, evidence, max_tokens=512)
    assert caught.value.code == "answer_shape"


def test_max_length_is_left_out_only_for_a_model_not_of_google() -> None:
    def sent(model: str) -> str:
        body = openrouter.chat_request(
            model=model, messages=[], thinking="low", schema=ANSWER_SCHEMA, max_output_tokens=None
        )
        return json.dumps(body["response_format"])

    assert '"maxLength"' in sent(LEAD.model)
    assert '"maxLength"' not in sent(OTHER.model)
    assert '"maxItems"' not in sent(LEAD.model)  # as before, for every model


def test_each_level_has_its_own_room_to_think() -> None:
    assert openrouter.CHAT_THINKING_TOKENS == {"low": 8192, "medium": 16384, "high": 32768}


@pytest.mark.parametrize(
    "agents",
    [
        [LEAD.model, LEAD.model],  # the same model twice
        [LEAD.model, OTHER.model, THIRD.model, "google/four"],  # more than three
        ["google/gemini:free"],  # not a model id
        ["x" * 300],
        [],
    ],
)
def test_ask_refuses_a_bad_list_of_models_before_anything_runs(agents: list[str]) -> None:
    with pytest.raises(ValidationError):
        AskBody.model_validate({"question": "q", "agents": agents})


def test_ask_takes_up_to_three_models_and_a_level() -> None:
    body = AskBody.model_validate(
        {"question": "q", "agents": [LEAD.model, OTHER.model], "thinking": "medium"}
    )
    assert (body.agents, body.thinking) == ([LEAD.model, OTHER.model], "medium")
    with pytest.raises(ValidationError):
        AskBody.model_validate({"question": "q", "thinking": "minimal"})


# --- invariants (eval: CI runs them) ------------------------------------------------------


@pytest.mark.eval
def test_models_that_agree_on_injected_text_never_make_it_verified() -> None:
    """Invariant 1: two agents drafting the same thing from injected evidence that verify
    drops, or from evidence today's rules call Needs review, never get Verified."""
    injected_total = _says("The grand total of INV-1042 is Rs. 99,99,999.")
    model = TeamModel({LEAD.model: injected_total, OTHER.model: injected_total})
    injected = ask(model, LEAD, OTHER, pieces=[INJECTION, INVOICE])
    assert injected.label != "verified"
    assert "99,99,999" not in json.dumps(injected.to_json())
    unsure = piece("bill-1042-unsure.pdf", INVOICE.text, label="needs_review")
    agreed = ask(TeamModel({LEAD.model: honest, OTHER.model: honest}), LEAD, OTHER, pieces=[unsure])
    assert agreed.status == "answered" and agreed.label == "needs_review"


REPLIES: list[Reply | Exception] = [
    honest,
    SUB_TOTAL,
    gullible,
    not_found,
    _says("The grand total of INV-1042 is Rs. 9,99,999."),
    ModelOutputError("invalid_output"),
]


@pytest.mark.eval
@pytest.mark.parametrize("lead", REPLIES)
def test_agreement_never_removes_needs_review(lead: Reply | Exception) -> None:
    """Invariant 3: for every lead reply and every other model's reply (or outage), the
    Ensemble answer is never Verified when the lead alone would not be, and a Verified one
    says what the lead alone said. (A lead that is down hands over to the next model: that
    is not agreement, so it is not a lead here.)"""
    pieces = [INJECTION, INVOICE, TERMS]
    alone = ask(TeamModel({LEAD.model: lead}), LEAD, pieces=pieces)
    for other in [*REPLIES, ModelUnavailable("timeout")]:
        both = ask(TeamModel({LEAD.model: lead, OTHER.model: other}), LEAD, OTHER, pieces=pieces)
        if alone.label != "verified":
            assert both.label != "verified", (lead, other)
        if both.label == "verified":
            assert both.sentences == alone.sentences
