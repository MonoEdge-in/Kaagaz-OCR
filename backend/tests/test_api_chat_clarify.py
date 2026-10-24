"""PRD 6 B1 in POST /api/chat with a fake answer service: a clarify is stored as its question,
its resume state stays on the server, and only the open clarify is answered, by its choices."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.routers.chat import CLARIFY_GONE, answer_text
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, answer_json

RESUME: dict[str, Any] = {"question": "Land in Puny?", "kind": "place", "as_typed": "Puny", "remaining": []}
OTHER = {"id": "other", "label": "Something else", "kind": "other"}


def _clarify(*documents: str) -> dict[str, Any]:
    """A stored clarify, as AnswerService.to_json writes it."""
    picks = [
        {"id": f"c{n}", "label": label, "kind": "pick", "document_ids": [document]}
        for n, (label, document) in enumerate(zip(("Pune", "Panvel"), documents, strict=False), 1)
    ]
    clarify = {"id": "x", "question": "Did you mean Pune?", "choices": [*picks, OTHER], "multi": False}
    resume = {**RESUME, "assumptions": []}
    return {**answer_json(status="clarify"), "clarify": {**clarify, "turn": 1}, "resume": resume}


def test_a_clarifys_text_is_its_question() -> None:
    assert answer_text(_clarify("d1")) == "Did you mean Pune?"


@pytest.mark.db
def test_only_the_open_clarify_is_answered_by_its_own_live_stored_choices(db: Database) -> None:
    api = Api(db)
    signed, other = api.new_signed(), api.new_signed()
    live = api.add_document(signed.customer_id, name="pune.pdf", uploaded_by=signed.user_id)
    binned = api.add_document(signed.customer_id, name="panvel.pdf", uploaded_by=signed.user_id)
    api.answers.reply = _clarify(live, binned)
    asked = signed.client.post("/api/chat", json={"question": "Land in Puny?"}, headers=CSRF).json()
    conversation, message = asked["conversation_id"], asked["answer"]["id"]
    assert (asked["answer"]["text"], "resume" in asked["answer"]["answer"]) == ("Did you mean Pune?", False)
    assert signed.client.delete(f"/api/documents/{binned}", headers=CSRF).status_code == 204
    shown = signed.client.get(f"/api/conversations/{conversation}").json()["messages"][1]["answer"]
    assert [c["label"] for c in shown["clarify"]["choices"]] == ["Pune", "Something else"]
    assert "resume" not in shown

    def reply(choices: list[str], to: str = message, asker: Any = signed, **body: Any) -> Any:
        body = {"conversation_id": conversation, "reply": {"message_id": to, "choice_ids": choices}, **body}
        return asker.client.post("/api/chat", json=body, headers=CSRF)

    refused = [reply(["c5"]), reply(["c1"], to=str(uuid.uuid4())), reply(["c1"], conversation_id=None)]
    assert [(r.status_code, r.json()["error"]["message"]) for r in refused] == [(409, CLARIFY_GONE)] * 3
    assert reply(["other"]).status_code == reply([]).status_code == 400  # "Something else" sends nothing
    assert reply(["c1"], asker=other).status_code == 404  # another company's user never reaches it
    assert api.answers.resumed == []
    api.answers.reply = answer_json()

    answered = reply(["c1"]).json()

    assert answered["question"]["text"] == "Pune"  # the stored label, never client text
    (pending,) = api.answers.resumed
    assert (pending.question, pending.picked, pending.turn) == ("Land in Puny?", ("Pune",), 1)
    assert [asked for _, asked in api.answers.asked] == ["Land in Puny?", "Pune"]
    assert reply(["c1"]).status_code == 409  # answered: no longer open


@pytest.mark.db
def test_a_did_you_mean_pick_is_suggested_for_review_and_typed_words_answer_too(db: Database) -> None:  # B2
    api = Api(db)
    signed = api.new_signed()
    clarify = _clarify(api.add_document(signed.customer_id, name="pune.pdf", uploaded_by=signed.user_id))
    api.answers.reply = {**clarify, "resume": {**clarify["resume"], "confirm": True}}
    first = signed.client.post("/api/chat", json={"question": "Land in Puny?"}, headers=CSRF).json()
    conversation = first["conversation_id"]

    def reply(message: str, choices: list[str], question: str = "") -> Any:
        body: dict[str, Any] = {"conversation_id": conversation, "question": question}
        body["reply"] = {"message_id": message, "choice_ids": choices}
        return signed.client.post("/api/chat", json=body, headers=CSRF).json()

    second = reply(first["answer"]["id"], ["c1"])  # the answer is a clarify again
    typed = reply(second["answer"]["id"], [], "Pimpri")

    assert (typed["question"]["text"], api.answers.resumed[-1].typed) == ("Pimpri", "Pimpri")
    with db.customer(signed.customer_id) as conn:
        rows = conn.execute(
            text("SELECT word, meaning, status, submitted_by FROM organization_word_suggestions")
        )
        suggested = [tuple(map(str, row)) for row in rows]
    assert suggested == [("Puny", "Pune", "pending", signed.user_id)]  # once: never for typed words
    assert api.audit_actions(signed.customer_id).count("organization_word.suggested") == 1
