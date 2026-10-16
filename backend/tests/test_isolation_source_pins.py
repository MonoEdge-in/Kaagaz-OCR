"""Ranked sources across customers (0036): customer A cannot pin a source on B's answer,
pin B's document on A's own answer, or see B's picks.

A's 404s come from the customer (session, the route's SQL and row level security), never
from a broken route: the control, B's own pick on the same ids, is 204 and shows.
"""

import pytest
from sqlalchemy import text

from kaagaz.db import chat as chat_sql
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, TestClient

pytestmark = pytest.mark.db


def test_a_cannot_pin_on_bs_answer_or_with_bs_document(db: Database) -> None:
    api = Api(db)
    b_user = api.new_signed()
    b_doc = api.add_document(b_user.customer_id, name="b.pdf", uploaded_by=b_user.user_id)
    b_body = b_user.client.post("/api/chat", json={"question": "b?"}, headers=CSRF).json()
    a_user = api.new_signed()
    a_doc = api.add_document(a_user.customer_id, name="a.pdf", uploaded_by=a_user.user_id)
    a_body = a_user.client.post("/api/chat", json={"question": "a?"}, headers=CSRF).json()

    def pin(client: TestClient, message_id: str, document_id: str) -> int:
        pick = {"document_id": document_id, "page_no": 1, "previous_rank": 1}
        return client.post(f"/api/messages/{message_id}/pin", json=pick, headers=CSRF).status_code

    assert pin(a_user.client, b_body["answer"]["id"], b_doc) == 404  # B's answer, B's document
    assert pin(a_user.client, b_body["answer"]["id"], a_doc) == 404  # B's answer, A's document
    assert pin(a_user.client, a_body["answer"]["id"], b_doc) == 404  # A's answer, B's document
    for customer_id in (a_user.customer_id, b_user.customer_id):
        with db.customer(customer_id) as conn:
            assert conn.execute(text("SELECT count(*) FROM source_pins")).scalar_one() == 0

    # The control: B's own pick on the same ids is taken, and only B sees it.
    assert pin(b_user.client, b_body["answer"]["id"], b_doc) == 204
    shown = b_user.client.get(f"/api/conversations/{b_body['conversation_id']}").json()["messages"][1]
    assert shown["pin"] == {"document_id": b_doc, "page_no": 1, "ref_id": None}
    with db.customer(a_user.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM source_pins")).scalar_one() == 0
    a_shown = a_user.client.get(f"/api/conversations/{a_body['conversation_id']}").json()["messages"][1]
    assert a_shown["pin"] is None


def test_another_users_or_customers_picks_never_reach_a_question(db: Database) -> None:
    # Most relevant picks rank later answers (kaagaz/answering/pins.py): the asker's own only.
    api = Api(db)
    a_user = api.new_signed()
    a_doc = api.add_document(a_user.customer_id, name="a.pdf", uploaded_by=a_user.user_id)
    a_body = a_user.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF).json()
    pick = {"document_id": a_doc, "page_no": 1, "previous_rank": 2}
    assert (
        a_user.client.post(f"/api/messages/{a_body['answer']['id']}/pin", json=pick, headers=CSRF).status_code
        == 204
    )

    colleague = api.sign_in(a_user.customer_id)
    b_user = api.new_signed()
    for signed in (colleague, b_user):
        assert signed.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF).status_code == 200
        assert api.answers.pins[-1] == []
    # The control: A's own next question gets the pick.
    follow = {"question": "and the dates?", "conversation_id": a_body["conversation_id"]}
    assert a_user.client.post("/api/chat", json=follow, headers=CSRF).status_code == 200
    assert [p.document_id for p in api.answers.pins[-1]] == [a_doc]

    # Row level security alone: B's session sees none of A's picks, even when asked by A's ids.
    with db.customer(b_user.customer_id) as conn:
        assert chat_sql.pins_for(conn, a_user.customer_id, a_user.user_id, a_body["conversation_id"]) == []
