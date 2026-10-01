"""POST /api/chat, GET /api/conversations, GET /api/conversations/{id} and
POST /api/messages/{id}/feedback.

Why the order in ``ask``: the answer is worked out outside a transaction (it
may wait on the model), then the conversation, both messages and the audit row are written in
one transaction. The audit row holds only the ids and ``answer.status``.

Each citation in a reply carries its page for the preview, looked up in one
query each time the answer is shown and never stored (views.answer_with_pages).

A conversation belongs to the user who started it; anyone else, in this
customer or another, gets "not found".
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.views import (
    answer_with_pages,
    answer_with_source_states,
    citation_pages,
    cited_document_ids,
    conversation_summary,
    message,
)
from kaagaz.db import audit
from kaagaz.db import chat as chat_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MAX_TITLE = 200
NOT_FOUND_TEXT = "I couldn't find this in your documents."


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    conversation_id: uuid.UUID | None = None


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
    if conversation_id is not None:
        with context.db.customer(user.customer_id) as conn:
            if chat_sql.get_conversation(conn, user.customer_id, conversation_id, user.user_id) is None:
                raise ApiError(404, "not_found")

    answer = context.answers.answer(user.customer_id, question).to_json()

    with context.db.customer(user.customer_id) as conn:
        if conversation_id is None:
            conversation_id = chat_sql.create_conversation(
                conn, user.customer_id, user.user_id, question[:MAX_TITLE]
            )
        elif chat_sql.get_conversation(conn, user.customer_id, conversation_id, user.user_id) is None:
            raise ApiError(404, "not_found")
        asked = chat_sql.add_message(
            conn, user.customer_id, conversation_id, role="user", message_text=question, answer=None
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
        pages = chat_sql.cited_pages(conn, user.customer_id, citation_pages(answer))
    shown = message(answered)
    shown["answer"] = answer_with_pages(shown["answer"], pages)
    return {"conversation_id": conversation_id, "question": message(asked), "answer": shown}


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
        pages = chat_sql.cited_pages(
            conn, user.customer_id, [page for row in rows for page in citation_pages(row["answer"])]
        )
    messages = []
    for row in rows:
        shown = message(row, row["feedback"])
        if shown["answer"]:
            # Each citation's page, and sources moved to Trash, deleted or replaced since:
            # see views.answer_with_pages and views.answer_with_source_states.
            shown["answer"] = answer_with_source_states(answer_with_pages(shown["answer"], pages), states)
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
