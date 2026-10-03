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

A conversation belongs to the user who started it; anyone else, in this
customer or another, gets "not found".
"""

import json
import uuid
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import RowMapping

from kaagaz.answering.rewrite import Turn
from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.views import answer_with_source_states, cited_document_ids, conversation_summary, message
from kaagaz.db import audit
from kaagaz.db import chat as chat_sql
from kaagaz.search.scope import EVERYTHING, ScopeBody, resolve

router = APIRouter()

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
def ask(body: AskBody, user: User, context: Context) -> dict[str, Any]:
    question = body.question.strip()
    if not question:
        raise ApiError(400, "bad_request", "Type a question first.")
    conversation_id = None if body.conversation_id is None else str(body.conversation_id)
    chosen = body.scope
    recent: Sequence[RowMapping] = []
    with context.db.customer(user.customer_id) as conn:
        if conversation_id is not None:
            if chat_sql.get_conversation(conn, user.customer_id, conversation_id, user.user_id) is None:
                raise ApiError(404, "not_found")
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
    answer = context.answers.answer(user.customer_id, question, scope=scope, history=history).to_json()

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
    return {"conversation_id": conversation_id, "question": message(asked), "answer": message(answered)}


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
