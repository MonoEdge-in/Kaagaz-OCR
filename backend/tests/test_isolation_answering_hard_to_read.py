"""The pages that couldn't be read (PRD 5 F6) never cross customers.

Customer A has an unreadable page with word marks. Customer B's not-found answer never
names it, and B asking for the word marks of A's document by its id gets none (row
level security and the customer filter). The control: A's own answer names the page
and A's marks are found, so B's empty results come from the customer.
"""

import pytest

from kaagaz.answering.hard_to_read import PgReadingQuality
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import span_to_json
from kaagaz.reading.spans import BoxLocation, Span
from kaagaz.search.scope import Scope
from tests.settings_factory import make_settings
from tests.test_api_page_fixes import read_page
from tests.test_api_support import NOW, Api

pytestmark = pytest.mark.db

QUESTION = "What is the electricity bill amount?"


def test_b_never_sees_as_unreadable_pages_or_word_marks(db: Database) -> None:
    api = Api(db)
    a = api.new_signed().customer_id
    b = api.new_signed().customer_id
    a_doc = api.add_document(a, detected_type="jpeg", name="Sharma Register.jpg")
    read_page(api, a, a_doc, 1, read_quality="unreadable", read_issues=["blurry"], illegible_words=2)
    mark = Span("[?]", "[?]", BoxLocation(1, 0.4, 0.2, 0.5, 0.3), None, origin="ocr", quality="illegible")
    api.add_reader_output(a, a_doc, [span_to_json(mark)], reader="gemini")
    reading = PgReadingQuality(db)
    service = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)

    b_answer = service.answer(b, QUESTION)
    b_scoped = service.answer(b, QUESTION, scope=Scope(document_ids=(a_doc,)))

    for answer in (b_answer, b_scoped):
        assert answer.status == "not_found"
        assert (answer.reading_note, answer.unreadable_pages, answer.unreadable_page_count) == (None, (), 0)
        assert "Sharma" not in str(answer.to_json())
    assert reading.flagged_pages(b) == ([], 0)
    assert reading.flagged_pages(b, scope=Scope(document_ids=(a_doc,))) == ([], 0)
    assert reading.word_marks(b, [a_doc]) == []

    a_answer = service.answer(a, QUESTION)
    assert a_answer.reading_note == (
        "Not found in the readable text. 1 page to check: Sharma Register.jpg page 1."
    )
    assert [mark.document_id for mark in reading.word_marks(a, [a_doc])] == [a_doc]
