"""The pages that couldn't be read and the word marks (PRD 5 F6), against a real PostgreSQL.

``PgReadingQuality.flagged_pages`` names only pages of the customer's current, ready
documents out of Trash, inside the chat's scope, unreadable first; a page kept as is is
still named. ``word_marks`` gives only the marks of the cited documents' first reader.
The not-found answer through the real service names the page (the exit test's blurred
page). Customer separation: tests/test_isolation_answering_hard_to_read.py.
"""

from typing import Any

import pytest

from kaagaz.answering.hard_to_read import MAX_NAMED_PAGES, FlaggedPage, PgReadingQuality
from kaagaz.answering.service import AnswerService
from kaagaz.db.documents import accept_page
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import span_to_json
from kaagaz.reading.spans import BoxLocation, Span
from kaagaz.search.scope import Scope
from tests.settings_factory import make_settings
from tests.test_api_page_fixes import read_page
from tests.test_api_support import NOW, Api

pytestmark = pytest.mark.db


def _scan(api: Api, customer_id: str, name: str, **columns: Any) -> str:
    return api.add_document(customer_id, detected_type="jpeg", name=name, **columns)


def test_f6_flagged_pages_are_unreadable_then_uncertain_of_live_current_documents(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed("reviewer")
    c = signed.customer_id
    register = _scan(api, c, "Register.jpg")
    read_page(api, c, register, 1, read_quality="uncertain", read_issues=["handwriting_hard"], unsure_words=3)
    invoice = _scan(api, c, "Invoice_12.pdf", page_count=3)
    read_page(api, c, invoice, 1)  # good: never named
    read_page(api, c, invoice, 3, read_quality="unreadable", read_issues=["blurry"])
    binned = _scan(api, c, "binned.jpg", deleted_at=api.clock(), deleted_by=signed.user_id)
    read_page(api, c, binned, 1, read_quality="unreadable", read_issues=["blurry"])
    reading = _scan(api, c, "reading.jpg", status="clean")
    read_page(api, c, reading, 1, read_quality="unreadable", read_issues=["blurry"])
    older = _scan(api, c, "old.jpg", is_current=False)
    read_page(api, c, older, 1, read_quality="unreadable", read_issues=["blurry"])

    pages, total = PgReadingQuality(db).flagged_pages(c)

    assert pages == [
        FlaggedPage(invoice, "Invoice_12.pdf", 3, "unreadable"),
        FlaggedPage(register, "Register.jpg", 1, "uncertain"),
    ]
    assert total == 2


def test_f6_a_page_kept_as_is_is_still_named(db: Database) -> None:
    """PRD 5 section 12: "Keep as is" on an unreadable page, then a chat question about it:
    the answer still names the page as unreadable."""
    api = Api(db)
    signed = api.new_signed("reviewer")
    c = signed.customer_id
    blurred = _scan(api, c, "blurred.jpg")
    read_page(api, c, blurred, 1, read_quality="unreadable", read_issues=["blurry"])
    with db.customer(c) as conn:
        assert accept_page(conn, c, blurred, 1, user_id=signed.user_id, now=NOW)

    pages, total = PgReadingQuality(db).flagged_pages(c)

    assert (pages, total) == ([FlaggedPage(blurred, "blurred.jpg", 1, "unreadable")], 1)


def test_f6_flagged_pages_stay_inside_the_chats_scope(db: Database) -> None:
    api = Api(db)
    c = api.new_signed().customer_id
    inside, outside = _scan(api, c, "inside.jpg"), _scan(api, c, "outside.jpg")
    for document_id in (inside, outside):
        read_page(api, c, document_id, 1, read_quality="unreadable", read_issues=["glare"])

    pages, total = PgReadingQuality(db).flagged_pages(c, scope=Scope(document_ids=(inside,)))

    assert ([page.document_id for page in pages], total) == ([inside], 1)


def test_f6_at_most_the_named_pages_come_back_with_the_count_of_all(db: Database) -> None:
    api = Api(db)
    c = api.new_signed().customer_id
    document_id = _scan(api, c, "long scan.pdf", page_count=MAX_NAMED_PAGES + 3)
    for page_no in range(1, MAX_NAMED_PAGES + 4):
        read_page(api, c, document_id, page_no, read_quality="uncertain", read_issues=["unpinned"])

    pages, total = PgReadingQuality(db).flagged_pages(c)

    assert [page.page_no for page in pages] == list(range(1, MAX_NAMED_PAGES + 1))
    assert total == MAX_NAMED_PAGES + 3


def test_f6_word_marks_are_the_first_readers_unsure_and_illegible_words(db: Database) -> None:
    api = Api(db)
    c = api.new_signed().customer_id
    document_id = _scan(api, c, "Register.jpg")
    api.add_page(c, document_id)
    line = BoxLocation(1, 0.1, 0.2, 0.9, 0.3)
    spans = [
        Span("TOTAL 1180 [?]", "TOTAL 1180 [?]", line, None, "paragraph", "ocr", box_source="found"),
        Span("1180", "1180", BoxLocation(1, 0.4, 0.2, 0.5, 0.3), None, origin="ocr", quality="unsure"),
        Span("[?]", "[?]", BoxLocation(1, 0.6, 0.2, 0.7, 0.3), None, origin="ocr", quality="illegible"),
        Span("Paid", "Paid", BoxLocation(1, 0.1, 0.5, 0.3, 0.55), 0.9, origin="ocr"),
    ]
    api.add_reader_output(c, document_id, [span_to_json(span) for span in spans], reader="gemini")
    second = [
        span_to_json(
            Span("1180", "1180", BoxLocation(1, 0.4, 0.2, 0.5, 0.3), None, origin="ocr", quality="unsure")
        )
    ]
    api.add_reader_output(c, document_id, second, role="second", reader="pdfium")

    marks = PgReadingQuality(db).word_marks(c, [document_id])

    assert sorted((mark.document_id, mark.location) for mark in marks) == [
        (document_id, BoxLocation(1, 0.4, 0.2, 0.5, 0.3)),
        (document_id, BoxLocation(1, 0.6, 0.2, 0.7, 0.3)),
    ]
    assert PgReadingQuality(db).word_marks(c, []) == []


def test_a_page_read_by_the_local_reader_is_one_mark_over_the_whole_page(db: Database) -> None:
    """Council 16 Oct 2026: a page graded unreadable, or read locally (a timeout, a refusal),
    has the backup reader's text, so every citation on it is hard to read. Another
    customer's flagged page never shows."""
    api = Api(db)
    c = api.new_signed().customer_id
    timed_out = _scan(api, c, "7-12.jpg")
    read_page(api, c, timed_out, 1, read_quality="unreadable", read_issues=["reader_refused"])
    backup = _scan(api, c, "Bill.jpg")
    read_page(api, c, backup, 2, read_quality="uncertain", read_issues=["read_locally"])
    fine = _scan(api, c, "Clear.jpg")
    read_page(api, c, fine, 1, read_quality="uncertain", read_issues=["unpinned"])
    other = api.new_signed().customer_id
    theirs = _scan(api, other, "Theirs.jpg")
    read_page(api, other, theirs, 1, read_quality="unreadable", read_issues=["reader_refused"])

    marks = PgReadingQuality(db).word_marks(c, [timed_out, backup, fine, theirs])

    assert sorted((mark.document_id, mark.location) for mark in marks) == sorted(
        [(timed_out, BoxLocation(1, 0.0, 0.0, 1.0, 1.0)), (backup, BoxLocation(2, 0.0, 0.0, 1.0, 1.0))]
    )


def test_f6_the_exit_tests_blurred_page_is_named_when_the_value_is_not_found(db: Database) -> None:
    """Exit test, Chat: a value printed only on the blurred page is "not found in the
    readable text", naming that page. The real service and database, no model."""
    api = Api(db)
    c = api.new_signed().customer_id
    blurred = _scan(api, c, "blurred photo.jpg")
    read_page(api, c, blurred, 1, read_quality="unreadable", read_issues=["blurry", "no_text"])
    service = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)

    answer = service.answer(c, "What is the electricity bill amount?")

    assert answer.status == "not_found"
    assert answer.reading_note == (
        "Not found in the readable text. 1 page couldn't be read: blurred photo.jpg page 1."
    )
    assert answer.to_json()["unreadable_pages"] == [
        {
            "document_id": blurred,
            "document_name": "blurred photo.jpg",
            "page_no": 1,
            "read_quality": "unreadable",
        }
    ]
