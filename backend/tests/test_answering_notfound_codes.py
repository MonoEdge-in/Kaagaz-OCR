"""Every not-found answer logs one line with a fixed reason code, and nothing else:
never the question or the page text (council 4 Oct 2026, cross-language PR A)."""

import logging
from datetime import date
from typing import Any

import pytest

from kaagaz.ai.client import ModelUnavailable
from kaagaz.search.scope import Scope
from tests.test_answering_golden import QUESTION, A, FakeModel, _says, honest, register_row, service

CANARY = "Zq9Canary"


def says_not_found(system: str, user: str) -> dict[str, Any]:
    return {"status": "not_found", "sentences": []}


def _codes(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.__dict__["code"] for r in caplog.records if r.getMessage() == "answer not found"]


@pytest.mark.parametrize(
    ("question", "model", "code"),
    [
        ("???", None, "notfound_empty_question"),
        (f"What is the salary of {CANARY}?", FakeModel(), "notfound_gate"),
        (QUESTION, FakeModel(says_not_found), "notfound_model"),
        (
            QUESTION,
            FakeModel(_says("The grand total of INV-1042 is Rs. 9,99,999.")),
            "notfound_verify_dropped_all",
        ),
        ("What is the grand total of INV-1042 in March 2026?", None, "notfound_no_model:date"),
        (
            "What is the grand total of INV-1042 in March 2026?",
            FakeModel(chat_error=ModelUnavailable("timeout")),
            "notfound_model_error:timeout:date",
        ),
    ],
)
def test_each_search_exit_logs_its_code(
    caplog: pytest.LogCaptureFixture, question: str, model: FakeModel | None, code: str
) -> None:
    caplog.set_level(logging.DEBUG)
    answer = service(model=model, records=[])[0].answer(A, question)
    assert answer.status == "not_found"
    assert _codes(caplog) == [code]
    assert CANARY.casefold() not in caplog.text.casefold()
    assert "1,20,000" not in caplog.text  # no page text either


def test_an_answer_logs_no_code(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert service(model=FakeModel(honest))[0].answer(A, QUESTION).status == "answered"
    assert _codes(caplog) == []


def test_the_scope_and_fixed_exits_log_their_codes(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="kaagaz.answering.service")
    nothing = Scope(document_ids=())
    assert service()[0].answer(A, QUESTION, scope=nothing).status == "not_found"
    june = [register_row(2, "Sharma Traders", 120000, date(2026, 6, 10))]  # none last month
    answer = service(records=june)[0].answer(A, "Kiska profit sabse jyada tha last month?")
    assert (answer.status, answer.route) == ("not_found", "fixed_query")
    assert _codes(caplog) == ["notfound_empty_scope", "notfound_fixed_no_rows"]
