"""Greetings, thanks and "what can you do" (Tushar, 24 Oct 2026): answered at once with no
search and no model, never as an error; anything else still goes to Ask."""

import re

import pytest
from sqlalchemy import text

from kaagaz.answering import ready, small_talk
from kaagaz.answering.ready import ReadyAnswers, ShownQuestions
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api

BANNED = re.compile(r"\b(sorry|unfortunately|perfect|guaranteed|instant|magic|couldn't)\b", re.IGNORECASE)


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("hi", "greet"),
        ("Hello!", "greet"),
        ("hii kaagaz", "greet"),
        ("Namaste ji", "greet"),
        ("नमस्ते", "greet"),
        ("good morning", "greet"),
        ("thanks", "thanks"),
        ("Thank you so", None),  # "so" is no small-talk word
        ("dhanyavad", "thanks"),
        ("who are you?", "who"),
        ("Kaagaz kya hai", "who"),
        ("What can Kaagaz do?", "help"),
        ("help", "help"),
        ("hi, what can you do", "help"),
        ("aap kya kar sakte ho", "help"),
    ],
)
def test_small_talk_is_known_by_its_whole_message(question: str, kind: str | None) -> None:
    assert small_talk.kind(question) == kind


@pytest.mark.parametrize(
    "question",
    [
        "hi 42",
        "help me find invoice 42",
        "thanks what is the total",
        "Kiska profit sabse jyada tha",
        "hello patil",
        "hi what is the total of the invoice from mehta",
        "who are the owners of survey 72",
        "",
    ],
)
def test_anything_else_goes_to_ask(question: str) -> None:
    assert small_talk.kind(question) is None


def test_the_replies_are_plain_honest_and_name_the_asker() -> None:
    greet = small_talk.sentences("greet", "Namaste", "Tushar")
    assert greet[0].startswith("Namaste, Tushar.")
    assert small_talk.sentences("greet", "hi", None)[0].startswith("Hello. ")
    help_list = small_talk.sentences("help", "what can you do", "Tushar")
    assert help_list == ["Here is what I can do for you:", *small_talk.CAN_DO]
    for talk in ("greet", "thanks", "who", "help"):
        for line in small_talk.sentences(talk, "hi", "Tushar"):  # type: ignore[arg-type]
            assert not BANNED.search(line), line
            assert not re.search(r"\d", line), line
    answer = small_talk.answer("help", "help", None, ["a", "b", "c", "d"])
    assert (answer["status"], answer["route"], answer["model_used"], answer["citations"]) == (
        "answered",
        "small_talk",
        False,
        [],
    )
    assert answer["suggestions"] == ["a", "b", "c"]


@pytest.fixture(autouse=True)
def _empty() -> None:
    ready.READY = ReadyAnswers()
    ready.SHOWN = ShownQuestions()


@pytest.mark.db
def test_hi_is_answered_at_once_with_questions_to_try_and_never_becomes_a_habit(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    api.add_document(signed.customer_id, name="7-12.pdf", doc_type="land_record")
    for _ in range(2):
        reply = signed.client.post("/api/chat", json={"question": "hi"}, headers=CSRF)
        assert reply.status_code == 200, reply.text
    body = reply.json()["answer"]
    assert body["answer"]["route"] == "small_talk"
    assert body["answer"]["suggestions"] == ["Who are the owners named in the land records?"]
    assert body["text"].startswith("Hello")
    assert api.answers.asked == []  # no search, no model
    items = signed.client.get("/api/chat/ready-questions").json()["items"]
    assert "hi" not in [item["text"] for item in items]
    with db.customer(signed.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM messages")).scalar_one() == 4


@pytest.mark.db
def test_a_question_with_nothing_close_offers_questions_this_library_answers(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    api.add_document(signed.customer_id, name="bill.pdf", doc_type="invoice")
    api.answers.reply = {**api.answers.reply, "status": "not_found", "sentences": [], "citations": []}
    reply = signed.client.post("/api/chat", json={"question": "Who signed the lease?"}, headers=CSRF)
    assert reply.status_code == 200
    assert reply.json()["answer"]["answer"]["suggestions"] == [
        "Which invoice had the largest total last month?"
    ]
