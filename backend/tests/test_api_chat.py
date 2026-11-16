"""Chat against a real database, with a fake answer service.

Pure helpers (the answer text, and a saved answer's sources as they are now)
are tested first, without a database.
"""

import inspect
import json
import re
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import ready
from kaagaz.answering.unchecked import unchecked_sentence
from kaagaz.answering.voice import SOFT_CLOSEST
from kaagaz.api.app import create_app
from kaagaz.api.auth import CurrentUser, current_user
from kaagaz.api.routers import chat as chat_router
from kaagaz.api.routers.chat import NOT_FOUND_TEXT, SHORT_TITLE, TOO_MANY_QUESTIONS, answer_text, short_title
from kaagaz.api.views import GONE_SENTENCE, answer_with_source_states, cited_document_ids
from kaagaz.db.engine import Database, make_engine
from kaagaz.search.scope import ScopeBody
from tests.settings_factory import make_settings
from tests.test_api_support import (
    CSRF,
    NOW,
    PASSWORD,
    Api,
    ApiFakeQueue,
    ApiFakeStorage,
    FakeAnswers,
    TestClient,
    answer_json,
)

PAN = "ABCDE1234F"  # made up
MONEY = re.compile(r"usd|price|budget|cost|spend|\$|₹", re.IGNORECASE)  # features 25-27: never to members
REGION = {"kind": "box", "page": 1, "box": [0.1, 0.2, 0.3, 0.25]}


def _citation(n: int, document_id: str) -> dict[str, Any]:
    """A saved citation, as ``Citation.to_json`` writes it."""
    return {
        "n": n,
        "kind": "piece",
        "ref_id": f"piece-{n}",
        "document_id": document_id,
        "document_name": f"bill {n}.pdf",
        "detected_type": "pdf",
        "page_no": 1,
        "regions": [REGION],
        "snippet": f"Total {n}00",
        "label": "verified",
        "label_regions": [REGION],  # PRD 3 H4
    }


def _cited_answer(*document_ids: str, closest: tuple[str, ...] = ()) -> dict[str, Any]:
    """An answer with one sentence per document, each citing only its own, and
    one more sentence citing all of them."""
    numbers = list(range(1, len(document_ids) + 1))
    return {
        **answer_json(),
        "sentences": [{"text": f"Sentence {n}.", "citations": [n]} for n in numbers]
        + [{"text": "All of them.", "citations": numbers}],
        "citations": [
            _citation(n, document_id) for n, document_id in zip(numbers, document_ids, strict=True)
        ],
        "closest_documents": [{"id": each, "display_name": f"{each}.pdf"} for each in closest],
    }


def test_answer_text_joins_sentences_or_says_not_found() -> None:
    assert answer_text(answer_json()) == "The total is 1,200."
    assert answer_text(answer_json(status="not_found")) == NOT_FOUND_TEXT
    assert answer_text({"status": "answered", "sentences": "junk"}) == NOT_FOUND_TEXT
    # PRD 6 decision 9: the screen's words, the closest documents' when there are any.
    assert (
        answer_text({**answer_json(status="not_found"), "closest_documents": [{"id": "d1"}]}) == SOFT_CLOSEST
    )


def test_ask_is_a_plain_def_so_the_answer_is_saved_after_the_asker_leaves() -> None:
    # Tushar, 16 Oct 2026: a question must be answered even when the page that asked is
    # gone. A plain def runs on a worker thread that a client going away never stops, so
    # both messages are still written; an async def could be cancelled at an await.
    assert not inspect.iscoroutinefunction(chat_router.ask)


def test_the_unchecked_answer_never_mentions_review() -> None:
    # Changed on purpose (Tushar, 16 Oct 2026: no Needs review in Ask). Still no number.
    sentence = unchecked_sentence("profit")
    assert sentence == "A profit figure is in these documents. Open them to see it."
    assert not re.search(r"review|check|won't|couldn't|sorry|\d", sentence, re.IGNORECASE)


def test_cited_document_ids_are_the_citations_and_the_closest_documents() -> None:
    answer = _cited_answer("d1", "d2", closest=("d3",))
    assert cited_document_ids(answer) == ["d1", "d2", "d3"]
    assert cited_document_ids(None) == []
    assert cited_document_ids(answer_json(status="not_found")) == []


def test_also_mentions_documents_are_checked_and_a_gone_one_leaves_the_list_and_the_count() -> None:
    row = {"page_no": 1, "pages": [1], "snippet": "Jalna"}
    mentions = {
        "terms": ["Jalna"],
        "total": 9,
        "documents": [{**row, "document_id": d, "document_name": f"{d}.pdf"} for d in ("m1", "m2", "m3")],
    }
    answer = {**_cited_answer("d1"), "mentions": mentions}
    assert cited_document_ids(answer) == ["d1", "m1", "m2", "m3"]
    shown = answer_with_source_states(answer, {"d1": "live", "m1": "live", "m2": "in_trash"})
    assert [d["document_id"] for d in shown["mentions"]["documents"]] == ["m1"]
    assert shown["mentions"]["total"] == 7
    assert answer["mentions"] is mentions and len(mentions["documents"]) == 3  # stored answer unchanged
    assert "mentions" not in answer_with_source_states(_cited_answer("d1"), {"d1": "live"})


def test_a_saved_answer_shows_each_source_as_it_is_now() -> None:
    answer = _cited_answer("live", "older", "binned", "gone", closest=("live", "binned", "gone"))
    states = {"live": "live", "older": "older_version", "binned": "in_trash"}  # "gone": deleted forever

    shown = answer_with_source_states(answer, states)

    assert [(c["document_id"], c["source_state"]) for c in shown["citations"]] == [
        ("live", "live"),
        ("older", "older_version"),
        ("binned", "in_trash"),
        ("gone", "deleted"),
    ]
    # A live or older citation keeps its words and its place on the page.
    for kept in shown["citations"][:2]:
        assert (kept["snippet"], kept["regions"]) == (f"Total {kept['n']}00", [REGION])
    # Trash means deleted: neither its words nor its place show any more.
    for gone in shown["citations"][2:]:
        assert (gone["snippet"], gone["regions"], gone["label_regions"]) == (None, [], [])
        assert gone["document_name"] == f"bill {gone['n']}.pdf"
    assert [sentence["text"] for sentence in shown["sentences"]] == [
        "Sentence 1.",
        "Sentence 2.",
        GONE_SENTENCE,
        GONE_SENTENCE,
        "All of them.",  # two of its four sources are still there
    ]
    assert shown["closest_documents"] == [{"id": "live", "display_name": "live.pdf"}]
    # The stored answer is never changed, so a restore brings everything back.
    assert answer == _cited_answer("live", "older", "binned", "gone", closest=("live", "binned", "gone"))


def test_a_sentence_whose_every_source_is_gone_is_replaced_and_one_without_sources_is_kept() -> None:
    answer = {
        **_cited_answer("binned", "gone"),
        "sentences": [
            {"text": "From both.", "citations": [1, 2]},
            {"text": "No source.", "citations": []},
        ],
    }
    shown = answer_with_source_states(answer, {"binned": "in_trash"})
    assert [sentence["text"] for sentence in shown["sentences"]] == [GONE_SENTENCE, "No source."]
    assert {key: shown[key] for key in ("status", "label", "route")} == {
        key: answer[key] for key in ("status", "label", "route")
    }


def _offline_chat_client(user_id: str, **settings: object) -> TestClient:
    """An app whose database is never reached, signed in as ``user_id``."""
    app = create_app(
        make_settings(**settings),
        db=Database(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app"),
        storage=ApiFakeStorage(),
        queue=ApiFakeQueue(),
        answers=FakeAnswers(),
        clock=lambda: NOW,
    )
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        user_id=user_id,
        customer_id=str(uuid.uuid4()),
        email="a@b.c",
        display_name="A",
        role="member",
        customer_name="C",
        scope="app",
        session_version=1,
        must_change_password=False,
        totp_enabled=False,
        delete_forever_allowed=False,
    )
    return TestClient(app)


def test_a_user_asking_too_fast_is_429_with_a_plain_message_and_others_are_not() -> None:
    """Security checklist (15 Oct 2026): CHAT_QUESTIONS_PER_MINUTE questions a user a
    minute, checked first. An empty question is 400 before the database, so the
    first two use the limit up without one."""
    fast = _offline_chat_client(str(uuid.uuid4()), chat_questions_per_minute=2)
    for _ in range(2):
        assert fast.post("/api/chat", json={"question": " "}, headers=CSRF).status_code == 400

    refused = fast.post("/api/chat", json={"question": " "}, headers=CSRF)

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
    assert refused.json()["error"] == {"code": "rate_limited", "message": TOO_MANY_QUESTIONS}
    other = _offline_chat_client(str(uuid.uuid4()), chat_questions_per_minute=2)
    assert other.post("/api/chat", json={"question": " "}, headers=CSRF).status_code == 400


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
    # Changed on purpose (features 25-27, 15 Oct 2026): the audit row names who was asked to
    # draft the answer and at what level (S5c); with no chat model set anywhere, MODEL_CHAT at low.
    assert details == {
        "conversation_id": body["conversation_id"],
        "question_id": body["question"]["id"],
        "status": "answered",
        "agents": api.settings.model_chat,
        "thinking": "low",
    }


@pytest.mark.db
def test_a_not_found_while_the_model_is_down_says_so_and_is_never_busy(db: Database) -> None:
    """KZ-006: an outage is never shown as "not found". Changed on purpose (Tushar, 24 Oct
    2026: never "Kaagaz is busy"): it is no longer a 503; the reply says the AI is not
    responding (``outage``), keeps whatever closest documents there are, and is stored."""
    api = Api(db)
    signed = api.new_signed()
    api.answers.reply, api.answers.unavailable = answer_json(status="not_found"), True

    reply = signed.client.post("/api/chat", json={"question": "total?"}, headers=CSRF)

    assert reply.status_code == 200, reply.text
    body = reply.json()["answer"]
    assert body["answer"]["outage"] is True
    assert body["text"] == chat_router.OUTAGE_LINE
    assert "not" + " found" not in body["text"].casefold()


@pytest.mark.db
def test_an_outage_from_a_timeout_says_the_question_took_too_long(db: Database) -> None:
    """#535 (test-server log, 7 Nov 2026): "ask again in a minute" times out the same way, so a
    timeout says what to do instead; every other outage keeps OUTAGE_LINE."""
    api = Api(db)
    signed = api.new_signed()
    api.answers.unavailable = True
    api.answers.reply = {**answer_json(status="not_found"), "outage_reason": "timeout"}
    timed_out = signed.client.post("/api/chat", json={"question": "total?"}, headers=CSRF).json()["answer"]
    assert timed_out["text"] == chat_router.TIMEOUT_LINE
    assert timed_out["answer"]["outage"] is True and timed_out["answer"]["outage_reason"] == "timeout"
    # Changed on purpose (B13, 7 Nov 2026): neutral words; the screen adds "Swift answers faster."
    # only when the question was asked with Precision, which it knows and the server does not.
    assert (
        chat_router.TIMEOUT_LINE
        == "This question took too long for the AI. Ask about fewer documents, then try again."
    )
    api.answers.reply = answer_json(status="not_found")
    down = signed.client.post("/api/chat", json={"question": "sum?"}, headers=CSRF).json()["answer"]
    assert down["text"] == chat_router.OUTAGE_LINE and "outage_reason" not in down["answer"]


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
def test_openings_greet_by_name_once_and_never_repeat_over_ten_answers(db: Database) -> None:
    """PRD 6 A: an answer service that replies the same every time still gets a new opening
    each time, the first name only on the first; the name never in a message's text (the
    history a follow-up's rewrite reads). An answer from before v2 gets none."""
    api = Api(db)
    signed = api.new_signed()  # "Test member"; NOW is 15:30 in India
    api.answers.reply = {**answer_json(), "version": 2, "opening": None, "documents": [], "assumptions": []}
    conversation: dict[str, str] = {}
    openings = []
    for turn in range(10):
        body = signed.client.post(
            "/api/chat", json={"question": f"total {turn}?", **conversation}, headers=CSRF
        ).json()
        conversation = {"conversation_id": body["conversation_id"]}
        openings.append(body["answer"]["answer"]["opening"])

    assert openings[0] == "Good afternoon, Test. Here's what I found."
    said = [line for line in openings if line]
    assert len(said) == len(set(said)) > 1 and all("Test" not in line for line in said[1:])
    saved = signed.client.get(f"/api/conversations/{conversation['conversation_id']}").json()["messages"]
    assert [m["answer"]["opening"] for m in saved if m["role"] == "assistant"] == openings
    assert not any("Test" in m["text"] for m in saved)
    api.answers.reply = answer_json()
    old = signed.client.post("/api/chat", json={"question": "total?"}, headers=CSRF).json()
    assert "opening" not in old["answer"]["answer"]


@pytest.mark.db
def test_a_clarifying_question_never_spends_the_greeting(db: Database) -> None:
    """A clarify shows no opening, so the first real answer after it still greets by name."""
    api = Api(db)
    signed = api.new_signed()  # "Test member"; NOW is 15:30 in India
    v2 = {**answer_json(), "version": 2, "opening": None, "documents": [], "assumptions": []}
    clarify = {"question": "Did you mean Pune?", "choices": []}
    api.answers.reply = {**v2, "status": "clarify", "clarify": clarify}
    asked = signed.client.post("/api/chat", json={"question": "land in Puny?"}, headers=CSRF).json()
    assert asked["answer"]["answer"].get("opening") is None
    api.answers.reply = v2
    conversation = {"conversation_id": asked["conversation_id"]}
    answered = signed.client.post("/api/chat", json={"question": "Pune", **conversation}, headers=CSRF).json()
    assert answered["answer"]["answer"]["opening"] == "Good afternoon, Test. Here's what I found."


def test_a_new_conversations_title_is_its_first_question_made_short() -> None:
    assert short_title("  What is the   total\non bill 12?  ") == "What is the total on bill 12?"
    long = "Which supplier sent the most invoices in September and what was the total amount of them all"
    title = short_title(long)
    assert len(title) <= SHORT_TITLE and title.endswith("…") and long.startswith(title[:-1])
    assert not title[:-1].endswith(" ")
    assert short_title("q" * 1000) == "q" * (SHORT_TITLE - 1) + "…"  # one long word: cut inside it


# Changed on purpose (UI audit, 16 Oct 2026): A1, the title is the first question made short.
@pytest.mark.db
def test_title_is_the_question_made_short(db: Database) -> None:
    signed = Api(db).new_signed()
    body = signed.client.post("/api/chat", json={"question": "q" * 1000}, headers=CSRF).json()
    summary = signed.client.get("/api/conversations").json()["items"][0]
    assert summary["id"] == body["conversation_id"]
    assert summary["title"] == short_title("q" * 1000)


@pytest.mark.db
def test_the_owner_renames_and_deletes_a_conversation_and_each_is_audited(db: Database) -> None:
    signed = Api(db).new_signed()
    body = signed.client.post("/api/chat", json={"question": "mine"}, headers=CSRF).json()
    path = f"/api/conversations/{body['conversation_id']}"

    renamed = signed.client.patch(path, json={"title": "  GST bills\x07 "}, headers=CSRF)
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "GST bills"  # trimmed, control characters gone
    assert signed.client.get(path).json()["title"] == "GST bills"
    assert signed.client.patch(path, json={"title": "   "}, headers=CSRF).status_code == 400
    assert signed.client.patch(path, json={"title": "x" * 201}, headers=CSRF).status_code == 400

    assert signed.client.delete(path, headers=CSRF).status_code == 204
    assert signed.client.get(path).status_code == 404
    assert signed.client.get("/api/conversations").json()["items"] == []
    assert signed.client.delete(path, headers=CSRF).status_code == 404
    with db.customer(signed.customer_id) as conn:
        left = conn.execute(
            text("SELECT count(*) FROM messages WHERE conversation_id = :id"), {"id": body["conversation_id"]}
        ).scalar_one()
        actions = conn.execute(
            text("SELECT action FROM audit_log WHERE target_id = :id ORDER BY created_at"),
            {"id": body["conversation_id"]},
        ).scalars()
        assert left == 0
        assert list(actions) == ["chat.rename", "chat.delete"]


@pytest.mark.db
# Changed on purpose (#644): a longer question is answered, cut keeping its questions; past MAX_TYPED, 400.
@pytest.mark.parametrize("question", ["", "   ", "x" * 20_001])
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
    path = f"/api/conversations/{body['conversation_id']}"
    assert colleague.client.patch(path, json={"title": "theirs now"}, headers=CSRF).status_code == 404
    assert colleague.client.delete(path, headers=CSRF).status_code == 404
    assert signed.client.get(path).json()["title"] == "mine"
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
    assert PAN in rows[0][1]


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
        "rewritten_question",
        "understood_words",
        "reading_note",  # PRD 5 F6: null, no page here is flagged
        "unreadable_pages",
        "unreadable_page_count",
        "related_documents",  # "Also in" (16 Oct 2026)
        "version",  # PRD 6 A, contract v2: the first answer greets "Test member" by name
        "opening",
        "documents",
        "assumptions",
        # Changed on purpose (24 Oct 2026): nothing close, so questions this library answers
        # (none here: the library is empty).
        "suggestions",
    }
    assert answer["suggestions"] == []
    assert (answer["version"], answer["opening"], answer["documents"]) == (2, "Good afternoon, Test.", [])
    assert answer["status"] == "not_found"
    assert body["answer"]["text"] == NOT_FOUND_TEXT
    assert (answer["reading_note"], answer["unreadable_pages"], answer["unreadable_page_count"]) == (
        None,
        [],
        0,
    )
    assert answer["label"] is None
    assert answer["model_used"] is False
    assert body["answer"]["text"] == NOT_FOUND_TEXT


@pytest.mark.db
def test_a_saved_answer_shows_a_source_moved_to_trash_as_gone(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    binned = api.add_document(signed.customer_id, name="bill 1.pdf", uploaded_by=signed.user_id)
    live = api.add_document(signed.customer_id, name="bill 2.pdf", uploaded_by=signed.user_id)
    api.answers.reply = _cited_answer(binned, live, closest=(binned, live))
    body = signed.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF).json()
    assert signed.client.delete(f"/api/documents/{binned}", headers=CSRF).status_code == 204

    messages = signed.client.get(f"/api/conversations/{body['conversation_id']}").json()["messages"]

    shown = messages[1]["answer"]
    assert [(c["document_id"], c["source_state"]) for c in shown["citations"]] == [
        (binned, "in_trash"),
        (live, "live"),
    ]
    assert (shown["citations"][0]["snippet"], shown["citations"][0]["regions"]) == (None, [])
    assert (shown["citations"][1]["snippet"], shown["citations"][1]["regions"]) == ("Total 200", [REGION])
    assert [sentence["text"] for sentence in shown["sentences"]] == [
        GONE_SENTENCE,
        "Sentence 2.",
        "All of them.",
    ]
    assert [each["id"] for each in shown["closest_documents"]] == [live]
    # Restored, the answer is whole again: the stored answer was never changed.
    assert signed.client.post(f"/api/documents/{binned}/restore", headers=CSRF).status_code == 200
    again = signed.client.get(f"/api/conversations/{body['conversation_id']}").json()["messages"][1]["answer"]
    assert [c["source_state"] for c in again["citations"]] == ["live", "live"]
    assert {key: value for key, value in again.items() if key != "citations"} == {
        key: value for key, value in api.answers.reply.items() if key != "citations"
    }


@pytest.mark.db
def test_a_scope_is_resolved_saved_with_the_question_and_inherited_by_a_follow_up(db: Database) -> None:
    """PRD 3 H2; inheriting is PROVISIONAL (plan decision 9). Sending {} widens it again."""
    api = Api(db)
    signed = api.new_signed()
    folder = api.add_folder(signed.customer_id, signed.user_id, "Suppliers")

    def ask(**body: Any) -> Any:
        reply = signed.client.post("/api/chat", json={"question": "total?", **body}, headers=CSRF)
        assert reply.status_code == 200, reply.text
        return reply.json()

    first = ask(scope={"folder_id": folder})
    conversation = first["conversation_id"]
    ask(conversation_id=conversation)
    ask(conversation_id=conversation, scope={})
    ask(conversation_id=conversation)

    assert [scope.folder_ids for scope in api.answers.scopes] == [(folder,), (folder,), None, None]
    messages = signed.client.get(f"/api/conversations/{conversation}").json()["messages"]
    saved = ScopeBody.model_validate({"folder_id": folder}).model_dump(mode="json")
    everything = ScopeBody().model_dump()
    assert [m["scope"] for m in messages if m["role"] == "user"] == [saved, saved, everything, everything]
    assert first["question"]["scope"] == saved and first["answer"]["scope"] is None


@pytest.mark.db
def test_a_scope_too_big_to_save_is_400_before_asking(db: Database) -> None:
    api = Api(db)
    many = [str(uuid.uuid4()) for _ in range(50)]
    scope = {"tag_ids": many, "document_ids": many, "doc_types": ["invoice", "receipt", "contract"]}
    reply = api.new_signed().client.post("/api/chat", json={"question": "?", "scope": scope}, headers=CSRF)
    assert reply.status_code == 400
    assert api.answers.asked == []


# --- answer settings (features 25-27, council 15 Oct 2026, C10) ------------------------------


@pytest.mark.db
def test_chat_options_list_only_what_the_organisation_allows_and_no_money(db: Database) -> None:
    signed = Api(db).new_signed()
    reply = signed.client.get("/api/chat/options")
    assert reply.status_code == 200
    # No chat model set anywhere: nothing to pick, the one level, no Ensemble (as before).
    assert reply.json() == {
        "agents": [],
        "levels": ["low"],
        "default_level": "low",
        "ensemble": False,
        "max_agents": 3,
    }
    assert MONEY.search(json.dumps(reply.json())) is None


@pytest.mark.db
@pytest.mark.parametrize(
    ("choice", "status", "code"),
    [
        ({"agents": ["google/not-allowed-1"]}, 403, "model_not_allowed"),
        ({"thinking": "high"}, 403, "thinking_not_allowed"),
        ({"agents": ["google/a-1", "google/a-1"]}, 400, "bad_request"),
        ({"agents": ["google/a-1", "google/b-1", "google/c-1", "google/d-1"]}, 400, "bad_request"),
    ],
)
def test_ask_refuses_a_choice_it_may_not_make_before_anything_is_stored(
    db: Database, choice: dict[str, Any], status: int, code: str
) -> None:
    api = Api(db)
    signed = api.new_signed()
    reply = signed.client.post("/api/chat", json={"question": "total?", **choice}, headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["code"]) == (status, code)
    assert MONEY.search(reply.text) is None and "google/" not in reply.text  # plain words, no model id
    assert api.answers.asked == []
    with db.customer(signed.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM messages")).scalar_one() == 0


@pytest.mark.db
def test_ask_sends_the_checked_choice_to_the_answer(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    chosen = {"agents": [api.settings.model_chat], "thinking": "low"}
    reply = signed.client.post("/api/chat", json={"question": "total?", **chosen}, headers=CSRF)
    assert reply.status_code == 200
    [choice] = api.answers.choices
    assert choice is not None
    assert ([agent.model for agent in choice.agents], choice.thinking) == ([api.settings.model_chat], "low")


@pytest.mark.db
def test_ask_naming_several_models_answers_with_the_first_allowed_one_only(db: Database) -> None:
    # Changed on purpose (Tushar, 16 Oct 2026, one model in Ask): an older screen sending
    # several is answered by one, never refused for naming more.
    api = Api(db)
    signed = api.new_signed()
    chosen = {"agents": ["google/not-allowed-1", api.settings.model_chat], "thinking": "low"}
    reply = signed.client.post("/api/chat", json={"question": "total?", **chosen}, headers=CSRF)
    assert reply.status_code == 200, reply.text
    [choice] = api.answers.choices
    assert choice is not None and [agent.model for agent in choice.agents] == [api.settings.model_chat]


def test_first_allowed_keeps_one_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # Changed on purpose (Tushar, 16 Oct 2026, one model in Ask).
    usable = [{"id": "google/b-1"}, {"id": "google/c-1"}]
    monkeypatch.setattr(chat_router, "usable_chat_models", lambda *_args, **_kw: usable)
    context: Any = type("Context", (), {"settings": None})()
    conn: Any = None

    def first(agents: list[str] | None) -> list[str] | None:
        return chat_router._first_allowed(conn, "c", agents, context)

    assert first(None) is None  # the organisation's default
    assert first(["google/a-1"]) == ["google/a-1"]  # one: resolve_chat decides
    assert first(["google/a-1", "google/c-1", "google/b-1"]) == ["google/c-1"]
    assert first(["google/x-1", "google/y-1"]) == ["google/x-1"]  # refused as before


def test_first_allowed_with_no_chat_model_set_keeps_model_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """#239: with no chat model set, MODEL_CHAT is the one allowed (as ``resolve_chat``), so an
    older screen naming it after another is answered by it, not refused."""
    monkeypatch.setattr(chat_router, "usable_chat_models", lambda *_args, **_kw: [])
    settings = make_settings()
    context: Any = type("Context", (), {"settings": settings})()
    conn: Any = None
    agents = ["google/x-1", settings.model_chat]
    assert chat_router._first_allowed(conn, "c", agents, context) == [settings.model_chat]
    assert chat_router._first_allowed(conn, "c", ["google/x-1", "google/y-1"], context) == ["google/x-1"]


@pytest.mark.db
def test_a_pinned_source_shows_on_top_after_a_reload_and_every_pick_is_kept(db: Database) -> None:
    # Ranked sources (Tushar, 16 Oct 2026, 0036): "Most relevant" puts one source on top.
    api = Api(db)
    signed = api.new_signed()
    first = api.add_document(signed.customer_id, name="bill 1.pdf", uploaded_by=signed.user_id)
    second = api.add_document(signed.customer_id, name="bill 2.pdf", uploaded_by=signed.user_id)
    api.answers.reply = _cited_answer(first, second)
    body = signed.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF).json()
    path = f"/api/messages/{body['answer']['id']}/pin"
    conversation = f"/api/conversations/{body['conversation_id']}"
    assert [m["pin"] for m in signed.client.get(conversation).json()["messages"]] == [None, None]

    picks = [(second, 2, None, 2), (first, 1, "p1", 2)]
    for document_id, page_no, ref_id, previous_rank in picks:
        pick = {
            "document_id": document_id,
            "page_no": page_no,
            "ref_id": ref_id,
            "previous_rank": previous_rank,
        }
        assert signed.client.post(path, json=pick, headers=CSRF).status_code == 204

    messages = signed.client.get(conversation).json()["messages"]
    assert messages[1]["pin"] == {"document_id": first, "page_no": 1, "ref_id": "p1"}  # the newest pick
    assert messages[1]["answer"]["sentences"] == api.answers.reply["sentences"]  # the text never changes
    with db.customer(signed.customer_id) as conn:
        rows = conn.execute(
            text(
                "SELECT document_id::text, page_no, source_ref, previous_rank FROM source_pins"
                " ORDER BY created_at"
            )
        ).all()
    assert [tuple(row) for row in rows] == picks


@pytest.mark.db
@pytest.mark.parametrize(
    "pick",
    [
        {"page_no": 1, "previous_rank": 1},
        {"document_id": "not-a-uuid", "page_no": 1, "previous_rank": 1},
        {"document_id": str(uuid.uuid4()), "page_no": 0, "previous_rank": 1},
        {"document_id": str(uuid.uuid4()), "page_no": 1, "previous_rank": 0},
        {"document_id": str(uuid.uuid4()), "page_no": 1, "previous_rank": 1, "extra": True},
        {"document_id": str(uuid.uuid4()), "page_no": 1, "previous_rank": 1, "ref_id": ""},
    ],
)
def test_a_bad_pick_is_400(db: Database, pick: dict[str, Any]) -> None:
    api = Api(db)
    signed = api.new_signed()
    body = signed.client.post("/api/chat", json={"question": "q"}, headers=CSRF).json()
    reply = signed.client.post(f"/api/messages/{body['answer']['id']}/pin", json=pick, headers=CSRF)
    assert reply.status_code == 400


@pytest.mark.db
def test_a_pick_on_a_question_a_colleagues_answer_or_an_unknown_document_is_404(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    colleague = api.sign_in(signed.customer_id, "admin")
    document = api.add_document(signed.customer_id, name="bill.pdf", uploaded_by=signed.user_id)
    body = signed.client.post("/api/chat", json={"question": "mine"}, headers=CSRF).json()
    pick = {"document_id": document, "page_no": 1, "previous_rank": 1}

    def pin(client: TestClient, message_id: str, chosen: dict[str, Any]) -> int:
        return client.post(f"/api/messages/{message_id}/pin", json=chosen, headers=CSRF).status_code

    assert pin(colleague.client, body["answer"]["id"], pick) == 404
    assert pin(signed.client, body["question"]["id"], pick) == 404
    assert pin(signed.client, body["answer"]["id"], {**pick, "document_id": str(uuid.uuid4())}) == 404
    with db.customer(signed.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM source_pins")).scalar_one() == 0


@pytest.mark.db
def test_624_home_suggestions_come_with_a_first_answer_only(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#624: "Try one of these" (Home's questions) follows a first answer that found nothing close,
    never a follow-up in a conversation, where they do not fit."""
    monkeypatch.setattr(ready, "questions", lambda *_: [("What is the rent?", "d1")])
    api = Api(db)
    signed = api.new_signed()
    api.answers.reply = answer_json(status="not_found")

    first = signed.client.post("/api/chat", json={"question": "Measurement?"}, headers=CSRF).json()
    follow = {"question": "And the other document?", "conversation_id": first["conversation_id"]}
    second = signed.client.post("/api/chat", json=follow, headers=CSRF).json()

    assert first["answer"]["answer"]["suggestions"] == ["What is the rent?"]
    assert "suggestions" not in second["answer"]["answer"]
