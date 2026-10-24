"""The numbers rule for totals (PRD 6 C, kaagaz/answering/totals.py), through the service on
fakes, no database: a total only when every matching record prints its figure and every
figure is verified, with its working; otherwise the records, each figure as printed, and one
sentence saying why, whose numbers are counts of what is listed. The SQL that finds the
records is tests/test_answering_fixed_queries_db.py."""

import random
import re
import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest

from kaagaz.answering.fixed_queries import MAX_CITED_VALUES, Figure, ValueRef
from kaagaz.answering.service import Answer, AnswerService
from kaagaz.extraction.parsing import parse_amount
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

QUESTION = "Total profit of invoices in September 2026?"
BOX = {"kind": "box", "page": 1, "box": [0.5, 0.8, 0.9, 0.85]}
WHY = ", so I haven't added up the profit in September 2026."
DEVANAGARI = "१,१८,०००"  # noqa: RUF001 - 1,18,000 in Devanagari digits, on purpose


class Preset(FakeFixedQueries):
    """``figures`` gives the records set here, as the SQL would have matched them."""

    def __init__(self, figures: Sequence[Figure]) -> None:
        super().__init__({A: []})
        self.preset = list(figures)

    def available_fields(self, customer_id: str, fields: Sequence[str]) -> set[str]:
        return {"profit", "invoice_date"} & set(fields)

    def figures(
        self,
        customer_id: str,
        metric: str,
        date_fields: Sequence[str],
        start: date | None,
        end: date | None,
        *,
        invoices: bool,
    ) -> list[Figure]:
        assert (metric, start, end) == ("profit", date(2026, 9, 1), date(2026, 10, 1))
        return self.preset


def figure(
    name: str,
    text: str | None = None,
    *,
    label: str = "verified",
    printed: int | None = None,
    dated: bool = True,
    date_verified: bool = True,
    region: dict[str, object] = BOX,
) -> Figure:
    """A record of ``name`` printing ``text`` as its one figure, or none (``text`` None)."""
    field, shown = ("profit", text) if text is not None else ("invoice_number", "INV-1")
    document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, name))
    ref = ValueRef(str(uuid.uuid4()), document_id, name, "pdf", 1, dict(region), field, shown, label)
    amount = None if text is None else parse_amount(text)
    return Figure(
        value=ref,
        amount=amount,
        printed=(0 if text is None else 1) if printed is None else printed,
        verified=amount is not None and label == "verified",
        dated=dated,
        date_verified=dated and date_verified,
    )


def ask(
    figures: Sequence[Figure],
    question: str = QUESTION,
    *,
    fixed: FakeFixedQueries | None = None,
    unchecked: FakeUnchecked | None = None,
) -> Answer:
    answer = AnswerService(
        db=UNUSED_DB,
        model=None,
        settings=SETTINGS,
        clock=lambda: NOW,
        retriever=FakeRetriever({}),
        fixed_queries=Preset(figures) if fixed is None else fixed,
        unchecked=FakeUnchecked() if unchecked is None else unchecked,
    ).answer(A, question)
    assert_well_formed(answer)
    return answer


def texts(answer: Answer) -> list[str]:
    return [sentence.text for sentence in answer.sentences]


SHARMA = figure("INV-2041 Sharma.pdf", "₹18,400")
PATIL = figure("INV-2057 Patil.pdf", "Rs. 9,250")
OM = figure("INV-2063 Om Steel.pdf", "31,000")
LINES = ["INV-2041 Sharma.pdf: ₹18,400.", "INV-2057 Patil.pdf: Rs. 9,250.", "INV-2063 Om Steel.pdf: 31,000."]
SHARMA_LINE, PATIL_LINE, OM_LINE = LINES


# --- the truth table ----------------------------------------------------------------------------


def test_every_figure_printed_and_verified_gives_the_total_with_its_working() -> None:
    answer = ask([SHARMA, PATIL, OM])
    assert texts(answer) == ["The total profit in September 2026 was 58,650, from 3 invoices.", *LINES]
    assert (answer.status, answer.label, answer.route) == ("answered", "verified", "fixed_query")
    # The working: each figure with its document and its spot, cited on its own line.
    assert [s.citations for s in answer.sentences] == [[1, 2, 3], [1], [2], [3]]
    assert [(c.document_name, c.snippet, c.regions) for c in answer.citations] == [
        ("INV-2041 Sharma.pdf", "profit: ₹18,400", [BOX]),
        ("INV-2057 Patil.pdf", "profit: Rs. 9,250", [BOX]),
        ("INV-2063 Om Steel.pdf", "profit: 31,000", [BOX]),
    ]


@pytest.mark.parametrize(
    ("records", "said"),
    [
        (  # one missing
            [SHARMA, figure("INV-5 Blank.pdf"), OM],
            ["1 of the 3 invoices has no profit I can add", SHARMA_LINE, OM_LINE,
             "INV-5 Blank.pdf: no profit printed."],
        ),
        (  # one needs review
            [SHARMA, figure("INV-2057 Patil.pdf", "Rs. 9,250", label="needs_review"), OM],
            ["1 of the 3 profit figures is not verified yet", SHARMA_LINE,
             "INV-2057 Patil.pdf: Rs. 9,250, not verified yet.", OM_LINE],
        ),
        (  # the only one needs review
            [figure("bill.pdf", "1,180.00", label="needs_review")],
            ["The one profit figure is not verified yet", "bill.pdf: 1,180.00, not verified yet."],
        ),
        (  # an undated invoice in a dated question
            [SHARMA, PATIL, OM, figure("INV-9 Undated.pdf", "5,000", dated=False)],
            ["1 of the 4 invoices has no date I can read", *LINES,
             "INV-9 Undated.pdf: 5,000, no date I can read."],
        ),
        (  # a date not verified
            [SHARMA, figure("INV-2057 Patil.pdf", "Rs. 9,250", date_verified=False), OM],
            ["1 of the 3 invoice dates is not verified yet", SHARMA_LINE,
             "INV-2057 Patil.pdf: Rs. 9,250, date not verified yet.", OM_LINE],
        ),
        (  # a figure that cannot be pointed at: never verified, never listed
            [SHARMA, figure("INV-7 Lost.pdf", "7,000", region={})],
            ["1 of the 2 profit figures is not verified yet", SHARMA_LINE],
        ),
        (  # two figures in one record, or one that is not a number
            [SHARMA, figure("INV-3 Twice.pdf", printed=2), figure("INV-4 Nil.pdf", "Nil")],
            ["2 of the 3 invoices have no profit I can add", SHARMA_LINE,
             "INV-3 Twice.pdf: no profit I can add.", "INV-4 Nil.pdf: no profit I can add."],
        ),
    ],
)  # fmt: skip
def test_otherwise_no_total_one_reason_in_counts_and_every_record_as_printed(
    records: list[Figure], said: list[str]
) -> None:
    answer = ask(records)
    more = " 1 is listed here; 1 more is not." if len(said) == len(records) else ""
    assert texts(answer) == [said[0] + WHY + more, *said[1:]]
    assert answer.label == "needs_review"
    unverified = [c.document_name for c in answer.citations if c.label != "verified"]
    assert unverified == [r.value.document_name for r in records if r.value and r.value.label != "verified"]


def test_indian_and_devanagari_figures_are_summed_exactly() -> None:
    texts_ = ["₹1,20,000", DEVANAGARI, "Rs. 12,34,567.50", "0.005"]
    figures = [figure(f"{n}.pdf", text) for n, text in enumerate(texts_)]
    assert [f.amount for f in figures] == [120000, 118000, Decimal("1234567.50"), Decimal("0.005")]
    answer = ask(figures)
    assert texts(answer)[0] == "The total profit in September 2026 was 14,72,567.505, from 4 invoices."
    assert texts(answer)[2] == f"1.pdf: {DEVANAGARI}."


def test_past_the_cap_the_head_says_how_many_more_and_figures_come_first() -> None:
    answer = ask([figure(f"Bill {i}.pdf", "1,000") for i in range(MAX_CITED_VALUES + 5)])
    assert texts(answer)[0] == (
        "The total profit in September 2026 was 25,000, from 25 invoices. 20 are listed here; 5 more are not."
    )
    assert len(answer.citations) == MAX_CITED_VALUES == len(answer.sentences) - 1
    blanks = ask([*(figure(f"Blank {i}.pdf") for i in range(MAX_CITED_VALUES)), SHARMA])
    assert texts(blanks)[1] == LINES[0] and texts(blanks)[0].endswith(" 20 are listed here; 1 more is not.")


def test_card_values_are_never_counted() -> None:
    """A figure only a card holds (always needs review) is never a number, as before; beside
    checked figures, cards are not even asked (the SQL leaving cards out is in the db tests)."""
    cards = FakeUnchecked({A: {"profit": [{"id": str(uuid.uuid4()), "display_name": "Sept report.pdf"}]}})
    only_cards = ask([], fixed=FakeFixedQueries({A: []}), unchecked=cards)
    assert only_cards.status == "unchecked" and not any(ch.isdigit() for ch in only_cards.sentences[0].text)
    beside_cards = ask([SHARMA], "Total profit in September 2026?", unchecked=cards)
    assert texts(beside_cards) == [
        "The total profit in September 2026 was 18,400, from 1 record.",
        SHARMA_LINE,
    ]
    assert cards.calls == [(A, ("profit", "net_profit"))]  # asked once, for the card-only question


# --- the property: every number is a figure as printed, a permitted total, or a count -----------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[Decimal]:
    return [Decimal(n.replace(",", "")) for n in _NUMBER.findall(text.replace("September 2026", ""))]


def _generated(rng: random.Random) -> list[Figure]:
    figures = []
    for i in range(rng.choice([1, 2, 3, 5, 14, 25])):
        amount = Decimal(rng.randint(-5000, 5_000_000)) / rng.choice([1, 1, 100, 1000])
        text = rng.choice(["{}", "Rs. {}", "₹{}"]).format(f"{amount:,f}") if rng.random() < 0.85 else None
        figures.append(
            figure(
                f"{rng.choice(['Sharma', 'Patil', 'Om Steel'])} {chr(97 + i % 26)}.pdf",
                text,
                label="verified" if rng.random() < 0.85 else "needs_review",
                printed=2 if text is None and rng.random() < 0.2 else None,
                dated=rng.random() < 0.93,
                date_verified=rng.random() < 0.95,
                region=BOX if rng.random() < 0.97 else {},
            )
        )
    return figures


def _reason(figures: Sequence[Figure]) -> tuple[int, str] | None:
    """The oracle: how many records the first reason covers, and the note their lines carry."""
    checked = [f.verified and f.value is not None and bool(f.value.region) for f in figures]
    reasons = [
        (sum(not f.dated for f in figures), "no date I can read"),
        (sum(f.amount is None for f in figures), "no profit"),
        (sum(f.amount is not None and not ok for f, ok in zip(figures, checked, strict=True)), ", not "),
        (sum(f.dated and not f.date_verified for f in figures), "date not verified"),
    ]
    return next((reason for reason in reasons if reason[0]), None)


def test_whatever_the_mix_every_number_is_printed_permitted_or_a_count() -> None:
    rng = random.Random(20261024)  # noqa: S311 - a fixed seed makes test records, not a secret
    totals = withheld = 0
    for _ in range(500):
        figures = _generated(rng)
        answer = ask(figures)
        if answer.status == "not_found":  # no source, no answer: nothing could be pointed at
            assert not any(f.value and f.value.region for f in figures)
            continue
        head, *lines = answer.sentences
        cited = {c.n: c for c in answer.citations}
        for line in lines:  # a figure as printed, with its citation, or no number at all
            [n] = line.citations
            assert set(_numbers(line.text)) <= set(_numbers(cited[n].snippet)), line.text
        n, listed = len(figures), len(lines)
        more = n - listed
        said = _numbers(head.text)
        if more:  # "20 are listed here; 5 more are not."
            assert said[-2:] == [listed, more]
            said = said[:-2]
        reason = _reason(figures)
        if reason is None:  # the total, the exact sum of every figure, from n records
            exact = sum((f.amount for f in figures if f.amount is not None), Decimal(0))
            assert head.text.startswith("The total") and said == [exact, n]
            totals += 1
        else:  # "x of the n invoices ...", "all n invoices ...", "the one invoice ...": counts only
            x, note = reason
            assert not head.text.startswith("The total")
            assert said == ([] if n == 1 else [n] if x == n else [x, n]), head.text
            flagged = sum(note in line.text for line in lines)
            assert flagged == x if not more else flagged <= x
            withheld += 1
    assert totals > 20 and withheld > 200  # both sides of the rule were checked
