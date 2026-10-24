"""Follow-ups (PRD 3 H1) and cross-page citations (H4) in AnswerService, with the golden
fakes (tests/test_answering_golden.py). The model routes the rewrite call and the draft
call separately, so one canned reply can never serve both."""

import dataclasses
from collections.abc import Callable
from datetime import date
from typing import Any

from kaagaz.answering import draft, rewrite
from kaagaz.answering.rewrite import Turn
from tests.test_answering_golden import A, FakeModel, honest, piece, register_row, service

HIGHEST = Turn("Kiska profit sabse jyada tha last month?", "Sharma Traders had the highest profit, 1,20,000.")


class RoutingModel(FakeModel):
    """``rewritten`` answers the rewrite calls, ``drafts`` the draft calls; ``calls`` records
    (which, user prompt). A call of any other kind fails the test."""

    def __init__(self, rewritten: str, drafts: Callable[[str, str], dict[str, Any]] = honest) -> None:
        super().__init__(drafts)
        self.rewritten = rewritten
        self.calls: list[tuple[str, str]] = []

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
        if system == rewrite.SYSTEM_PROMPT:
            self.calls.append(("rewrite", user))
            return {"question": self.rewritten}
        assert system == draft.SYSTEM_PROMPT
        self.calls.append(("draft", user))
        return super().chat_json(system=system, user=user, schema=schema, max_tokens=max_tokens)


def test_the_fixed_query_plan_runs_on_the_rewrite() -> None:
    model = RoutingModel("Kiska profit sabse kam tha last month?")
    answers, retriever, fixed = service(model=model)

    answer = answers.answer(A, "Aur sabse kam?", history=[HIGHEST]).to_json()

    assert answer["rewritten_question"] == "Kiska profit sabse kam tha last month?"
    said = [s["text"] for s in answer["sentences"]]
    assert said == ["Mehta Textiles had the lowest profit, 80,000, in August 2026."]
    # Changed on purpose (PRD 6 C2, 24 Oct 2026): the numbers rule is checked first ("figures").
    assert (fixed.calls, retriever.calls, [kind for kind, _ in model.calls]) == (
        ["figures", "top_group"],
        [],
        ["rewrite"],
    )


def test_search_runs_on_the_rewrite_and_a_refused_one_is_never_used() -> None:
    """Edge case 8: a number the history does not hold means the question as asked."""
    turn = Turn("Who sent bill INV-1042?", "Sharma Traders sent INV-1042.")
    accepted = RoutingModel("What is the grand total of INV-1042?")
    refused = RoutingModel("What is the grand total of INV-1042 from 2025?")
    (first, first_search, _), (second, second_search, _) = service(model=accepted), service(model=refused)

    used = first.answer(A, "And its grand total?", history=[turn]).to_json()
    kept = second.answer(A, "And its grand total?", history=[turn]).to_json()

    assert (used["status"], used["rewritten_question"]) == (
        "answered",
        "What is the grand total of INV-1042?",
    )
    assert [kind for kind, _ in accepted.calls] == ["rewrite", "draft"]
    assert first_search.calls[0][1] == "grand total inv-1042"
    assert (kept["rewritten_question"], second_search.calls[0][1]) == (None, "grand total")


def test_no_history_or_the_model_off_means_no_rewrite() -> None:
    model = RoutingModel("What is the grand total of INV-1042?")
    assert service(model=model)[0].answer(A, "Grand total of INV-1042?").rewritten_question is None
    assert [kind for kind, _ in model.calls] == ["draft"]
    off = service()[0].answer(A, "Aur sabse kam munafa kiska tha last month?", history=[HIGHEST])
    assert (off.rewritten_question, off.normalized_question) == (None, "aur lowest profit whose last month")


def test_a_cross_page_piece_cites_both_of_its_pages() -> None:
    """H4: page_from and page_to, and a spot on each page even when the answer used one line."""
    on_15 = {"kind": "box", "page": 15, "box": [0.1, 0.8, 0.5, 0.82]}
    on_16 = {"kind": "box", "page": 16, "box": [0.1, 0.1, 0.3, 0.12]}
    report = piece("report.pdf", "Profit of the month Sept is\n$12,000", regions=[on_15, on_16])

    def profit(system: str, user: str) -> dict[str, Any]:
        return {"status": "answered", "sentences": [{"text": "The profit was $12,000.", "evidence": [1]}]}

    pieces = [dataclasses.replace(report, page_from=15, page_to=16)]
    answer = service(pieces=pieces, model=RoutingModel("", profit))[0].answer(A, "Profit of the month?")

    (citation,) = answer.to_json()["citations"]
    assert (citation["page_no"], citation["page_from"], citation["page_to"]) == (15, 15, 16)
    assert citation["regions"] == [on_15, on_16]
    assert (citation["label_page_no"], citation["label_regions"]) == (None, [])


def test_a_value_whose_label_is_on_another_page_carries_the_label_spot() -> None:
    label = {"kind": "box", "page": 1, "box": [0.1, 0.9, 0.2, 0.92]}
    spot = {"kind": "box", "page": 2, "box": [0.6, 0.1, 0.8, 0.12]}
    row = register_row(2, "Sharma Traders", 120000, date(2026, 8, 10))
    row[1] = dataclasses.replace(
        row[1],
        ref=dataclasses.replace(row[1].ref, page_no=2, region=spot, label_page_no=1, label_region=label),
    )

    answer = service(records=[row])[0].answer(A, "Kiska profit sabse jyada tha last month?").to_json()

    by_field = {c["snippet"].split(":")[0]: c for c in answer["citations"]}
    profit = by_field["profit"]
    assert (profit["page_no"], profit["page_from"], profit["page_to"]) == (2, 2, 2)
    assert (profit["label_page_no"], profit["label_regions"]) == (1, [label])
    assert (by_field["date"]["label_page_no"], by_field["date"]["label_regions"]) == (None, [])
