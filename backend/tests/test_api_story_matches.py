"""Story matches through the API: beside GET /api/search's documents, GET
/api/me/story-matches for the chat, and the recorder: with the real answer service and
a model that records every call (then fails, so the answer falls back), a story-only
word never reaches a prompt, a later embed, the answer, the messages or a log line.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from kaagaz.ai.client import ModelOutputError
from kaagaz.answering.service import AnswerService
from kaagaz.api.routers import search
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from tests.settings_factory import make_settings
from tests.test_api_my_story import put
from tests.test_api_support import CSRF, Api, Signed
from tests.test_indexing_db import FakeModel
from tests.test_search_db import add_pieces

pytestmark = pytest.mark.db

MARK = "Zorbanuk"  # in the story only, never in a question
STORY = f"Papa ne 2018 me jo Daregaon wali jamin ka 7/12 diya tha, bank loan ke liye, {MARK} ke ghar"
QUESTION = "daregaon jamin papa loan"
UNKNOWN = "7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"


def matches(signed: Signed, q: str, **query: Any) -> Any:
    reply = signed.client.get("/api/me/story-matches", params={"q": q, **query})
    assert reply.status_code == 200, reply.text
    return reply.json()["story_matches"]


def test_search_lists_story_matches_beside_the_documents_with_a_band_only(db: Database) -> None:
    model = FakeModel()
    api = Api(db, model=model)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id, name="7-12 extract.pdf")
    assert put(signed, document, STORY).status_code == 201

    reply = signed.client.get("/api/search", params={"q": QUESTION})
    body = reply.json()
    assert body["documents"] == []  # the document has no pieces: only the story found it
    assert body["story_matches"] == [
        {"document_id": document, "display_name": "7-12 extract.pdf", "doc_type": None, "band": "match"}
    ]
    assert MARK not in reply.text and "score" not in json.dumps(body["story_matches"])


@pytest.mark.parametrize("error", [OperationalError("SELECT", {}, OSError()), BackendUnavailable("database")])
def test_a_story_failure_never_fails_the_search(
    db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    api = Api(db, model=FakeModel())
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)
    add_pieces(db, signed.customer_id, document, [("Daregaon 7/12 extract", None)])

    def fails(*_: object, **__: object) -> None:
        raise error

    monkeypatch.setattr(search, "find_story_matches", fails)
    body = signed.client.get("/api/search", params={"q": "Daregaon"}).json()

    assert ([d["id"] for d in body["documents"]], body["story_matches"]) == ([document], [])
    (record,) = [r for r in caplog.records if r.getMessage() == "story matches left out"]
    assert record.__dict__["code"] == type(error).__name__ and "Daregaon" not in str(record.__dict__)


def test_the_chat_route_finds_my_story_with_no_model_call_and_checks_its_input(db: Database) -> None:
    model = FakeModel()
    api = Api(db, model=model)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id)
    put(signed, document, STORY)
    calls = len(model.calls)  # the save embedded the story

    assert matches(signed, QUESTION) == [
        {"document_id": document, "display_name": "invoice.pdf", "doc_type": None, "band": "match"}
    ]
    assert matches(signed, QUESTION, document_ids=[api.add_document(signed.customer_id)]) == []
    assert [m["band"] for m in matches(signed, "Daregaon")] == ["maybe"]
    assert matches(signed, QUESTION, folder_id=UNKNOWN) == []
    assert matches(signed, "   ") == []
    assert len(model.calls) == calls  # meaning is OFF: the question is never embedded
    assert signed.client.get("/api/me/story-matches", params={"q": "x" * 1001}).status_code == 400
    assert signed.client.get("/api/me/story-matches", params={"q": QUESTION, "other": "1"}).status_code == 400
    assert api.client().get("/api/me/story-matches", params={"q": QUESTION}).status_code == 401


class Recorder(FakeModel):
    """Records every text the model is given; every chat fails, so the answer falls back."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        model: str | None = None,
        thinking: str | None = None,
        provider: str | None = None,
        deadline: float | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        self.prompts.extend((system, user))
        raise ModelOutputError("answer_shape")


def test_a_story_never_reaches_a_prompt_an_answer_a_message_or_a_log(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    model = Recorder()
    settings = make_settings(model_enabled=True)
    answers = AnswerService(
        db=db, model=model, settings=settings, clock=lambda: datetime(2026, 10, 3, tzinfo=UTC)
    )
    api = Api(db, settings=settings, model=model, answers=answers)
    signed = api.new_signed()
    document = api.add_document(signed.customer_id, name="7-12 extract.pdf")
    add_pieces(db, signed.customer_id, document, [("Daregaon 7/12 extract, bank loan entry", None)])
    put(signed, document, STORY)
    saved = len(model.calls)

    first = signed.client.post("/api/chat", json={"question": QUESTION}, headers=CSRF).json()
    beside = matches(signed, QUESTION)  # the chat screen's parallel call
    conversation = first["conversation_id"]
    follow = {"question": "aur bank loan wala?", "conversation_id": conversation}
    second = signed.client.post("/api/chat", json=follow, headers=CSRF).json()
    stored = signed.client.get(f"/api/conversations/{conversation}").text

    assert [m["document_id"] for m in beside] == [document]  # the control: the story is there
    assert len(model.prompts) >= 4  # the draft and the follow-up's rewrite (with its Turns) ran
    assert QUESTION in "".join(model.prompts)
    seen = [*model.prompts, *(t for call in model.calls[saved:] for t in call), json.dumps([first, second])]
    seen += [stored, *(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)]
    assert not [text for text in seen if MARK.lower() in text.lower()]  # story_latin is lower case
    assert "story_matches" not in json.dumps([first, second])
