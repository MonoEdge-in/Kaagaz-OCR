"""A conversation's summary and memory (PRD 5 M3) belong to one user of one customer
(real PostgreSQL): another customer, or a colleague in the same customer, can neither
read nor write them, nor ask in that conversation, and the API never returns them.
Each is paired with the owner's own call. Marked ``isolation`` by its file name.
"""

import pytest

from kaagaz.answering.memory import Memory
from kaagaz.db import chat as chat_sql
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

REMEMBERED = Memory(summary="The chat asked about the sellers.", names=("Sharma Traders",))


def _save(db: Database, signed: Signed, conversation: str, *, expected_turn: int = 0) -> bool:
    with db.customer(signed.customer_id) as conn:
        return chat_sql.save_memory(
            conn,
            signed.customer_id,
            conversation,
            signed.user_id,
            summary=REMEMBERED.summary,
            memory=REMEMBERED.to_json(),
            turn=6,
            expected_turn=expected_turn,
        )


def _read(db: Database, signed: Signed, conversation: str) -> object:
    with db.customer(signed.customer_id) as conn:
        state = chat_sql.conversation_memory(conn, signed.customer_id, conversation, signed.user_id)
    return None if state is None else (state["summary"], state["memory"], state["summary_turn"])


def _asked(api: Api, signed: Signed) -> str:
    reply = signed.client.post("/api/chat", json={"question": "Who were the sellers?"}, headers=CSRF)
    assert reply.status_code == 200, reply.text
    return str(reply.json()["conversation_id"])


@pytest.mark.parametrize("who", ["another customer", "a colleague"])
def test_only_the_owner_reads_or_writes_the_memory_or_asks_in_the_conversation(
    db: Database, who: str
) -> None:
    api = Api(db)
    owner = api.new_signed()
    other = api.new_signed() if who == "another customer" else api.sign_in(owner.customer_id, "admin")
    conversation = _asked(api, owner)

    assert _save(db, other, conversation) is False  # 0 rows: the customer and user filters
    assert _read(db, other, conversation) is None
    assert _read(db, owner, conversation) == (None, None, 0)  # nothing was written
    asked = other.client.post(
        "/api/chat",
        json={"question": "And the second seller?", "conversation_id": conversation},
        headers=CSRF,
    )
    assert asked.status_code == 404
    assert len(api.answers.asked) == 1  # the other's question never reached the answer service

    assert _save(db, owner, conversation) is True  # the control
    assert _read(db, owner, conversation) == (REMEMBERED.summary, REMEMBERED.to_json(), 6)
    assert _read(db, other, conversation) is None


def test_a_stale_turn_writes_nothing(db: Database) -> None:
    api = Api(db)
    owner = api.new_signed()
    conversation = _asked(api, owner)
    assert _save(db, owner, conversation) is True
    assert _save(db, owner, conversation, expected_turn=0) is False  # another reply wrote turn 6 first


def test_the_api_never_returns_the_summary_or_the_memory(db: Database) -> None:
    api = Api(db)
    owner = api.new_signed()
    conversation = _asked(api, owner)
    assert _save(db, owner, conversation) is True
    shown = owner.client.get(f"/api/conversations/{conversation}").text
    listed = owner.client.get("/api/conversations").text
    for text in (shown, listed):
        assert "Sharma Traders" not in text and REMEMBERED.summary not in text


def test_another_customer_gets_no_name_for_a_document_id(db: Database) -> None:
    """The memory's document names (for the rewrite) are read for the reader's own live
    documents only: another customer's id gives nothing, even listed in a memory."""
    api = Api(db)
    owner, other = api.new_signed(), api.new_signed()
    document = api.add_document(owner.customer_id, name="rent-agreement.pdf")
    with db.customer(other.customer_id) as conn:
        assert chat_sql.live_document_names(conn, other.customer_id, [document]) == {}
    with db.customer(owner.customer_id) as conn:  # the control
        assert chat_sql.live_document_names(conn, owner.customer_id, [document]) == {
            document: "rent-agreement.pdf"
        }
