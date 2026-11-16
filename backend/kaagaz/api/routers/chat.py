"""POST /api/chat, GET /api/chat/options, GET /api/conversations, GET, PATCH {title}
and DELETE /api/conversations/{id}, and POST /api/messages/{id}/feedback.

Useful and Not useful (PRD 6 F1): a rating is ``up`` (Useful) or ``down`` (Not useful);
Not useful may carry what went wrong (``reason_codes``, the boxes ticked) and up to 500
characters, and ``notice_shown`` says the form told the person their company admin can see
the answer, the question, the reason and their name. At most FEEDBACK_PER_MINUTE ratings a
minute per person (429 after). The typed reason is stored and shown to the company admin
only: never sent to a model, never in an audit row's details and never logged.

A new conversation's title is its first question made short (``short_title``, UI audit
A1, 16 Oct 2026: no model call); its owner may rename it or delete it with its messages
(audited ``chat.rename`` / ``chat.delete``, ids only).

Who drafts the answer (features 25-27, council 15 Oct 2026, C7, C10): the asker may name
the chat models (by id, at most MAX_AGENTS, more than one only with the organisation's
Ensemble switch) and the thinking level; ``resolve_chat`` checks them for this
organisation in the first transaction, before anything is stored (403 otherwise), and
the audit row names them. ``GET /api/chat/options`` lists what may be chosen, by name.

Why the order in ``ask``: one short transaction checks the conversation and
resolves the scope (PRD 3 H2), the one sent or else the last question's
(PROVISIONAL, plan decision 9), again for this customer now: a deleted folder or
another customer's ids match nothing. It also reads the last CHAT_HISTORY_TURNS
question and answer pairs for a follow-up's rewrite (H1). The answer is worked
out outside a transaction (it may wait on the model), then the conversation, both
messages (the question with its scope and rewrite) and the audit row are written
in one transaction.
The audit row holds only the ids, ``answer.status`` and the models asked and their level.
A fresh question asked again unchanged is answered from its stored answer in one more short
transaction (#535, answering/ready.py ``repeat_key``), and saved and audited as any answer.

The asker's own words (My words, A3) may help the answer, which then lists them in
``understood_words``; they are never in a message's text, so never in the history.

A chat that remembers (PRD 5 M3): the first transaction also reads the conversation's
summary and memory (its documents that are no longer live left out), which go to the
follow-up rewrite only. When this question is a multiple of CHAT_SUMMARY_EVERY questions
past the last summary (``summary_due``: after a failed one, the next try is that many
questions later), a background task writes a new one after the reply (``_remember``); it
gives up quietly when the model fails or another reply wrote it first, and logs only the
code of a database or model error. Neither is ever in a reply or a log.

Clarifying questions (PRD 6 B1): ``reply`` answers a clarify (stored as its question) only while
it is the conversation's newest message, by its stored choices (``clarify.reply_to``); else 409.

A conversation belongs to the user who started it; anyone else, in this
customer or another, gets "not found".

AI spend (model choice, council 11 Oct 2026, SP1): the model calls of an answer are
billed to the asker, kind ``chat``, with the conversation once it is known (a new one
is made only after the answer); the memory's to the same user, kind ``memory``
(``usage_scope``).
"""

import asyncio
import contextlib
import contextvars
import functools
import json
import logging
import math
import threading
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import Connection, RowMapping
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.ai.usage import usage_scope
from kaagaz.answering import ready, small_talk, stream
from kaagaz.answering.clarify import (
    MAX_CHOICES,
    Pending,
    answers_clarify,
    latin_key,
    picked_page,
    reply_to,
    without_resume,
)
from kaagaz.answering.evidence import looks_like_instructions
from kaagaz.answering.memory import MAX_DOCUMENTS, NO_MEMORY, VERSION, Memory, from_row, live
from kaagaz.answering.pins import Pin
from kaagaz.answering.rewrite import Turn
from kaagaz.answering.sanitize import clean_text
from kaagaz.answering.stream import Events, StreamCancelled
from kaagaz.answering.voice import SOFT_NOTHING, first_name, numbers, opening, soft_line, unsupported
from kaagaz.answering.words_text import WordTextError, check_meaning, check_word
from kaagaz.api.auth import FEATURE_OFF, CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import MESSAGES, ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.names import clean_name
from kaagaz.api.routers.health import running_commit
from kaagaz.api.views import (
    LOCKED_NOTE,
    NO_ACCESS,
    cited_document_ids,
    conversation_summary,
    message,
    no_access,
    shown_message,
)
from kaagaz.db import ai_settings, audit, organization_words
from kaagaz.db import chat as chat_sql
from kaagaz.db.models import (
    LEVELS,
    MAX_AGENTS,
    NO_MODEL,
    AiNotAllowed,
    ChatChoice,
    effective_settings,
    resolve_chat,
    strongest_chat,
    usable_chat_models,
)
from kaagaz.db.organization_words import Suggestion
from kaagaz.db.sqlstate import log_unavailable
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.search.scope import EVERYTHING, ScopeBody, resolve
from kaagaz.settings import MODEL_ID

router = APIRouter()
logger = logging.getLogger(__name__)
_timing_log = logging.getLogger("kaagaz.answering.timing")  # a repeat's line, as an answer's (#535)

Context = Annotated[AppContext, Depends(get_context)]

MAX_TITLE = 200
SHORT_TITLE = 60  # characters of the first question a new conversation's title keeps
NOT_FOUND_TEXT = SOFT_NOTHING  # PRD 6 decision 9: the screen's words (answering/voice.py)
MAX_SCOPE_BYTES = 4096  # the messages.scope CHECK (migration 0006)
TOO_MANY_QUESTIONS = "You have asked many questions in a short time. Please wait a minute and ask again."
FEEDBACK_PER_MINUTE = 20  # PRD 6 F1: ratings a person may send a minute (issue #244)
TOO_MANY_RATINGS = "You have sent a lot of feedback in a short time. Please wait a minute and try again."
feedback_limits = RateLimiter(FEEDBACK_PER_MINUTE)


@functools.cache
def chat_limits(per_minute: int) -> RateLimiter:
    """Questions a user may ask a minute (CHAT_QUESTIONS_PER_MINUTE, security checklist
    15 Oct 2026), beside the site-wide limit: one limiter a process for each value."""
    return RateLimiter(per_minute)


ModelId = Annotated[str, StringConstraints(max_length=257, pattern=f"^{MODEL_ID.pattern}$")]
ChoiceId = Annotated[str, StringConstraints(pattern=r"^(c[1-9]|all)$")]
CLARIFY_GONE = "That question is no longer open. Ask again."
SUGGESTED = "organization_word.suggested"


class ReplyBody(BaseModel):
    """PRD 6 B1: the picked choices' ids; the choices are the stored message's, never the client's.
    None picked (B2): ``question`` holds the words typed instead."""

    model_config = ConfigDict(extra="forbid")

    message_id: uuid.UUID
    choice_ids: list[ChoiceId] = Field(default_factory=list, max_length=MAX_CHOICES + 1)


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(default="", max_length=1000)  # left out with a reply
    reply: ReplyBody | None = None
    conversation_id: uuid.UUID | None = None
    scope: ScopeBody | None = None  # left out: the conversation's last scope, or everything
    # Features 25-27 (C10, S8): who drafts the answer, checked again for this customer
    # (resolve_chat); left out, the organisation's default. Ids only, never trusted.
    agents: list[ModelId] | None = Field(default=None, min_length=1, max_length=MAX_AGENTS)
    thinking: Literal["low", "medium", "high"] | None = None
    # Streaming Ask (#535): the question's message id, chosen by the client and sent again with
    # its fallback, so one question is never answered or saved twice (``ask``).
    question_id: uuid.UUID | None = None

    @field_validator("agents")
    @classmethod
    def _distinct(cls, agents: list[str] | None) -> list[str] | None:
        if agents is not None and len(set(agents)) != len(agents):
            raise ValueError("each model once")
        return agents


# The 403s of resolve_chat (C10), in plain words: no model id or level is named.
NOT_ALLOWED = {
    "model_not_allowed": "That way of answering is not available. Choose another in Answer settings.",
    "ensemble_not_allowed": "Your organisation does not allow more than one answer model at once.",
    "thinking_not_allowed": "That thinking level is not available here. Choose another in Answer settings.",
}


class TitleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)  # then clean_name, as a folder's


def short_title(question: str) -> str:
    """The first question as a short title: one line, at most SHORT_TITLE characters, cut
    at a word with "…" when longer. O(len(question))."""
    line = " ".join(question.split())
    if len(line) <= SHORT_TITLE:
        return line
    cut = line[: SHORT_TITLE - 1]
    at = cut.rfind(" ")
    return (cut[:at] if at > SHORT_TITLE // 2 else cut).rstrip(" ,.;:") + "…"


class PinBody(BaseModel):
    """A source put on top of an answer (ranked sources, Tushar, 16 Oct 2026): where it is,
    and where it was in the list before (1 is the top)."""

    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    page_no: int = Field(ge=1, le=100000)
    # The cited source's ref_id, so one of two sources on one page; None for Also in.
    ref_id: str | None = Field(default=None, min_length=1, max_length=100)
    previous_rank: int = Field(ge=1, le=1000)


ReasonCode = Literal["wrong_documents", "document_missing", "wrong_figure", "did_not_understand", "other"]


class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["up", "down"]
    reason: str | None = Field(default=None, max_length=500)
    reason_codes: list[ReasonCode] = Field(default_factory=list, max_length=len(chat_sql.REASONS))
    notice_shown: bool = False

    @model_validator(mode="after")
    def _reasons_on_down(self) -> "FeedbackBody":
        if self.rating == "up" and (self.reason or self.reason_codes):
            raise ValueError("reasons go with Not useful only")
        return self


# KZ-006 (24 Oct 2026): every model was down, so this is no answer and no "not found" either.
OUTAGE_LINE = "The AI is not responding right now, so this is not a written answer. Ask again in a minute."
# #535 (test-server log, 7 Nov 2026): a draft that took too long times out the same way a minute
# later, so its outage (``outage_reason`` "timeout") says what to do instead.
TIMEOUT_LINE = "This question took too long for the AI. Ask about fewer documents, then try again."


def answer_text(answer: dict[str, Any]) -> str:
    """The assistant message's plain text: its sentences joined, a clarify's question, or the
    not-found line (decision 9: the closest documents' when it lists any; #410: naming the words
    no document has). Never the opening, so the asker's name is never in the history a rewrite
    reads."""
    clarify = answer.get("clarify") if answer.get("status") == "clarify" else None
    if isinstance(clarify, dict) and isinstance(clarify.get("question"), str):
        return str(clarify["question"])
    sentences = answer.get("sentences")
    texts = []
    if answer.get("status") in ("answered", "unchecked") and isinstance(sentences, list):
        texts = [s["text"] for s in sentences if isinstance(s, dict) and isinstance(s.get("text"), str)]
    if answer.get("outage"):
        return TIMEOUT_LINE if answer.get("outage_reason") == "timeout" else OUTAGE_LINE
    missing = answer.get("missing_words")
    words = [word for word in missing if isinstance(word, str)] if isinstance(missing, list) else []
    return " ".join(texts) or soft_line(bool(answer.get("closest_documents")), words)


# Streaming Ask (#535): the question ids being answered in this process now (``ask``).
_IN_PROGRESS: dict[str, threading.Event] = {}
_IN_PROGRESS_LOCK = threading.Lock()
ANSWERING_ALREADY = "That question is still being answered. Open this conversation again in a minute."

# Answers being written at once (A3, stability pass, 7 Nov 2026). One answer holds at most two
# pooled connections at a time, each only for a short transaction (its own, and step 19's early
# search beside the plan call), and otherwise waits on the model. The pool is 15 a process
# (db/engine.py: POOL_SIZE 10 + MAX_OVERFLOW 5), and a request waits at most POOL_TIMEOUT_SECONDS
# (10) for one. So STREAM_WORKERS (5) streamed and POSTED_ANSWERS (2) posted answers at once hold
# at most 2 x 7 = 14, and every other request always finds one. Past them an answer waits its turn
# instead of failing, for up to ANSWER_STREAM_MAX_SECONDS (180), and its own time cap starts only
# once it runs: a stream in the stream pool's queue (pings keep it open), a POST for a free slot
# (waiting off the threadpool every route shares). At 10 to 45 seconds an answer, the last of 20
# people asking at once starts within about two minutes.
# One person has at most ANSWERS_PER_USER answers being written, streamed and posted together.
ANSWERS_PER_USER = 2
POSTED_ANSWERS = 2
TOO_MANY_RUNNING = "You already have answers being written. Wait for one to finish."
_POSTED = threading.BoundedSemaphore(POSTED_ANSWERS)
_ANSWERING: dict[str, int] = {}  # user id: answers being written (under _IN_PROGRESS_LOCK)


def _take_turn(user_id: str) -> bool:
    """One of this person's ANSWERS_PER_USER answers at once; False when they are all in use."""
    with _IN_PROGRESS_LOCK:
        if _ANSWERING.get(user_id, 0) >= ANSWERS_PER_USER:
            return False
        _ANSWERING[user_id] = _ANSWERING.get(user_id, 0) + 1
        return True


def _end_turn(user_id: str) -> None:
    with _IN_PROGRESS_LOCK:
        left = _ANSWERING.pop(user_id, 1) - 1
        if left > 0:
            _ANSWERING[user_id] = left


def _new_answer(body: "AskBody", user: CurrentUser) -> str | None:
    """Whose turn this question takes: the asker's, unless it is one still being answered here (a
    client's fallback, which ``ask`` waits for). 429 while the asker has no turn left."""
    with _IN_PROGRESS_LOCK:
        if body.question_id is not None and str(body.question_id) in _IN_PROGRESS:
            return None
        if _ANSWERING.get(user.user_id, 0) >= ANSWERS_PER_USER:
            raise ApiError(429, "rate_limited", TOO_MANY_RUNNING, headers={"Retry-After": "10"})
    return user.user_id


POSTED_POLL_SECONDS = 0.25


@router.post("/chat")
async def ask_posted(
    body: AskBody, user: User, context: Context, background: BackgroundTasks
) -> dict[str, Any]:
    """``ask`` (on the threadpool) within the asker's turns and the process's POSTED_ANSWERS (A3).
    The wait for a slot is here, on the event loop, so waiting answers hold no pool thread; one
    that finds none in ANSWER_STREAM_MAX_SECONDS is 503, logged ``posted_answers_full``."""
    turn = _new_answer(body, user)
    if turn is None:
        return await run_in_threadpool(ask, body, user, context, background)
    if not _take_turn(turn):
        raise ApiError(429, "rate_limited", TOO_MANY_RUNNING, headers={"Retry-After": "10"})
    try:
        gives_up = time.monotonic() + context.settings.answer_stream_max_seconds
        while not _POSTED.acquire(blocking=False):
            if time.monotonic() >= gives_up:
                logger.warning("no slot for a posted answer", extra={"code": "posted_answers_full"})
                raise ApiError(503, "backend_unavailable")
            await asyncio.sleep(POSTED_POLL_SECONDS)
        try:
            return await run_in_threadpool(ask, body, user, context, background)
        finally:
            _POSTED.release()
    finally:
        _end_turn(turn)


def ask(
    body: AskBody,
    user: CurrentUser,
    context: AppContext,
    background: BackgroundTasks,
    *,
    channel: str | None = None,
) -> dict[str, Any]:
    """The answer's ids come first (streaming Ask, #535): the question's (the client's
    ``question_id``, else a new one) and its conversation's (the one asked in, else a new one),
    sent as ``started`` on the stream before any database work. A ``question_id`` this person
    already asked returns that saved answer, waiting while this process still works on it: never
    a second model call (a client's fallback after a broken stream). One of anyone else's is 409,
    as an id that never was would be once saved (row level security hides another
    organisation's). Then ``_ask``. ``channel`` (#603): ``whatsapp`` for a question asked on
    WhatsApp, run as the person by the channel process (kaagaz/channel/chat.py): a conversation it
    starts is marked so (history's "From WhatsApp") and its audit row says ``via``; None, the browser."""
    question_id = str(body.question_id) if body.question_id else str(uuid.uuid4())
    landing = str(body.conversation_id) if body.conversation_id else str(uuid.uuid4())
    stream.emit("started", {"conversation_id": landing, "question_id": question_id})
    mine: threading.Event | None = None
    for _ in range(2):  # wait once for a run of the same question here, then answer it ourselves
        with _IN_PROGRESS_LOCK:
            running = _IN_PROGRESS.get(question_id)
            if running is None:
                mine = _IN_PROGRESS[question_id] = threading.Event()
        if running is None:
            break
        running.wait(context.settings.answer_stream_max_seconds + 30)
        found = _saved_reply(context, user, question_id)
        if found is not None:
            return _with_note(context, user, found)
    if mine is None:
        raise ApiError(409, "conflict", ANSWERING_ALREADY)
    try:
        found = None if body.question_id is None else _saved_reply(context, user, question_id)
        reply = found or _ask(body, user, context, background, landing, question_id, channel)
    finally:
        with _IN_PROGRESS_LOCK:
            _IN_PROGRESS.pop(question_id, None)
        mine.set()
    return _with_note(context, user, reply)


def _with_note(context: AppContext, user: CurrentUser, reply: dict[str, Any]) -> dict[str, Any]:
    """PRD 7 (#605, p2:79): the reply with Ask's fixed line, ``locked_note``, for a person who may
    open a locked file (None for anyone else). Added each time it is shown, never saved."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        opens = chat_sql.opens_locked(conn, user.customer_id, user.user_id)
    return {**reply, "locked_note": LOCKED_NOTE if opens else None}


def _saved_reply(context: AppContext, user: CurrentUser, question_id: str) -> dict[str, Any] | None:
    """#535: the reply ``ask`` gave for ``question_id``, as it was saved, its sources checked as
    a conversation's are (PRD 7: replaced whole once one is hidden); None when it was not; 409
    when it is not this person's question (nothing about it is said)."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        found = chat_sql.saved_turn(conn, user.customer_id, question_id)
        if found is None:
            return None
        owner, question, answer = found
        if owner != user.user_id or question["role"] != "user" or answer is None:
            raise ApiError(409, "conflict")
        named = cited_document_ids(answer["answer"]) + _scope_ids(question)
        states = chat_sql.document_states(conn, user.customer_id, named)
    return {
        "conversation_id": str(question["conversation_id"]),
        "question": message(question),
        "answer": shown_message(answer, states, scope=_scope_ids(question)),
    }


def _ask(
    body: AskBody,
    user: CurrentUser,
    context: AppContext,
    background: BackgroundTasks,
    landing: str,
    question_id: str,
    channel: str | None = None,
) -> dict[str, Any]:
    """``ask``'s work: the answer to ``body``, saved as ``question_id`` in the conversation asked
    in, else a new one named ``landing`` (on ``channel``)."""
    if not chat_limits(context.settings.chat_questions_per_minute).allow(user.user_id):
        raise ApiError(429, "rate_limited", TOO_MANY_QUESTIONS, headers={"Retry-After": "60"})
    question = body.question.strip()
    answering = body.reply
    if not question and (answering is None or not answering.choice_ids):
        raise ApiError(400, "bad_request", "Type a question first.")
    if answering is not None and not answering.choice_ids and not answers_clarify(question):
        answering = None  # B2: a long or question-like text is a new question; the clarify is left
    if answering is not None and body.conversation_id is None:
        raise ApiError(409, "conflict", CLARIFY_GONE)
    conversation_id = None if body.conversation_id is None else str(body.conversation_id)
    chosen = body.scope
    recent: Sequence[RowMapping] = []
    memory, summary_turn, turns = NO_MEMORY, 0, 0
    last_cited: list[str] = []
    names: dict[str, str] = {}  # #621: the documents earlier answers cited that are still live
    earlier: list[str | None] = []  # the conversation's openings so far (PRD 6 A)
    pending: Pending | None = None
    forget = False  # PRD 7 (#602): the memory tracks a document the asker can no longer see
    with context.db.user(user.customer_id, user.user_id) as conn:
        if conversation_id is not None:
            state = chat_sql.conversation_memory(conn, user.customer_id, conversation_id, user.user_id)
            if state is None:
                raise ApiError(404, "not_found")
            summary_turn, turns = int(state["summary_turn"]), int(state["turns"])
            pairs = max(context.settings.chat_history_turns, 1)
            recent, remembered = _shown_turns(conn, user.customer_id, conversation_id, 2 * pairs, state)
            forget = remembered is None
            memory = NO_MEMORY if remembered is None else remembered
            # #629: the last answer's documents the asker can still see (current, out of Trash).
            names = chat_sql.live_document_names(conn, user.customer_id, _cited(recent))  # #621
            last_cited = [document for document in _cited(recent[:1]) if document in names]
            earlier = chat_sql.openings(conn, user.customer_id, conversation_id)
            if chosen is None:
                chosen = _saved_scope(recent)
            if answering is not None:
                pending = _pending(recent, answering, question)
                question = ", ".join(pending.labels or pending.picked) or question  # #629: as shown
        scope = (
            EVERYTHING
            if chosen is None
            else resolve(conn, user.customer_id, chosen, timezone=context.settings.timezone)
        )
        pins, switched, choice = _inputs(conn, user, context, conversation_id, body.agents, body.thinking)
        # Home's questions (answering/ready.py): a fresh question about everything may have its
        # answer ready, made with these same inputs from these same documents.
        fresh = conversation_id is None and answering is None and chosen is None
        # "hi", "thanks", "what can you do" (answering/small_talk.py): answered here, no search.
        talk = small_talk.kind(question) if answering is None else None
        examples = [text for text, _ in ready.questions(conn, user.customer_id, user.user_id)] if talk else []
        # #535: a fresh question (no conversation, no clarify), in any scope, may be a repeat.
        repeatable = conversation_id is None and answering is None and talk is None
        made_from = ready.stamp(conn, user.customer_id) if repeatable else ""
        tagged = ready.tagged(conn, user.customer_id, scope.tag_ids) if repeatable else []
        ready_key = (
            ready.key(user.customer_id, user.user_id, question, (pins, switched, choice), made_from)
            if fresh and talk is None
            else None
        )
    repeat_key, repeated, started = None, None, time.monotonic()
    kept = None if ready_key is None else ready.READY.get(ready_key)
    if repeatable:
        # #535 (answering/ready.py): a fresh question asked again by this person, with these same
        # inputs, today, on this code, while nothing it reads changed: its stored answer, at once.
        today = context.clock().astimezone(ZoneInfo(context.settings.timezone)).date()
        shaped = ready.shaping(context.settings)
        inputs = (pins, switched, choice, scope, tagged, today, running_commit(), shaped)
        with context.db.user(user.customer_id, user.user_id) as conn:  # the asker's own words too
            repeat_key = ready.repeat_key(conn, user.customer_id, user.user_id, question, inputs, made_from)
            repeated = chat_sql.repeated_answer(conn, user.customer_id, user.user_id, repeat_key)
            # PRD 7 (#602): a kept answer naming a document the asker can't see now is not served.
            ready_json = None if kept is None else kept.to_json()
            named = cited_document_ids(ready_json) + cited_document_ids(repeated)
            states = chat_sql.document_states(conn, user.customer_id, named)
            kept = None if no_access(ready_json, states) else kept
            repeated = None if no_access(repeated, states) else repeated
    saved = None if chosen is None else chosen.model_dump(mode="json")
    # json.dumps writes ", " and ": " as jsonb's text does, so this is the CHECK's length.
    if saved is not None and len(json.dumps(saved).encode()) > MAX_SCOPE_BYTES:
        raise ApiError(400, "bad_request", "Choose fewer documents or tags.")

    history = _history(recent, names)
    with usage_scope(
        context.db,
        customer_id=user.customer_id,
        user_id=user.user_id,
        kind="chat",
        conversation_id=conversation_id,
        as_person=True,
    ) as spend:
        degraded = copied = False  # #535: the model or a vector was down; served from the cache
        if talk is not None:
            answer = small_talk.answer(talk, question, first_name(user.display_name), examples)
        else:
            reply = (
                kept
                or _stored(repeated, started)
                or context.answers.answer(
                    user.customer_id,
                    question,
                    scope=scope,
                    history=history,
                    user_id=user.user_id,
                    memory=memory,
                    choice=choice,
                    pins=pins,
                    pending=pending,
                    clarify=switched["nlq_clarify"],
                    cited=_cited(recent),
                    last_cited=last_cited,
                )
            )
            # KZ-006: an outage is never "not found": the reply says the AI is not responding
            # (``outage``, OUTAGE_LINE) and lists the closest documents; never a "busy" error.
            answer, degraded, copied = reply.to_json(), reply.degraded, isinstance(reply, _Stored)
            if reply.unavailable:
                answer["outage"] = True
            # A clarifying question shows no opening (ClarifyView), so it never spends the greeting.
            if answer.get("version") == 2 and answer.get("status") != "clarify":
                answer["opening"] = _opening(answer, question, user.display_name, earlier, context)

        try:
            # #535: a streamed answer stopped by its cap is never saved, and one saved is finished.
            with stream.saving(), context.db.user(user.customer_id, user.user_id) as conn:
                saved_to = _save(
                    conn,
                    body,
                    user,
                    context,
                    landing,
                    question_id,
                    conversation_id,
                    answering,
                    question,
                    answer,
                    repeat_key,
                    saved,
                    choice,
                    pending,
                    degraded,
                    copied,
                    channel,
                )
        except IntegrityError:  # #535: saved meanwhile by another process (the client's fallback)
            found = _saved_reply(context, user, question_id)
            if found is None:
                raise ApiError(409, "conflict") from None
            return found
        conversation_id, asked, answered = saved_to
        spend.conversation_id = conversation_id  # committed: the ledger row may name it
    if fresh:
        ready.SHOWN.used(user.customer_id, user.user_id, question)  # Home's list changes only now
    due = summary_due(turns + 1, summary_turn, context.settings.chat_summary_every)
    if due or (forget and conversation_id is not None and _rebuild_due(conversation_id)):
        background.add_task(
            _remember, context, user.customer_id, user.user_id, conversation_id, summary_turn, turns + 1
        )
    shown = message(answered)
    if copied and shown["answer"]:  # A10: a stored copy is shown as a conversation shows it
        with context.db.user(user.customer_id, user.user_id) as conn:
            states = chat_sql.document_states(conn, user.customer_id, cited_document_ids(shown["answer"]))
        shown = shown_message(answered, states)
    return {"conversation_id": conversation_id, "question": message(asked), "answer": shown}


def _save(
    conn: Connection,
    body: AskBody,
    user: CurrentUser,
    context: AppContext,
    landing: str,
    question_id: str,
    conversation_id: str | None,
    answering: ReplyBody | None,
    question: str,
    answer: dict[str, Any],
    repeat_key: str | None,
    saved: dict[str, Any] | None,
    choice: ChatChoice,
    pending: Pending | None,
    degraded: bool,
    copied: bool,
    channel: str | None = None,
) -> tuple[str, RowMapping, RowMapping]:
    """``_ask``'s one save transaction: the conversation (new, named ``landing``, on ``channel``),
    both messages (the question as ``question_id``) and the audit row; (the conversation, the
    question's row, the answer's row)."""
    first = conversation_id is None  # #624: Home's suggestions belong to a first answer only
    if conversation_id is None:
        conversation_id = chat_sql.create_conversation(
            conn,
            user.customer_id,
            user.user_id,
            short_title(question),
            conversation_id=landing,
            channel=channel,
        )
    elif chat_sql.get_conversation(conn, user.customer_id, conversation_id, user.user_id) is None:
        raise ApiError(404, "not_found")
    # PRD 6 B1: checked again under the conversation's lock, so two replies never both answer it.
    if answering and chat_sql.newest_message(conn, user.customer_id, conversation_id) != str(
        answering.message_id
    ):
        raise ApiError(409, "conflict", CLARIFY_GONE)
    if (
        first
        and answer.get("status") == "not_found"
        and not answer.get("documents")
        and not answer.get("outage")
    ):
        # Nothing close (24 Oct 2026): the best next step is a question this library answers.
        answer["suggestions"] = [
            text
            for text, _ in ready.questions(conn, user.customer_id, user.user_id)
            if ready.same_question(text) != ready.same_question(question)
        ][:3]
    if repeat_key is not None and _reusable(answer, degraded):
        # #535: found again by a repeat; a copy served from the cache only marks it, so its
        # Not useful still counts (db/chat.py), and is never itself served.
        answer[ready.REPEAT_COPY if copied else ready.REPEAT_KEY] = repeat_key
    asked = chat_sql.add_message(
        conn,
        user.customer_id,
        conversation_id,
        role="user",
        message_text=question,
        answer=None,
        scope=saved,
        rewritten_question=answer.get("rewritten_question"),
        message_id=question_id,
    )
    answered = chat_sql.add_message(
        conn,
        user.customer_id,
        conversation_id,
        role="assistant",
        message_text=answer_text(answer),
        answer=answer,
    )
    status = answer.get("status")
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action="chat.ask",
        target_type="message",
        target_id=str(answered["id"]),
        details={
            "conversation_id": conversation_id,
            "question_id": str(asked["id"]),
            "status": status if isinstance(status, str) else None,
            # S5c: who was asked to draft it (model ids, lead first, comma-separated:
            # details hold no lists) and at what level.
            "agents": ",".join(agent.model for agent in choice.agents),
            "thinking": choice.thinking,
            **({"via": channel} if channel else {}),
        },
    )
    if pending is not None:
        _suggest(conn, user, pending)
    return conversation_id, asked, answered


@dataclass(frozen=True)
class _Stored:
    """#535: a repeated question's stored answer, answered as ``AnswerService.answer``'s is."""

    found: dict[str, Any]
    unavailable: bool = False  # only a found answer is stored for a repeat (db/chat.py)
    degraded: bool = False  # nor one made while the model or a vector was down (``_reusable``)

    def to_json(self) -> dict[str, Any]:
        return without_resume(self.found)  # no key; the opening is written again for now


def _reusable(answer: dict[str, Any], degraded: bool) -> bool:
    """#535: whether a repeat may be answered with ``answer``: found (answered or unchecked), drafted
    by the model or by a fixed query (never quoted while the model was down or off), and made with
    the model and the question's vector up (``degraded`` False)."""
    found = answer.get("status") in ("answered", "unchecked")
    return found and not degraded and (bool(answer.get("model_used")) or answer.get("route") != "search")


def _stored(found: dict[str, Any] | None, started: float) -> _Stored | None:
    """``found`` to answer with, its timing line logged as the answer's (``cached=1``); None for none."""
    if found is None:
        return None
    _timing_log.info(
        "answer timing route=%s status=%s outcome=%s cached=1 total_ms=%.0f streamed=%d",
        found.get("route"),
        found.get("status"),
        found.get("label"),
        (time.monotonic() - started) * 1000,
        stream.EVENTS.get() is not None,
    )
    return _Stored(found)


# Streaming Ask (#535): what the stream sends while it waits, and its plain words.
HEARTBEAT_SECONDS = 10.0
TOO_SLOW = "This answer is taking too long. Try a narrower question or Swift."
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/chat/stream")
def ask_stream(body: AskBody, user: User, context: Context) -> StreamingResponse:
    """``POST /api/chat`` streamed (#535): the same body, auth and work, sent as server-sent
    events (``stream_answer``): ``started`` (where the answer will land), ``stage``, ``sources``
    and each checked ``sentence`` while the answer is worked out, then ``done`` with exactly the
    reply ``POST /api/chat`` gives (``ask`` itself, saved the same way), or ``error`` with its
    code and words. A client that leaves stops the events only: the answer is finished and saved
    (Tushar, 16 Oct 2026), and the memory's background task runs after it, as after ``ask``'s."""
    return StreamingResponse(
        stream_answer(
            functools.partial(ask, body, user),
            context,
            cap=context.settings.answer_stream_max_seconds,
            turn=_new_answer(body, user),
        ),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


def sse(name: str, data: dict[str, Any]) -> str:
    """One server-sent event; the JSON as FastAPI writes a reply's (one line)."""
    text = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return f"event: {name}\ndata: {text}\n\n"


# #535 (review): every streamed answer's work runs on one bounded pool, never a thread per
# stream; RUNNING holds the ones not finished yet (a graceful stop, and the tests, wait on them).
# Its size: A3's note above ``ask_posted``.
STREAM_WORKERS = 5
_STREAMS = ThreadPoolExecutor(max_workers=STREAM_WORKERS, thread_name_prefix="answer-stream")
RUNNING: set[Future[None]] = set()


async def stream_answer(
    work: Callable[..., dict[str, Any]],
    context: AppContext,
    *,
    cap: float,
    heartbeat: float | None = None,
    turn: str | None = None,
) -> AsyncGenerator[str]:
    """#535: ``work(context=..., background=...)`` (``ask``) on the stream pool, with
    ``stream.EVENTS`` set so the answer sends its events (``started`` first); each event as it
    comes, ": ping" after ``heartbeat`` seconds (HEARTBEAT_SECONDS) with none, then ``done`` with
    its reply or ``error`` with its code and words (an ``ApiError``'s, else what the app's handlers
    would send). Past ``cap`` seconds from when it starts: ``error`` "answer_timeout" (TOO_SLOW,
    logged with that code), and the answer is stopped (``Events.cancel``: its model stream closed
    at the next check, nothing saved), unless it is being saved or saved already: it is finished
    then, and ``done`` follows. A client that goes away before ``done`` only stops the events
    (``Events.detach``): the answer is finished and saved, as ``POST /api/chat``'s is (Tushar, 16 Oct 2026).
    ``turn``: the asker whose turn (A3) it takes from here until the reply is sent (not the
    memory's write after it); none left, an ``error`` "rate_limited" (the route has said 429
    already, unless another came in between). Still waiting for the stream pool after ``cap``
    seconds (A3): it never runs, and the error is the busy words, logged ``answer_queue_full``."""
    if turn is not None and not _take_turn(turn):
        yield sse("error", {"code": "rate_limited", "message": TOO_MANY_RUNNING})
        return
    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    def post(name: str, data: dict[str, Any]) -> None:
        with contextlib.suppress(RuntimeError):  # the stream's loop is gone
            loop.call_soon_threadsafe(inbox.put_nowait, (name, data))

    def replied() -> None:
        if turn is not None:
            _end_turn(turn)

    events, started = Events(post), threading.Event()
    # The request's context, and the events, on the pool's thread. submit, not run_in_executor:
    # the work goes on after the stream ends, with no asyncio future left behind on a closed loop.
    running = _STREAMS.submit(
        contextvars.copy_context().run, _work, work, context, events, post, started, replied
    )
    RUNNING.add(running)
    running.add_done_callback(RUNNING.discard)
    queued_until: float | None = loop.time() + cap
    ends = math.inf
    every = HEARTBEAT_SECONDS if heartbeat is None else heartbeat
    try:
        while True:
            if queued_until is not None and started.is_set():
                queued_until, ends = None, loop.time() + cap  # it runs: its own time starts
            left = (ends if queued_until is None else queued_until) - loop.time()
            if left <= 0 and queued_until is not None:
                if running.cancel():  # never started: the pool was full the whole time
                    replied()
                    logger.warning("streamed answer never started", extra={"code": "answer_queue_full"})
                    yield sse(
                        "error", {"code": "backend_unavailable", "message": MESSAGES["backend_unavailable"]}
                    )
                    return
                queued_until, ends = None, loop.time() + cap  # it started just now
                continue
            if left <= 0:
                # A9: off the loop: it waits while a save holds the answer's lock.
                if await asyncio.to_thread(events.cancel):
                    logger.warning(
                        "streamed answer too slow", extra={"code": "answer_timeout", **events.landing}
                    )
                    yield sse("error", {"code": "answer_timeout", "message": TOO_SLOW})
                    return
                ends = math.inf  # saved already: it is finished, and done follows
                continue
            try:
                name, data = await asyncio.wait_for(inbox.get(), min(every, left))
            except TimeoutError:
                if loop.time() < (ends if queued_until is None else queued_until):
                    yield ": ping\n\n"
                continue
            yield sse(name, data)
            if name in ("done", "error"):
                return
    finally:
        events.detach()  # the client left (or the stream ended): no more events, the answer goes on


def _work(
    work: Callable[..., dict[str, Any]],
    context: AppContext,
    events: Events,
    post: Callable[[str, dict[str, Any]], None],
    started: threading.Event,
    replied: Callable[[], None],
) -> None:
    """``stream_answer``'s work: ``started`` set, the answer, ``replied()`` (the asker's turn back,
    A3), then ``done`` or ``error``; nothing once stopped by the cap. Once saved the answer is
    finished: ``done`` goes out (if anyone still listens) and its background tasks (the memory,
    ``ask``'s) run here, each in its own try, whether the client stayed or not. A failure is logged
    with where the answer would have landed."""
    token = stream.EVENTS.set(events)
    started.set()
    background = BackgroundTasks()
    try:
        try:
            reply = work(context=context, background=background)
        except StreamCancelled:
            return  # stopped by the cap: the stream has said so already
        except ApiError as error:
            post("error", {"code": error.code, "message": error.message})
            return
        except (BackendUnavailable, OperationalError, PoolTimeout) as error:
            if not isinstance(error, BackendUnavailable):  # #620: one made by unavailable() was logged there
                log_unavailable(error)
            post("error", {"code": "backend_unavailable", "message": MESSAGES["backend_unavailable"]})
            return
        except Exception as error:  # noqa: BLE001 - the stream's boundary, as ErrorMiddleware's
            logger.error("streamed answer failed", extra={"code": type(error).__name__, **events.landing})
            post("error", {"code": "internal_error", "message": MESSAGES["internal_error"]})
            return
        finally:
            replied()
        post("done", {"reply": jsonable_encoder(reply)})
        for task in background.tasks:
            try:
                task.func(*task.args, **task.kwargs)
            except Exception as error:  # noqa: BLE001 - after the reply, as Starlette's own tasks
                logger.warning("background task failed", extra={"code": type(error).__name__})
    finally:
        stream.EVENTS.reset(token)


def _inputs(
    conn: Connection,
    user: CurrentUser,
    context: AppContext,
    conversation_id: str | None,
    agents: list[str] | None,
    thinking: str | None,
) -> tuple[list[Pin], dict[str, bool], ChatChoice]:
    """What an answer is made with besides its question: the asker's picks and what Kaagaz
    learned, the switches, and who drafts it. Ask off, or a model not allowed: ApiError."""
    # Most relevant picks (answering/pins.py): the asker's own only, live documents only.
    pins = [
        Pin(row["document_id"], int(row["page_no"]), bool(row["same"]), str(row["question"]))
        for row in chat_sql.pins_for(conn, user.customer_id, user.user_id, conversation_id)
    ]
    switched = ai_settings.switches(conn, user.customer_id)  # 0038, 0039; PRD 6's in 0052
    if not switched["ask"]:
        raise ApiError(403, "feature_off", FEATURE_OFF)
    # What Kaagaz learned (PRD 6 F3, answering/pins.py): this customer's Useful and
    # Wrong documents marks; lifts decides which apply to this question and to whom. With
    # the nlq_learning switch off, the query is not run at all.
    learned = chat_sql.learned_for(conn, user.customer_id, user.user_id) if switched["nlq_learning"] else []
    pins += [
        Pin(
            row["document_id"],
            int(row["page_no"]),
            False,
            str(row["question"]),
            kind="useful" if row["useful"] else "wrong",
            user_id=str(row["user_id"]),
            own=bool(row["own"]),
        )
        for row in learned
    ]
    try:
        agents = _first_allowed(conn, user.customer_id, agents, context)
        choice = _choice(conn, user, agents, thinking, context)
    except AiNotAllowed as refused:
        raise ApiError(403, refused.code, NOT_ALLOWED[refused.code]) from None
    return pins, switched, choice


# Home asks on Swift unless the asker kept Precision (components/chat/logic.ts defaultPick).
READY_THINKING = "medium"
Inputs = tuple[list[Pin], dict[str, bool], ChatChoice]


def _home_inputs(conn: Connection, user: CurrentUser, context: AppContext) -> Inputs | None:
    """``_inputs`` for a question asked from Home: Swift when allowed, else the
    organisation's own level; None when this person cannot ask."""
    for thinking in (READY_THINKING, None):
        try:
            return _inputs(conn, user, context, None, None, thinking)
        except ApiError:
            continue
    return None


@router.get("/chat/ready-questions")
def ready_questions(user: User, context: Context, background: BackgroundTasks) -> dict[str, Any]:
    """Home's questions for this person (answering/ready.py): ``{items: [{text, yours,
    ready}]}``, their own most-asked first; any whose answer is not ready yet is made after
    the reply, so a press a moment later answers at once."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        made_from = ready.stamp(conn, user.customer_id)
        version = ready.access_of(made_from)  # PRD 7: a list made before an access change is not shown
        # The same questions on every visit until one is used (ready.ShownQuestions).
        picked = ready.SHOWN.get(user.customer_id, user.user_id, version) or ready.questions(
            conn, user.customer_id, user.user_id
        )
        inputs = _home_inputs(conn, user, context)
    keys = (
        {}
        if inputs is None
        else {q: ready.key(user.customer_id, user.user_id, q, inputs, made_from) for q, _ in picked}
    )
    ready.SHOWN.keep(user.customer_id, user.user_id, picked, version)
    cold = [q for q, _ in picked if q in keys and ready.READY.claim(keys[q])]
    if cold and inputs is not None:
        background.add_task(_make_ready, context, user, cold, inputs, keys)
    return {
        "items": [
            {
                "text": q,
                "yours": yours,
                "ready": q in keys and q not in cold and ready.READY.get(keys[q]) is not None,
            }
            for q, yours in picked
        ]
    }


def _make_ready(
    context: AppContext,
    user: CurrentUser,
    asked: list[str],
    inputs: Inputs,
    keys: dict[str, tuple[str, ...]],
) -> None:
    """After the reply: each question's answer as Ask would make it fresh, kept when it
    answered. It runs after the response is sent, so a failure is logged as its code only,
    and the question is answered the usual way when pressed."""
    pins, switched, choice = inputs
    for question in asked:
        try:
            with usage_scope(
                context.db, customer_id=user.customer_id, user_id=user.user_id, kind="chat", as_person=True
            ):
                reply = context.answers.answer(
                    user.customer_id,
                    question,
                    scope=EVERYTHING,
                    history=[],
                    user_id=user.user_id,
                    memory=NO_MEMORY,
                    choice=choice,
                    pins=pins,
                    pending=None,
                    clarify=switched["nlq_clarify"],
                    cited=[],
                )
        except (SQLAlchemyError, BackendUnavailable, ModelUnavailable, ModelOutputError) as error:
            if isinstance(error, OperationalError | PoolTimeout):  # #620: its class and SQLSTATE
                log_unavailable(error)
            logger.warning("ready answer not made", extra={"code": type(error).__name__})
            ready.READY.release(keys[question])
            continue
        if reply.unavailable or reply.to_json().get("status") not in ("answered", "unchecked"):
            ready.READY.release(keys[question])
        else:
            ready.READY.put(keys[question], reply)


@router.get("/chat/options")
def chat_options(user: User, context: Context) -> dict[str, Any]:
    """Features 25-27 (C10): the answer settings the asker's organisation allows now, for
    the composer: its usable chat models by name (the default first, ``resolve_chat``'s
    list), the thinking levels and the default one, and whether more than one model may
    answer at once (its Ensemble switch and at least two models), at most ``max_agents``.
    No price, cost or budget (Tushar, 15 Oct 2026). Two statements."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows = usable_chat_models(conn, user.customer_id, settings=context.settings)
        allowed = effective_settings(conn, user.customer_id)
    levels = allowed["chat_levels"] or ["low"]
    return {
        "agents": [
            {"id": row["id"], "name": row["name"] or row["id"], "default": bool(row["is_default_chat"])}
            for row in rows
        ],
        "levels": [level for level in LEVELS if level in levels],
        "default_level": allowed["chat_default_level"] or "low",
        "ensemble": bool(allowed["ensemble"]) and len(rows) >= 2,
        "max_agents": MAX_AGENTS,
    }


def _opening(
    answer: dict[str, Any], question: str, display_name: str, earlier: list[str | None], context: AppContext
) -> str | None:
    """PRD 6 A: the answer's opening line (answering/voice.py), the asker's first name only on
    a conversation's first answer, by the time of day in TIMEZONE. It never reaches a prompt.
    Decision 2's final check runs over it too (it holds no number): failing, there is none."""
    line = opening(
        found=answer.get("status") != "not_found",
        display_name=display_name,
        earlier=earlier,
        now=context.clock(),
        zone=ZoneInfo(context.settings.timezone),
    )
    reason = None if line is None else unsupported(line, numbers(question))
    if reason is not None:
        logger.info("answer text dropped", extra={"code": f"final_check:opening:{reason}"})
        return None
    return line


def _choice(
    conn: Connection, user: CurrentUser, agents: list[str] | None, thinking: str | None, context: AppContext
) -> ChatChoice:
    """Who drafts the answer under the asker's Ask setting (AI settings, 0038): nothing
    saved, ``resolve_chat`` as before; None, no model (``NO_MODEL``); Low and High those
    thinking levels, the request's own level capped at it; Max high on the strongest
    usable answer model unless the request names one. Ask's Precision (high asked for,
    and still high; Vrushit UAT, 25 Oct 2026) is on that strongest model too, unless the
    request names one, so it differs from Swift in its model as well as its level. One
    statement more than before."""
    ceiling = effective_settings(conn, user.customer_id)
    saved = ai_settings.rows(conn, user.customer_id, user.user_id)
    task = ai_settings.resolve("ask", saved, *ai_settings.ask_ceiling(ceiling))
    if task.chosen and task.effective == "none":
        return NO_MODEL
    asked = thinking
    if task.chosen:
        thinking = ai_settings.ask_thinking(task.effective, ceiling["chat_levels"] or ["low"], thinking)
    precision = asked == "high" and thinking == "high"
    if agents is None and thinking is not None and (precision or (task.chosen and task.effective == "max")):
        agents = strongest_chat(
            usable_chat_models(conn, user.customer_id, settings=context.settings), thinking
        )
    return resolve_chat(conn, user.customer_id, agents, thinking, settings=context.settings)


def _first_allowed(
    conn: Connection, customer_id: str, agents: list[str] | None, context: AppContext
) -> list[str] | None:
    """One model answers in Ask (Tushar, 16 Oct 2026): a request naming several (an older
    screen) gets the first one this organisation allows, never a refusal for naming more;
    none allowed, the first, which ``resolve_chat`` refuses as before. With no chat model set,
    MODEL_CHAT is the one allowed, as in ``resolve_chat``. One query, only then."""
    if agents is None or len(agents) <= 1:
        return agents
    usable = {row["id"] for row in usable_chat_models(conn, customer_id, settings=context.settings)}
    usable = usable or {context.settings.model_chat}
    return [next((agent for agent in agents if agent in usable), agents[0])]


# PRD 7 (#602, review): a memory made again (``forget``) whose rebuild keeps failing is tried at
# most once in this many seconds a conversation (this process's own record, oldest dropped).
REBUILD_SECONDS = 600
_REBUILT: dict[str, float] = {}
_REBUILT_LOCK = threading.Lock()


def _rebuild_due(conversation_id: str) -> bool:
    """Whether the memory of ``conversation_id`` may be made again now; notes the try."""
    now = time.monotonic()
    with _REBUILT_LOCK:
        if now - _REBUILT.get(conversation_id, -math.inf) < REBUILD_SECONDS:
            return False
        _REBUILT[conversation_id] = now
        if len(_REBUILT) > 10_000:
            _REBUILT.pop(next(iter(_REBUILT)))
        return True


def _locked(answer: Mapping[str, Any] | None, states: Mapping[str, str]) -> bool:
    """PRD 7 (#605): whether ``answer`` names a document now locked (``document_states``)."""
    return any(states.get(document) == "locked" for document in cited_document_ids(answer))


def _shown_turns(
    conn: Connection, customer_id: str, conversation_id: str, limit: int, state: RowMapping
) -> tuple[list[RowMapping], Memory | None]:
    """PRD 7 (#602): the conversation's newest ``limit`` messages (``recent_messages``) without
    the answers the asker may no longer see whole (``views.no_access``) and those naming a document
    now locked (#605), so no history, cited id
    or open clarify comes from one; and the stored summary and memory with the live documents'
    names, or None when it tracks a document they can no longer see (or deleted forever) or one now
    locked (PRD 7, #605: its summary may hold that file's words), or was
    written before every document it read was tracked (no ``VERSION``): it is not used, and is
    made again (question 7). Two statements, three when the memory names a document."""
    recent = chat_sql.recent_messages(conn, customer_id, conversation_id, limit)
    stored = from_row(state["summary"], state["memory"])
    named = [*stored.documents, *(d for row in recent for d in cited_document_ids(row["answer"]))]
    states = chat_sql.document_states(conn, customer_id, named)
    # PRD 7 (#605): an answer naming a document now locked goes too, though the person may open it.
    shown = [
        row for row in recent if not no_access(row["answer"], states) and not _locked(row["answer"], states)
    ]
    written = state["memory"].get("v") if isinstance(state["memory"], Mapping) else None
    if stored != NO_MEMORY and written != VERSION:
        return shown, None
    if any(document not in states or states[document] == "locked" for document in stored.documents):
        return shown, None
    return shown, live(stored, chat_sql.live_document_names(conn, customer_id, stored.documents))


def summary_due(turn: int, summary_turn: int, every: int) -> bool:
    """M3: question ``turn`` (from 1) writes the memory when it is a multiple of
    CHAT_SUMMARY_EVERY (``every``) questions past the last written one, ``summary_turn``.
    After a failed write the next try is ``every`` questions later, never every question."""
    since = turn - summary_turn
    return since > 0 and since % every == 0


def _remember(
    context: AppContext, customer_id: str, user_id: str, conversation_id: str, expected_turn: int, turn: int
) -> None:
    """M3, after the reply (``_write_memory``). It runs after the response is sent, so
    nothing can be shown: a database, connection or model error is logged as its code only
    (never a traceback, which could hold the chat's words) and nothing is written."""
    try:
        with usage_scope(
            context.db,
            customer_id=customer_id,
            user_id=user_id,
            kind="memory",
            conversation_id=conversation_id,
            as_person=True,
        ):
            _write_memory(context, customer_id, user_id, conversation_id, expected_turn, turn)
    except (SQLAlchemyError, BackendUnavailable, ModelUnavailable, ModelOutputError) as error:
        logger.warning("memory not written", extra={"code": type(error).__name__})


def _write_memory(
    context: AppContext, customer_id: str, user_id: str, conversation_id: str, expected_turn: int, turn: int
) -> None:
    """The summary and memory rewritten from the memory so far and the last
    CHAT_SUMMARY_EVERY turns, with the documents their answers cited, written at ``turn``
    only if ``summary_turn`` is still ``expected_turn``. Nothing is written when the model
    fails or another reply wrote first. The model call is outside any transaction. PRD 7
    (#602): only the answers the asker may still see; a memory tracking a document they can't
    is made again from those alone (``NO_MEMORY`` before them)."""
    every = context.settings.chat_summary_every
    with context.db.user(customer_id, user_id) as conn:
        state = chat_sql.conversation_memory(conn, customer_id, conversation_id, user_id)
        if state is None or state["summary_turn"] != expected_turn:
            return
        recent, remembered = _shown_turns(conn, customer_id, conversation_id, 2 * every, state)
    previous = NO_MEMORY if remembered is None else remembered
    # PRD 7 (#602, review): every document a shown answer names, a clarify's choices too: the memory
    # may hold anything the turns say, so it tracks all they read.
    cited = [
        d for row in reversed(recent) if row["role"] == "assistant" for d in cited_document_ids(row["answer"])
    ]
    if len(set(cited)) > MAX_DOCUMENTS:  # never one id dropped: not written this time
        logger.info("memory not written", extra={"code": "memory_too_many_documents"})
        return
    memory = context.answers.remember(_history(recent), previous, cited)
    if memory is None:
        return
    with context.db.user(customer_id, user_id) as conn:
        chat_sql.save_memory(
            conn,
            customer_id,
            conversation_id,
            user_id,
            summary=memory.summary,
            memory=memory.to_json(),
            turn=turn,
            expected_turn=expected_turn,
        )


def _pending(recent: Sequence[RowMapping], reply: ReplyBody, question: str) -> Pending:
    """The open clarify (the newest message) answered by ``reply``, or by the typed ``question``
    when nothing is picked; 409 if not open or not its own."""
    newest = recent[0] if recent else None
    if newest is None or newest["role"] != "assistant" or str(newest["id"]) != str(reply.message_id):
        raise ApiError(409, "conflict", CLARIFY_GONE)
    found = reply_to(newest["answer"] or {}, reply.choice_ids, None if reply.choice_ids else question)
    if found is None:
        raise ApiError(409, "conflict", CLARIFY_GONE)
    return found


def _suggest(conn: Connection, user: CurrentUser, pending: Pending) -> None:
    """Decision 6 (PRD 6 B2): a "Did you mean" pick ("Puny" meant Pune) is submitted to the
    organisation-words review as a pending suggestion, never straight to the company; its meaning
    is the name's Latin spelling, which the vocabulary matches once a reviewer approves it. PRD 7
    (#602, question 9): a pick of one document records it and the page its name is on
    (``picked_page``, through the view), so the word shows and aliases only for who sees it."""
    if not (pending.confirm and pending.as_typed and len(pending.picked) == 1):
        return
    try:
        word, meaning = check_word(pending.as_typed).word, check_meaning(latin_key(pending.picked[0]).title())
    except WordTextError:  # not a word the review takes (a number, a long phrase): nothing to suggest
        return
    texts: dict[str, Any] = {"word": word.text, "word_fold": word.fold, "meaning": meaning.text}
    texts["meaning_fold"] = meaning.fold
    one = pending.documents[0] if len(pending.documents) == 1 else None
    page = None if one is None else picked_page(conn, user.customer_id, one, pending)
    if page is not None:
        texts |= {"document_id": one, "page_no": page}
    row = organization_words.submit(conn, user.customer_id, user.user_id, **texts)
    if isinstance(row, Suggestion):  # None: already pending or approved; or the company's limit
        details = {"status": "pending", "source": "clarify"}
        target = {"target_type": "organization_word_suggestion", "target_id": row.id}
        audit.record(
            conn, user.customer_id, actor_user_id=user.user_id, action=SUGGESTED, details=details, **target
        )


def _history(recent: Sequence[RowMapping], names: Mapping[str, str] | None = None) -> list[Turn]:
    """The question and answer pairs of ``recent`` (newest first), oldest first. Only an
    answered answer gives its sentences, and a clarify its question: after "not found" the
    turn is its question only (edge case 3). #621: each answered turn also names the documents
    it cited that are in ``names`` (``chat_sql.live_document_names``: current, out of Trash),
    each cleaned and never one that reads like instructions. O(m) for m messages."""
    turns: list[Turn] = []
    answer: str | None = None
    cited: tuple[str, ...] = ()
    for row in recent:  # an answer comes just before its question
        if row["role"] == "assistant":
            saved = row["answer"] or {}
            answer = answer_text(saved) if saved.get("status") in ("answered", "clarify") else None
            shown = (clean_text((names or {}).get(document, "")) for document in _cited([row]))
            cited = tuple(dict.fromkeys(n for n in shown if n and not looks_like_instructions(n)))
        else:
            turns.append(Turn(str(row["text"]), answer, cited))
            answer, cited = None, ()
    return turns[::-1]


def _cited(recent: Sequence[RowMapping]) -> list[str]:
    """The documents the answered answers of ``recent`` (newest first) cite, newest turn first,
    once each: the stored messages' own, never the client's (PRD 6 B, a missing subject)."""
    cited = (
        str(citation["document_id"])
        for row in recent
        if row["role"] == "assistant" and (row["answer"] or {}).get("status") == "answered"
        for citation in row["answer"].get("citations", [])
        if isinstance(citation, dict) and citation.get("document_id")
    )
    return list(dict.fromkeys(cited))


def _saved_scope(recent: Sequence[RowMapping]) -> ScopeBody | None:
    """The scope the newest question in ``recent`` used, parsed again; None if it had
    none. One that no longer parses matches nothing: a scope is never widened."""
    saved = next((row["scope"] for row in recent if row["role"] == "user"), None)
    if saved is None:
        return None
    try:
        return ScopeBody.model_validate(saved)
    except ValidationError:
        return ScopeBody(document_ids=[])


@router.get("/conversations")
def list_conversations(user: User, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows = chat_sql.list_conversations(conn, user.customer_id, user.user_id)
    return {"items": [conversation_summary(row) for row in rows]}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        found = chat_sql.get_conversation(conn, user.customer_id, str(conversation_id), user.user_id)
        if found is None:
            raise ApiError(404, "not_found")
        rows = chat_sql.messages_with_feedback(conn, user.customer_id, str(conversation_id), user.user_id)
        cited = [document_id for row in rows for document_id in cited_document_ids(row["answer"])]
        states = chat_sql.document_states(
            conn, user.customer_id, cited + [d for r in rows for d in _scope_ids(r)]
        )
        opens = chat_sql.opens_locked(conn, user.customer_id, user.user_id)
    messages = []
    scope: list[str] = []  # the question's, for its answer
    replaced = False  # an answer before this one was replaced whole
    picks: set[str] = set()  # the choices of a clarify just replaced
    for row in rows:
        pin = (
            None
            if row["pin_document_id"] is None
            else {
                "document_id": row["pin_document_id"],
                "page_no": row["pin_page_no"],
                "ref_id": row["pin_ref_id"],
            }
        )
        # Sources moved to Trash, replaced, hidden or deleted since: views.answer_with_source_states.
        shown = shown_message(row, states, row["feedback"], pin, scope)
        if row["role"] == "user":
            scope = _scope_ids(row)
            if picks and set(str(row["text"]).split(", ")) <= picks:
                shown["text"] = EARLIER_CHOICE  # PRD 7 (#602): a pick among choices now hidden
            picks = set()
        elif shown["answer"] and shown["answer"]["status"] == NO_ACCESS:
            replaced = True
            picks = {str(c.get("label")) for c in (row["answer"].get("clarify") or {}).get("choices", [])}
        elif shown["answer"] and replaced:
            shown["answer"] = _without_reading(shown["answer"])
        messages.append(shown)
    return {
        "id": str(found["id"]),
        "title": found["title"],
        "messages": messages,
        "locked_note": LOCKED_NOTE if opens else None,  # PRD 7 (#605, p2:79)
    }


# PRD 7 (#602, review): what a reply to a clarify replaced whole says instead of its pick.
EARLIER_CHOICE = "A choice you made earlier"


def _without_reading(answer: dict[str, Any]) -> dict[str, Any]:
    """An answer after one replaced whole, in the same conversation: how it read the question (a
    follow-up may have read the hidden answer) is left out."""
    blank: dict[str, Any] = {
        "rewritten_question": None,
        "normalized_question": "",
        "assumptions": [],
        "understood_words": [],
    }
    return {**answer, **{key: value for key, value in blank.items() if key in answer}}


def _scope_ids(row: RowMapping) -> list[str]:
    """The document ids a question message was asked inside (its ScopeBody), else none."""
    scope = row["scope"]
    return [str(each) for each in scope.get("document_ids") or []] if isinstance(scope, Mapping) else []


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: uuid.UUID, body: TitleBody, user: User, context: Context
) -> dict[str, Any]:
    title = clean_name(body.title, max_length=MAX_TITLE)
    if title is None:
        raise ApiError(400, "bad_request", "Enter a name for the conversation.")
    with context.db.user(user.customer_id, user.user_id) as conn:
        found = chat_sql.rename_conversation(
            conn, user.customer_id, str(conversation_id), user.user_id, title
        )
        if found is None:
            raise ApiError(404, "not_found")
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="chat.rename",
            target_type="conversation",
            target_id=str(conversation_id),
        )
    return conversation_summary(found)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: uuid.UUID, user: User, context: Context) -> Response:
    """The conversation and its messages and feedback (ON DELETE CASCADE); its AI spend
    stays, without the conversation (model_usage, ON DELETE SET NULL)."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        if not chat_sql.delete_conversation(conn, user.customer_id, str(conversation_id), user.user_id):
            raise ApiError(404, "not_found")
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="chat.delete",
            target_type="conversation",
            target_id=str(conversation_id),
        )
    return Response(status_code=204)


@router.post("/messages/{message_id}/feedback", status_code=204)
def send_feedback(message_id: uuid.UUID, body: FeedbackBody, user: User, context: Context) -> Response:
    if not feedback_limits.allow(user.user_id):
        raise ApiError(429, "rate_limited", TOO_MANY_RATINGS, headers={"Retry-After": "60"})
    reason = (body.reason.strip() or None) if body.reason else None
    with context.db.user(user.customer_id, user.user_id) as conn:
        stored = chat_sql.upsert_feedback(
            conn,
            user.customer_id,
            str(message_id),
            user.user_id,
            rating=body.rating,
            reason=reason,
            reason_codes=[code for code in chat_sql.REASONS if code in body.reason_codes],
            notice_shown=body.notice_shown,
        )
    if not stored:
        raise ApiError(404, "not_found")
    return Response(status_code=204)


@router.post("/messages/{message_id}/pin", status_code=204)
def pin_source(message_id: uuid.UUID, body: PinBody, user: User, context: Context) -> Response:
    """ "Most relevant": the asker puts one source of an answer on top (0036). Kept as
    relevance feedback; the answer's text never changes. 404 for anyone else's answer or a
    document that is not this customer's."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        stored = chat_sql.add_pin(
            conn,
            user.customer_id,
            str(message_id),
            user.user_id,
            document_id=str(body.document_id),
            page_no=body.page_no,
            previous_rank=body.previous_rank,
            ref_id=body.ref_id,
        )
    if not stored:
        raise ApiError(404, "not_found")
    return Response(status_code=204)
