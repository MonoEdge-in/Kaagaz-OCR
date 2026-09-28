"""Chat against a real database, with a fake answer service."""

import pytest
from sqlalchemy import text

from kaagaz.api.app import create_app
from kaagaz.api.routers.chat import NOT_FOUND_TEXT, answer_text
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, NOW, PASSWORD, Api, TestClient, answer_json

PAN = "ABCDE1234F"  # made up


def test_answer_text_joins_sentences_or_says_not_found() -> None:
    assert answer_text(answer_json()) == "The total is 1,200."
    assert answer_text(answer_json(status="not_found")) == NOT_FOUND_TEXT
    assert answer_text({"status": "answered", "sentences": "junk"}) == NOT_FOUND_TEXT


@pytest.mark.db
def test_ask_keeps_the_question_as_asked_stores_both_messages_and_audits(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()

    reply = signed.client.post("/api/chat", json={"question": f"  total for PAN {PAN}?  "}, headers=CSRF)

    assert reply.status_code == 200
    body = reply.json()
    asked = f"total for PAN {PAN}?"
    assert api.answers.asked == [(signed.customer_id, asked)]
    assert body["question"]["role"] == "user"
    assert body["question"]["text"] == asked
    assert body["question"]["answer"] is None
    assert body["question"]["feedback"] is None
    assert body["answer"]["role"] == "assistant"
    assert body["answer"]["answer"] == answer_json()
    assert body["answer"]["text"] == "The total is 1,200."
    assert body["answer"]["feedback"] is None

    with db.customer(signed.customer_id) as conn:
        stored = " ".join(conn.execute(text("SELECT text FROM messages")).scalars().all())
        title: str = conn.execute(text("SELECT title FROM conversations")).scalar_one()
        details: dict[str, object] = conn.execute(
            text("SELECT details FROM audit_log WHERE action = 'chat.ask'")
        ).scalar_one()
    assert PAN in stored
    assert title == asked
    assert details == {
        "conversation_id": body["conversation_id"],
        "question_id": body["question"]["id"],
        "status": "answered",
    }


@pytest.mark.db
def test_follow_up_goes_into_the_same_conversation_in_order(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = signed.client.post("/api/chat", json={"question": "first?"}, headers=CSRF).json()
    api.answers.reply = answer_json(status="not_found")
    second = signed.client.post(
        "/api/chat", json={"question": "second?", "conversation_id": first["conversation_id"]}, headers=CSRF
    ).json()
    assert second["conversation_id"] == first["conversation_id"]
    assert second["answer"]["text"] == NOT_FOUND_TEXT

    conversation = signed.client.get(f"/api/conversations/{first['conversation_id']}").json()
    assert conversation["title"] == "first?"
    assert [(m["role"], m["text"]) for m in conversation["messages"]] == [
        ("user", "first?"),
        ("assistant", "The total is 1,200."),
        ("user", "second?"),
        ("assistant", NOT_FOUND_TEXT),
    ]


@pytest.mark.db
def test_title_is_cut_to_200(db: Database) -> None:
    signed = Api(db).new_signed()
    body = signed.client.post("/api/chat", json={"question": "q" * 1000}, headers=CSRF).json()
    summary = signed.client.get("/api/conversations").json()["items"][0]
    assert summary["id"] == body["conversation_id"]
    assert len(summary["title"]) == 200


@pytest.mark.db
@pytest.mark.parametrize("question", ["", "   ", "x" * 1001])
def test_bad_questions_are_400(db: Database, question: str) -> None:
    api = Api(db)
    signed = api.new_signed()
    reply = signed.client.post("/api/chat", json={"question": question}, headers=CSRF)
    assert reply.status_code == 400
    assert api.answers.asked == []


@pytest.mark.db
def test_unknown_conversation_is_404_before_asking(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    reply = signed.client.post(
        "/api/chat",
        json={"question": "hi", "conversation_id": "7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"},
        headers=CSRF,
    )
    assert reply.status_code == 404
    assert api.answers.asked == []


@pytest.mark.db
def test_conversations_list_is_this_users_newest_first(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    colleague = api.sign_in(signed.customer_id, "member")
    older = signed.client.post("/api/chat", json={"question": "older"}, headers=CSRF).json()
    newer = signed.client.post("/api/chat", json={"question": "newer"}, headers=CSRF).json()
    colleague.client.post("/api/chat", json={"question": "theirs"}, headers=CSRF)

    items = signed.client.get("/api/conversations").json()["items"]

    assert [item["id"] for item in items] == [newer["conversation_id"], older["conversation_id"]]
    assert set(items[0]) == {"id", "title", "created_at"}


@pytest.mark.db
def test_a_colleague_cannot_open_or_rate_my_conversation(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    colleague = api.sign_in(signed.customer_id, "admin")
    body = signed.client.post("/api/chat", json={"question": "mine"}, headers=CSRF).json()

    assert colleague.client.get(f"/api/conversations/{body['conversation_id']}").status_code == 404
    reply = colleague.client.post(
        f"/api/messages/{body['answer']['id']}/feedback", json={"rating": "up"}, headers=CSRF
    )
    assert reply.status_code == 404


@pytest.mark.db
def test_feedback_upserts_and_shows_in_the_conversation(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    body = signed.client.post("/api/chat", json={"question": "q"}, headers=CSRF).json()
    answer_id = body["answer"]["id"]

    first = signed.client.post(f"/api/messages/{answer_id}/feedback", json={"rating": "up"}, headers=CSRF)
    second = signed.client.post(
        f"/api/messages/{answer_id}/feedback",
        json={"rating": "down", "reason": f"wrong total for {PAN}"},
        headers=CSRF,
    )

    assert first.status_code == 204
    assert second.status_code == 204
    messages = signed.client.get(f"/api/conversations/{body['conversation_id']}").json()["messages"]
    assert [m["feedback"] for m in messages] == [None, "down"]
    with db.customer(signed.customer_id) as conn:
        rows = conn.execute(text("SELECT rating, reason FROM feedback")).all()
    assert len(rows) == 1
    assert rows[0][0] == "down"
    assert PAN not in rows[0][1]


@pytest.mark.db
def test_feedback_on_a_question_or_with_bad_input_is_refused(db: Database) -> None:
    signed = Api(db).new_signed()
    body = signed.client.post("/api/chat", json={"question": "q"}, headers=CSRF).json()
    question_id, answer_id = body["question"]["id"], body["answer"]["id"]
    on_question = signed.client.post(
        f"/api/messages/{question_id}/feedback", json={"rating": "up"}, headers=CSRF
    )
    assert on_question.status_code == 404
    bad_rating = signed.client.post(
        f"/api/messages/{answer_id}/feedback", json={"rating": "meh"}, headers=CSRF
    )
    assert bad_rating.status_code == 400
    long_reason = signed.client.post(
        f"/api/messages/{answer_id}/feedback", json={"rating": "down", "reason": "x" * 501}, headers=CSRF
    )
    assert long_reason.status_code == 400


@pytest.mark.db
def test_default_wiring_uses_the_real_answer_service_without_a_model(db: Database) -> None:
    """No ``answers`` passed: create_app builds AnswerService (MODEL_ENABLED=false).
    A customer with no documents gets a stored not-found answer, never a guess."""
    api = Api(db)
    app = create_app(
        make_settings(model_enabled=False), db=db, storage=api.storage, queue=api.queue, clock=lambda: NOW
    )
    customer_id, _ = api.customer()
    _, email = api.add_user(customer_id, "member")
    client = TestClient(app)
    client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF)

    body = client.post("/api/chat", json={"question": "Kiska profit sabse jyada tha?"}, headers=CSRF).json()

    answer = body["answer"]["answer"]
    assert set(answer) == {
        "status",
        "label",
        "sentences",
        "citations",
        "closest_documents",
        "route",
        "normalized_question",
        "model_used",
    }
    assert answer["status"] == "not_found"
    assert answer["label"] is None
    assert answer["model_used"] is False
    assert body["answer"]["text"] == NOT_FOUND_TEXT
