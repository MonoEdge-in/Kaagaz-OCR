"""Chat against a real database, with a fake answer service.

Pure helpers (the answer text, and a saved answer's sources as they are now)
are tested first, without a database.
"""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.app import create_app
from kaagaz.api.routers.chat import NOT_FOUND_TEXT, answer_text
from kaagaz.api.views import GONE_SENTENCE, answer_with_source_states, citation_pages, cited_document_ids
from kaagaz.db import chat as chat_sql
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, NOW, PASSWORD, Api, TestClient, answer_json, statements

PAN = "ABCDE1234F"  # made up
REGION = {"kind": "box", "page": 1, "box": [0.1, 0.2, 0.3, 0.25]}
# The PageSummary of the page Api.add_page adds: what a source preview draws.
PAGE = {
    "page_no": 1,
    "width": 595.0,
    "height": 842.0,
    "unit": "pt",
    "has_image": True,
    "route": None,
    "effective_dpi": None,
    "low_resolution": False,
}


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


def test_cited_document_ids_are_the_citations_and_the_closest_documents() -> None:
    answer = _cited_answer("d1", "d2", closest=("d3",))
    assert cited_document_ids(answer) == ["d1", "d2", "d3"]
    assert cited_document_ids(None) == []
    assert cited_document_ids(answer_json(status="not_found")) == []


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
        assert (gone["snippet"], gone["regions"]) == (None, [])
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


def test_a_source_in_trash_or_deleted_loses_its_page_and_an_older_version_keeps_it() -> None:
    answer = _cited_answer("live", "older", "binned", "gone")
    answer["citations"] = [{**each, "page": PAGE} for each in answer["citations"]]
    states = {"live": "live", "older": "older_version", "binned": "in_trash"}  # "gone": deleted forever
    shown = answer_with_source_states(answer, states)
    assert [each["page"] for each in shown["citations"]] == [PAGE, PAGE, None, None]


# Stored page numbers the pages table cannot hold: a bool, beyond int4, below 1.
NOT_PAGE_NUMBERS = (True, 2**31, 0)


def test_only_a_page_number_the_pages_table_can_hold_is_looked_up() -> None:
    """Sending one of NOT_PAGE_NUMBERS would fail the whole query (council review W2)."""
    answer = _cited_answer(*["d1"] * 4)
    for each, page_no in zip(answer["citations"], (1, *NOT_PAGE_NUMBERS), strict=True):
        each["page_no"] = page_no
    assert citation_pages(answer) == [("d1", 1)]


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
    }
    assert answer["status"] == "not_found"
    assert answer["label"] is None
    assert answer["model_used"] is False
    assert body["answer"]["text"] == NOT_FOUND_TEXT


@pytest.mark.db
def test_a_saved_answer_shows_a_source_moved_to_trash_as_gone(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    binned = api.add_document(signed.customer_id, name="bill 1.pdf", uploaded_by=signed.user_id)
    live = api.add_document(signed.customer_id, name="bill 2.pdf", uploaded_by=signed.user_id)
    for document in (binned, live):
        api.add_page(signed.customer_id, document)
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
    assert [each.get("page") for each in shown["citations"]] == [None, PAGE]
    with db.customer(signed.customer_id) as conn:  # the page lookup itself leaves Trash out
        found = chat_sql.cited_pages(conn, signed.customer_id, [(binned, 1), (live, 1)])
    assert set(found) == {(live, 1)}
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
    assert [each.get("page") for each in again["citations"]] == [PAGE, PAGE]
    assert {key: value for key, value in again.items() if key != "citations"} == {
        key: value for key, value in api.answers.reply.items() if key != "citations"
    }


@pytest.mark.db
def test_each_citation_carries_its_page_when_asked_and_reopened_and_it_is_never_stored(
    db: Database,
) -> None:
    """The preview's page (PageSummary) is looked up each time the answer is shown, so the
    screen never fetches a whole document for it. An older version keeps its page."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    older = api.add_document(c, name="bill 1.pdf", is_current=False)
    api.add_document(c, family_id=older, version_no=2)
    live = api.add_document(c, name="bill 2.pdf")
    for document in (older, live):
        api.add_page(c, document)
    api.answers.reply = _cited_answer(older, live)

    body = signed.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF).json()
    opened = signed.client.get(f"/api/conversations/{body['conversation_id']}").json()["messages"][1]

    assert [each.get("page") for each in body["answer"]["answer"]["citations"]] == [PAGE, PAGE]
    assert [(each["source_state"], each.get("page")) for each in opened["answer"]["citations"]] == [
        ("older_version", PAGE),
        ("live", PAGE),
    ]
    with db.customer(c) as conn:
        stored: dict[str, Any] = conn.execute(
            text("SELECT answer FROM messages WHERE role = 'assistant'")
        ).scalar_one()
    assert stored == api.answers.reply  # no page in the stored answer


@pytest.mark.db
def test_a_stored_page_number_that_is_not_a_page_gets_no_page_and_the_conversation_still_opens(
    db: Database,
) -> None:
    """True (which Python counts as 1), 2**31 and 0 beside a real page 1 of the same document."""
    api = Api(db)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)
    api.add_page(signed.customer_id, document)
    api.answers.reply = _cited_answer(*[document] * 4)
    for each, page_no in zip(api.answers.reply["citations"], (1, *NOT_PAGE_NUMBERS), strict=True):
        each["page_no"] = page_no

    asked = signed.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF)
    assert asked.status_code == 200
    opened = signed.client.get(f"/api/conversations/{asked.json()['conversation_id']}")
    assert opened.status_code == 200
    for answer in (asked.json()["answer"]["answer"], opened.json()["messages"][1]["answer"]):
        assert [each["page"] for each in answer["citations"]] == [PAGE, None, None, None]


@pytest.mark.db
def test_a_live_citation_of_a_page_its_document_does_not_have_gets_no_page(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)
    api.add_page(signed.customer_id, document)
    api.answers.reply = _cited_answer(document)
    api.answers.reply["citations"][0]["page_no"] = 2

    body = signed.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF).json()
    opened = signed.client.get(f"/api/conversations/{body['conversation_id']}").json()["messages"][1]

    assert body["answer"]["answer"]["citations"][0]["page"] is None
    (cited,) = opened["answer"]["citations"]
    assert (cited["source_state"], cited["page"]) == ("live", None)


@pytest.mark.db
def test_the_pages_cost_the_same_statements_for_1_or_18_citations(db: Database) -> None:
    """One batched page lookup, on asking and on reopening, however many sources there are."""
    api = Api(db)
    signed = api.new_signed()
    documents = [api.add_document(signed.customer_id, name=f"bill {n}.pdf") for n in range(1, 19)]
    for document in documents:
        api.add_page(signed.customer_id, document)

    def counts(cited: list[str]) -> tuple[int, int]:
        api.answers.reply = _cited_answer(*cited)
        replies: list[Any] = []
        asked = statements(
            db,
            lambda: replies.append(
                signed.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF).json()
            ),
        )
        path = f"/api/conversations/{replies[0]['conversation_id']}"
        opened = statements(db, lambda: replies.append(signed.client.get(path).json()["messages"][1]))
        for answer in (replies[0]["answer"]["answer"], replies[1]["answer"]):
            assert [each.get("page") for each in answer["citations"]] == [PAGE] * len(cited)
        return asked, opened

    assert counts(documents[:1]) == counts(documents)
