"""Conversations, their messages and the thumbs up or down on answers.

Why here: the SQL for chat in one place. Every function takes a connection
from ``Database.customer(customer_id)`` and filters on ``customer_id`` too.
A conversation belongs to one user: every read and every feedback write also
checks ``user_id``, so a colleague in the same customer cannot open it either.

Question text is stored already masked (the API masks it first). The answer
is stored as the jsonb ``Answer.to_json()`` gives.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection, RowMapping, text

CONVERSATION_LIST_LIMIT = 50


def create_conversation(conn: Connection, customer_id: str, user_id: str, title: str) -> str:
    conversation_id: object = conn.execute(
        text("INSERT INTO conversations (customer_id, user_id, title) VALUES (:c, :u, :t) RETURNING id"),
        {"c": customer_id, "u": user_id, "t": title},
    ).scalar_one()
    return str(conversation_id)


def get_conversation(
    conn: Connection, customer_id: str, conversation_id: str, user_id: str
) -> RowMapping | None:
    """The conversation if this user owns it, else None."""
    return (
        conn.execute(
            text(
                "SELECT id, title, created_at FROM conversations"
                " WHERE customer_id = :c AND id = :id AND user_id = :u"
            ),
            {"c": customer_id, "id": conversation_id, "u": user_id},
        )
        .mappings()
        .first()
    )


def list_conversations(conn: Connection, customer_id: str, user_id: str) -> Sequence[RowMapping]:
    """This user's newest conversations. Index (customer_id, user_id, created_at)."""
    return (
        conn.execute(
            text(
                "SELECT id, title, created_at FROM conversations"
                " WHERE customer_id = :c AND user_id = :u"
                " ORDER BY created_at DESC, id DESC LIMIT :limit"
            ),
            {"c": customer_id, "u": user_id, "limit": CONVERSATION_LIST_LIMIT},
        )
        .mappings()
        .all()
    )


def add_message(
    conn: Connection,
    customer_id: str,
    conversation_id: str,
    *,
    role: str,
    message_text: str,
    answer: Mapping[str, Any] | None,
) -> RowMapping:
    """Store one message. ``answer`` is set for assistant messages only (a table check)."""
    return (
        conn.execute(
            text(
                "INSERT INTO messages (customer_id, conversation_id, role, text, answer)"
                " VALUES (:c, :conv, :role, :text, CAST(:answer AS jsonb))"
                " RETURNING id, role, text, answer, created_at"
            ),
            {
                "c": customer_id,
                "conv": conversation_id,
                "role": role,
                "text": message_text,
                "answer": None if answer is None else json.dumps(answer),
            },
        )
        .mappings()
        .one()
    )


def messages_with_feedback(
    conn: Connection, customer_id: str, conversation_id: str, user_id: str
) -> Sequence[RowMapping]:
    """Messages oldest first, each with this user's rating. One query (no N+1)."""
    return (
        conn.execute(
            text(
                "SELECT m.id, m.role, m.text, m.answer, m.created_at, f.rating AS feedback"
                " FROM messages m"
                " LEFT JOIN feedback f ON f.customer_id = m.customer_id AND f.message_id = m.id"
                " AND f.user_id = :u"
                " WHERE m.customer_id = :c AND m.conversation_id = :conv"
                " ORDER BY m.created_at, m.role DESC, m.id"
            ),
            {"c": customer_id, "conv": conversation_id, "u": user_id},
        )
        .mappings()
        .all()
    )


def upsert_feedback(
    conn: Connection,
    customer_id: str,
    message_id: str,
    user_id: str,
    *,
    rating: str,
    reason: str | None,
) -> bool:
    """Rate an assistant message in one of this user's conversations. One rating per
    (message, user): a second vote replaces the first. False if there is no such
    message for this user."""
    stored = conn.execute(
        text(
            "INSERT INTO feedback (customer_id, message_id, user_id, rating, reason)"
            " SELECT m.customer_id, m.id, :u, :rating, :reason"
            " FROM messages m JOIN conversations cv"
            " ON cv.customer_id = m.customer_id AND cv.id = m.conversation_id"
            " WHERE m.customer_id = :c AND m.id = :m AND m.role = 'assistant' AND cv.user_id = :u"
            " ON CONFLICT (message_id, user_id)"
            " DO UPDATE SET rating = EXCLUDED.rating, reason = EXCLUDED.reason, created_at = now()"
            " RETURNING id"
        ),
        {"c": customer_id, "m": message_id, "u": user_id, "rating": rating, "reason": reason},
    ).first()
    return stored is not None
