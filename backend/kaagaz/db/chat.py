"""Conversations, their messages and Useful or Not useful on answers.

Why here: the SQL for chat in one place. Every function takes a connection
from ``Database.customer(customer_id)`` and filters on ``customer_id`` too.
A conversation belongs to one user: every read and every feedback write also
checks ``user_id``, so a colleague in the same customer cannot open it either.

Question text is stored as asked, stripped of surrounding spaces, with the
scope it was asked in (``ScopeBody.model_dump``, PRD 3 H2). The answer is stored
as the jsonb ``Answer.to_json()`` gives.

A conversation's summary and memory (PRD 5 M3) are read and written with its owner
only, like every read here, and never returned by the API. A write names the
``summary_turn`` it read (optimistic): when another reply wrote them first, it
changes nothing.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection, RowMapping, text

CONVERSATION_LIST_LIMIT = 50
QUESTIONS_SHOWN = 50

# File management P2: this user's questions (the user message, which holds the scope) with
# their answers (the assistant message written in the same transaction, so the same
# created_at), where the scope's document_ids or the answer's citations[].document_id name
# a version of the family. Read through conversations_user and messages_conversation: only
# this person's messages are looked at, never the table.
_DOCUMENT_QUESTIONS = text(
    "SELECT q.id, q.conversation_id, q.text AS question, q.created_at, a.answer FROM conversations c"
    " JOIN messages q ON q.customer_id = c.customer_id AND q.conversation_id = c.id AND q.role = 'user'"
    " JOIN messages a ON a.customer_id = q.customer_id AND a.conversation_id = q.conversation_id"
    " AND a.role = 'assistant' AND a.created_at = q.created_at"
    " WHERE c.customer_id = :c AND c.user_id = :u AND EXISTS (SELECT 1 FROM documents v"
    " WHERE v.customer_id = :c AND v.family_id = CAST(:f AS uuid)"
    " AND (q.scope -> 'document_ids' @> jsonb_build_array(v.id::text)"
    " OR a.answer -> 'citations' @> jsonb_build_array(jsonb_build_object('document_id', v.id::text))))"
    " ORDER BY q.created_at DESC, q.id DESC LIMIT :n"
)


def document_questions(
    conn: Connection, customer_id: str, user_id: str, family_id: str
) -> Sequence[RowMapping]:
    """P2, newest first, at most QUESTIONS_SHOWN. O(M log v) for this user's M messages and
    the family's v versions (documents_family_version).
    ponytail: reads every message of this person; a GIN index on the citations if one
    person's history ever makes it slow."""
    params = {"c": customer_id, "u": user_id, "f": family_id, "n": QUESTIONS_SHOWN}
    return conn.execute(_DOCUMENT_QUESTIONS, params).mappings().all()


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


def conversation_memory(
    conn: Connection, customer_id: str, conversation_id: str, user_id: str
) -> RowMapping | None:
    """M3: the conversation's ``summary``, ``memory`` and ``summary_turn``, and ``turns``,
    how many questions it holds, if this user owns it, else None. One query."""
    return (
        conn.execute(
            text(
                "SELECT cv.summary, cv.memory, cv.summary_turn,"
                " (SELECT count(*) FROM messages m WHERE m.customer_id = :c AND m.conversation_id = cv.id"
                " AND m.role = 'user') AS turns"
                " FROM conversations cv WHERE cv.customer_id = :c AND cv.id = :id AND cv.user_id = :u"
            ),
            {"c": customer_id, "id": conversation_id, "u": user_id},
        )
        .mappings()
        .first()
    )


def save_memory(
    conn: Connection,
    customer_id: str,
    conversation_id: str,
    user_id: str,
    *,
    summary: str,
    memory: Mapping[str, Any],
    turn: int,
    expected_turn: int,
) -> bool:
    """M3: write the summary ("" as NULL) and memory at ``turn``, only if this user owns the
    conversation and its ``summary_turn`` is still ``expected_turn``. False when nothing
    changed."""
    written = conn.execute(
        text(
            "UPDATE conversations SET summary = :s, memory = CAST(:m AS jsonb), summary_turn = :t"
            " WHERE customer_id = :c AND id = :id AND user_id = :u AND summary_turn = :old"
        ),
        {
            "c": customer_id,
            "id": conversation_id,
            "u": user_id,
            "s": summary or None,
            "m": json.dumps(memory, ensure_ascii=False),
            "t": turn,
            "old": expected_turn,
        },
    )
    return written.rowcount == 1


def rename_conversation(
    conn: Connection, customer_id: str, conversation_id: str, user_id: str, title: str
) -> RowMapping | None:
    """The renamed conversation if this user owns it, else None (nothing changed)."""
    return (
        conn.execute(
            text(
                "UPDATE conversations SET title = :t"
                " WHERE customer_id = :c AND id = :id AND user_id = :u RETURNING id, title, created_at"
            ),
            {"c": customer_id, "id": conversation_id, "u": user_id, "t": title},
        )
        .mappings()
        .first()
    )


def delete_conversation(conn: Connection, customer_id: str, conversation_id: str, user_id: str) -> bool:
    """Deletes this user's conversation, its messages and their feedback (ON DELETE
    CASCADE). False when it is not theirs: nothing changed."""
    deleted = conn.execute(
        text("DELETE FROM conversations WHERE customer_id = :c AND id = :id AND user_id = :u"),
        {"c": customer_id, "id": conversation_id, "u": user_id},
    )
    return deleted.rowcount == 1


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
    scope: Mapping[str, Any] | None = None,
    rewritten_question: str | None = None,
) -> RowMapping:
    """Store one message. ``answer`` is set for assistant messages only (a table check);
    ``scope`` for a question asked inside one, ``rewritten_question`` for a follow-up (H1)."""
    return (
        conn.execute(
            text(
                "INSERT INTO messages (customer_id, conversation_id, role, text, answer, scope,"
                " rewritten_question)"
                " VALUES (:c, :conv, :role, :text, CAST(:answer AS jsonb), CAST(:scope AS jsonb), :rq)"
                " RETURNING id, role, text, answer, scope, created_at"
            ),
            {
                "c": customer_id,
                "conv": conversation_id,
                "role": role,
                "text": message_text,
                "answer": None if answer is None else json.dumps(answer),
                "scope": None if scope is None else json.dumps(scope),
                "rq": rewritten_question,
            },
        )
        .mappings()
        .one()
    )


def messages_with_feedback(
    conn: Connection, customer_id: str, conversation_id: str, user_id: str
) -> Sequence[RowMapping]:
    """Messages oldest first, each with this user's rating and newest source pick
    (``pin_document_id``, ``pin_page_no``; 0036). One query (no N+1): the pick is one
    index lookup per message (source_pins_message)."""
    return (
        conn.execute(
            text(
                "SELECT m.id, m.role, m.text, m.answer, m.scope, m.created_at, f.rating AS feedback,"
                " p.document_id AS pin_document_id, p.page_no AS pin_page_no, p.source_ref AS pin_ref_id"
                " FROM messages m"
                " LEFT JOIN feedback f ON f.customer_id = m.customer_id AND f.message_id = m.id"
                " AND f.user_id = :u"
                " LEFT JOIN LATERAL (SELECT s.document_id, s.page_no, s.source_ref FROM source_pins s"
                " WHERE s.customer_id = m.customer_id AND s.message_id = m.id AND s.user_id = :u"
                " ORDER BY s.created_at DESC, s.id LIMIT 1) p ON true"
                " WHERE m.customer_id = :c AND m.conversation_id = :conv"
                " ORDER BY m.created_at, m.role DESC, m.id"
            ),
            {"c": customer_id, "conv": conversation_id, "u": user_id},
        )
        .mappings()
        .all()
    )


def recent_messages(
    conn: Connection, customer_id: str, conversation_id: str, limit: int
) -> Sequence[RowMapping]:
    """The conversation's newest ``limit`` messages, newest first (an answer before its
    question: both are written in one transaction, so they share ``created_at``). One
    query on (conversation_id, created_at), O(limit log M)."""
    return (
        conn.execute(
            text(
                "SELECT m.role, m.text, m.answer, m.scope FROM messages m"
                " WHERE m.customer_id = :c AND m.conversation_id = :conv"
                " ORDER BY m.created_at DESC, m.role, m.id DESC LIMIT :n"
            ),
            {"c": customer_id, "conv": conversation_id, "n": limit},
        )
        .mappings()
        .all()
    )


def document_states(conn: Connection, customer_id: str, document_ids: Sequence[str]) -> dict[str, str]:
    """What became of each document a saved answer cites: ``live`` (the current
    version, out of Trash), ``older_version`` (a newer version replaced it),
    ``in_trash``. A document deleted forever is simply missing from the result.
    One query, O(k log D) for k ids."""
    if not document_ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT id, deleted_at IS NOT NULL AS in_trash, is_current FROM documents"
            " WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"c": customer_id, "ids": sorted(set(document_ids))},
    ).all()
    return {
        str(row.id): "in_trash" if row.in_trash else "live" if row.is_current else "older_version"
        for row in rows
    }


def live_document_names(conn: Connection, customer_id: str, document_ids: Sequence[str]) -> dict[str, str]:
    """M3: the display name of each of ``document_ids`` that is live (the current version,
    out of Trash); one replaced, in Trash or deleted forever is missing. One query."""
    if not document_ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT id, display_name FROM documents WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
            " AND deleted_at IS NULL AND is_current"
        ),
        {"c": customer_id, "ids": sorted(set(document_ids))},
    ).all()
    return {str(row.id): str(row.display_name) for row in rows}


def add_pin(
    conn: Connection,
    customer_id: str,
    message_id: str,
    user_id: str,
    *,
    document_id: str,
    page_no: int,
    previous_rank: int,
    ref_id: str | None = None,
) -> bool:
    """Put one source on top of an answer in one of this user's conversations (0036). Every
    pick is kept as relevance feedback; the newest is the order shown. The document must be
    this customer's. False if there is no such answer or document for this user."""
    stored = conn.execute(
        text(
            "INSERT INTO source_pins"
            " (customer_id, message_id, user_id, document_id, page_no, source_ref, previous_rank)"
            " SELECT m.customer_id, m.id, :u, d.id, :page, :ref, :rank"
            " FROM messages m JOIN conversations cv"
            " ON cv.customer_id = m.customer_id AND cv.id = m.conversation_id"
            " JOIN documents d ON d.customer_id = m.customer_id AND d.id = :d"
            " WHERE m.customer_id = :c AND m.id = :m AND m.role = 'assistant' AND cv.user_id = :u"
            " RETURNING id"
        ),
        {
            "c": customer_id,
            "m": message_id,
            "u": user_id,
            "d": document_id,
            "page": page_no,
            "ref": ref_id,
            "rank": previous_rank,
        },
    ).first()
    return stored is not None


PIN_LIMIT = 50  # the asker's newest picks a question reads (answering/pins.py)


def pins_for(
    conn: Connection, customer_id: str, user_id: str, conversation_id: str | None
) -> Sequence[RowMapping]:
    """This user's newest pick of each of their answers (the table keeps every pick), on a
    live document (out of Trash, the current version): ``document_id``, ``page_no``,
    ``same`` (in ``conversation_id``) and ``question``, the answer's ``normalized_question``.
    Those of this conversation first, then the newest, at most ``PIN_LIMIT``. One query.

    ponytail: it reads every pick of this user in the customer (source_pins_message leads on
    the message, not the user); an index on (customer_id, user_id, created_at) if picks grow."""
    return (
        conn.execute(
            text(
                "SELECT p.document_id::text AS document_id, p.page_no,"
                " coalesce(p.conversation_id = CAST(:conv AS uuid), false) AS same, p.question"
                " FROM (SELECT DISTINCT ON (s.message_id) s.id, s.document_id, s.page_no, s.created_at,"
                " m.conversation_id, coalesce(m.answer ->> 'normalized_question', '') AS question"
                " FROM source_pins s"
                " JOIN messages m ON m.customer_id = s.customer_id AND m.id = s.message_id"
                " JOIN conversations cv ON cv.customer_id = m.customer_id AND cv.id = m.conversation_id"
                " AND cv.user_id = :u"
                " WHERE s.customer_id = :c AND s.user_id = :u"
                " ORDER BY s.message_id, s.created_at DESC, s.id) p"
                " JOIN documents d ON d.customer_id = :c AND d.id = p.document_id"
                " AND d.deleted_at IS NULL AND d.is_current"
                " ORDER BY same DESC, p.created_at DESC, p.id LIMIT :n"
            ),
            {"c": customer_id, "u": user_id, "conv": conversation_id, "n": PIN_LIMIT},
        )
        .mappings()
        .all()
    )


# PRD 6 F1: what went wrong, the boxes on Not useful, in the form's order (0050's CHECK).
REASONS = ("wrong_documents", "document_missing", "wrong_figure", "did_not_understand", "other")


def upsert_feedback(
    conn: Connection,
    customer_id: str,
    message_id: str,
    user_id: str,
    *,
    rating: str,
    reason: str | None,
    reason_codes: Sequence[str] = (),
    notice_shown: bool = False,
) -> bool:
    """Rate an assistant message in one of this user's conversations. One rating per
    (message, user): a second vote replaces the first, with its reasons and whether the
    notice was shown, and opens it again (no handled mark). False if there is no such
    message for this user."""
    stored = conn.execute(
        text(
            "INSERT INTO feedback"
            " (customer_id, message_id, user_id, rating, reason, reason_codes, notice_shown)"
            " SELECT m.customer_id, m.id, :u, :rating, :reason, CAST(:codes AS text[]), :notice"
            " FROM messages m JOIN conversations cv"
            " ON cv.customer_id = m.customer_id AND cv.id = m.conversation_id"
            " WHERE m.customer_id = :c AND m.id = :m AND m.role = 'assistant' AND cv.user_id = :u"
            " ON CONFLICT (message_id, user_id)"
            " DO UPDATE SET rating = EXCLUDED.rating, reason = EXCLUDED.reason,"
            " reason_codes = EXCLUDED.reason_codes, notice_shown = EXCLUDED.notice_shown,"
            " handled_at = NULL, handled_by = NULL, created_at = now()"
            " RETURNING id"
        ),
        {
            "c": customer_id,
            "m": message_id,
            "u": user_id,
            "rating": rating,
            "reason": reason,
            "codes": list(reason_codes),
            "notice": notice_shown,
        },
    ).first()
    return stored is not None
