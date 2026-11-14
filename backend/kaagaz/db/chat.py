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
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection, RowMapping, text

logger = logging.getLogger(__name__)

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
    " WHERE c.customer_id = :c AND c.user_id = :u AND EXISTS (SELECT 1 FROM visible_documents v"
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


def create_conversation(
    conn: Connection, customer_id: str, user_id: str, title: str, *, conversation_id: str | None = None
) -> str:
    """A new conversation; ``conversation_id``: the id named for it before (streaming Ask's
    ``started``, #535), else a new one."""
    made: object = conn.execute(
        text(
            "INSERT INTO conversations (id, customer_id, user_id, title)"
            " VALUES (COALESCE(CAST(:id AS uuid), gen_random_uuid()), :c, :u, :t) RETURNING id"
        ),
        {"id": conversation_id, "c": customer_id, "u": user_id, "t": title},
    ).scalar_one()
    return str(made)


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
    message_id: str | None = None,
) -> RowMapping:
    """Store one message. ``answer`` is set for assistant messages only (a table check);
    ``scope`` for a question asked inside one, ``rewritten_question`` for a follow-up (H1);
    ``message_id`` the id named for it before (streaming Ask's ``started``, #535), else a new one."""
    return (
        conn.execute(
            text(
                "INSERT INTO messages (id, customer_id, conversation_id, role, text, answer, scope,"
                " rewritten_question)"
                " VALUES (COALESCE(CAST(:id AS uuid), gen_random_uuid()), :c, :conv, :role, :text,"
                " CAST(:answer AS jsonb), CAST(:scope AS jsonb), :rq)"
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
                "id": message_id,
            },
        )
        .mappings()
        .one()
    )


def messages_with_feedback(
    conn: Connection, customer_id: str, conversation_id: str, user_id: str
) -> Sequence[RowMapping]:
    """Messages oldest first, each with this user's rating and newest source pick
    (``pin_document_id``, ``pin_page_no``; 0036) of a document they can still see (PRD 7,
    #602). One query (no N+1): the pick is one index lookup per message (source_pins_message)."""
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
                " AND EXISTS (SELECT 1 FROM visible_documents v WHERE v.customer_id = s.customer_id"
                " AND v.id = s.document_id)"
                " ORDER BY s.created_at DESC, s.id LIMIT 1) p ON true"
                " WHERE m.customer_id = :c AND m.conversation_id = :conv"
                " ORDER BY m.created_at, m.role DESC, m.id"
            ),
            {"c": customer_id, "conv": conversation_id, "u": user_id},
        )
        .mappings()
        .all()
    )


def openings(conn: Connection, customer_id: str, conversation_id: str) -> list[str | None]:
    """PRD 6 A: the stored opening of each of the conversation's answers, oldest first (None
    for an answer without one), so a line is never used twice in it. Clarifying questions are
    left out: none is shown, so the first real answer still greets by name. One query on
    (conversation_id, created_at), O(M log M) for its M messages."""
    return list(
        conn.execute(
            text(
                "SELECT m.answer ->> 'opening' FROM messages m"
                " WHERE m.customer_id = :c AND m.conversation_id = :conv AND m.role = 'assistant'"
                " AND m.answer ->> 'status' IS DISTINCT FROM 'clarify'"
                " ORDER BY m.created_at, m.id"
            ),
            {"c": customer_id, "conv": conversation_id},
        ).scalars()
    )


def repeated_answer(conn: Connection, customer_id: str, user_id: str, key: str) -> dict[str, Any] | None:
    """#535 (answering/ready.py ``repeat_key``): the newest stored answer of the last day saved with
    ``key`` (``repeat_key``), found (answered or unchecked), while no answer saved with it, the
    first or a copy served from it (``repeat_copy``), is rated Not useful; None when there is none.
    Only ``user_id``'s own conversations of the last day are read (the key holds the asker too),
    through their indexes (conversations_user, messages_conversation), so only their answers'
    JSON is opened. One statement."""
    found = conn.execute(
        text(
            "WITH mine AS (SELECT m.id, m.answer, m.created_at FROM conversations c"
            " JOIN messages m ON m.conversation_id = c.id AND m.customer_id = c.customer_id"
            " WHERE c.customer_id = :c AND c.user_id = :u AND c.created_at > now() - interval '1 day'"
            " AND m.role = 'assistant' AND m.created_at > now() - interval '1 day'"
            " AND (m.answer ->> 'repeat_key' = :k OR m.answer ->> 'repeat_copy' = :k))"
            " SELECT answer FROM mine WHERE answer ->> 'repeat_key' = :k"
            " AND answer ->> 'status' IN ('answered', 'unchecked') AND answer -> 'outage' IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM mine r JOIN feedback f ON f.customer_id = :c"
            " AND f.message_id = r.id WHERE f.rating = 'down')"
            " ORDER BY created_at DESC LIMIT 1"
        ),
        {"c": customer_id, "u": user_id, "k": key},
    ).scalar()
    return None if found is None else dict(found)


def saved_turn(
    conn: Connection, customer_id: str, question_id: str
) -> tuple[str, RowMapping, RowMapping | None] | None:
    """#535 (streaming Ask's ``question_id``): the question message with this id, as
    ``add_message`` returns it, its conversation's owner, and the answer saved with it (the first
    assistant message of its conversation from its time on: both are written in one
    transaction); None when this customer has no such message (row level security hides any
    other's). Two statements by primary key and the conversation's messages."""
    question = (
        conn.execute(
            text(
                "SELECT q.id, q.role, q.text, q.answer, q.scope, q.created_at, q.conversation_id,"
                " c.user_id AS owner FROM messages q JOIN conversations c"
                " ON c.id = q.conversation_id AND c.customer_id = q.customer_id"
                " WHERE q.customer_id = :c AND q.id = CAST(:id AS uuid)"
            ),
            {"c": customer_id, "id": question_id},
        )
        .mappings()
        .first()
    )
    if question is None:
        return None
    answer = (
        conn.execute(
            text(
                "SELECT id, role, text, answer, scope, created_at FROM messages"
                " WHERE customer_id = :c AND conversation_id = :conv AND role = 'assistant'"
                " AND created_at >= :at ORDER BY created_at, id LIMIT 1"
            ),
            {"c": customer_id, "conv": question["conversation_id"], "at": question["created_at"]},
        )
        .mappings()
        .first()
    )
    return str(question["owner"]), question, answer


def newest_message(conn: Connection, customer_id: str, conversation_id: str) -> str | None:
    """The conversation's newest message id, read after locking the conversation's row for the
    rest of the transaction, so concurrent replies to one clarify are taken one at a time."""
    params = {"c": customer_id, "conv": conversation_id}
    conn.execute(
        text("SELECT id FROM conversations WHERE customer_id = :c AND id = :conv FOR NO KEY UPDATE"), params
    )
    newest = conn.execute(
        text(
            "SELECT m.id::text FROM messages m WHERE m.customer_id = :c AND m.conversation_id = :conv"
            " ORDER BY m.created_at DESC, m.role, m.id DESC LIMIT 1"
        ),
        params,
    ).scalar()
    return None if newest is None else str(newest)


def recent_messages(
    conn: Connection, customer_id: str, conversation_id: str, limit: int
) -> Sequence[RowMapping]:
    """The conversation's newest ``limit`` messages, newest first (an answer before its
    question: both are written in one transaction, so they share ``created_at``). One
    query on (conversation_id, created_at), O(limit log M)."""
    return (
        conn.execute(
            text(
                "SELECT m.id, m.role, m.text, m.answer, m.scope FROM messages m"
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
    ``in_trash``. PRD 7 (#602, question 1): read through the view only, so a document
    missing from the result is one the person can no longer see, hidden or deleted
    forever alike (``views.answer_with_source_states`` then replaces the whole answer).
    One query, O(k log D) for k ids."""
    if not document_ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT id, deleted_at IS NOT NULL AS in_trash, is_current FROM visible_documents"
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
    out of Trash) and that the person can see; one replaced, in Trash, deleted forever or
    hidden (PRD 7) is missing. One query."""
    if not document_ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT id, display_name FROM visible_documents WHERE customer_id = :c"
            " AND id = ANY(CAST(:ids AS uuid[]))"
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
            " JOIN visible_documents d ON d.customer_id = m.customer_id AND d.id = :d"
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
    live document (out of Trash, the current version) they can see: ``document_id``, ``page_no``,
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
                " JOIN visible_documents d ON d.customer_id = :c AND d.id = p.document_id"
                " AND d.deleted_at IS NULL AND d.is_current"
                " ORDER BY same DESC, p.created_at DESC, p.id LIMIT :n"
            ),
            {"c": customer_id, "u": user_id, "conv": conversation_id, "n": PIN_LIMIT},
        )
        .mappings()
        .all()
    )


LEARN_LIMIT = 200  # PRD 6 F3: the newest Useful marks a question reads (Wrong documents: all)
LEARN_DAYS = 90  # PRD 6 F3: a Useful or Wrong documents mark older than this teaches no more

# PRD 6 F3 (answering/pins.py): each cited page of an answered message rated Useful, or Not
# useful for wrong documents, in the last :days days, on a live current document that no
# admin removed since (learning_removals). Every person's in the customer (RLS and :c), on a
# document the asker can see (PRD 7, #602; it narrows the Admin's "What Kaagaz learned" list
# too, until PR 10's owner-rights functions: question 5).
MARKS = (
    "SELECT f.user_id, f.rating, f.created_at, c.document_id, c.page_no, d.display_name,"
    " coalesce(m.answer ->> 'normalized_question', '') AS question"
    " FROM feedback f"
    " JOIN messages m ON m.customer_id = f.customer_id AND m.id = f.message_id"
    " CROSS JOIN LATERAL (SELECT DISTINCT CAST(e ->> 'document_id' AS uuid) AS document_id,"
    " CAST(e ->> 'page_no' AS integer) AS page_no FROM jsonb_array_elements(CASE WHEN"
    " jsonb_typeof(m.answer -> 'citations') = 'array' THEN m.answer -> 'citations' ELSE '[]' END) e"
    " WHERE e ->> 'document_id' ~ '^[0-9a-f-]{36}$' AND e ->> 'page_no' ~ '^[0-9]{1,6}$') c"
    " JOIN visible_documents d ON d.customer_id = f.customer_id AND d.id = c.document_id"
    " AND d.deleted_at IS NULL AND d.is_current"
    " WHERE f.customer_id = :c AND f.created_at >= now() - make_interval(days => :days)"
    " AND m.answer ->> 'status' = 'answered'"
    " AND (f.rating = 'up' OR 'wrong_documents' = ANY (f.reason_codes))"
    " AND NOT EXISTS (SELECT 1 FROM learning_removals r WHERE r.customer_id = f.customer_id"
    " AND r.document_id = c.document_id AND r.removed_at >= f.created_at)"
)
# Every Wrong documents mark (never capped, so a stop never lapses), then the newest Useful
# ones, one more than LEARN_LIMIT so learned_for can tell when the cap cut the window short.
_LEARNED_SQL = (
    "SELECT * FROM (SELECT x.document_id::text AS document_id, x.page_no, x.question, false AS useful,"
    " x.user_id::text AS user_id, x.user_id = CAST(:u AS uuid) AS own, x.created_at FROM ({marks}) x"
    " WHERE x.rating = 'down') w"
    " UNION ALL SELECT * FROM (SELECT x.document_id::text, x.page_no, x.question, true, x.user_id::text,"
    " x.user_id = CAST(:u AS uuid), x.created_at FROM ({marks}) x WHERE x.rating = 'up'"
    " ORDER BY x.created_at DESC, x.document_id, x.page_no LIMIT :n) u"
)
_LEARNED = text(_LEARNED_SQL.format(marks=MARKS))  # fixed fragments only, as db/models.py's


def learned_for(conn: Connection, customer_id: str, user_id: str) -> list[RowMapping]:
    """PRD 6 F3: every Wrong documents mark (MARKS) and the newest LEARN_LIMIT Useful ones (more
    in the window: logged, a code only); ``useful``, ``user_id`` and ``own`` say whose. One query."""
    params = {"c": customer_id, "u": user_id, "days": LEARN_DAYS, "n": LEARN_LIMIT + 1}
    rows = conn.execute(_LEARNED, params).mappings().all()
    useful = sorted((row for row in rows if row["useful"]), key=lambda row: row["created_at"], reverse=True)
    if len(useful) > LEARN_LIMIT:
        logger.warning("learned marks capped", extra={"code": "learn_limit"})
    return [row for row in rows if not row["useful"]] + useful[:LEARN_LIMIT]


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
