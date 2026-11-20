"""The retrieval scorer (#721, kaagaz/answering/scorecard.py): the file, the scores and the report."""

import dataclasses
import io
import json
from typing import Any

import pytest

from kaagaz.answering.scorecard import Expected, Item, Score, load, run, score
from tests.test_answering_context import page

BILL = Item("What is the total?", (Expected("bill.pdf", (3,)),))
LEASES = Item("Compare the rent in the two leases", (Expected("a.pdf", (2,)), Expected("b.pdf", (4, 5))))


def test_a_file_loads_each_question_with_its_documents_and_pages() -> None:
    raw = [
        {"question": BILL.question, "expect": [{"document": "bill.pdf", "pages": [3]}]},
        {
            "question": LEASES.question,
            "expect": [{"document": "a.pdf", "pages": [2]}, {"document": "b.pdf", "pages": [4, 5]}],
        },
    ]

    assert load(json.dumps(raw)) == [BILL, LEASES]


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


def test_a_question_scores_the_share_of_its_expected_pages_and_the_first_rank() -> None:
    """Principal review: a comparison of two files that finds one scores half, never all."""
    other = page(3, "Total: Rs 500", document="other.pdf")
    wrong_page = page(2, "Sub total", document="bill.pdf")
    spanning = dataclasses.replace(page(2, "Total: Rs 900", document="bill.pdf"), page_to=3)

    assert score(BILL, [other, wrong_page, spanning]) == Score(1, 1, 3)
    assert score(BILL, [other, wrong_page]) == Score(0, 1, None)
    assert score(LEASES, [page(4, "Rent", document="b.pdf"), page(2, "Rent", document="a.pdf")]) == Score(
        2, 3, 1
    )


def test_the_report_gives_page_recall_any_found_mrr_each_kind_citations_and_the_misses() -> None:
    found = {
        BILL.question: [page(1, "x", document="bill.pdf"), page(3, "Total", document="bill.pdf")],
        LEASES.question: [page(2, "Rent", document="a.pdf")],
    }
    citations = {BILL.question: [("bill.pdf", 3, 3), ("bill.pdf", 1, 1)], LEASES.question: []}
    out = io.StringIO()

    run([BILL, LEASES], lambda q: found[q], out, lambda q: citations[q])

    assert out.getvalue().splitlines() == [
        "all: page recall 0.67, any page found 2 of 2, MRR 0.75",  # (1 + 1/3) / 2; (1/2 + 1) / 2
        "compare: page recall 0.33, any page found 1 of 1, MRR 1.00",  # Ask's own kinds
        "unknown: page recall 1.00, any page found 1 of 1, MRR 0.50",
        "citations on an expected page: 1 of 2 (0.50), 1 of 2 answered",
        "missed 2 of 3 pages: Compare the rent in the two leases",
    ]


def test_without_answers_no_question_is_asked_in_full() -> None:
    out = io.StringIO()

    run([BILL], lambda q: [], out)

    assert out.getvalue().splitlines() == [
        "all: page recall 0.00, any page found 0 of 1, MRR 0.00",
        "unknown: page recall 0.00, any page found 0 of 1, MRR 0.00",
        "missed 1 of 1 pages: What is the total?",
    ]
