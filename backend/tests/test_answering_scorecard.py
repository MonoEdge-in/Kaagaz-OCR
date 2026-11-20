"""The retrieval scorer (#721, kaagaz/answering/scorecard.py): the file, the ranks and the report."""

import dataclasses
import io
import json
from typing import Any

import pytest

from kaagaz.answering.scorecard import Expected, Item, first_rank, load, run
from tests.test_answering_context import page

BILL = Item("What is the total?", (Expected("bill.pdf", (3,)),), "fact")
DEED = Item("Who sold the land?", (Expected("deed.pdf", (1, 2)), Expected("copy.pdf", (5,))))


def test_a_file_loads_each_question_with_its_documents_pages_and_kind() -> None:
    raw = [
        {"question": BILL.question, "expect": [{"document": "bill.pdf", "pages": [3]}], "kind": "fact"},
        {
            "question": DEED.question,
            "expect": [{"document": "deed.pdf", "pages": [1, 2]}, {"document": "copy.pdf", "pages": [5]}],
        },
    ]

    assert load(json.dumps(raw)) == [BILL, DEED]


@pytest.mark.parametrize(
    "entry",
    [
        {"expect": [{"document": "a.pdf", "pages": [1]}]},  # no question
        {"question": " ", "expect": [{"document": "a.pdf", "pages": [1]}]},
        {"question": "q", "expect": []},
        {"question": "q", "expect": [{"document": "a.pdf", "pages": []}]},
        {"question": "q", "expect": [{"document": "a.pdf", "pages": [0]}]},
        {"question": "q", "expect": [{"document": "a.pdf", "pages": [True]}]},
        {"question": "q", "expect": [{"document": "", "pages": [1]}]},
        {"question": "q", "expect": [{"document": "a.pdf", "pages": [1]}, "b.pdf"]},
        {"question": "q", "expect": [{"document": "a.pdf", "pages": [1]}], "kind": 3},
        "q",
    ],
)
def test_a_malformed_question_names_its_place(entry: Any) -> None:
    good = {"question": "q", "expect": [{"document": "a.pdf", "pages": [1]}]}

    with pytest.raises(ValueError, match=r"^question 2: "):
        load(json.dumps([good, entry]))


@pytest.mark.parametrize("text", ["{}", "[]", "not json"])
def test_a_file_that_is_no_list_of_questions_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        load(text)


def test_the_rank_is_the_first_hit_on_an_expected_page() -> None:
    other = page(3, "Total: Rs 500", document="other.pdf")
    wrong_page = page(2, "Sub total", document="bill.pdf")
    spanning = dataclasses.replace(page(2, "Total: Rs 900", document="bill.pdf"), page_to=3)

    assert first_rank(BILL, [other, wrong_page, spanning]) == 3
    assert first_rank(BILL, [other, wrong_page]) is None
    assert first_rank(DEED, [page(5, "Seller", document="copy.pdf")]) == 1


def test_the_report_gives_recall_mrr_each_kind_citations_and_the_misses() -> None:
    found = {BILL.question: [page(1, "x", document="bill.pdf"), page(3, "Total", document="bill.pdf")]}
    citations = {BILL.question: [("bill.pdf", 3, 3), ("bill.pdf", 1, 1)], DEED.question: []}
    out = io.StringIO()

    run([BILL, DEED], lambda q: found.get(q, []), out, lambda q: citations[q])

    assert out.getvalue().splitlines() == [
        "all: recall 1 of 2 (0.50), MRR 0.25",
        "fact: recall 1 of 1 (1.00), MRR 0.50",
        "citations on an expected page: 1 of 2 (0.50), 1 of 2 answered",
        "missed: Who sold the land?",
    ]


def test_without_answers_no_question_is_asked_in_full() -> None:
    out = io.StringIO()

    run([DEED], lambda q: [], out)

    assert out.getvalue().splitlines() == [
        "all: recall 0 of 1 (0.00), MRR 0.00",
        "missed: Who sold the land?",
    ]
