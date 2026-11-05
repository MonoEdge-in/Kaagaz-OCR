"""Home's questions with their answers ready (Tushar, 24 Oct 2026): up to three questions for
each person, their own habits first, then ones that suit their library; each answer made in
the background and given at once when that question is asked fresh, never to anyone else,
never after their documents change, never with other settings."""

import uuid

import pytest
from sqlalchemy import text

from kaagaz.answering import ready
from kaagaz.answering.ready import MAX_ENTRIES, SHOWN_SECONDS, TTL_SECONDS, ReadyAnswers, same_question
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api

LAND = "Who are the owners named in the land records?"
INVOICE = "Which invoice had the largest total last month?"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_two_askings_of_one_question_are_the_same_question() -> None:
    assert same_question("  Who  are the OWNERS? ") == same_question("who are the owners?")
    assert ready.key("c", "u", "A  b", (1,), "s") == ready.key("c", "u", "a b", (1,), "s")
    assert ready.key("c", "u", "a b", (1,), "s") != ready.key("c", "u2", "a b", (1,), "s")
    assert ready.key("c", "u", "a b", (1,), "s") != ready.key("c", "u", "a b", (2,), "s")
    assert ready.key("c", "u", "a b", (1,), "s") != ready.key("c", "u", "a b", (1,), "s2")


def test_a_ready_answer_is_kept_for_a_day_and_the_oldest_go_first() -> None:
    clock = Clock()
    kept = ReadyAnswers(clock)
    assert kept.claim(("a",)) is True
    assert kept.claim(("a",)) is False  # a second Home load does not make it twice
    kept.put(("a",), "answer")
    assert kept.get(("a",)) == "answer"
    assert kept.claim(("a",)) is False
    clock.now = TTL_SECONDS + 1
    assert kept.get(("a",)) is None
    assert kept.claim(("a",)) is True
    kept.release(("a",))
    assert kept.claim(("a",)) is True
    for n in range(MAX_ENTRIES + 1):
        kept.put((str(n),), n)
    assert kept.get(("0",)) is None
    assert kept.get((str(MAX_ENTRIES),)) == MAX_ENTRIES


@pytest.fixture(autouse=True)
def _empty() -> None:
    ready.READY = ReadyAnswers()
    ready.SHOWN = ready.ShownQuestions()


def _land_record(api: Api, customer_id: str) -> str:
    return api.add_document(customer_id, name="7-12 Extract.pdf", doc_type="land_record")


@pytest.mark.db
def test_home_offers_questions_for_the_library_and_answers_them_at_once(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _land_record(api, signed.customer_id)

    first = signed.client.get("/api/chat/ready-questions").json()["items"]
    assert first == [{"text": LAND, "yours": False, "ready": False}]
    assert api.answers.asked == [(signed.customer_id, LAND)]  # made after the reply

    again = signed.client.get("/api/chat/ready-questions").json()["items"]
    assert again == [{"text": LAND, "yours": False, "ready": True}]
    assert len(api.answers.asked) == 1

    reply = signed.client.post("/api/chat", json={"question": LAND}, headers=CSRF)
    assert reply.status_code == 200
    assert reply.json()["answer"]["answer"]["status"] == "answered"
    assert len(api.answers.asked) == 1  # the ready answer, no new draft
    with db.customer(signed.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM messages")).scalar_one() == 2


@pytest.mark.db
def test_a_ready_answer_is_never_given_after_the_documents_change_or_to_someone_else(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _land_record(api, signed.customer_id)
    signed.client.get("/api/chat/ready-questions")

    other = api.sign_in(signed.customer_id)
    other.client.post("/api/chat", json={"question": LAND}, headers=CSRF)
    assert len(api.answers.asked) == 2  # someone else asks: a fresh draft

    _land_record(api, signed.customer_id)  # the library changed
    signed.client.post("/api/chat", json={"question": LAND}, headers=CSRF)
    assert len(api.answers.asked) == 3

    signed.client.get("/api/chat/ready-questions")  # ready again for the changed library
    asked = len(api.answers.asked)
    started = signed.client.post("/api/chat", json={"question": "Which survey number?"}, headers=CSRF).json()
    signed.client.post(
        "/api/chat", json={"question": LAND, "conversation_id": started["conversation_id"]}, headers=CSRF
    )
    assert len(api.answers.asked) == asked + 2  # inside a conversation: always a fresh draft


@pytest.mark.db
def test_a_persons_own_habits_come_first_and_follow_ups_are_left_out(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _land_record(api, signed.customer_id)
    api.add_document(signed.customer_id, name="bill.pdf", doc_type="invoice")
    habit = f"What is the total for party {uuid.uuid4().hex[:6]}?"
    for _ in range(2):
        first = signed.client.post("/api/chat", json={"question": habit}, headers=CSRF).json()
    signed.client.post(
        "/api/chat",
        json={"question": "And the date of it?", "conversation_id": first["conversation_id"]},
        headers=CSRF,
    )

    items = signed.client.get("/api/chat/ready-questions").json()["items"]
    assert [(item["text"], item["yours"]) for item in items] == [
        (habit, True),
        (INVOICE, False),
        (LAND, False),
    ]  # one each: by name

    someone_else = api.sign_in(signed.customer_id)
    theirs = someone_else.client.get("/api/chat/ready-questions").json()["items"]
    assert habit not in [item["text"] for item in theirs]


@pytest.mark.db
def test_home_keeps_the_same_questions_until_one_is_used(db: Database) -> None:
    """Tushar, 24 Oct 2026: "if the user uses a recommended question, only then update them"."""
    api = Api(db)
    signed = api.new_signed()
    _land_record(api, signed.customer_id)
    shown = [item["text"] for item in signed.client.get("/api/chat/ready-questions").json()["items"]]
    assert shown == [LAND]

    habit = "What is the area of survey 72?"
    for _ in range(2):  # a new habit, but no shown question used: the list stays
        signed.client.post("/api/chat", json={"question": habit}, headers=CSRF)
    still = [item["text"] for item in signed.client.get("/api/chat/ready-questions").json()["items"]]
    assert still == [LAND]

    signed.client.post("/api/chat", json={"question": LAND}, headers=CSRF)  # one is used
    after = signed.client.get("/api/chat/ready-questions").json()["items"]
    assert [(item["text"], item["yours"]) for item in after] == [(habit, True), (LAND, True)]


def test_homes_list_stays_a_day_at_most_and_goes_once_one_is_used() -> None:
    """Tushar, 24 Oct 2026: no new questions until one is used, and 24 hours."""
    clock = Clock()
    shown = ready.ShownQuestions(clock)
    shown.keep("c", "u", [("Who owns 72?", True)])
    clock.now = SHOWN_SECONDS - 1
    shown.keep("c", "u", [("Who owns 72?", True)])  # shown again: its day does not restart
    assert shown.get("c", "u") == [("Who owns 72?", True)]
    clock.now = SHOWN_SECONDS + 1
    assert shown.get("c", "u") is None
    shown.keep("c", "u", [("Who owns 72?", True)])
    shown.used("c", "u", "  who OWNS 72? ")
    assert shown.get("c", "u") is None
    shown.keep("c", "u", [])  # nothing to show is never kept
    assert shown.get("c", "u") is None


@pytest.mark.db
def test_a_question_about_one_document_is_never_a_habit(db: Database) -> None:
    """The screenshot of 24 Oct 2026: "What is this document about?" was asked about one
    document, so it cannot be answered for all of them."""
    api = Api(db)
    signed = api.new_signed()
    deed = _land_record(api, signed.customer_id)
    for _ in range(2):
        signed.client.post(
            "/api/chat",
            json={"question": "What is this document about?", "scope": {"document_ids": [deed]}},
            headers=CSRF,
        )
    items = signed.client.get("/api/chat/ready-questions").json()["items"]
    assert [item["text"] for item in items] == [LAND]


@pytest.mark.db
def test_b2_a_document_being_read_never_makes_home_make_its_answers_again(db: Database) -> None:
    """Production QA B2: each progress write of a document being read moved the newest
    ``updated_at``, so every Home look paid for its ready answers again. A document not read
    yet (received or clean) is no part of the stamp; once read, it changes the stamp at once."""
    api = Api(db)
    signed = api.new_signed()
    api.add_document(signed.customer_id, name="7-12 Extract.pdf", doc_type="land_record")
    reading = api.add_document(signed.customer_id, name="deed.pdf", doc_type="land_record", status="clean")
    signed.client.get("/api/chat/ready-questions")
    made = len(api.answers.asked)
    assert made > 0
    for page in range(1, 4):  # Home looks again while deed.pdf is being read
        with db.customer(signed.customer_id) as conn:
            conn.execute(
                text("UPDATE documents SET progress = CAST(:p AS jsonb) WHERE id = :d"),
                {"p": f'{{"step": "reading", "done": {page}, "total": 5}}', "d": reading},
            )
        signed.client.get("/api/chat/ready-questions")
    assert len(api.answers.asked) == made
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE documents SET status = 'ready', progress = NULL WHERE id = :d"), {"d": reading})
    signed.client.get("/api/chat/ready-questions")
    assert len(api.answers.asked) > made  # read now: the answers are made again
