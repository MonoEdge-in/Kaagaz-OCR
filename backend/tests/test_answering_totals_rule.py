"""The numbers rule for totals (PRD 6 C, kaagaz/answering/totals.py), through the service on
fakes, no database: a total only when every matching record prints its figure and every
figure is verified, with its working; otherwise the records, each figure as printed, and one
sentence saying why, whose numbers are counts of what is listed. The SQL that finds the
records is tests/test_answering_fixed_queries_db.py."""

import dataclasses
import random
import re
import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest

from kaagaz.answering import fixed_queries
from kaagaz.answering.fixed_queries import MAX_CITED_VALUES, Figure, ValueRef
from kaagaz.answering.service import Answer, AnswerService
from kaagaz.answering.totals import PRINTED_TOTAL, PRINTED_TOTALS, tell
from kaagaz.extraction.parsing import parse_amount
from tests.test_answering_golden import (
    NOW,
    SETTINGS,
    UNUSED_DB,
    A,
    FakeFixedQueries,
    FakeRetriever,
    FakeUnchecked,
    Record,
    Stored,
    assert_well_formed,
    register_row,
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
        group_fields: Sequence[str] = (),
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
    total: bool = False,
    doubtful: bool = False,
) -> Figure:
    """A record of ``name`` printing ``text`` as its one figure, or none (``text`` None);
    ``total``: its sheet's printed total row instead (PRD 6 C2)."""
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
        printed_total=total,
        looks_total=doubtful,
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
        (  # C2: a row that reads like a total but does not add up: listed, never added
            [SHARMA, figure("ledger.xlsx", "9,000", doubtful=True), OM],
            ["1 of the 3 invoices reads like a total", SHARMA_LINE,
             "ledger.xlsx: 9,000, reads like a total.", OM_LINE],
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


# --- PRD 6 C2: a sheet's printed total, a party's sum, and what a highest rests on ---------------


def test_a_sheet_that_prints_its_own_total_is_never_added_up() -> None:
    ledger = [
        figure("ledger.xlsx", "3,500", total=True),
        figure("ledger.xlsx", "1,000"),
        figure("ledger.xlsx", "2,500"),
    ]
    answer = ask(ledger)
    assert texts(answer) == [
        PRINTED_TOTAL,
        "ledger.xlsx, its printed total: 3,500.",
        "ledger.xlsx: 1,000.",
        "ledger.xlsx: 2,500.",
    ]
    assert answer.label == "needs_review" and "7,000" not in str(answer.to_json())
    assert [c.snippet for c in answer.citations] == ["profit: 3,500", "profit: 1,000", "profit: 2,500"]
    # Before every other reason, its own label said; two sheets each printing one.
    unsure = ask(
        [
            figure("ledger.xlsx", "3,500", total=True, label="needs_review"),
            figure("ledger.xlsx", "1,000", dated=False),
        ]
    )
    assert texts(unsure) == [
        PRINTED_TOTAL,
        "ledger.xlsx, its printed total: 3,500, not verified yet.",
        "ledger.xlsx: 1,000, no date I can read.",
    ]
    two = [
        figure("a.xlsx", "10", total=True),
        figure("a.xlsx", "10"),
        figure("b.xlsx", "5", total=True),
        figure("b.xlsx", "5"),
    ]
    assert texts(ask(two))[0] == PRINTED_TOTALS
    # A ranking leaves the printed total out: it is not a record.
    told = tell(ledger, "profit", "", "record", ranking=True)
    assert [line for line, _, _ in told.lines] == ["ledger.xlsx: 1,000.", "ledger.xlsx: 2,500."]


def test_every_query_that_compares_or_adds_knows_a_printed_total_row() -> None:
    for name in ("_TOP_GROUP", "_EXTREME_VALUE", "_FIGURES"):
        statement = getattr(fixed_queries, name).text
        assert ": ((grand|net|sub)[[:space:]-]*)?totals?([^a-z]|$)'" in statement, name
        assert ": (एकूण|एकुण|कुल|योग)([[:space:]:.,;()/0-9-]|$)'" in statement, name
        assert "FROM totals x" in statement, name
        assert "), sums AS (  -- each sheet's other rows, summed once" in statement, name  # linear


RANKING = "Kiska profit sabse jyada tha last month?"
COMPARED = ", so I haven't compared the parties' profit in August 2026."
AUG = date(2026, 8, 10)


def without(record: Record, field: str) -> Record:
    return [stored for stored in record if stored.ref.field != field]


def ranked(records: Sequence[Record]) -> tuple[Answer, FakeFixedQueries]:
    fixed = FakeFixedQueries({A: list(records)})
    return ask([], RANKING, fixed=fixed), fixed


def test_a_partys_sum_is_shown_when_every_record_prints_a_verified_figure() -> None:
    sharma, mehta = (
        register_row(2, "Sharma Traders", 70000, AUG),
        register_row(3, "Mehta Textiles", 80000, AUG),
    )
    answer, fixed = ranked([sharma, mehta, register_row(4, "Sharma Traders", 50000, AUG)])
    assert texts(answer) == ["Sharma Traders had the highest profit, 1,20,000, in August 2026."]
    assert answer.label == "verified" and fixed.calls == ["figures", "top_group"]


@pytest.mark.parametrize(
    ("odd", "head", "line"),
    [
        (without(register_row(4, "Sharma Traders", 50000, AUG), "profit"),
         "1 of the 3 records has no profit I can add", "profit-register.xlsx: no profit printed."),
        (register_row(4, "Sharma Traders", 50000, AUG, label="needs_review"),
         "1 of the 3 profit figures is not verified yet", "profit-register.xlsx: 50000, not verified yet."),
        (without(register_row(4, "Sharma Traders", 50000, AUG), "party_name"),
         "1 of the 3 records has no party I can read", "profit-register.xlsx: 50000, no party I can read."),
        (without(register_row(4, "Sharma Traders", 50000, AUG), "date"),
         "1 of the 3 records has no date I can read", "profit-register.xlsx: 50000, no date I can read."),
        ([dataclasses.replace(s, ref=dataclasses.replace(s.ref, label="needs_review"))
          if s.ref.field == "party_name" else s for s in register_row(4, "Sharrna Traders", 50000, AUG)],
         "1 of the 3 party names is not verified yet",
         "profit-register.xlsx: 50000, party not verified yet."),
    ],
)  # fmt: skip
def test_otherwise_no_ranking_one_reason_and_every_record_as_printed(
    odd: Record, head: str, line: str
) -> None:
    answer, fixed = ranked(
        [register_row(2, "Sharma Traders", 70000, AUG), register_row(3, "Mehta Textiles", 80000, AUG), odd]
    )
    assert texts(answer) == [
        head + COMPARED,
        "profit-register.xlsx: 70000.",
        "profit-register.xlsx: 80000.",
        line,
    ]
    assert answer.label == "needs_review" and fixed.calls == ["figures"]  # no party's sum is asked for
    assert "1,20,000" not in str(answer.to_json()) and "1,50,000" not in str(answer.to_json())


def test_a_highest_is_verified_only_when_every_value_compared_is() -> None:
    top, other = (
        register_row(2, "Sharma Traders", 120000, AUG),
        register_row(3, "Mehta", 80000, AUG, label="needs_review"),
    )
    answer = ask([], "What was the highest profit?", fixed=FakeFixedQueries({A: [top, other]}))
    assert texts(answer) == [
        "The highest profit was 1,20,000.",
        "Some of the values behind this are not verified yet.",
    ]
    assert answer.label == "needs_review"
    assert answer.sentences[0].citations == [1]  # the answer cites its own value; the second, the other
    assert [answer.citations[n - 1].snippet for n in answer.sentences[1].citations] == ["profit: 80000"]
    verified = ask(
        [], "What was the highest profit?", fixed=FakeFixedQueries({A: [top, register_row(3, "M", 8, AUG)]})
    )
    assert texts(verified) == ["The highest profit was 1,20,000."] and verified.label == "verified"
    # A value not verified that cannot be pointed at: needs review, and no sentence citing verified ones.
    lost = [dataclasses.replace(s, ref=dataclasses.replace(s.ref, region={})) for s in other]
    unseen = ask([], "What was the highest profit?", fixed=FakeFixedQueries({A: [top, lost]}))
    assert texts(unseen) == ["The highest profit was 1,20,000."] and unseen.label == "needs_review"


def _register(rng: random.Random) -> list[Record]:
    records = []
    for i in range(rng.choice([1, 2, 3, 6, 12, 25])):
        party = rng.choice(["Sharma Traders", "SHARMA   traders", "Mehta Textiles", "Om Steel"])
        label = "verified" if rng.random() < 0.9 else "needs_review"
        record = register_row(
            i + 2, party, rng.randint(-500, 500_000), date(2026, 8, rng.randint(1, 31)), label=label
        )
        record = without(record, rng.choice(["profit", "party_name", "date", *[""] * 17]))
        if record[0].ref.field == "party_name" and rng.random() < 0.05:
            record[0] = dataclasses.replace(
                record[0], ref=dataclasses.replace(record[0].ref, label="needs_review")
            )
        records.append(record)
    return records


def _first(record: Record, field: str) -> Stored | None:
    return next((stored for stored in record if stored.ref.field == field), None)


def test_whatever_the_mix_a_partys_sum_is_exact_and_only_over_verified_figures() -> None:
    rng = random.Random(20261025)  # noqa: S311 - a fixed seed makes test records, not a secret
    shown = withheld = 0
    for _ in range(400):
        records = _register(rng)
        answer, fixed = ranked(records)
        if answer.status == "not_found":  # no field to rank by: the search route, which finds nothing
            continue
        figures = [_first(r, "profit") for r in records]
        reasons = [
            sum(_first(r, "date") is None for r in records),
            sum(f is None for f in figures),
            sum(_first(r, "party_name") is None for r in records),
            sum(f is not None and f.ref.label != "verified" for f in figures),
            sum(r[0].ref.field == "party_name" and r[0].ref.label != "verified" for r in records),
        ]
        head = answer.sentences[0].text
        if not any(reasons):  # every party's exact sum is known: the best is shown
            sums: dict[str, Decimal] = {}
            for record, profit in zip(records, figures, strict=True):
                party = _first(record, "party_name")
                assert party is not None and profit is not None and profit.number is not None
                key = " ".join(party.ref.value_text.casefold().split())
                sums[key] = sums.get(key, Decimal(0)) + profit.number
            assert _numbers(head.replace("August 2026", "")) == [max(sums.values())], head
            assert answer.label == "verified" and len(answer.sentences) == 1
            shown += 1
        else:  # one reason, in counts of the records listed; never a party's sum
            assert COMPARED in head and fixed.calls == ["figures"], head
            n, x = len(records), next(reason for reason in reasons if reason)
            said = _numbers(head.split(COMPARED)[0])
            assert said == ([] if n == 1 else [n] if x == n else [x, n]), head
            cited = {c.n: c for c in answer.citations}
            for line in answer.sentences[1:]:
                assert set(_numbers(line.text)) <= set(_numbers(cited[line.citations[0]].snippet)), line.text
            withheld += 1
    assert shown > 20 and withheld > 100  # both sides of the rule were checked


def test_whatever_the_mix_a_highest_is_verified_only_over_verified_values() -> None:
    rng = random.Random(20261026)  # noqa: S311 - a fixed seed makes test records, not a secret
    for _ in range(400):
        records = _register(rng)
        answer = ask([], "What was the highest profit?", fixed=FakeFixedQueries({A: records}))
        compared = [f for f in (_first(r, "profit") for r in records) if f is not None]
        if not compared:
            assert answer.status == "not_found"
            continue
        assert _numbers(answer.sentences[0].text) == [max(f.number or 0 for f in compared)]
        unsure = {f.ref.value_id for f in compared if f.ref.label != "verified"}
        assert (answer.label == "verified") is not bool(unsure)
        if unsure:
            [_, said] = answer.sentences
            cited = {answer.citations[n - 1].ref_id for n in said.citations}
            assert cited == unsure if len(unsure) <= MAX_CITED_VALUES else cited < unsure
