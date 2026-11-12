"""Ask speed (24 Oct 2026, service step 19), with fakes: a first question's search beside its
plan call gives the same answer, sooner; the plan call's deadline. The database side (each
thread's own transaction, the fused search's ranking) is in test_ask_speed_db.py."""

import dataclasses
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from kaagaz.ai.client import ModelUnavailable
from kaagaz.answering import clarify, rewrite
from kaagaz.answering.expansion import NO_EXPANSION, Expansion
from kaagaz.answering.rewrite import Turn
from kaagaz.answering.service import AnswerService
from kaagaz.search.scope import Scope
from kaagaz.settings import Settings
from tests.test_answering_clarify import PUNY, FakeVocabulary, entry, plan
from tests.test_answering_context import (
    LEAVES,
    OFFER_LETTER,
    SEARCH_ONLY,
    SETTINGS,
    A,
    FakeContext,
    Fused,
    Routed,
)
from tests.test_answering_golden import NOW, UNUSED_DB, FakeFixedQueries, FakeHit, FakeUnchecked

LEAVE_QUESTION = "How many days of casual leave do I get?"


class Planner(Routed):
    """``Routed`` whose rewrite call takes ``plan_s`` seconds (a ``deadline`` before then is a
    ``timeout``, as OpenRouterClient's) and gives the question as asked, ``aspects`` and
    ``planned``; ``deadlines``: each rewrite call's."""

    def __init__(self, *, planned: Any = None, aspects: Sequence[str] = (), plan_s: float = 0.0) -> None:
        super().__init__(LEAVES, aspects=aspects)
        self.planned, self.plan_s = plan() if planned is None else planned, plan_s
        self.deadlines: list[float | None] = []

    def chat_json(self, *, system: str, user: str, schema: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if system != rewrite.SYSTEM_PROMPT:
            return super().chat_json(system=system, user=user, schema=schema)
        deadline = kwargs.get("deadline")
        self.deadlines.append(deadline)
        if deadline is not None and time.monotonic() + self.plan_s > deadline:
            time.sleep(max(0.0, deadline - time.monotonic()))
            raise ModelUnavailable("timeout")
        time.sleep(self.plan_s)
        return {**super().chat_json(system=system, user=user, schema=schema), "plan": self.planned}


class Timed(Fused):
    """``Fused`` whose every search takes ``search_s`` seconds; ``threads``: each search's thread."""

    def __init__(self, pieces: list[FakeHit], search_s: float = 0.0) -> None:
        super().__init__(pieces)
        self.search_s = search_s
        self.threads: list[int] = []

    def search(
        self,
        customer_id: str,
        text: str,
        embedding: list[float] | None,
        limit: int,
        scope: Scope,
        expansion: Expansion = NO_EXPANSION,
    ) -> list[FakeHit]:
        self.threads.append(threading.get_ident())
        time.sleep(self.search_s)
        return super().search(customer_id, text, embedding, limit, scope, expansion)


def service(
    model: Planner,
    retriever: Timed,
    *entries: Any,
    context: FakeContext | None = None,
    settings: Settings = SEARCH_ONLY,
) -> AnswerService:
    return AnswerService(
        db=UNUSED_DB,
        model=model,
        settings=settings,
        clock=lambda: NOW,
        retriever=retriever,
        translated_search=retriever,
        fixed_queries=FakeFixedQueries({}),
        unchecked=FakeUnchecked(),
        context=context,
        vocabulary=FakeVocabulary(*entries),
    )


HISTORY = [Turn("What is the salary?", "The salary is Rs. 50,000 a month.")]
KRISHANA = "How many days of casual leave does Krishana get?"
NIKHIL = entry("person", "Nikhil Varde")  # a name the questions never use
# name: (question, history, the rewrite call's plan, its aspects, the vocabulary, a context port,
# the searches the early one adds: 0 when it is the answer's, 1 when it is dropped)
CASES: dict[str, tuple[str, list[Turn], Any, list[str], list[Any], bool, int]] = {
    "first question": (LEAVE_QUESTION, [], plan(), [], [NIKHIL], False, 0),
    "follow-up": (LEAVE_QUESTION, HISTORY, plan(), [], [], False, 0),
    "clarify": (PUNY, [], plan(places=["Puny"]), [], [entry("place", "Pune")], False, 1),
    "name resolved": (
        KRISHANA,
        [],
        plan(people=["Krishana"]),
        [],
        [entry("person", "Krishna Sohoni")],
        False,
        1,
    ),
    # Changed on purpose (#535): the early search is fused with the aspects', never searched again.
    "aspects": (LEAVE_QUESTION, [], plan(), ["holidays", "casual leave", "maternity leave"], [], True, 0),
}


def ask(name: str) -> tuple[str, Timed]:
    question, history, planned, aspects, entries, context, _ = CASES[name]
    retriever = Timed(OFFER_LETTER)
    model = Planner(planned=planned, aspects=aspects)
    port = FakeContext(OFFER_LETTER, fits=False) if context else None
    answer = service(model, retriever, *entries, context=port).answer(A, question, history=history)
    return json.dumps(answer.to_json(), sort_keys=True), retriever


@pytest.mark.parametrize("name", CASES)
def test_the_answer_is_the_same_byte_for_byte_with_and_without_the_overlap(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clarify, "uuid", SimpleNamespace(uuid4=lambda: "clarify-1"))  # its one random part
    overlapped, early = ask(name)
    # A first question's search ran beside the plan call, on a thread of its own; a follow-up's never.
    beside = [thread for thread in early.threads if thread != threading.get_ident()]
    assert len(beside) == (0 if name == "follow-up" else 1)

    def one_after_another(self: AnswerService, rewriting: Callable[[], Any], *_: Any) -> tuple[Any, None]:
        return rewriting(), None  # no early search: the steps as they ran before step 19

    monkeypatch.setattr(AnswerService, "_overlapped", one_after_another)
    alone, retriever = ask(name)
    assert all(thread == threading.get_ident() for thread in retriever.threads)
    assert len(early.threads) - len(retriever.threads) == CASES[name][-1]
    assert overlapped == alone
    shown = json.loads(alone)
    assert shown["status"] == ("clarify" if name == "clarify" else "answered")
    assert (shown["rewritten_question"] is not None) == (name == "name resolved")


def test_a_first_question_takes_the_plan_calls_time_not_the_plan_and_the_search(
    caplog: pytest.LogCaptureFixture,
) -> None:
    model, retriever = Planner(plan_s=1.0), Timed(OFFER_LETTER, search_s=0.6)
    answers = service(model, retriever, NIKHIL)  # a vocabulary: the plan call is made

    caplog.set_level(logging.INFO, logger="kaagaz.answering.timing")
    start = time.monotonic()
    answer = answers.answer(A, LEAVE_QUESTION)
    took = time.monotonic() - start

    assert answer.status == "answered" and len(retriever.threads) == 1
    assert 1.0 <= took < 1.4  # 1.6 one after another
    (line,) = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    found = re.search(r" overlap=1 rewrite_ms=(\d+) .* overlap_ms=(\d+) ", line)
    assert found is not None, line
    assert int(found[1]) >= 1000 and int(found[2]) >= 600  # the search's time ran inside the plan's


def test_a_plan_call_past_its_deadline_is_skipped_on_a_first_question_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = dataclasses.replace(SEARCH_ONLY, answer_plan_timeout_seconds=0.2)
    model, retriever = Planner(planned=plan(places=["Puny"]), plan_s=5.0), Timed(OFFER_LETTER)
    answers = service(model, retriever, entry("place", "Pune"), settings=settings)
    question = "How many days of casual leave do I get in Puny?"

    caplog.set_level(logging.INFO, logger="kaagaz.answering.rewrite")
    start = time.monotonic()
    answer = answers.answer(A, question)
    assert time.monotonic() - start < 1.0
    assert answer.status == "answered"  # without its plan: no "Did you mean Pune?"
    logged = [r for r in caplog.records if r.name == "kaagaz.answering.rewrite"]
    assert [getattr(r, "code", None) for r in logged] == ["plan_timeout"]
    assert "Puny" not in caplog.text

    model.plan_s = 0.3  # a follow-up keeps the model's own timeout: its plan comes, and asks
    followed = answers.answer(A, question, history=HISTORY)
    assert followed.status == "clarify" and model.deadlines == [pytest.approx(start + 0.2, abs=0.1), None]


@pytest.mark.parametrize("whole", [True, False])
def test_with_no_vocabulary_a_first_question_makes_no_plan_call_and_answers_the_same(
    whole: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    def first(*entries: Any) -> tuple[str, Planner]:
        model, context = Planner(), FakeContext(OFFER_LETTER) if whole else None
        answers = service(
            model, Timed(OFFER_LETTER), *entries, context=context, settings=SETTINGS if whole else SEARCH_ONLY
        )
        return json.dumps(answers.answer(A, LEAVE_QUESTION).to_json(), sort_keys=True), model

    skipped, model = first()
    assert model.rewrites == [] and json.loads(skipped)["status"] == "answered"
    assert len(first(NIKHIL)[1].rewrites) == 1  # a vocabulary: the plan call as today
    monkeypatch.setattr(FakeVocabulary, "any_keys", lambda self, customer_id: True)
    asked, today = first()  # the call as before this check: same answer, one call more
    assert len(today.rewrites) == 1 and today.rewrites[0].endswith("Plan: yes.")
    assert skipped == asked


def test_with_no_vocabulary_a_call_for_aspects_or_a_follow_up_is_as_today() -> None:
    aspects = Planner(aspects=["holidays"])
    service(aspects, Timed(OFFER_LETTER), context=FakeContext(OFFER_LETTER, fits=False)).answer(
        A, LEAVE_QUESTION
    )
    followed = Planner()
    service(followed, Timed(OFFER_LETTER)).answer(A, LEAVE_QUESTION, history=HISTORY)
    for model in (aspects, followed):
        (prompt,) = model.rewrites
        assert prompt.endswith("Plan: yes.")  # the same prompt: the plan still asked for
    assert "Aspects: at most 6." in aspects.rewrites[0]
