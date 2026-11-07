"""POST /api/chat/stream (#535) end to end: the real AnswerService on PostgreSQL, the real
OpenRouterClient against the fake OpenRouter API streaming its reply in chunks.

What is pinned: the events in order; the sources before any sentence; a sentence verify drops
never sent; ``done`` the very reply POST /api/chat gives; the one unstreamed redraft on bad JSON;
the time cap; a client that goes away (no half answer saved, the model's stream closed); another
organisation's documents never in the sources; a fixed-calculation answer (stages, then done);
the heartbeat; and the timing line.
"""

import asyncio
import functools
import json
import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import BackgroundTasks

from kaagaz.ai.openrouter import ModelGate, OpenRouterClient
from kaagaz.answering.service import AnswerService
from kaagaz.api.auth import CurrentUser
from kaagaz.api.context import AppContext
from kaagaz.api.routers import chat as chat_router
from kaagaz.api.routers.chat import TOO_SLOW, AskBody, ask, stream_answer
from kaagaz.db.engine import Database
from tests.openrouter_fake import (
    KEY,
    OpenRouterFake,
    Recorded,
    Reply,
    embed_reply,
    json_reply,
    json_stream,
    running_fake,
    stream_reply,
)
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, NOW, Api, Signed
from tests.test_isolation_answering import HINGLISH, SEARCH, _seed

pytestmark = pytest.mark.db

GOOD = "The grand total of INV-1042 is Rs. 1,20,000."
UNBACKED = "The tax on INV-1042 is Rs. 99,999."  # no such amount in the evidence: verify drops it
SELLER = "The seller is Sharma Traders."
DRAFT = {
    "status": "answered",
    "sentences": [
        {"text": UNBACKED, "evidence": [1]},
        {"text": GOOD, "evidence": [1]},
        {"text": SELLER, "evidence": [1]},
    ],
}


@dataclass
class World:
    api: Api
    signed: Signed
    ids: dict[str, str]
    fake: OpenRouterFake

    def stream(self, question: str = SEARCH, signed: Signed | None = None) -> list[tuple[str, Any]]:
        reply = (signed or self.signed).client.post(
            "/api/chat/stream", json={"question": question}, headers=CSRF
        )
        assert reply.status_code == 200, reply.text
        assert reply.headers["content-type"].startswith("text/event-stream")
        assert reply.headers["cache-control"] == "no-cache" and reply.headers["x-accel-buffering"] == "no"
        return events(reply.text)

    def plain(self, question: str = SEARCH) -> dict[str, Any]:
        reply = self.signed.client.post("/api/chat", json={"question": question}, headers=CSRF)
        assert reply.status_code == 200, reply.text
        return dict(reply.json())

    def conversations(self) -> list[Any]:
        return list(self.signed.client.get("/api/conversations").json()["items"])


def events(text: str) -> list[tuple[str, Any]]:
    """The stream as (event, data) pairs, a heartbeat as ("ping", None); every block well formed."""
    assert text.endswith("\n\n")
    found: list[tuple[str, Any]] = []
    for block in text[:-2].split("\n\n"):
        if block == ": ping":
            found.append(("ping", None))
            continue
        name, data = block.split("\n")
        assert name.startswith("event: ") and data.startswith("data: ")
        found.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return found


def names(found: list[tuple[str, Any]]) -> list[str]:
    return [name if name != "stage" else f"stage:{data['stage']}" for name, data in found if name != "ping"]


def _embeddings(request: Recorded) -> Reply:
    if request.action == "embeddings":
        return embed_reply([[1.0] + [0.0] * 1023 for _ in request.body["input"]])
    return Reply(400, {"error": {"code": 400, "message": "unscripted"}})


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        server.responder = _embeddings
        yield server


def world(db: Database, fake: OpenRouterFake, **settings: Any) -> World:
    values = {
        "model_enabled": True,
        "openrouter_api_key": KEY,
        "openrouter_base_url": fake.url,
        "model_rpm": 10**9,
        "model_timeout_seconds": 10.0,
        **settings,
    }
    made = make_settings(**values)
    client = OpenRouterClient(made, gate=ModelGate(made, workers=1))
    answers = AnswerService(db=db, model=client, settings=made, clock=lambda: NOW)
    api = Api(db, settings=made, model=client, answers=answers)
    signed = api.new_signed()
    return World(api, signed, _seed(db, signed.customer_id), fake)


def _stopped() -> None:
    """Every streamed answer's thread has ended (a stopped one at its next check)."""
    ends = time.monotonic() + 10
    while any(thread.name == "answer-stream" for thread in threading.enumerate()):
        assert time.monotonic() < ends, "a streamed answer never stopped"
        time.sleep(0.02)


def test_the_events_come_in_order_sources_first_and_only_checked_sentences(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)
    fake.script("completions", json_stream(DRAFT, size=9))

    found = w.stream()

    assert names(found) == [
        "stage:understanding",
        "stage:searching",
        "sources",
        "stage:writing",
        "sentence",
        "sentence",
        "stage:checking",
        "done",
    ]
    sources = dict(found)["sources"]
    assert sources == {"documents": [{"id": w.ids["invoice"], "name": "bill.pdf"}], "passages": 1}
    sentences = [data for name, data in found if name == "sentence"]
    assert [(s["index"], s["text"]) for s in sentences] == [(0, GOOD), (1, SELLER)]  # UNBACKED never sent
    assert UNBACKED not in json.dumps(found)
    done = found[-1][1]["reply"]["answer"]["answer"]
    assert [s["text"] for s in done["sentences"]] == [GOOD, SELLER]
    # Each streamed sentence's citations are the answer's own, field for field but the spot on
    # the page, which the answer works out from every sentence citing it.
    for sentence, final in zip(sentences, done["sentences"], strict=True):
        assert [c["n"] for c in sentence["citations"]] == final["citations"] == [1]
        streamed, cited = dict(sentence["citations"][0]), dict(done["citations"][0])
        for spot in ("regions", "page_no", "hard_to_read"):
            streamed.pop(spot), cited.pop(spot)
        assert streamed == cited
    assert fake.bodies()[-1]["stream"] is True
    assert len(w.conversations()) == 1  # saved as POST /api/chat saves it


def test_done_is_exactly_the_reply_post_api_chat_gives(db: Database, fake: OpenRouterFake) -> None:
    w = world(db, fake)
    fake.script("completions", json_reply(DRAFT), json_stream(DRAFT))

    plain = w.plain()
    streamed = w.stream()[-1][1]["reply"]

    assert streamed.keys() == plain.keys() == {"conversation_id", "question", "answer"}
    for side in ("question", "answer"):
        assert streamed[side].keys() == plain[side].keys()
        same = {key: value for key, value in streamed[side].items() if key not in ("id", "created_at")}
        assert same == {key: value for key, value in plain[side].items() if key not in ("id", "created_at")}
    assert streamed["conversation_id"] != plain["conversation_id"]  # two questions, two conversations
    assert [body["stream"] for body in fake.bodies()] == [False, True]


def test_bad_json_is_drafted_once_more_unstreamed_then_its_checked_sentences_and_done(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)
    fake.script("completions", stream_reply(['{"status": "answered", "sentences": [{"te']), json_reply(DRAFT))

    found = w.stream()

    assert names(found) == [
        "stage:understanding",
        "stage:searching",
        "sources",
        "stage:writing",
        "stage:checking",
        "sentence",
        "sentence",
        "done",
    ]
    assert [body["stream"] for body in fake.bodies()] == [True, False]
    assert [data["text"] for name, data in found if name == "sentence"] == [GOOD, SELLER]
    assert [s["text"] for s in found[-1][1]["reply"]["answer"]["answer"]["sentences"]] == [GOOD, SELLER]


def test_past_the_time_cap_it_says_so_in_plain_words_and_saves_nothing(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake, answer_stream_max_seconds=1.0)
    slow = json_stream(DRAFT, size=2, every=0.1)
    fake.script("completions", slow)

    found = w.stream()

    assert found[-1] == ("error", {"code": "answer_timeout", "message": TOO_SLOW})
    assert "done" not in names(found)
    _stopped()
    assert slow.stream is not None and slow.sent < len(slow.stream)  # the model's stream was closed
    assert w.conversations() == []


def test_a_client_that_goes_away_stops_the_answer_and_nothing_half_done_is_saved(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)
    slow = json_stream(DRAFT, size=3, every=0.05)
    fake.script("completions", slow)
    context = w.api.app.state.kaagaz
    assert isinstance(context, AppContext)
    user = CurrentUser(
        user_id=w.signed.user_id,
        customer_id=w.signed.customer_id,
        email=w.signed.email,
        display_name="Test member",
        role="member",
        customer_name="Test customer",
        scope="app",
        session_version=1,
        must_change_password=False,
        totp_enabled=False,
        delete_forever_allowed=False,
    )

    async def leave_after_the_first_sentence() -> list[str]:
        work = functools.partial(ask, AskBody(question=SEARCH), user, background=BackgroundTasks())
        stream = stream_answer(work, context, cap=30.0)
        seen = []
        async for chunk in stream:
            seen.append(chunk)
            if chunk.startswith("event: sentence"):
                break
        await stream.aclose()  # what the server does when the browser goes away (or Stop)
        return seen

    seen = asyncio.run(leave_after_the_first_sentence())

    assert seen[-1].startswith("event: sentence")
    _stopped()
    assert slow.stream is not None and slow.sent < len(slow.stream)
    assert w.conversations() == []


def test_another_organisations_documents_never_appear_in_the_stream(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)
    other = w.api.new_signed()
    theirs = _seed(db, other.customer_id)
    fake.script("completions", json_stream(DRAFT), json_stream(DRAFT))

    mine, for_them = w.stream(), w.stream(signed=other)

    assert dict(mine)["sources"]["documents"] == [{"id": w.ids["invoice"], "name": "bill.pdf"}]
    assert dict(for_them)["sources"]["documents"] == [{"id": theirs["invoice"], "name": "bill.pdf"}]
    for found, foreign in ((mine, theirs), (for_them, w.ids)):
        dumped = json.dumps(found)
        assert not any(value in dumped for value in foreign.values())


def test_a_fixed_calculation_sends_its_stages_then_done_with_no_draft(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)

    found = w.stream(HINGLISH)

    assert names(found) == ["stage:understanding", "stage:searching", "done"]
    answer = found[-1][1]["reply"]["answer"]["answer"]
    assert (answer["status"], answer["route"]) == ("answered", "fixed_query")
    assert fake.bodies() == []  # no model call at all


def test_a_heartbeat_keeps_a_quiet_stream_open(
    db: Database, fake: OpenRouterFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world(db, fake)
    monkeypatch.setattr(chat_router, "HEARTBEAT_SECONDS", 0.05)
    fake.script("completions", json_stream(DRAFT, size=40, every=0.3))

    found = w.stream()

    assert ("ping", None) in found and found[-1][0] == "done"


def test_the_timing_line_says_streamed_and_when_the_first_sentence_went(
    db: Database, fake: OpenRouterFake, caplog: pytest.LogCaptureFixture
) -> None:
    w = world(db, fake)
    fake.script("completions", json_stream(DRAFT), json_reply(DRAFT))
    caplog.set_level(logging.INFO, logger="kaagaz.answering.timing")

    w.stream()
    w.plain()

    streamed, plain = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert " streamed=1 first_sentence_ms=" in streamed and "first_sentence_ms=0" not in streamed
    assert plain.endswith(" streamed=0 first_sentence_ms=0")


def test_an_error_keeps_the_code_and_words_post_api_chat_gives(db: Database, fake: OpenRouterFake) -> None:
    w = world(db, fake)
    found = w.stream("   ")
    assert found == [("error", {"code": "bad_request", "message": "Type a question first."})]
