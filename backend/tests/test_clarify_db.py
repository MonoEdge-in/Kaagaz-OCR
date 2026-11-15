"""PRD 6 B review fixes against a real database: the vocabulary narrowed to the chat's scope, a
property keyed as #245 keys it, and the statement served by value_keys' B-tree index."""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import clarify
from kaagaz.answering.clarify import Entity, Pending, PgVocabulary, after_reply
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from tests.db_support import insert_document, insert_key, new_customer
from tests.settings_factory import make_settings
from tests.test_answering_golden import NOW
from tests.test_ask_speed_db import Model
from tests.test_search_db import SLOW_WORD, add_document, add_pieces, axis

pytestmark = pytest.mark.db


def test_the_scope_narrows_the_vocabulary_and_a_property_pick_stays_live(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        mine, other = insert_document(conn, customer), insert_document(conn, customer)
        insert_key(conn, customer, mine, "person", "Nikhil Patel")
        insert_key(conn, customer, other, "person", "Nikhil Desai")
        insert_key(conn, customer, mine, "property", "Survey 45/2")
    everywhere, scoped = PgVocabulary(db), PgVocabulary(db).within([mine])
    asked = [Entity("person", "Nikhil")]

    assert sorted(e.label for e in everywhere.candidates(customer, asked)[0]) == [
        "Nikhil Desai",
        "Nikhil Patel",
    ]
    assert [e.label for e in scoped.candidates(customer, asked)[0]] == ["Nikhil Patel"]  # review 4
    pending = Pending("Unowned share of the property?", "property", None, (), (), 1, ("Survey 45/2",))
    assert after_reply(everywhere, customer, pending).question == "Unowned share of the property Survey 45/2"


def test_the_vocabulary_is_aggregated_once_and_read_through_indexes_under_rls(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
        for n in range(200):
            insert_key(conn, customer, document, "person", f"Person {n}")
    with db.engine.begin() as conn:  # the owner: fresh statistics
        conn.execute(text("ANALYZE value_keys"))
    params: dict[str, Any] = {
        "c": customer,
        "n": [0],
        "kinds": ["person"],
        "latins": ["nikhil"],
        "sounds": ["nkl"],
    }
    params |= {"folds": ["nikhil"], "most": 10, "scoped": None}
    with db.customer(customer) as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(conn.execute(text("EXPLAIN " + clarify._CANDIDATES.text), params).scalars())
    assert "CTE vocabulary" in plan and "Seq Scan on value_keys" not in plan, plan  # review 5
    assert any("value_keys_" in line and "Index" in line for line in plan.splitlines()), plan


def test_any_keys_is_one_exists_read_through_an_index_under_rls(db: Database) -> None:  # Ask speed
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
        for n in range(200):
            insert_key(conn, customer, document, "person", f"Person {n}")
    with db.engine.begin() as conn:  # the owner: fresh statistics
        conn.execute(text("ANALYZE value_keys"))
    for scoped in (None, [document]):
        with db.customer(customer) as conn:
            conn.execute(text("SET LOCAL enable_seqscan = off"))
            params = {"c": customer, "scoped": scoped}
            plan = "\n".join(conn.execute(text("EXPLAIN " + clarify._ANY_KEYS.text), params).scalars())
        assert "Seq Scan on value_keys" not in plan, plan
        assert any("value_keys_" in line and "Index" in line for line in plan.splitlines()), plan
    assert PgVocabulary(db).any_keys(customer) and PgVocabulary(db).within([document]).any_keys(customer)


def test_a_property_pick_is_answered_when_the_word_list_times_out(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production, 15 Nov 2026: picking "१४ १ पैकी" on a clarify gave the silent 503, the word
    list's statement timeout again. The pick's search drops that list (#619) and still answers."""
    customer, _ = new_customer(db)
    document = add_document(db, customer, name="7-12 Wagholi.pdf")
    piece = "गट नंबर १४ १ पैकी, property measurement 20 m by 30 m"
    add_pieces(db, customer, document, [(piece, axis(0))])
    with db.customer(customer) as conn:
        insert_key(conn, customer, document, "property", "१४ १ पैकी")
    settings = make_settings(model_embed="test-embed", answer_whole_doc_tokens=0)
    answers = AnswerService(db=db, model=Model(), settings=settings, clock=lambda: NOW)
    question = "Can you find me the measurement of this property in any other document?"
    pending = Pending(question, "property", None, (), (), 1, ("१४ १ पैकी",))
    control = answers.answer(customer, question, pending=pending)
    assert control.status == "answered" and [c.document_id for c in control.citations] == [document]

    monkeypatch.setattr(hybrid, "STATEMENT_TIMEOUT_MS", 100)
    monkeypatch.setattr(hybrid, "_WORD", text(SLOW_WORD))
    answer = answers.answer(customer, question, pending=pending)  # until #619: OperationalError, 503

    assert answer.status == "answered" and [c.document_id for c in answer.citations] == [document]
