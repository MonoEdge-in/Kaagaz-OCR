"""Which Most relevant picks a later question gets (Tushar, 16 Oct 2026; kaagaz/db/chat.py
``pins_for``, kaagaz/answering/pins.py): the asker's own, the newest of each answer, this
conversation's marked, and none on a document in Trash. Another user's or customer's picks:
tests/test_isolation_source_pins.py."""

import pytest
from sqlalchemy import text

from kaagaz.answering.pins import Pin
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

ASKED = "what is the total"  # answer_json's normalized_question


def ask(signed: Signed, question: str, conversation_id: str | None = None) -> dict[str, str]:
    body = {"question": question} | ({} if conversation_id is None else {"conversation_id": conversation_id})
    reply = signed.client.post("/api/chat", json=body, headers=CSRF)
    assert reply.status_code == 200, reply.text
    data = reply.json()
    return {"conversation_id": data["conversation_id"], "answer_id": data["answer"]["id"]}


def pin(signed: Signed, answer_id: str, document_id: str, page_no: int) -> None:
    pick = {"document_id": document_id, "page_no": page_no, "previous_rank": 2}
    reply = signed.client.post(f"/api/messages/{answer_id}/pin", json=pick, headers=CSRF)
    assert reply.status_code == 204, reply.text


def test_a_later_question_gets_the_newest_pick_this_conversation_first(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = api.add_document(signed.customer_id, name="bill 1.pdf", uploaded_by=signed.user_id)
    second = api.add_document(signed.customer_id, name="bill 2.pdf", uploaded_by=signed.user_id)
    asked = ask(signed, "totals?")
    pin(signed, asked["answer_id"], second, 2)
    pin(signed, asked["answer_id"], first, 1)  # the newest pick of the answer wins

    ask(signed, "and the dates?", asked["conversation_id"])
    assert api.answers.pins[-1] == [Pin(first, 1, True, ASKED)]
    ask(signed, "totals again?")  # a new conversation: the same pick, from another one
    assert api.answers.pins[-1] == [Pin(first, 1, False, ASKED)]


def test_a_pick_on_a_document_in_trash_is_ignored_and_never_falls_back(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = api.add_document(signed.customer_id, name="bill 1.pdf", uploaded_by=signed.user_id)
    second = api.add_document(signed.customer_id, name="bill 2.pdf", uploaded_by=signed.user_id)
    asked = ask(signed, "totals?")
    pin(signed, asked["answer_id"], second, 1)
    pin(signed, asked["answer_id"], first, 1)
    with db.customer(signed.customer_id) as conn:
        conn.execute(
            text("UPDATE documents SET deleted_at = now() WHERE customer_id = :c AND id = :d"),
            {"c": signed.customer_id, "d": first},
        )

    ask(signed, "and the dates?", asked["conversation_id"])
    assert api.answers.pins[-1] == []  # not the older pick of bill 2 either
