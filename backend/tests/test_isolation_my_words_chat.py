"""My words help only the user who wrote them (council 3 Oct 2026, A3).

The real AnswerService (PgRetriever, PgWords inside ``Database.user``) with no model.
A1 wrote "khilwa: warehouse"; A2 (same customer) and B1 (customer B, the same
document) ask the same question and get no help from it, and their answers are the
same before and after A1 wrote the word. A1 is the positive control.
"""

import uuid
from typing import Any

import pytest

from kaagaz.answering.expansion import PgWords
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from tests.db_support import new_customer
from tests.settings_factory import make_settings
from tests.test_api_support import NOW
from tests.test_search_db import add_document, add_pieces
from tests.test_user_words_db import added, new_member

pytestmark = pytest.mark.db

QUESTION = "khilwa Bhiwandi?"


def _ask(db: Database, customer: str, user: str) -> dict[str, Any]:
    answers = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)
    answer = answers.answer(customer, QUESTION, user_id=user).to_json()
    for citation in answer["citations"]:  # a piece id per customer: compare what was found
        citation.pop("ref_id")
    return answer


def test_only_the_writer_of_a_word_is_helped_by_it(db: Database) -> None:
    a, a1 = new_customer(db)
    a2 = new_member(db, a)
    b, b1 = new_customer(db)
    for customer in (a, b):
        add_pieces(db, customer, add_document(db, customer), [("Warehouses at Bhiwandi", None)])
    before = {user: _ask(db, customer, user) for customer, user in ((a, a1), (a, a2), (b, b1))}

    added(db, a, a1, "khilwa", "warehouse")

    mine = _ask(db, a, a1)
    assert (mine["status"], mine["label"]) == ("answered", "needs_review")
    assert mine["understood_words"] == [{"word": "khilwa", "meaning": "warehouse"}]
    assert before[a1]["status"] == "not_found"  # the word made the difference
    for customer, user in ((a, a2), (b, b1)):
        theirs = _ask(db, customer, user)
        assert theirs == before[user]
        assert (theirs["status"], theirs["understood_words"]) == ("not_found", [])


def test_the_words_are_read_for_the_asking_user_only(db: Database) -> None:
    a, a1 = new_customer(db)
    a2 = new_member(db, a)
    b, b1 = new_customer(db)
    added(db, a, a1, "khilwa", "warehouse")
    words = PgWords(db)

    assert [(w.word, w.meaning) for w in words.words(a, a1)] == [("khilwa", "warehouse")]
    assert words.words(a, a2) == []
    assert words.words(b, b1) == []
    assert words.words(b, a1) == []  # A1's id under another customer
    assert words.words(a, str(uuid.uuid4())) == []
