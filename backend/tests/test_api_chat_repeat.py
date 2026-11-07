"""#535 (Ask speed, PR 2): a repeated question is answered at once from its stored answer, with no
model call: the same question (case and spacing aside), asked fresh by the same person, in the
same scope, with the same answer settings, the same day, while the organisation's documents and
the words that steer the search are unchanged (answering/ready.py ``repeat_key``). Kept in the
database (the stored answer message), so every API process finds it, under RLS. Review of #535:
only an answer the model drafted (or a fixed one), never one made while the model or the vectors
were down; never once any copy is rated Not useful; a tag scope's documents and the pieces still
without a vector are in the key too."""

import dataclasses
import logging
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.ai.client import ModelUnavailable
from kaagaz.answering import ready
from kaagaz.answering.words_text import check_meaning, check_word
from kaagaz.db import user_words
from kaagaz.db.engine import Database
from kaagaz.search.hybrid import vector_literal
from kaagaz.settings import Settings
from tests.db_support import insert_tag, tag_document
from tests.settings_factory import make_settings
from tests.test_answering_golden import QUESTION as GOLDEN_QUESTION
from tests.test_answering_golden import A, FakeModel, service
from tests.test_api_support import CSRF, Api, Signed, answer_json
from tests.test_operator_auth_db import _owner
from tests.test_search_db import RECIPE, add_pieces, axis

pytestmark = pytest.mark.db

QUESTION = "What is the grand total of INV-1042?"
DRAFTED = {**answer_json(), "model_used": True}  # an answer the model wrote


def library(db: Database) -> tuple[Api, Signed, str]:
    """A signed-in member, one ready document, and answers the model drafts."""
    api = Api(db)
    signed = api.new_signed()
    api.answers.reply = dict(DRAFTED)
    return api, signed, api.add_document(signed.customer_id)


def rate_down(signed: Signed, asked: dict[str, Any]) -> None:
    sent = signed.client.post(
        f"/api/messages/{asked['answer']['id']}/feedback", json={"rating": "down"}, headers=CSRF
    )
    assert sent.status_code == 204


def ask(signed: Signed, question: str = QUESTION, **body: Any) -> dict[str, Any]:
    reply = signed.client.post("/api/chat", json={"question": question, **body}, headers=CSRF)
    assert reply.status_code == 200, reply.text
    found: dict[str, Any] = reply.json()
    return found


def test_a_second_identical_question_makes_no_model_call_and_gives_the_same_answer(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    api, signed, _ = library(db)
    caplog.set_level(logging.INFO, logger="kaagaz.answering.timing")

    first = ask(signed)
    again = ask(signed, "  what is the GRAND total of inv-1042? ")  # case and spacing aside

    assert len(api.answers.asked) == 1  # the second made no model call, no search
    assert again["answer"]["answer"] == first["answer"]["answer"] == DRAFTED  # no server field
    assert again["answer"]["text"] == first["answer"]["text"]
    assert again["conversation_id"] != first["conversation_id"]  # a conversation of its own, saved
    assert again["question"]["text"] == "what is the GRAND total of inv-1042?"
    lines = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert len(lines) == 1 and " cached=1 " in f"{lines[0]} " and "status=answered" in lines[0]
    with db.customer(signed.customer_id) as conn:
        audited = conn.execute(text("SELECT count(*) FROM audit_log WHERE action = 'chat.ask'")).scalar_one()
    assert audited == 2


def test_a_changed_document_or_word_is_answered_again(db: Database) -> None:
    api, signed, document = library(db)
    ask(signed)

    with db.customer(signed.customer_id) as conn:  # a document changed (renamed, re-read, Trash...)
        conn.execute(text("UPDATE documents SET display_name = 'bill.pdf' WHERE id = :d"), {"d": document})
    ask(signed)
    assert len(api.answers.asked) == 2
    ask(signed)
    assert len(api.answers.asked) == 2  # unchanged since: the stored one

    api.add_document(signed.customer_id, name="new.pdf")  # a document added
    ask(signed)
    assert len(api.answers.asked) == 3

    with db.user(signed.customer_id, signed.user_id) as conn:  # one of the asker's own words
        word, meaning = check_word("bill").word, check_meaning("invoice")
        user_words.add_word(
            conn, signed.customer_id, signed.user_id, word=word, meaning=meaning, source="manual"
        )
    ask(signed)
    assert len(api.answers.asked) == 4


def test_the_key_holds_the_day_and_every_input(db: Database) -> None:
    """Another day ("last month" may mean another month), the running code, a switch, the picks,
    the scope, a tag scope's documents, an answer-shaping setting or the stamp is another key; the
    same inputs are the same key, whatever the case and spacing of the question."""
    api = Api(db)
    signed = api.new_signed()
    settings = make_settings()
    inputs = [
        ["pin"],
        {"nlq_clarify": True},
        "choice",
        "scope",
        ["tagged-1"],
        date(2026, 9, 27),
        "commit-1",
        ready.shaping(settings),
    ]
    changes = {
        0: ["pin", "pin-2"],
        1: {"nlq_clarify": False},
        3: "scope-2",
        4: ["tagged-1", "tagged-2"],
        5: date(2026, 9, 28),
        6: "commit-2",
        7: ready.shaping(dataclasses.replace(settings, answer_max_evidence=settings.answer_max_evidence + 1)),
    }
    with db.user(signed.customer_id, signed.user_id) as conn:

        def key(question: str, given: list[Any], made_from: str = "stamp-1") -> str:
            return ready.repeat_key(conn, signed.customer_id, signed.user_id, question, given, made_from)

        same = key(QUESTION, inputs)
        assert key(f"  {QUESTION.upper()} ", inputs) == same
        assert key(QUESTION, inputs, "stamp-2") != same
        for place, changed in changes.items():
            assert key(QUESTION, [changed if n == place else each for n, each in enumerate(inputs)]) != same
    for name in ("answer_whole_doc_tokens", "answer_swift_input_tokens", "query_aspects_max", "model_chat"):
        assert name in ready.ANSWER_SHAPING


def test_other_answer_settings_or_scope_or_a_follow_up_never_get_the_stored_answer(db: Database) -> None:
    api, signed, document = library(db)
    first = ask(signed)

    ask(signed, scope={"document_ids": [document]})  # another scope
    assert len(api.answers.asked) == 2
    ask(signed, conversation_id=first["conversation_id"])  # a follow-up: its history may change it
    assert len(api.answers.asked) == 3
    _owner(  # an organisation switch the answer reads
        db,
        "INSERT INTO org_feature_switches (customer_id, feature, enabled) VALUES (:c, 'nlq_clarify', false)",
        {"c": signed.customer_id},
    )
    ask(signed)
    assert len(api.answers.asked) == 4 and api.answers.clarifies[-1] is False


def test_another_person_or_organisation_never_gets_the_stored_answer(db: Database) -> None:
    api, signed, _ = library(db)
    ask(signed)

    colleague = api.sign_in(signed.customer_id)  # their own words and picks may steer it
    ask(colleague)
    other = api.new_signed()  # another organisation
    api.add_document(other.customer_id)
    ask(other)

    assert api.answers.asked == [
        (signed.customer_id, QUESTION),
        (signed.customer_id, QUESTION),
        (other.customer_id, QUESTION),
    ]


def test_only_a_found_answer_is_reused_and_never_one_rated_not_useful(db: Database) -> None:
    api, signed, _ = library(db)
    api.answers.reply = {**answer_json(status="not_found"), "model_used": True}
    ask(signed)
    ask(signed)
    assert len(api.answers.asked) == 2  # a not found is asked again: the model may have been down

    api.answers.reply = dict(DRAFTED)
    rated = ask(signed)
    assert len(api.answers.asked) == 3
    rate_down(signed, rated)
    ask(signed)
    assert len(api.answers.asked) == 4  # rated Not useful: answered again


@pytest.mark.parametrize("rated", ["copy", "original"])
def test_any_copy_rated_not_useful_stops_every_copy_being_reused(db: Database, rated: str) -> None:
    api, signed, _ = library(db)
    original = ask(signed)
    copy = ask(signed)  # served from the original, and stored with the same key
    assert len(api.answers.asked) == 1
    rate_down(signed, copy if rated == "copy" else original)
    ask(signed)
    assert len(api.answers.asked) == 2


def test_a_tag_scope_is_answered_again_when_its_documents_change(db: Database) -> None:
    api, signed, document = library(db)
    with db.customer(signed.customer_id) as conn:
        tag = insert_tag(conn, signed.customer_id, signed.user_id, "march")
        tag_document(conn, signed.customer_id, document, tag)
    scoped = {"tag_ids": [tag]}
    ask(signed, scope=scoped)
    ask(signed, scope=scoped)
    assert len(api.answers.asked) == 1
    with db.customer(signed.customer_id) as conn:  # the tag taken off: the document moves out
        conn.execute(text("DELETE FROM document_tags WHERE document_id = :d"), {"d": document})
    ask(signed, scope=scoped)
    assert len(api.answers.asked) == 2


@pytest.mark.parametrize(
    ("reply", "degraded", "reused"),
    [
        ({"model_used": False}, False, False),  # quoted without the model: it was down or off
        ({"model_used": True}, True, False),  # the question's vector or search was degraded
        ({"model_used": False, "route": "fixed_query"}, False, True),  # a fixed answer needs no model
        ({"model_used": True}, False, True),
    ],
)
def test_only_an_answer_made_with_the_model_up_is_reused(
    db: Database, reply: dict[str, Any], degraded: bool, reused: bool
) -> None:
    api, signed, _ = library(db)
    api.answers.reply = {**answer_json(), **reply}
    api.answers.degraded = degraded
    ask(signed)
    ask(signed)
    assert len(api.answers.asked) == (1 if reused else 2)


def test_an_answer_made_before_the_vectors_arrive_is_not_reused_after(db: Database) -> None:
    api, signed, document = library(db)
    add_pieces(db, signed.customer_id, document, [("Grand Total: Rs. 1,20,000", None)])
    ask(signed)
    ask(signed)
    assert len(api.answers.asked) == 1
    with db.customer(signed.customer_id) as conn:  # the worker fills the vector in
        conn.execute(
            text("UPDATE pieces SET embedding = CAST(:v AS halfvec(1024)), embedding_model = :m"),
            {"v": vector_literal(axis(0)), "m": RECIPE},
        )
    ask(signed)
    assert len(api.answers.asked) == 2
    second = api.add_document(signed.customer_id, name="copy.pdf")
    add_pieces(db, signed.customer_id, second, [("Sub Total: Rs. 1,00,000", axis(1))])
    ask(signed)
    assert len(api.answers.asked) == 3
    with db.customer(signed.customer_id) as conn:  # a line correction clears one vector (#527)
        conn.execute(
            text("UPDATE pieces SET embedding = NULL, embedding_model = NULL WHERE document_id = :d"),
            {"d": second},
        )
    ask(signed)
    assert len(api.answers.asked) == 4


def test_an_answer_marks_a_missing_vector_or_outage_as_degraded() -> None:
    settings: Settings = make_settings(answer_min_evidence_score=0.6, answer_max_evidence=5)
    up = service(model=FakeModel(), settings=settings)[0].answer(A, GOLDEN_QUESTION)
    down = service(model=FakeModel(embed_error=ModelUnavailable("timeout")), settings=settings)[0]
    without = down.answer(A, GOLDEN_QUESTION)  # found by words: answered, never an outage
    assert (up.status, up.degraded) == ("answered", False)
    assert (without.status, without.unavailable, without.degraded) == ("answered", False, True)


def test_the_stored_key_is_never_shown(db: Database) -> None:
    _, signed, _ = library(db)
    asked = ask(signed)
    with db.customer(signed.customer_id) as conn:
        stored = conn.execute(
            text("SELECT answer FROM messages WHERE id = :m"), {"m": asked["answer"]["id"]}
        ).scalar_one()
    assert len(stored["repeat_key"]) == 64  # a sha256, never the question or the inputs
    shown = signed.client.get(f"/api/conversations/{asked['conversation_id']}").json()
    assert "repeat_key" not in str(shown) and "repeat_key" not in str(asked)
