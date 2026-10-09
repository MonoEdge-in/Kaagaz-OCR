"""POST /api/chat, GET /api/conversations, GET /api/conversations/{id} and
POST /api/messages/{id}/feedback.

Why the order in ``ask``: one short transaction checks the conversation and
resolves the scope (PRD 3 H2), the one sent or else the last question's
(PROVISIONAL, plan decision 9), again for this customer now: a deleted folder or
another customer's ids match nothing. It also reads the last CHAT_HISTORY_TURNS
question and answer pairs for a follow-up's rewrite (H1). The answer is worked
out outside a transaction (it may wait on the model), then the conversation, both
messages (the question with its scope and rewrite) and the audit row are written
in one transaction.
The audit row holds only the ids and ``answer.status``.

The asker's own words (My words, A3) may help the answer, which then lists them in
``understood_words``; they are never in a message's text, so never in the history.

A chat that remembers (PRD 5 M3): the first transaction also reads the conversation's
summary and memory (its documents that are no longer live left out), which go to the
follow-up rewrite only. When this question is a multiple of CHAT_SUMMARY_EVERY questions
past the last summary (``summary_due``: after a failed one, the next try is that many
questions later), a background task writes a new one after the reply (``_remember``); it
gives up quietly when the model fails or another reply wrote it first, and logs only the
code of a database or model error. Neither is ever in a reply or a log.

A conversation belongs to the user who started it; anyone else, in this
customer or another, gets "not found".
"""

import json
import logging
import uuid
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Connection, RowMapping
from sqlalchemy.exc import SQLAlchemyError

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.answering.memory import NO_MEMORY, Memory, from_row, live
from kaagaz.answering.rewrite import Turn
from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.views import answer_with_source_states, cited_document_ids, conversation_summary, message
from kaagaz.db import audit
from kaagaz.db import chat as chat_sql
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.search.scope import EVERYTHING, ScopeBody, resolve

router = APIRouter()
logger = logging.getLogger(__name__)

Context = Annotated[AppContext, Depends(get_context)]

MAX_TITLE = 200
NOT_FOUND_TEXT = "I couldn't find this in your documents."
MAX_SCOPE_BYTES = 4096  # the messages.scope CHECK (migration 0006)


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    conversation_id: uuid.UUID | None = None
    scope: ScopeBody | None = None  # left out: the conversation's last scope, or everything


class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["up", "down"]
    reason: str | None = Field(default=None, max_length=500)


def answer_text(answer: dict[str, Any]) -> str:
    """The assistant message's plain text: its sentences joined, or the not-found line."""
    sentences = answer.get("sentences")
    texts = []
    if answer.get("status") == "answered" and isinstance(sentences, list):
        texts = [s["text"] for s in sentences if isinstance(s, dict) and isinstance(s.get("text"), str)]
    return " ".join(texts) or NOT_FOUND_TEXT


@router.post("/chat")
def ask(body: AskBody, user: User, context: Context, background: BackgroundTasks) -> dict[str, Any]:
    question = body.question.strip()
    if not question:
        raise ApiError(400, "bad_request", "Type a question first.")
    conversation_id = None if body.conversation_id is None else str(body.conversation_id)
    chosen = body.scope
    recent: Sequence[RowMapping] = []
    memory, summary_turn, turns = NO_MEMORY, 0, 0
    with context.db.customer(user.customer_id) as conn:
        if conversation_id is not None:
            state = chat_sql.conversation_memory(conn, user.customer_id, conversation_id, user.user_id)
            if state is None:
                raise ApiError(404, "not_found")
            memory = _live_memory(conn, user.customer_id, state)
            summary_turn, turns = int(state["summary_turn"]), int(state["turns"])
            pairs = max(context.settings.chat_history_turns, 1)
            recent = chat_sql.recent_messages(conn, user.customer_id, conversation_id, 2 * pairs)
            if chosen is None:
                chosen = _saved_scope(recent)
        scope = (
            EVERYTHING
            if chosen is None
            else resolve(conn, user.customer_id, chosen, timezone=context.settings.timezone)
        )
    saved = None if chosen is None else chosen.model_dump(mode="json")
    # json.dumps writes ", " and ": " as jsonb's text does, so this is the CHECK's length.
    if saved is not None and len(json.dumps(saved).encode()) > MAX_SCOPE_BYTES:
        raise ApiError(400, "bad_request", "Choose fewer documents or tags.")

    history = _history(recent)
    answer = context.answers.answer(
        user.customer_id, question, scope=scope, history=history, user_id=user.user_id, memory=memory
    ).to_json()

    with context.db.customer(user.customer_id) as conn:
        if conversation_id is None:
            conversation_id = chat_sql.create_conversation(
                conn, user.customer_id, user.user_id, question[:MAX_TITLE]
            )
        elif chat_sql.get_conversation(conn, user.customer_id, conversation_id, user.user_id) is None:
            raise ApiError(404, "not_found")
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
            },
        )
    if summary_due(turns + 1, summary_turn, context.settings.chat_summary_every):
        background.add_task(
            _remember, context, user.customer_id, user.user_id, conversation_id, summary_turn, turns + 1
        )
    return {"conversation_id": conversation_id, "question": message(asked), "answer": message(answered)}


def _live_memory(conn: Connection, customer_id: str, state: RowMapping) -> Memory:
    """The stored summary and memory, without the documents that are no longer live
    (Trash, an older version, deleted forever). One query when it names documents."""
    stored = from_row(state["summary"], state["memory"])
    return live(stored, chat_sql.document_states(conn, customer_id, stored.documents))


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


def _history(recent: Sequence[RowMapping]) -> list[Turn]:
    """The question and answer pairs of ``recent`` (newest first), oldest first. Only an
    answered answer gives its sentences: after "not found" the turn is its question only
    (edge case 3). O(m) for m messages."""
    turns: list[Turn] = []
    answer: str | None = None
    for row in recent:  # an answer comes just before its question
        if row["role"] == "assistant":
            saved = row["answer"] or {}
            answer = answer_text(saved) if saved.get("status") == "answered" else None
        else:
            turns.append(Turn(str(row["text"]), answer))
            answer = None
    return turns[::-1]


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
        shown = message(row, row["feedback"])
        if shown["answer"]:
            # Sources moved to Trash, deleted or replaced since: see views.answer_with_source_states.
            shown["answer"] = answer_with_source_states(shown["answer"], states)
        messages.append(shown)
    return {"id": str(found["id"]), "title": found["title"], "messages": messages}


@router.post("/messages/{message_id}/feedback", status_code=204)
def send_feedback(message_id: uuid.UUID, body: FeedbackBody, user: User, context: Context) -> Response:
    reason = body.reason.strip() if body.reason else None
    with context.db.customer(user.customer_id) as conn:
        stored = chat_sql.upsert_feedback(
            conn,
            user.customer_id,
            str(message_id),
            user.user_id,
            rating=body.rating,
            reason=reason,
        )
    if not stored:
        raise ApiError(404, "not_found")
    return Response(status_code=204)
