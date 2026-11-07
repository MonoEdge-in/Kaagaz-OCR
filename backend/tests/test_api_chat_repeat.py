"""#535 (Ask speed, PR 2): a repeated question is answered at once from its stored answer, with no
model call: the same question (case and spacing aside), asked fresh by the same person, in the
same scope, with the same answer settings, the same day, while the organisation's documents and
the words that steer the search are unchanged (answering/ready.py ``repeat_key``). Kept in the
database (the stored answer message), so every API process finds it, under RLS."""

import logging
from datetime import date
from functools import partial
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import ready
from kaagaz.answering.words_text import check_meaning, check_word
from kaagaz.db import user_words
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed, answer_json
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

QUESTION = "What is the grand total of INV-1042?"


def ask(signed: Signed, question: str = QUESTION, **body: Any) -> dict[str, Any]:
    reply = signed.client.post("/api/chat", json={"question": question, **body}, headers=CSRF)
    assert reply.status_code == 200, reply.text
    found: dict[str, Any] = reply.json()
    return found


def test_a_second_identical_question_makes_no_model_call_and_gives_the_same_answer(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    api = Api(db)
    signed = api.new_signed()
    api.add_document(signed.customer_id)
    caplog.set_level(logging.INFO, logger="kaagaz.answering.timing")

    first = ask(signed)
    again = ask(signed, "  what is the GRAND total of inv-1042? ")  # case and spacing aside

    assert len(api.answers.asked) == 1  # the second made no model call, no search
    assert again["answer"]["answer"] == first["answer"]["answer"] == answer_json()  # no server field
    assert again["answer"]["text"] == first["answer"]["text"]
    assert again["conversation_id"] != first["conversation_id"]  # a conversation of its own, saved
    assert again["question"]["text"] == "what is the GRAND total of inv-1042?"
    lines = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert len(lines) == 1 and " cached=1 " in f"{lines[0]} " and "status=answered" in lines[0]
    with db.customer(signed.customer_id) as conn:
        audited = conn.execute(text("SELECT count(*) FROM audit_log WHERE action = 'chat.ask'")).scalar_one()
    assert audited == 2


def test_a_changed_document_or_word_is_answered_again(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)
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
    """Another day ("last month" may mean another month), the running code or any setting is
    another key; the same inputs are the same key, whatever the case and spacing of the question."""
    api = Api(db)
    signed = api.new_signed()
    inputs = ("pins", {"nlq_clarify": True}, "choice", "scope", date(2026, 9, 27), "commit-1")
    with db.user(signed.customer_id, signed.user_id) as conn:
        key = partial(ready.repeat_key, conn, signed.customer_id, signed.user_id)
        same = key(QUESTION, inputs)
        assert key(f"  {QUESTION.upper()} ", inputs) == same
        for n, changed in enumerate([date(2026, 9, 28), "commit-2", {"nlq_clarify": False}]):
            other = list(inputs)
            other[[4, 5, 1][n]] = changed
            assert key(QUESTION, other) != same


def test_other_answer_settings_or_scope_or_a_follow_up_never_get_the_stored_answer(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)
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
    api = Api(db)
    signed = api.new_signed()
    api.add_document(signed.customer_id)
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
    api = Api(db)
    signed = api.new_signed()
    api.add_document(signed.customer_id)
    api.answers.reply = answer_json(status="not_found")
    ask(signed)
    ask(signed)
    assert len(api.answers.asked) == 2  # a not found is asked again: the model may have been down

    api.answers.reply = answer_json()
    rated = ask(signed)
    assert len(api.answers.asked) == 3
    sent = signed.client.post(
        f"/api/messages/{rated['answer']['id']}/feedback", json={"rating": "down"}, headers=CSRF
    )
    assert sent.status_code == 204
    ask(signed)
    assert len(api.answers.asked) == 4  # rated Not useful: answered again


def test_the_stored_key_is_never_shown(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    api.add_document(signed.customer_id)
    asked = ask(signed)
    with db.customer(signed.customer_id) as conn:
        stored = conn.execute(
            text("SELECT answer FROM messages WHERE id = :m"), {"m": asked["answer"]["id"]}
        ).scalar_one()
    assert len(stored["repeat_key"]) == 64  # a sha256, never the question or the inputs
    shown = signed.client.get(f"/api/conversations/{asked['conversation_id']}").json()
    assert "repeat_key" not in str(shown) and "repeat_key" not in str(asked)
