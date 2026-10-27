"""How the assistant talks (PRD 6 package A; build spec 24 Oct 2026, decisions 2, 9, 14).

The opening's rules (first name, time of day, never a line twice), the soft not-found
wording shared with the screen, each listed document's why, the closest documents (never
by meaning alone; a whole document's from a search), and the final number check, with the
answering service's fakes (test_answering_golden.py, test_answering_context.py).
"""

import dataclasses
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from kaagaz.answering.evidence import closest_documents
from kaagaz.answering.voice import (
    LEAD_INS,
    MAX_OPENING_WORDS,
    SOFT_CLOSEST,
    SOFT_NOTHING,
    first_name,
    greeting,
    opening,
    title,
    unsupported,
    why,
)
from kaagaz.api.views import answer_with_source_states, cited_document_ids
from tests.test_answering_context import A as WHOLE
from tests.test_answering_context import FakeContext, Fused, Routed, answers, page
from tests.test_answering_golden import (
    INVOICE,
    QUESTION,
    A,
    FakeModel,
    assert_well_formed,
    evidence_blocks,
    piece,
    service,
)

INDIA = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 24, 13, 0, tzinfo=UTC)  # 18:30 in India


@pytest.mark.parametrize(
    ("display_name", "expected"),
    [
        ("Vrushit Patel", "Vrushit"),
        ("vrushit patel", "Vrushit"),
        ("José Fernandes", "José"),
        ("Dr. Anita Rao", "Anita"),
        ("Shri Ramesh Kulkarni", "Ramesh"),
        ("SMT Sunita", "Sunita"),
        ("श्री रामचंद्र पाटील", "रामचंद्र"),
        ("सीता", "सीता"),
        ("tushar@monoedge.in", None),  # an e-mail address
        ("Tushar tushar@monoedge.in", None),
        ("J. Smith", None),  # an initial
        ("J Smith", None),
        ("Mr", None),  # a title alone
        ("Team 7", None),  # a digit
        ("Accounts-Team", None),  # not letters only
        ("Ramचंद्र", None),  # two scripts
        ("A" * 31, None),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_the_first_name_is_a_plain_first_word_or_none(display_name: str | None, expected: str | None) -> None:
    assert first_name(display_name) == expected


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        (datetime(2026, 10, 23, 23, 29, tzinfo=UTC), "Good evening"),  # 04:59 in India
        (datetime(2026, 10, 23, 23, 30, tzinfo=UTC), "Good morning"),  # 05:00
        (datetime(2026, 10, 24, 6, 29, tzinfo=UTC), "Good morning"),  # 11:59
        (datetime(2026, 10, 24, 6, 30, tzinfo=UTC), "Good afternoon"),  # 12:00
        (datetime(2026, 10, 24, 11, 29, tzinfo=UTC), "Good afternoon"),  # 16:59
        (datetime(2026, 10, 24, 11, 30, tzinfo=UTC), "Good evening"),  # 17:00
    ],
)
def test_the_time_of_day_is_indias_at_the_band_edges(utc: datetime, expected: str) -> None:
    assert greeting(utc, INDIA) == expected


def test_a_clock_without_a_timezone_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        greeting(datetime(2026, 10, 24, 9, 0), INDIA)  # noqa: DTZ001 - the case refused


def test_the_name_is_only_in_the_first_opening_and_no_line_repeats_over_ten_turns() -> None:
    earlier: list[str | None] = []
    for turn in range(10):
        line = opening(
            found=turn % 3 != 2, display_name="Dr. Vrushit Patel", earlier=earlier, now=NOW, zone=INDIA
        )
        earlier.append(line)
    assert earlier[0] == "Good evening, Vrushit. Here's what I found."
    said = [line for line in earlier if line is not None]
    assert len(said) == len(set(said)) == 1 + len(LEAD_INS)
    assert all("Vrushit" not in line for line in said[1:])
    for line in said:
        assert len(line.split()) <= MAX_OPENING_WORDS and line.isascii()  # no emoji
    # A not-found answer (turns 2, 5, 8) never gets a found-only line; all used up, none.
    found = ["Sure.", "Got it.", "Here they are.", "Here's what your documents say."]
    found += ["All right.", "Of course."]
    assert earlier[1:] == [*found, None, None, None]


def test_no_name_greets_by_the_time_of_day_and_a_not_found_answer_gets_no_found_line() -> None:
    for name in ("info@sharma.in", "", None):
        assert opening(found=False, display_name=name, earlier=[], now=NOW, zone=INDIA) == "Good evening."
    # Answers saved before PRD 6 have no opening: still not the first answer.
    assert opening(found=True, display_name="Vrushit", earlier=[None], now=NOW, zone=INDIA) == "Sure."


def test_the_soft_not_found_wording_is_the_screens_and_never_apologises() -> None:
    screen = Path(__file__).parents[2] / "frontend" / "components" / "chat" / "answer.ts"
    code = screen.read_text(encoding="utf-8")
    assert f'NOT_FOUND_CLOSEST_TEXT = "{SOFT_CLOSEST}";' in code
    assert f'NOT_FOUND_TEXT = "{SOFT_NOTHING}";' in code
    for line in (SOFT_CLOSEST, SOFT_NOTHING):
        assert not re.search(r"sorry|couldn't|could not|unfortunately|\bno\.", line, re.IGNORECASE)


LAND_CARD: dict[str, Any] = {
    "doc_type": "land_record",
    "document_date": {"value": "12/09/2026", "date": "2026-09-12"},
    "parties": [
        {"role": "owner", "name": "Vrushit Patel", "name_latin": None},
        {"role": "owner", "name": "कृष्णा शिंदे", "name_latin": "Krishna Shinde"},
        {"role": "seller", "name": "Asha Rao 2", "name_latin": None},
    ],
    "identifiers": [{"kind": "pan", "value": "ABCDE1234F"}, {"kind": "survey_number", "value": "45/2"}],
}


def test_a_title_names_the_type_and_identifier_as_printed_and_why_has_no_digit() -> None:
    assert title("scan_7.pdf", LAND_CARD) == "Land record, Survey 45/2"  # never the PAN
    invoice = {"doc_type": "invoice", "identifiers": [{"kind": "invoice_number", "value": "INV-2041"}]}
    assert title("bill.pdf", invoice) == "Invoice INV-2041"
    assert title("bill.pdf", {"doc_type": "other"}) == "bill.pdf"
    assert title("bill.pdf", None) == "bill.pdf"
    said = why("Land owned by Vrushit and Krishna in September?", LAND_CARD, words=["land"], dated=True)
    assert said == "Land record, owner Vrushit Patel, owner कृष्णा शिंदे, mentions land, dated September"
    assert why("Who sold it, Asha?", LAND_CARD) == ""  # "Asha Rao 2" holds a digit: left out
    assert why("total?", None, fields=["total_amount"]) == "Total amount as printed"


def test_the_closest_documents_are_never_close_by_meaning_alone() -> None:
    meaning = piece("policy.pdf", "Employees may work from home on Fridays.")
    worded = piece("lease.pdf", "Leave and licence for the Nashik Road office.")
    typo = piece("rent.pdf", "Rent agreement, godown 7, Sinnar.")
    typo = dataclasses.replace(typo, fuzzy_score=0.4)
    found = closest_documents([meaning, worded, typo], ["lease", "nashik", "godown7"])
    assert [doc["display_name"] for doc in found] == ["lease.pdf", "rent.pdf"]
    assert found[0]["words"] == ["nashik"]
    many = [piece(f"nashik-{n}.pdf", "Nashik") for n in range(8)]
    assert len(closest_documents(many, ["nashik"])) == 5


def test_a_whole_documents_closest_come_from_a_search_not_its_reading_order() -> None:
    memo = page(1, "Board meeting minutes, Monday.", document="a-memo.pdf")
    lease = page(1, "Rent agreement for the Pune shop.", document="b-lease.pdf")
    retriever = Fused([memo, lease])
    model = Routed(lambda system, user: {"status": "not_found", "sentences": []})

    answer = answers(retriever, FakeContext([memo, lease]), model).answer(
        WHOLE, "Rent agreement for the Wakad shop?"
    )

    assert answer.status == "not_found"
    assert [doc["display_name"] for doc in answer.closest_documents] == ["b-lease.pdf"]
    # Changed on purpose (#381, 27 Oct 2026): the first search ranks the pieces for the draft (with
    # the vector); the closest still come from words and typos only, no vector.
    assert [call[2] is None for call in retriever.calls] == [False, True]
    assert [(d["title"], d["citations"]) for d in answer.documents] == [("b-lease.pdf", [])]
    assert answer.documents[0]["why"].startswith("Mentions rent and agreement")
    assert_well_formed(answer)


def _model(*sentences: str) -> Any:
    """A draft citing the evidence block that holds the invoice, one sentence per text."""

    def reply(system: str, user: str) -> dict[str, Any]:
        n = next(n for n, text in evidence_blocks(user).items() if "INV-1042" in text)
        return {"status": "answered", "sentences": [{"text": text, "evidence": [n]} for text in sentences]}

    return FakeModel(reply)


def test_the_final_check_drops_a_figure_not_as_printed_and_logs_only_its_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="kaagaz.answering.service")
    model = _model("The grand total of INV-1042 is Rs. 1,20,000.", "That is about Rs. 1.2 lakh.")

    answer = service(pieces=[INVOICE], model=model)[0].answer(A, QUESTION)

    # verify keeps both (a scale word multiplies); decision 2 keeps only the figure as printed.
    assert [s.text for s in answer.sentences] == ["The grand total of INV-1042 is Rs. 1,20,000."]
    assert [(d["title"], d["citations"]) for d in answer.documents] == [("bill-1042.pdf", [1])]
    dropped = [r for r in caplog.records if r.getMessage() == "answer text dropped"]
    assert [getattr(r, "code", None) for r in dropped] == ["final_check:sentence:number"]
    assert "lakh" not in caplog.text and "1.2" not in caplog.text
    assert_well_formed(answer)


def test_a_fraction_word_counts_as_a_number_and_nothing_left_is_not_found(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="kaagaz.answering.service")
    answer = service(pieces=[INVOICE], model=_model("Sharma Traders was paid one-fifth of INV-1042."))[
        0
    ].answer(A, QUESTION)
    assert answer.status == "not_found"
    codes = [getattr(r, "code", None) for r in caplog.records]
    assert "final_check:sentence:fraction" in codes and "notfound_final_check" in codes
    assert unsupported("Half of it.", {"half"}) is None and unsupported("Half of it.", set()) == "fraction"
    assert unsupported("Rs. 1,20,000 on 12/08/2026", {"120000", "12", "8", "2026"}) is None


def test_a_fixed_query_answer_keeps_its_total_and_lists_its_documents() -> None:
    answer = service()[0].answer(A, "Kiska profit sabse jyada tha last month?")
    assert answer.status == "answered" and answer.route == "fixed_query"
    assert answer.documents and all(d["citations"] for d in answer.documents)
    assert_well_formed(answer)


def _v2(*documents: str) -> dict[str, Any]:
    region = {"kind": "box", "page": 1, "box": [0.1, 0.2, 0.3, 0.25]}
    citations = [
        {"n": n, "document_id": d, "document_name": f"{d}.pdf", "snippet": "x", "regions": [region]}
        for n, d in enumerate(documents, start=1)
    ]
    return {
        "version": 2,
        "status": "answered",
        "sentences": [{"text": "x", "citations": [1]}],
        "citations": citations,
        "closest_documents": [],
        "documents": [
            {"document_id": d, "title": d, "why": "", "citations": [n]}
            for n, d in enumerate(documents, start=1)
        ],
    }


def test_a_saved_answer_drops_a_listed_document_that_is_gone_and_keeps_old_shapes() -> None:
    answer = _v2("live", "binned", "gone")
    assert cited_document_ids(answer)[-3:] == ["live", "binned", "gone"]
    shown = answer_with_source_states(answer, {"live": "live", "binned": "in_trash"})
    assert [d["document_id"] for d in shown["documents"]] == ["live"]
    assert len(answer["documents"]) == 3  # the stored answer is never changed
    old = {key: value for key, value in answer.items() if key not in ("version", "documents")}
    assert "documents" not in answer_with_source_states(old, {"live": "live"})
