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

import functools
import json
import logging
import uuid
from collections.abc import Sequence
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, Response
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
from sqlalchemy.exc import SQLAlchemyError

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.ai.usage import usage_scope
from kaagaz.answering import ready
from kaagaz.answering.clarify import MAX_CHOICES, Pending, answers_clarify, latin_key, reply_to
from kaagaz.answering.memory import NO_MEMORY, Memory, from_row, live
from kaagaz.answering.pins import Pin
from kaagaz.answering.rewrite import Turn
from kaagaz.answering.voice import SOFT_NOTHING, numbers, opening, soft_line, unsupported
from kaagaz.answering.words_text import WordTextError, check_meaning, check_word
from kaagaz.api.auth import FEATURE_OFF, CurrentUser, User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.names import clean_name
from kaagaz.api.views import answer_with_source_states, cited_document_ids, conversation_summary, message
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
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.search.scope import EVERYTHING, ScopeBody, resolve
from kaagaz.settings import MODEL_ID

router = APIRouter()
logger = logging.getLogger(__name__)

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


def answer_text(answer: dict[str, Any]) -> str:
    """The assistant message's plain text: its sentences joined, a clarify's question, or the
    not-found line (decision 9: the closest documents' when it lists any). Never the opening,
    so the asker's name is never in the history a rewrite reads."""
    clarify = answer.get("clarify") if answer.get("status") == "clarify" else None
    if isinstance(clarify, dict) and isinstance(clarify.get("question"), str):
        return str(clarify["question"])
    sentences = answer.get("sentences")
    texts = []
    if answer.get("status") in ("answered", "unchecked") and isinstance(sentences, list):
        texts = [s["text"] for s in sentences if isinstance(s, dict) and isinstance(s.get("text"), str)]
    return " ".join(texts) or soft_line(bool(answer.get("closest_documents")))


@router.post("/chat")
def ask(body: AskBody, user: User, context: Context, background: BackgroundTasks) -> dict[str, Any]:
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
    earlier: list[str | None] = []  # the conversation's openings so far (PRD 6 A)
    pending: Pending | None = None
    with context.db.customer(user.customer_id) as conn:
        if conversation_id is not None:
            state = chat_sql.conversation_memory(conn, user.customer_id, conversation_id, user.user_id)
            if state is None:
                raise ApiError(404, "not_found")
            memory = _live_memory(conn, user.customer_id, state)
            summary_turn, turns = int(state["summary_turn"]), int(state["turns"])
            pairs = max(context.settings.chat_history_turns, 1)
            recent = chat_sql.recent_messages(conn, user.customer_id, conversation_id, 2 * pairs)
            earlier = chat_sql.openings(conn, user.customer_id, conversation_id)
            if chosen is None:
                chosen = _saved_scope(recent)
            if answering is not None:
                pending = _pending(recent, answering, question)
                question = ", ".join(pending.picked) or question
        scope = (
            EVERYTHING
            if chosen is None
            else resolve(conn, user.customer_id, chosen, timezone=context.settings.timezone)
        )
        pins, switched, choice = _inputs(conn, user, context, conversation_id, body.agents, body.thinking)
        # Home's questions (answering/ready.py): a fresh question about everything may have its
        # answer ready, made with these same inputs from these same documents.
        fresh = conversation_id is None and answering is None and chosen is None
        ready_key = (
            ready.key(
                user.customer_id,
                user.user_id,
                question,
                (pins, switched, choice),
                ready.stamp(conn, user.customer_id),
            )
            if fresh
            else None
        )
    saved = None if chosen is None else chosen.model_dump(mode="json")
    # json.dumps writes ", " and ": " as jsonb's text does, so this is the CHECK's length.
    if saved is not None and len(json.dumps(saved).encode()) > MAX_SCOPE_BYTES:
        raise ApiError(400, "bad_request", "Choose fewer documents or tags.")

    history = _history(recent)
    with usage_scope(
        context.db,
        customer_id=user.customer_id,
        user_id=user.user_id,
        kind="chat",
        conversation_id=conversation_id,
    ) as spend:
        kept = None if ready_key is None else ready.READY.get(ready_key)
        reply = kept or context.answers.answer(
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
        )
        if reply.unavailable:  # KZ-006: an outage is never "not found"; nothing is stored
            raise ApiError(503, "backend_unavailable", headers={"Retry-After": "60"})
        answer = reply.to_json()
        if answer.get("version") == 2:
            answer["opening"] = _opening(answer, question, user.display_name, earlier, context)

        with context.db.customer(user.customer_id) as conn:
            if conversation_id is None:
                conversation_id = chat_sql.create_conversation(
                    conn, user.customer_id, user.user_id, short_title(question)
                )
            elif chat_sql.get_conversation(conn, user.customer_id, conversation_id, user.user_id) is None:
                raise ApiError(404, "not_found")
            # PRD 6 B1: checked again under the conversation's lock, so two replies never both answer it.
            if answering and chat_sql.newest_message(conn, user.customer_id, conversation_id) != str(
                answering.message_id
            ):
                raise ApiError(409, "conflict", CLARIFY_GONE)
            asked = chat_sql.add_message(
                conn,
                user.customer_id,
                conversation_id,
                role="user",
                message_text=question,
                answer=None,
                scope=saved,
                rewritten_question=answer.get("rewritten_question"),
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
                },
            )
            if pending is not None:
                _suggest(conn, user, pending)
        spend.conversation_id = conversation_id  # committed: the ledger row may name it
    if fresh:
        ready.SHOWN.used(user.customer_id, user.user_id, question)  # Home's list changes only now
    if summary_due(turns + 1, summary_turn, context.settings.chat_summary_every):
        background.add_task(
            _remember, context, user.customer_id, user.user_id, conversation_id, summary_turn, turns + 1
        )
    return {"conversation_id": conversation_id, "question": message(asked), "answer": message(answered)}


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
    # Wrong documents marks; lifts decides which apply to this question and to whom.
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
        for row in chat_sql.learned_for(conn, user.customer_id, user.user_id)
        if switched["nlq_learning"]
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
    with context.db.customer(user.customer_id) as conn:
        # The same questions on every visit until one is used (ready.ShownQuestions).
        picked = ready.SHOWN.get(user.customer_id, user.user_id) or ready.questions(
            conn, user.customer_id, user.user_id
        )
        inputs = _home_inputs(conn, user, context)
        made_from = ready.stamp(conn, user.customer_id)
    keys = (
        {}
        if inputs is None
        else {q: ready.key(user.customer_id, user.user_id, q, inputs, made_from) for q, _ in picked}
    )
    ready.SHOWN.keep(user.customer_id, user.user_id, picked)
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
            with usage_scope(context.db, customer_id=user.customer_id, user_id=user.user_id, kind="chat"):
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
    with context.db.customer(user.customer_id) as conn:
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
    usable answer model unless the request names one. One statement more than before."""
    ceiling = effective_settings(conn, user.customer_id)
    saved = ai_settings.rows(conn, user.customer_id, user.user_id)
    task = ai_settings.resolve("ask", saved, *ai_settings.ask_ceiling(ceiling))
    if task.chosen and task.effective == "none":
        return NO_MODEL
    if task.chosen:
        thinking = ai_settings.ask_thinking(task.effective, ceiling["chat_levels"] or ["low"], thinking)
        if task.effective == "max" and agents is None:
            agents = strongest_chat(
                usable_chat_models(conn, user.customer_id, settings=context.settings), thinking
            )
    return resolve_chat(conn, user.customer_id, agents, thinking, settings=context.settings)


def _first_allowed(
    conn: Connection, customer_id: str, agents: list[str] | None, context: AppContext
) -> list[str] | None:
    """One model answers in Ask (Tushar, 16 Oct 2026): a request naming several (an older
    screen) gets the first one this organisation allows, never a refusal for naming more;
    none allowed, the first, which ``resolve_chat`` refuses as before. One query, only then."""
    if agents is None or len(agents) <= 1:
        return agents
    usable = {row["id"] for row in usable_chat_models(conn, customer_id, settings=context.settings)}
    return [next((agent for agent in agents if agent in usable), agents[0])]


def _live_memory(conn: Connection, customer_id: str, state: RowMapping) -> Memory:
    """The stored summary and memory, without the documents that are no longer live
    (Trash, an older version, deleted forever), with the live ones' names. One query when
    it names documents."""
    stored = from_row(state["summary"], state["memory"])
    return live(stored, chat_sql.live_document_names(conn, customer_id, stored.documents))


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
    fails or another reply wrote first. The model call is outside any transaction."""
    every = context.settings.chat_summary_every
    with context.db.customer(customer_id) as conn:
        state = chat_sql.conversation_memory(conn, customer_id, conversation_id, user_id)
        if state is None or state["summary_turn"] != expected_turn:
            return
        previous = _live_memory(conn, customer_id, state)
        recent = chat_sql.recent_messages(conn, customer_id, conversation_id, 2 * every)
    cited = [
        str(citation["document_id"])
        for row in reversed(recent)
        if row["role"] == "assistant" and (row["answer"] or {}).get("status") == "answered"
        for citation in row["answer"].get("citations", [])
        if isinstance(citation, dict) and citation.get("document_id")
    ]
    memory = context.answers.remember(_history(recent), previous, cited)
    if memory is None:
        return
    with context.db.customer(customer_id) as conn:
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
    is the name's Latin spelling, which the vocabulary matches once a reviewer approves it."""
    if not (pending.confirm and pending.as_typed and len(pending.picked) == 1):
        return
    try:
        word, meaning = check_word(pending.as_typed).word, check_meaning(latin_key(pending.picked[0]).title())
    except WordTextError:  # not a word the review takes (a number, a long phrase): nothing to suggest
        return
    texts: dict[str, Any] = {"word": word.text, "word_fold": word.fold, "meaning": meaning.text}
    texts["meaning_fold"] = meaning.fold
    row = organization_words.submit(conn, user.customer_id, user.user_id, **texts)
    if isinstance(row, Suggestion):  # None: already pending or approved; or the company's limit
        details = {"status": "pending", "source": "clarify"}
        target = {"target_type": "organization_word_suggestion", "target_id": row.id}
        audit.record(
            conn, user.customer_id, actor_user_id=user.user_id, action=SUGGESTED, details=details, **target
        )


def _history(recent: Sequence[RowMapping]) -> list[Turn]:
    """The question and answer pairs of ``recent`` (newest first), oldest first. Only an
    answered answer gives its sentences, and a clarify its question: after "not found" the
    turn is its question only (edge case 3). O(m) for m messages."""
    turns: list[Turn] = []
    answer: str | None = None
    for row in recent:  # an answer comes just before its question
        if row["role"] == "assistant":
            saved = row["answer"] or {}
            answer = answer_text(saved) if saved.get("status") in ("answered", "clarify") else None
        else:
            turns.append(Turn(str(row["text"]), answer))
            answer = None
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
    with context.db.customer(user.customer_id) as conn:
        rows = chat_sql.list_conversations(conn, user.customer_id, user.user_id)
    return {"items": [conversation_summary(row) for row in rows]}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: uuid.UUID, user: User, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        found = chat_sql.get_conversation(conn, user.customer_id, str(conversation_id), user.user_id)
        if found is None:
            raise ApiError(404, "not_found")
        rows = chat_sql.messages_with_feedback(conn, user.customer_id, str(conversation_id), user.user_id)
        cited = [document_id for row in rows for document_id in cited_document_ids(row["answer"])]
        states = chat_sql.document_states(conn, user.customer_id, cited)
    messages = []
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
        shown = message(row, row["feedback"], pin)
        if shown["answer"]:
            # Sources moved to Trash, deleted or replaced since: see views.answer_with_source_states.
            shown["answer"] = answer_with_source_states(shown["answer"], states)
        messages.append(shown)
    return {"id": str(found["id"]), "title": found["title"], "messages": messages}


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: uuid.UUID, body: TitleBody, user: User, context: Context
) -> dict[str, Any]:
    title = clean_name(body.title, max_length=MAX_TITLE)
    if title is None:
        raise ApiError(400, "bad_request", "Enter a name for the conversation.")
    with context.db.customer(user.customer_id) as conn:
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
    with context.db.customer(user.customer_id) as conn:
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
    with context.db.customer(user.customer_id) as conn:
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
    with context.db.customer(user.customer_id) as conn:
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
