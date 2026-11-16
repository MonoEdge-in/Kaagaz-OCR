"""Answers know about pages that couldn't be read (PRD 5 F6), without a database.

The answer service with the golden tests' fakes and a fake ``ReadingQuality``: a
not-found answer names the flagged pages in scope with the PRD's note; a cited text
holding a word mark makes its citation hard to read and the answer needs review. A
saved answer drops the pages of a document that is gone. The PostgreSQL side is
tests/test_answering_hard_to_read_db.py.
"""

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

import pytest

from kaagaz.answering.hard_to_read import (
    HARD_TO_READ_NOTE,
    MAX_NAMED_PAGES,
    FlaggedPage,
    WordMark,
    hard_to_read,
    not_found_note,
)
from kaagaz.answering.service import Answer, AnswerService
from kaagaz.api.views import answer_with_source_states, cited_document_ids
from kaagaz.reading.spans import BoxLocation
from kaagaz.search.scope import EVERYTHING, Scope
from tests.test_answering_golden import (
    INVOICE,
    NOW,
    SETTINGS,
    UNUSED_DB,
    A,
    FakeFixedQueries,
    FakeRetriever,
    FakeUnchecked,
    Record,
    assert_well_formed,
    invoice_record,
    register,
    value,
)

INVOICE_12 = FlaggedPage(str(uuid.uuid4()), "Invoice_12.pdf", 3, "unreadable")
REGISTER = FlaggedPage(str(uuid.uuid4()), "Register.jpg", 1, "uncertain")
# The PRD's own example (F6).
TWO_PAGES_NOTE = (
    "Not found in the readable text. 2 pages to check: Invoice_12.pdf page 3, Register.jpg page 1."
)
# A word mark inside INVOICE's one box (page 1, 0.1..0.9 x 0.2..0.3), and one elsewhere.
INSIDE = BoxLocation(1, 0.5, 0.25, 0.6, 0.28)
BELOW = BoxLocation(1, 0.5, 0.6, 0.6, 0.62)


class FakeReading:
    def __init__(
        self, pages: Sequence[FlaggedPage] = (), total: int | None = None, marks: Sequence[WordMark] = ()
    ) -> None:
        self.pages, self.total, self.marks = list(pages), len(pages) if total is None else total, list(marks)
        self.scopes: list[Scope] = []
        self.asked_marks: list[list[str]] = []

    def flagged_pages(
        self, customer_id: str, *, scope: Scope = EVERYTHING, user_id: str | None = None
    ) -> tuple[list[FlaggedPage], int]:
        assert customer_id == A
        self.scopes.append(scope)
        return self.pages, self.total

    def word_marks(
        self, customer_id: str, document_ids: Sequence[str], *, user_id: str | None = None
    ) -> list[WordMark]:
        assert customer_id == A
        self.asked_marks.append(list(document_ids))
        return [mark for mark in self.marks if mark.document_id in document_ids]


def ask(
    question: str, reading: FakeReading, *, records: list[Record] | None = None, scope: Scope = EVERYTHING
) -> Answer:
    answers = AnswerService(
        db=UNUSED_DB,
        model=None,
        settings=SETTINGS,
        clock=lambda: NOW,
        retriever=FakeRetriever({A: [INVOICE]}),
        fixed_queries=FakeFixedQueries({A: register() if records is None else records}),
        unchecked=FakeUnchecked(),
        reading=reading,
    )
    answer = answers.answer(A, question, scope=scope)
    assert_well_formed(answer)
    return answer


# --- the note's words ----------------------------------------------------------------------


def test_f6_the_not_found_note_names_each_page_as_the_prd_says() -> None:
    assert not_found_note([INVOICE_12, REGISTER], 2) == TWO_PAGES_NOTE
    assert not_found_note([INVOICE_12], 1) == (
        "Not found in the readable text. 1 page to check: Invoice_12.pdf page 3."
    )


def test_f6_pages_past_the_named_ones_are_counted() -> None:
    assert not_found_note([INVOICE_12, REGISTER], 9) == (
        "Not found in the readable text. 9 pages to check:"
        " Invoice_12.pdf page 3, Register.jpg page 1, and 7 more."
    )


def test_f6_no_flagged_page_means_no_note() -> None:
    assert not_found_note([], 0) is None


def test_f6_a_word_mark_counts_only_inside_the_cited_box_of_its_own_document() -> None:
    box = [{"kind": "box", "page": 1, "box": [0.1, 0.2, 0.9, 0.3]}]
    doc = INVOICE.document_id
    assert hard_to_read(doc, box, [WordMark(doc, INSIDE)])
    assert not hard_to_read(doc, box, [WordMark(doc, BELOW)])
    assert not hard_to_read(doc, box, [WordMark(doc, BoxLocation(2, 0.5, 0.25, 0.6, 0.28))])  # another page
    assert not hard_to_read(doc, box, [WordMark(str(uuid.uuid4()), INSIDE)])  # another document
    assert not hard_to_read(
        doc, [{"kind": "cell", "page": 1, "row": 1, "column": 1}], [WordMark(doc, INSIDE)]
    )
    assert not hard_to_read(doc, [{"kind": "box"}], [WordMark(doc, INSIDE)])  # malformed: no spot


# --- not found: the pages that couldn't be read ------------------------------------------------


def test_f6_a_not_found_answer_names_the_pages_that_couldnt_be_read() -> None:
    reading = FakeReading([INVOICE_12, REGISTER])
    scope = Scope(document_ids=(INVOICE_12.document_id, REGISTER.document_id))

    answer = ask("What is the warranty period?", reading, scope=scope)

    assert (answer.status, answer.label) == ("not_found", None)
    assert answer.reading_note == TWO_PAGES_NOTE
    data = answer.to_json()
    assert data["unreadable_pages"] == [
        {
            "document_id": INVOICE_12.document_id,
            "document_name": "Invoice_12.pdf",
            "page_no": 3,
            "read_quality": "unreadable",
        },
        {
            "document_id": REGISTER.document_id,
            "document_name": "Register.jpg",
            "page_no": 1,
            "read_quality": "uncertain",
        },
    ]
    assert data["unreadable_page_count"] == 2
    assert reading.scopes == [scope]  # the chat's own scope, never a wider one


def test_f6_more_flagged_pages_than_named_are_counted() -> None:
    reading = FakeReading([INVOICE_12] * MAX_NAMED_PAGES, total=MAX_NAMED_PAGES + 4)
    answer = ask("What is the warranty period?", reading)
    assert answer.unreadable_page_count == MAX_NAMED_PAGES + 4
    assert answer.reading_note is not None and answer.reading_note.endswith(", and 4 more.")


def test_f6_without_a_flagged_page_a_not_found_answer_is_as_before() -> None:
    answer = ask("What is the warranty period?", FakeReading())
    data = answer.to_json()
    assert (
        data["status"],
        data["reading_note"],
        data["unreadable_pages"],
        data["unreadable_page_count"],
    ) == (
        "not_found",
        None,
        [],
        0,
    )


def test_f6_an_answered_question_never_lists_the_flagged_pages() -> None:
    reading = FakeReading([INVOICE_12])
    answer = ask("What is the grand total of INV-1042?", reading)
    assert answer.status == "answered"
    assert (answer.reading_note, answer.unreadable_pages) == (None, ())
    assert reading.scopes == []


# --- answered: part of the cited text was hard to read --------------------------------------


def test_f6_a_cited_piece_with_an_unsure_word_is_hard_to_read_and_needs_review() -> None:
    reading = FakeReading(marks=[WordMark(INVOICE.document_id, INSIDE)])

    answer = ask("What is the grand total of INV-1042?", reading)

    assert answer.status == "answered"
    assert [c.hard_to_read for c in answer.citations] == [True]
    assert answer.to_json()["citations"][0]["hard_to_read"] is True
    assert answer.label == "needs_review"  # the same question without the mark is verified
    assert answer.reading_note == HARD_TO_READ_NOTE == "Part of this text was hard to read."
    assert reading.asked_marks == [[INVOICE.document_id]]


def test_f6_a_word_mark_outside_the_cited_text_changes_nothing() -> None:
    reading = FakeReading(marks=[WordMark(INVOICE.document_id, BELOW)])
    answer = ask("What is the grand total of INV-1042?", reading)
    assert ([c.hard_to_read for c in answer.citations], answer.label, answer.reading_note) == (
        [False],
        "verified",
        None,
    )


def test_f6_a_cited_value_with_an_unsure_word_makes_a_fixed_answer_need_review() -> None:
    record = invoice_record(label="verified")
    total = next(stored.ref for stored in record if stored.ref.field == "total_amount")
    inside_total = BoxLocation(1, 0.6, 0.81, 0.7, 0.84)  # inside its box, 0.5..0.9 x 0.8..0.85
    plain = ask("What is the total amount?", FakeReading(), records=[record])
    marked = ask(
        "What is the total amount?",
        FakeReading(marks=[WordMark(total.document_id, inside_total)]),
        records=[record],
    )

    assert (plain.route, plain.status) == ("fixed_query", "answered")
    assert (marked.route, marked.status) == ("fixed_query", "answered")
    assert [c.hard_to_read for c in marked.citations if c.ref_id == total.value_id] == [True]
    assert (marked.label, marked.reading_note) == ("needs_review", HARD_TO_READ_NOTE)
    assert plain.reading_note is None and not any(c.hard_to_read for c in plain.citations)


def test_c2_a_hard_to_read_figure_of_any_party_withholds_the_ranking() -> None:
    """PRD 6 C2: Patil's 70,000 sits on an unsure word, Mehta's 80,000 is clean: still no ranking."""
    box = {"kind": "box", "page": 1, "box": [0.5, 0.2, 0.7, 0.3]}  # INSIDE is in it
    records = [
        [value(doc, "party_name", party, region=box), value(doc, "profit", text, number=n, region=box),
         value(doc, "date", "10/08/2026", day=date(2026, 8, 10), region=box)]
        for doc, party, text, n in [("patil.jpg", "Patil", "70,000", 70000),
                                    ("mehta.jpg", "Mehta", "80,000", 80000)]
    ]  # fmt: skip
    marks = [WordMark(records[0][0].ref.document_id, INSIDE)]
    answer = ask("Kiska profit sabse jyada tha last month?", FakeReading(marks=marks), records=records)
    assert answer.sentences[0].text == (
        "1 of the 2 profit figures is not verified yet, so I haven't compared the parties' profit"
        " in August 2026."
    )
    assert answer.label == "needs_review"


def _bills(count: int) -> list[Record]:
    """``count`` scanned bills of August 2026, one party each; INSIDE is in each profit's box only."""
    party, profit, day = (
        {"kind": "box", "page": 1, "box": box}
        for box in ([0.1, 0.2, 0.3, 0.3], [0.5, 0.2, 0.7, 0.3], [0.1, 0.5, 0.3, 0.6])
    )
    return [
        [value(f"bill-{n}.jpg", "party_name", f"Party {n}", region=party),
         value(f"bill-{n}.jpg", "profit", f"{n},000", number=n * 1000, region=profit),
         value(f"bill-{n}.jpg", "date", "10/08/2026", day=date(2026, 8, 10), region=day)]
        for n in range(1, count + 1)
    ]  # fmt: skip


TOTAL = "What was the total profit in August 2026?"
RANKING = "Kiska profit sabse jyada tha last month?"


@pytest.mark.parametrize(
    ("question", "head"),
    [(TOTAL, "1 of the 25 profit figures is not verified yet, so I haven't added up the profit"
              " in August 2026. 20 are listed here; 5 more are not."),
     (RANKING, "1 of the 25 profit figures is not verified yet, so I haven't compared the parties' profit"
               " in August 2026. 20 are listed here; 5 more are not.")],
)  # fmt: skip
def test_c_a_hard_to_read_figure_past_the_listed_records_still_withholds_the_answer(
    question: str, head: str
) -> None:
    """Record 23 of 25 is never listed (MAX_CITED_VALUES is 20), yet its figure is checked."""
    bills = _bills(25)
    reading = FakeReading(marks=[WordMark(bills[22][1].ref.document_id, INSIDE)])
    answer = ask(question, reading, records=bills)
    assert answer.sentences[0].text == head
    assert (answer.label, answer.reading_note) == ("needs_review", HARD_TO_READ_NOTE)
    assert "bill-23.jpg" not in {c.document_name for c in answer.citations}
    assert len(reading.asked_marks) == 1 and len(set(reading.asked_marks[0])) == 25  # one statement


@pytest.mark.parametrize(
    ("question", "head"),
    [(TOTAL, "The total profit in August 2026 was 3,25,000, from 25 records."
              " 20 are listed here; 5 more are not."),
     (RANKING, "Party 25 had the highest profit, 25,000, in August 2026.")],
)  # fmt: skip
def test_c_with_no_hard_to_read_figure_a_total_or_ranking_is_as_before(question: str, head: str) -> None:
    bills = _bills(25)
    for marks in ([], [WordMark(bills[22][0].ref.document_id, BELOW)]):  # none, or one off every value
        answer = ask(question, FakeReading(marks=marks), records=bills)
        assert answer.sentences[0].text == head
        assert (answer.label, answer.reading_note) == ("verified", None)
        assert not any(c.hard_to_read for c in answer.citations)


# --- a saved answer, shown later -------------------------------------------------------------


def _saved(*pages: FlaggedPage, total: int) -> dict[str, Any]:
    return {
        "status": "not_found",
        "label": None,
        "sentences": [],
        "citations": [],
        "closest_documents": [],
        "route": "search",
        "normalized_question": "warranty period",
        "model_used": False,
        "reading_note": not_found_note(list(pages), total),
        "unreadable_pages": [page.to_json() for page in pages],
        "unreadable_page_count": total,
    }


def test_f6_a_saved_answer_cites_the_documents_of_its_flagged_pages() -> None:
    saved = _saved(INVOICE_12, REGISTER, total=2)
    assert cited_document_ids(saved) == [INVOICE_12.document_id, REGISTER.document_id]


# Changed on purpose (#602, question 1): a document missing from the states replaces the whole
# answer (tests/test_isolation_ask_views.py); one in Trash is still left out of the note.
def test_f6_a_saved_answer_never_names_a_document_that_is_gone() -> None:
    saved = _saved(INVOICE_12, REGISTER, total=3)
    states = {REGISTER.document_id: "live", INVOICE_12.document_id: "in_trash"}

    shown = answer_with_source_states(saved, states)

    assert shown["unreadable_pages"] == [REGISTER.to_json()]
    assert shown["unreadable_page_count"] == 2
    assert shown["reading_note"] == (
        "Not found in the readable text. 2 pages to check: Register.jpg page 1, and 1 more."
    )
    assert "Invoice_12" not in str(shown)
    assert saved == _saved(INVOICE_12, REGISTER, total=3)  # the stored answer never changes


def test_f6_a_saved_answer_whose_pages_are_all_gone_has_no_note() -> None:
    shown = answer_with_source_states(_saved(INVOICE_12, total=1), {INVOICE_12.document_id: "in_trash"})
    assert (shown["reading_note"], shown["unreadable_pages"], shown["unreadable_page_count"]) == (None, [], 0)


def test_f6_a_saved_answer_whose_pages_are_live_or_older_is_as_saved() -> None:
    saved = _saved(INVOICE_12, REGISTER, total=2)
    shown = answer_with_source_states(
        saved, {INVOICE_12.document_id: "older_version", REGISTER.document_id: "live"}
    )
    assert {key: shown[key] for key in ("reading_note", "unreadable_pages", "unreadable_page_count")} == {
        key: saved[key] for key in ("reading_note", "unreadable_pages", "unreadable_page_count")
    }
