"""List and total questions are answered from every stored value, never the top hits (#381).

A total ("the total of all invoices", "the total of invoices over ₹50,000") is the fixed route's,
written in code from every record ``FixedQueries.figures`` matches (totals.py), so no row is
skipped however many there are. With fakes, no database; the open lists of owners and survey
numbers are tests/test_answering_owners_db.py's.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest

from kaagaz.answering.fixed_queries import MAX_CITED_VALUES, Figure
from kaagaz.answering.hinglish import normalise
from kaagaz.answering.intent import bound_asked, plan_fixed
from kaagaz.answering.service import Answer, AnswerService
from tests.test_answering_golden import (
    NOW,
    SETTINGS,
    UNUSED_DB,
    A,
    FakeFixedQueries,
    FakeRetriever,
    FakeUnchecked,
    assert_well_formed,
)
from tests.test_answering_totals_rule import figure


class Invoices(FakeFixedQueries):
    """``figures`` gives every invoice set here, as the SQL would match them; ``asked`` keeps what
    each call asked for."""

    def __init__(self, figures: Sequence[Figure]) -> None:
        super().__init__({A: []})
        self.preset = list(figures)
        self.asked: list[tuple[str, bool]] = []

    def available_fields(self, customer_id: str, fields: Sequence[str]) -> set[str]:
        return {"total_amount", "invoice_date"} & set(fields)

    def figures(
        self,
        customer_id: str,
        metric: str,
        date_fields: Sequence[str],
        start: date | None,
        end: date | None,
        *,
        invoices: bool,
        group_fields: Sequence[str] = (),
    ) -> list[Figure]:
        self.asked.append((metric, invoices))
        return self.preset


def ask(figures: Sequence[Figure], question: str) -> tuple[Answer, Invoices]:
    fixed = Invoices(figures)
    answer = AnswerService(
        db=UNUSED_DB,
        model=None,
        settings=SETTINGS,
        clock=lambda: NOW,
        retriever=FakeRetriever({}),
        fixed_queries=fixed,
        unchecked=FakeUnchecked(),
    ).answer(A, question)
    assert_well_formed(answer)
    return answer, fixed


def amounts(n: int, text: str) -> list[Figure]:
    return [figure(f"INV-{i} Bill.pdf", text) for i in range(n)]


def texts(answer: Answer) -> list[str]:
    return [s.text for s in answer.sentences]


def test_the_total_of_all_invoices_adds_every_one_however_many_and_lists_the_first() -> None:
    """Already the fixed route (PRD 6 C): proven here for "all" and "every", past the cap."""
    for question in ("What is the total of all invoices?", "Total amount of every invoice?"):
        answer, fixed = ask(amounts(MAX_CITED_VALUES + 7, "1,000"), question)
        assert fixed.asked == [("total_amount", True)] and answer.route == "fixed_query"
        assert texts(answer)[0] == (
            "The total amount was 27,000, from 27 invoices. 20 are listed here; 7 more are not."
        )


def test_a_total_over_a_bound_adds_every_invoice_over_it_and_only_those() -> None:
    big = [figure("INV-1 Big.pdf", "₹60,000"), figure("INV-2 Bigger.pdf", "Rs. 1,20,000")]
    small = [figure("INV-3 Small.pdf", "50,000"), figure("INV-4 Tiny.pdf", "9,250")]

    answer, _ = ask([small[0], big[0], small[1], big[1]], "What is the total of invoices over ₹50,000?")

    assert texts(answer) == [
        "The total amount of invoices over 50,000 was 1,80,000, from 2 invoices.",
        "INV-1 Big.pdf: ₹60,000.",
        "INV-2 Bigger.pdf: Rs. 1,20,000.",
    ]
    assert (answer.status, answer.label, answer.route) == ("answered", "verified", "fixed_query")
    assert [c.document_name for c in answer.citations] == ["INV-1 Big.pdf", "INV-2 Bigger.pdf"]


def test_a_total_under_a_bound_and_one_with_no_figure_to_compare_withholds_it() -> None:
    records = [figure("INV-1 A.pdf", "9,250"), figure("INV-2 B.pdf", "60,000"), figure("INV-3 Blank.pdf")]

    under, _ = ask(records[:2], "Total amount of invoices below Rs 10,000")
    assert texts(under)[0] == "The total amount of invoices under 10,000 was 9,250, from 1 invoice."

    # A record with no amount can't be compared: it is counted, so no total is given (PRD 6 C).
    withheld, _ = ask(records, "Total amount of invoices less than 10,000")
    assert texts(withheld)[0] == (
        "1 of the 2 invoices has no amount I can add, so I haven't added up the amount of invoices"
        " under 10,000."
    )
    assert texts(withheld)[1:] == ["INV-1 A.pdf: 9,250.", "INV-3 Blank.pdf: no amount printed."]
    assert withheld.label == "needs_review"


def test_no_invoice_past_the_bound_is_not_found() -> None:
    answer, _ = ask(amounts(3, "1,000"), "What is the total of invoices above 50000?")
    assert answer.status == "not_found" and answer.route == "fixed_query"


@pytest.mark.parametrize(
    ("question", "bound"),
    [
        ("total of invoices over ₹50,000", (">", Decimal(50000))),
        ("total amount of invoices more than Rs 1,00,000", (">", Decimal(100000))),
        ("total of invoices under 9,250.50", ("<", Decimal("9250.50"))),
        ("total of invoices over 50,000 and under 90,000", None),  # two sides
        ("total of invoices over 50,000 or 60,000", None),  # two numbers
        ("total of invoices over 50k", None),  # not plain digits
        ("total of invoices", None),  # no side
    ],
)
def test_the_bound_is_one_side_and_one_plain_number(question: str, bound: object) -> None:
    from kaagaz.answering.evidence import numbers_asked

    normalised = normalise(question)
    assert bound_asked(normalised, numbers_asked(question, normalised.keywords)) == bound


def test_a_bound_plans_only_a_total_and_its_words_are_known_only_with_it() -> None:
    fields = {"total_amount", "invoice_date", "party_name"}
    question = normalise("What is the highest invoice amount over 50,000?")
    assert plan_fixed(question, None, False, lambda _: fields, (">", Decimal(50000))) is None
    over = normalise("What is the total of invoices over 50,000?")
    assert plan_fixed(over, None, False, lambda _: fields) is None  # "over" alone: unknown, search
    planned = plan_fixed(over, None, False, lambda _: fields, (">", Decimal(50000)))
    assert planned is not None and planned.bound == (">", Decimal(50000)) and planned.invoices
    assert planned.within(Decimal(50001)) and not planned.within(Decimal(50000))
