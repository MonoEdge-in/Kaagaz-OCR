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
import itertools
import json
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import pytest
from sqlalchemy import text

from kaagaz.ai.openrouter import ModelGate, OpenRouterClient
from kaagaz.answering import service as service_module
from kaagaz.answering import stream as stream_module
from kaagaz.answering.service import AnswerService
from kaagaz.answering.stream import Events
from kaagaz.api.auth import CurrentUser
from kaagaz.api.context import AppContext
from kaagaz.api.routers import chat as chat_router
from kaagaz.api.routers.chat import TOO_SLOW, AskBody, ask, stream_answer
from kaagaz.db.engine import MAX_OVERFLOW, POOL_SIZE, Database
from tests.openrouter_fake import (
    KEY,
    OpenRouterFake,
    Recorded,
    Reply,
    embed_reply,
    json_reply,
    json_stream,
    running_fake,
    slow,
    stream_reply,
)
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, NOW, Api, FakeAnswers, Signed, TestClient
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

    def stream(
        self, question: str = SEARCH, signed: Signed | None = None, **body: Any
    ) -> list[tuple[str, Any]]:
        reply = (signed or self.signed).client.post(
            "/api/chat/stream", json={"question": question, **body}, headers=CSRF
        )
        assert reply.status_code == 200, reply.text
        assert reply.headers["content-type"].startswith("text/event-stream")
        assert reply.headers["cache-control"] == "no-cache" and reply.headers["x-accel-buffering"] == "no"
        return events(reply.text)

    def plain(self, question: str = SEARCH, signed: Signed | None = None, **body: Any) -> dict[str, Any]:
        reply = self.post(question, signed, **body)
        assert reply.status_code == 200, reply.text
        return dict(reply.json())

    def post(self, question: str = SEARCH, signed: Signed | None = None, **body: Any) -> Any:
        return (signed or self.signed).client.post(
            "/api/chat", json={"question": question, **body}, headers=CSRF
        )

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
    """Every streamed answer's work has ended (a stopped one at its next check)."""
    ends = time.monotonic() + 10
    while any(not done.done() for done in list(chat_router.RUNNING)):
        assert time.monotonic() < ends, "a streamed answer never stopped"
        time.sleep(0.02)


def test_the_events_come_in_order_sources_first_and_only_checked_sentences(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)
    fake.script("completions", json_stream(DRAFT, size=9))

    found = w.stream()

    assert names(found) == [
        "started",
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
    # One citation builder for both (review of #535): the last sentence sent has seen every
    # sentence the answer has, so its citations are the answer's, field for field.
    assert sentences[-1]["citations"] == done["citations"]
    assert fake.bodies()[-1]["stream"] is True
    assert len(w.conversations()) == 1  # saved as POST /api/chat saves it
    # started named where it landed: the conversation and the question, before the draft.
    started, reply = found[0][1], found[-1][1]["reply"]
    assert started == {"conversation_id": reply["conversation_id"], "question_id": reply["question"]["id"]}


def test_done_is_exactly_the_reply_post_api_chat_gives(db: Database, fake: OpenRouterFake) -> None:
    w = world(db, fake)
    fake.script("completions", json_reply(DRAFT), json_stream(DRAFT))

    plain = w.plain(signed=w.api.sign_in(w.signed.customer_id))  # its own asker: no repeat (PR 2)
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
        "started",
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


def test_the_cap_logs_its_code_writes_the_timing_line_and_bills_the_partial_draft(
    db: Database, fake: OpenRouterFake, caplog: pytest.LogCaptureFixture
) -> None:
    """Review of #535: a capped answer still writes its timing line (outcome=answer_timeout), a
    warning with the code, and a ledger row at the worst case for the draft it stopped."""
    w = world(db, fake, answer_stream_max_seconds=1.0)
    fake.script("completions", json_stream(DRAFT, size=2, every=0.1))
    caplog.set_level(logging.INFO)

    assert w.stream()[-1][0] == "error"
    _stopped()

    timing = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert len(timing) == 1 and " outcome=answer_timeout " in f"{timing[0]} "
    assert any(
        getattr(r, "code", None) == "answer_timeout" and r.levelno == logging.WARNING for r in caplog.records
    )
    with db.customer(w.signed.customer_id) as conn:
        drafted = conn.execute(
            text("SELECT count(*) FROM model_usage WHERE kind = 'chat' AND output_tokens > 0")
        ).scalar_one()
    assert drafted == 1  # never missing: the stopped draft is billed at its worst case


def test_a_client_that_goes_away_stops_the_events_but_the_answer_is_finished_and_saved(
    db: Database, fake: OpenRouterFake
) -> None:
    """Tushar, 16 Oct 2026: an answer is always finished and saved, even when the asker leaves (or
    presses Stop); it lands where ``started`` said."""
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
        stream = stream_answer(functools.partial(ask, AskBody(question=SEARCH), user), context, cap=30.0)
        seen = []
        async for chunk in stream:
            seen.append(chunk)
            if chunk.startswith("event: sentence"):
                break
        await stream.aclose()  # what the server does when the browser goes away (or Stop)
        return seen

    seen = asyncio.run(leave_after_the_first_sentence())

    assert seen[0].startswith("event: started") and seen[-1].startswith("event: sentence")
    started = json.loads(seen[0].split("data: ", 1)[1])
    _stopped()
    assert slow.stream is not None and slow.sent == len(slow.stream)  # the model's reply read to its end
    (saved,) = w.conversations()
    assert saved["id"] == started["conversation_id"]
    messages = w.signed.client.get(f"/api/conversations/{started['conversation_id']}").json()["messages"]
    assert [(m["role"], m["id"] if m["role"] == "user" else None) for m in messages] == [
        ("user", started["question_id"]),
        ("assistant", None),
    ]
    assert [s["text"] for s in messages[1]["answer"]["sentences"]] == [GOOD, SELLER]


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

    assert names(found) == ["started", "stage:understanding", "stage:searching", "done"]
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
    w.plain(signed=w.api.sign_in(w.signed.customer_id))  # another asker, so not a repeat

    streamed, plain = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert " streamed=1 first_sentence_ms=" in streamed and "first_sentence_ms=0" not in streamed
    assert plain.endswith(" streamed=0 first_sentence_ms=0")


def test_an_error_keeps_the_code_and_words_post_api_chat_gives(db: Database, fake: OpenRouterFake) -> None:
    w = world(db, fake)
    found = w.stream("   ")
    # started first (review of #535: before any work), then /api/chat's own error.
    assert names(found) == ["started", "error"]
    assert found[-1] == ("error", {"code": "bad_request", "message": "Type a question first."})


def test_a_repeated_question_on_the_stream_is_done_at_once_from_its_stored_answer(
    db: Database, fake: OpenRouterFake, caplog: pytest.LogCaptureFixture
) -> None:
    w = world(db, fake)
    fake.script("completions", json_stream(DRAFT))
    first = w.stream()
    caplog.set_level(logging.INFO, logger="kaagaz.answering.timing")

    again = w.stream("  what is the GRAND total of inv-1042? ")

    assert names(again) == ["started", "done"]  # no stage, no draft: the stored answer at once
    assert len(fake.bodies()) == 1  # no second model call
    assert again[-1][1]["reply"]["answer"]["answer"] == first[-1][1]["reply"]["answer"]["answer"]
    (line,) = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert " cached=1 " in line and line.endswith(" streamed=1")


def test_a_stream_without_usage_is_priced_on_the_ledger_and_marked_estimated(
    db: Database, fake: OpenRouterFake
) -> None:
    priced = "google/stream-ledger-test"  # its own catalogue row (prices per million tokens)
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO model_catalog (id, name, price_in_usd_per_m, price_out_usd_per_m)"
                " VALUES (:m, 'Stream ledger test', 2, 12) ON CONFLICT (id) DO NOTHING"
            ),
            {"m": priced},
        )
    w = world(db, fake, model_chat=priced)
    fake.script("completions", json_stream(DRAFT, with_usage=False))

    assert w.stream()[-1][0] == "done"

    with db.customer(w.signed.customer_id) as conn:
        rows = conn.execute(
            text(
                "SELECT input_tokens, output_tokens, cost_usd, cost_estimated FROM model_usage"
                " WHERE kind = 'chat'"
            )
        ).all()
    drafts = [row for row in rows if row.output_tokens > 0]  # the embedding's row has no output
    assert len(drafts) == 1
    (row,) = drafts
    assert row.input_tokens > 0 and row.cost_usd > 0 and row.cost_estimated  # never 0, never unmarked


def test_precision_reads_in_parts_then_a_slow_steady_draft_is_never_cut(
    db: Database, fake: OpenRouterFake, caplog: pytest.LogCaptureFixture
) -> None:
    """Test server, 7 Nov 2026: Precision (high thinking, read in parts) timed out at
    MODEL_TIMEOUT_SECONDS. On the stream the parts step runs (while "searching" shows), then the
    sources and "writing", and a draft that streams for longer than the model timeout in all,
    each gap shorter than it, finishes."""
    w = world(db, fake, model_timeout_seconds=0.5, answer_parts_tokens=1)
    facts = {"facts": [{"evidence": 1, "quote": "Grand Total: Rs. 1,20,000"}]}
    slow = json_stream(DRAFT, size=40, every=0.3)
    fake.script("completions", json_reply(facts), slow)
    caplog.set_level(logging.INFO, logger="kaagaz.answering.timing")

    with db.engine.begin() as conn:  # the organisation allows Precision (high thinking)
        conn.execute(
            text(
                "INSERT INTO customer_ai_settings (customer_id, chat_levels, chat_default_level)"
                " VALUES (:c, ARRAY['low', 'medium', 'high'], 'medium')"
            ),
            {"c": w.signed.customer_id},
        )

    found = w.stream(thinking="high")

    assert names(found) == [
        "started",
        "stage:understanding",
        "stage:searching",
        "sources",
        "stage:writing",
        "sentence",
        "sentence",
        "stage:checking",
        "done",
    ]
    parts, draft = fake.bodies()
    assert parts["stream"] is False and parts["reasoning"]["effort"] == "high"  # the part read, at Precision
    assert draft["stream"] is True
    assert slow.stream is not None and slow.sent == len(slow.stream)
    assert (len(slow.stream) - 1) * 0.3 > 0.5  # longer than MODEL_TIMEOUT_SECONDS in all
    (line,) = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert " parts=1 " in line and " streamed=1 " in line


def test_started_comes_before_any_database_work_with_the_clients_question_id(
    db: Database, fake: OpenRouterFake
) -> None:
    """Review of #535: the ids are named at the very start, before the rate limit, so even a
    refused question says where it would have landed first."""
    w = world(db, fake, chat_questions_per_minute=1)
    fake.script("completions", json_stream(DRAFT))
    mine = str(uuid.uuid4())

    first = w.stream(question_id=mine)
    refused = w.stream(question_id=str(uuid.uuid4()))

    assert first[0] == (
        "started",
        {"conversation_id": first[-1][1]["reply"]["conversation_id"], "question_id": mine},
    )
    assert first[-1][1]["reply"]["question"]["id"] == mine  # the client's id is the question's
    assert names(refused) == ["started", "error"] and refused[-1][1]["code"] == "rate_limited"


def test_a_question_id_already_saved_returns_that_answer_with_no_second_model_call(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)
    fake.script("completions", json_stream(DRAFT))
    mine = str(uuid.uuid4())
    streamed = w.stream(question_id=mine)[-1][1]["reply"]

    again = w.plain(question_id=mine)  # the client's fallback with the same id

    assert again == streamed
    assert len(fake.bodies()) == 1 and len(w.conversations()) == 1


def test_a_question_id_still_in_progress_is_waited_for_never_answered_twice(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)
    fake.script("completions", json_stream(DRAFT, size=4, every=0.05))
    mine = str(uuid.uuid4())
    streamed: list[Any] = []
    first = threading.Thread(target=lambda: streamed.append(w.stream(question_id=mine)))
    first.start()
    ends = time.monotonic() + 10
    while not fake.bodies():  # the stream's draft has started
        assert time.monotonic() < ends
        time.sleep(0.02)

    again = w.plain(question_id=mine)

    first.join()
    assert again == streamed[0][-1][1]["reply"]
    assert len(fake.bodies()) == 1  # one model call for both


def test_another_users_question_id_or_a_bad_one_is_refused_and_says_nothing(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)
    fake.script("completions", json_stream(DRAFT))
    mine = str(uuid.uuid4())
    w.stream(question_id=mine)
    other = w.api.sign_in(w.signed.customer_id)  # the same organisation, another person

    theirs = w.post(signed=other, question_id=mine)
    bad = w.post(question_id="not-a-uuid")

    assert (theirs.status_code, theirs.json()["error"]["code"]) == (409, "conflict")
    assert GOOD not in theirs.text and mine not in theirs.text
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "bad_request")
    assert len(fake.bodies()) == 1


def test_a_failing_background_task_is_its_own_log_and_the_broad_catch_names_the_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Review of #535: after the save the answer is done (``done`` goes out) whatever its memory
    task does; a failure of the answer itself is logged with where it would have landed."""
    caplog.set_level(logging.INFO)
    sent: list[tuple[str, Any]] = []
    ids = {"conversation_id": "c-1", "question_id": "q-1"}

    def saved(**kwargs: Any) -> dict[str, Any]:
        kwargs["background"].add_task(lambda: 1 / 0)
        return {"conversation_id": "c-1"}

    def broken(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    events = Events(lambda name, data: sent.append((name, data)))
    events.send("started", ids)  # as ask sends it first
    chat_router._work(saved, cast(AppContext, None), events, lambda n, d: sent.append((n, d)))
    chat_router._work(broken, cast(AppContext, None), events, lambda n, d: sent.append((n, d)))

    assert [name for name, _ in sent] == ["started", "done", "error"]
    background = [r for r in caplog.records if r.getMessage() == "background task failed"]
    failed = [r for r in caplog.records if r.getMessage() == "streamed answer failed"]
    assert len(background) == 1 and getattr(background[0], "code", None) == "ZeroDivisionError"
    assert len(failed) == 1
    assert (getattr(failed[0], "conversation_id", None), getattr(failed[0], "question_id", None)) == (
        "c-1",
        "q-1",
    )


def _precision(db: Database, fake: OpenRouterFake, monkeypatch: pytest.MonkeyPatch) -> World:
    """Precision reads in parts, each part's call with a deadline of half a second."""
    monkeypatch.setattr(service_module, "ENSEMBLE_SLACK_SECONDS", 0.0)
    w = world(db, fake, model_timeout_seconds=0.5, answer_parts_tokens=1)
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO customer_ai_settings (customer_id, chat_levels, chat_default_level)"
                " VALUES (:c, ARRAY['low', 'medium', 'high'], 'medium')"
            ),
            {"c": w.signed.customer_id},
        )
    return w


def test_after_the_parts_time_out_the_stream_still_drafts_from_all_the_evidence(
    db: Database, fake: OpenRouterFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agreed with the speed fixes: a streamed draft has the idle limit and the stream's cap, so
    after the parts time out it still drafts from all the evidence; /api/chat does not."""
    w = _precision(db, fake, monkeypatch)
    facts = {"facts": [{"evidence": 1, "quote": "Grand Total: Rs. 1,20,000"}]}
    fake.script("completions", slow(json_reply(facts), 2.0), json_stream(DRAFT))

    found = w.stream(thinking="high")

    assert [body["stream"] for body in fake.bodies()] == [False, True]
    assert [s["text"] for s in found[-1][1]["reply"]["answer"]["answer"]["sentences"]] == [GOOD, SELLER]


def test_after_the_parts_time_out_post_api_chat_makes_no_second_draft(
    db: Database, fake: OpenRouterFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _precision(db, fake, monkeypatch)
    facts = {"facts": [{"evidence": 1, "quote": "Grand Total: Rs. 1,20,000"}]}
    fake.script("completions", slow(json_reply(facts), 2.0))

    answer = w.plain(thinking="high")["answer"]["answer"]

    assert len(fake.bodies()) == 1  # the parts call only: no draft from all the evidence
    assert answer["model_used"] is False  # the lines found, quoted (the speed fixes' rule)


def test_a_stream_that_failed_and_was_quoted_is_degraded_never_reused_for_a_repeat(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)
    fake.script("completions", stream_reply(['{"status": "answered", "sen'], error=True))
    answers = w.api.app.state.kaagaz.answers
    token = stream_module.EVENTS.set(Events(lambda name, data: None))
    try:
        answer = answers.answer(w.signed.customer_id, SEARCH)
    finally:
        stream_module.EVENTS.reset(token)
    assert answer.model_used is False and answer.degraded is True  # quoted while the model failed


def test_a_repeat_on_the_stream_is_marked_as_a_copy_as_post_api_chat_marks_it(
    db: Database, fake: OpenRouterFake
) -> None:
    w = world(db, fake)
    fake.script("completions", json_stream(DRAFT))
    w.stream()
    w.stream("  what is the GRAND total of inv-1042? ")
    with db.customer(w.signed.customer_id) as conn:
        rows = (
            conn.execute(text("SELECT answer FROM messages WHERE role = 'assistant' ORDER BY created_at"))
            .scalars()
            .all()
        )
    first, copy = rows
    assert first.get("repeat_key") and "repeat_copy" not in first
    assert copy.get("repeat_copy") == first["repeat_key"] and "repeat_key" not in copy
    assert len(fake.bodies()) == 1


# --- answers being written at once (A3, A9: stability pass, 7 Nov 2026) ----------------------


class _Held(FakeAnswers):
    """Each answer waits for ``go``; ``started`` counts the answers being written."""

    def __init__(self) -> None:
        super().__init__()
        self.go = threading.Event()
        self.started = threading.Semaphore(0)

    def answer(self, *args: Any, **kwargs: Any) -> Any:
        self.started.release()
        assert self.go.wait(10)
        return super().answer(*args, **kwargs)


def _ask_in_thread(client: TestClient, replies: list[int]) -> threading.Thread:
    def post() -> None:
        replies.append(
            client.post("/api/chat", json={"question": "what is the total?"}, headers=CSRF).status_code
        )

    thread = threading.Thread(target=post)
    thread.start()
    return thread


def test_answers_at_once_fit_the_database_pool() -> None:
    """A3: each answer holds at most two pooled connections at a time; the streamed and posted
    answers at once never need the whole pool (db/engine.py), so the rest of the app always has one."""
    assert chat_router.ANSWERS_PER_USER == 2
    assert 2 * (chat_router.STREAM_WORKERS + chat_router.POSTED_ANSWERS) < POOL_SIZE + MAX_OVERFLOW
    assert chat_router._STREAMS._max_workers == chat_router.STREAM_WORKERS


@pytest.mark.db
def test_a_person_has_at_most_two_answers_being_written(db: Database) -> None:
    """A3: a third, posted or streamed, is 429 with plain words; once one is done, they may ask."""
    held = _Held()
    api = Api(db, answers=held)
    signed = api.new_signed()
    replies: list[int] = []
    threads = [_ask_in_thread(signed.client, replies) for _ in range(2)]
    assert held.started.acquire(timeout=10) and held.started.acquire(timeout=10)
    try:
        for path in ("/api/chat", "/api/chat/stream"):
            third = signed.client.post(path, json={"question": "and the seller?"}, headers=CSRF)
            assert third.status_code == 429, path
            assert third.json()["error"] == {"code": "rate_limited", "message": chat_router.TOO_MANY_RUNNING}
        other = api.new_signed()  # someone else asks as usual
        assert other.client.post("/api/chat/stream", json={"question": "hi"}, headers=CSRF).status_code == 200
    finally:
        held.go.set()
        for thread in threads:
            thread.join(10)
    assert replies == [200, 200]
    assert (
        signed.client.post("/api/chat", json={"question": "and the seller?"}, headers=CSRF).status_code == 200
    )


@pytest.mark.db
def test_posted_answers_past_the_process_cap_wait_their_turn(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A3: past POSTED_ANSWERS a question waits for a turn instead of failing."""
    monkeypatch.setattr(chat_router, "_POSTED", threading.BoundedSemaphore(1))
    held = _Held()
    api = Api(db, answers=held)
    replies: list[int] = []
    first = _ask_in_thread(api.new_signed().client, replies)
    assert held.started.acquire(timeout=10)
    second = _ask_in_thread(api.new_signed().client, replies)
    assert not held.started.acquire(timeout=0.5)  # waiting for the first
    held.go.set()
    first.join(10)
    second.join(10)
    assert replies == [200, 200]


def test_the_time_cap_never_blocks_the_stream_loop_while_an_answer_is_being_saved() -> None:
    """A9: past the cap, stopping the answer waits for a save under way; the wait is off the event
    loop, so every other stream on it goes on."""

    def slow_save(context: AppContext, background: Any) -> dict[str, Any]:
        stream_module.emit("started", {"conversation_id": "c", "question_id": "q"})
        with stream_module.saving():
            time.sleep(1.0)  # the save's transaction holds the events' lock
        return {"saved": True}

    async def run() -> tuple[list[str], float]:
        ticks: list[float] = []

        async def tick() -> None:
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        ticker = asyncio.create_task(tick())
        context = cast(AppContext, None)
        found = [chunk async for chunk in stream_answer(slow_save, context, cap=0.2)]
        ticker.cancel()
        ticks.append(time.monotonic())  # the stream ended: the last gap counts too
        return found, max(b - a for a, b in itertools.pairwise(ticks))

    found, longest_gap = asyncio.run(run())
    assert found[-1].startswith("event: done")  # saved: finished, and done follows
    assert longest_gap < 0.5
