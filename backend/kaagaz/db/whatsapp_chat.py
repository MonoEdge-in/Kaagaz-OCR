"""The WhatsApp chat's SQL (#603 W4, PRD 7 section 15), each in the person's own transaction
(``db.user``), so every read goes through PRD 7's views and rule as the browser's does.

* ``current``: the person's newest WhatsApp conversation (``conversations.channel``) and its newest
  message: a follow-up within the window continues it, and an open "Did you mean" is answered in it.
* ``on_whatsapp``: which of some documents may be named on WhatsApp: the person sees them (the
  view), they are out of Trash, and they are not locked (a locked file never appears on WhatsApp,
  even for a person who may open it).
* ``shared_with_me``: whether a document was given to the person directly ("Shared with you").
* ``holds_lock_grant``: whether the person may open any locked file (until PRD 7 PR 12, Ask still
  reads those: the chat then leaves out what cites no file, kaagaz/channel/chat.py).
* ``step``, ``set_step``, ``clear_step``: what the next message answers ("Which file?"), kept in the
  person's ``whatsapp_pickers`` row (``own_user_only``) until it expires; it holds no id.

Each is one statement on an index: O(log n), ``on_whatsapp`` O(k log n) for k ids.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text


@dataclass(frozen=True, slots=True)
class Current:
    """The newest WhatsApp conversation: its id, its newest message (id, role, an assistant
    message's status) and whether that message is within the window."""

    id: str
    message_id: str
    role: str
    status: str | None
    fresh: bool

    @property
    def clarify(self) -> bool:
        return self.role == "assistant" and self.status == "clarify"


# On conversations_user, then the conversation's messages (newest first, as db/chat.py's).
_CURRENT = text(
    "SELECT c.id, m.id AS message_id, m.role, m.answer->>'status' AS status,"
    " m.created_at > now() - make_interval(mins => :minutes) AS fresh"
    " FROM conversations c CROSS JOIN LATERAL (SELECT id, role, answer, created_at FROM messages"
    "  WHERE customer_id = c.customer_id AND conversation_id = c.id"
    "  ORDER BY created_at DESC, role, id DESC LIMIT 1) m"
    " WHERE c.customer_id = :c AND c.user_id = :u AND c.channel = 'whatsapp'"
    " ORDER BY c.created_at DESC, c.id DESC LIMIT 1"
)
_ON_WHATSAPP = text(
    "SELECT id FROM visible_documents WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
    " AND deleted_at IS NULL AND locked_at IS NULL"
)
_SHARED = text("SELECT CAST(:d AS uuid) IN (SELECT public.kaagaz_shared_with_me())")
_LOCK_GRANT = text("SELECT EXISTS (SELECT 1 FROM public.kaagaz_lock_open_to_me())")
_STEP = text(
    "SELECT state FROM whatsapp_pickers WHERE customer_id = :c AND user_id = :u AND expires_at > now()"
)
_SET_STEP = text(
    "INSERT INTO whatsapp_pickers AS p (customer_id, user_id, state, expires_at)"
    " VALUES (:c, :u, CAST(:state AS jsonb), now() + make_interval(mins => :minutes))"
    " ON CONFLICT (customer_id, user_id) DO UPDATE SET state = EXCLUDED.state,"
    " expires_at = EXCLUDED.expires_at, generation = p.generation + 1"
)
_CLEAR_STEP = text("DELETE FROM whatsapp_pickers WHERE customer_id = :c AND user_id = :u")


def current(conn: Connection, customer_id: str, user_id: str, minutes: int) -> Current | None:
    row = conn.execute(_CURRENT, {"c": customer_id, "u": user_id, "minutes": minutes}).mappings().first()
    if row is None:
        return None
    return Current(
        str(row["id"]), str(row["message_id"]), str(row["role"]), row["status"], bool(row["fresh"])
    )


def on_whatsapp(conn: Connection, customer_id: str, document_ids: Sequence[str]) -> set[str]:
    if not document_ids:
        return set()
    rows = conn.execute(_ON_WHATSAPP, {"c": customer_id, "ids": sorted(set(document_ids))})
    return {str(row[0]) for row in rows}


def shared_with_me(conn: Connection, document_id: str) -> bool:
    return bool(conn.execute(_SHARED, {"d": document_id}).scalar_one())


def holds_lock_grant(conn: Connection) -> bool:
    return bool(conn.execute(_LOCK_GRANT).scalar_one())


def step(conn: Connection, customer_id: str, user_id: str) -> dict[str, Any] | None:
    found = conn.execute(_STEP, {"c": customer_id, "u": user_id}).scalar()
    return found if isinstance(found, dict) else None


def set_step(conn: Connection, customer_id: str, user_id: str, state: dict[str, Any], minutes: int) -> None:
    params = {"c": customer_id, "u": user_id, "state": json.dumps(state), "minutes": minutes}
    conn.execute(_SET_STEP, params)


def clear_step(conn: Connection, customer_id: str, user_id: str) -> None:
    conn.execute(_CLEAR_STEP, {"c": customer_id, "u": user_id})
