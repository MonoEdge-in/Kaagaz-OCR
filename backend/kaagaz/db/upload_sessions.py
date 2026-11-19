"""Customer-scoped state for uploads that are still being transferred."""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from kaagaz.db.engine import Database


def create(
    conn: Connection,
    *,
    customer_id: str,
    user_id: str,
    session_id: str,
    storage_key: str,
    storage_upload_id: str,
    file_size: int,
    part_count: int,
    metadata: dict[str, object],
) -> None:
    conn.execute(
        text(
            "INSERT INTO upload_sessions"
            " (id, customer_id, user_id, storage_key, storage_upload_id, file_size, part_count,"
            " metadata, expires_at)"
            " VALUES (:id, :customer, :user, :key, :upload, :size, :parts, CAST(:metadata AS jsonb),"
            " now() + interval '7 days')"
        ),
        {
            "id": session_id,
            "customer": customer_id,
            "user": user_id,
            "key": storage_key,
            "upload": storage_upload_id,
            "size": file_size,
            "parts": part_count,
            "metadata": json.dumps(metadata),
        },
    )


def get(
    conn: Connection, customer_id: str, user_id: str, session_id: str, *, finished: bool = False
) -> dict[str, Any] | None:
    """The session; one already completed (``metadata.completed``, ``complete``) only with
    ``finished``: it can't be resumed or sent to."""
    row = (
        conn.execute(
            text(
                "SELECT id, customer_id, user_id, storage_key, storage_upload_id, file_size, part_count,"
                " metadata FROM upload_sessions WHERE customer_id = :customer AND user_id = :user"
                " AND id = :id AND expires_at > now()"
                " AND (CAST(:finished AS boolean) OR metadata -> 'completed' IS NULL)"
            ),
            {"customer": customer_id, "user": user_id, "id": session_id, "finished": finished},
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)


def candidates(
    conn: Connection,
    customer_id: str,
    user_id: str,
    filename: str,
    size: int,
    fingerprint: str,
    *,
    batch_id: str | None = None,
    item_id: str | None = None,
    replace: bool = False,
) -> list[dict[str, object]]:
    """Sessions of this file to resume. A finished one only for its own drop (``batch_id``) or
    folder upload item (review of #564): a Retry after a lost reply, which resumes it and gets the
    stored reply; a new drop of the same file is a new upload. #697: only sessions that ask the
    same of a copy in Trash (``replace``), so Upload as new file never gets the drop's old reply."""
    rows = conn.execute(
        text(
            "SELECT id, metadata FROM upload_sessions WHERE customer_id = :customer AND user_id = :user"
            " AND file_size = :size AND metadata->>'filename' = :filename"
            " AND metadata->>'fingerprint' = :fingerprint AND expires_at > now()"
            " AND (metadata -> 'completed' IS NULL OR metadata ->> 'batch_id' = :batch"
            " OR metadata ->> 'folder_upload_item_id' = :item)"
            " AND coalesce(CAST(metadata ->> 'replace_trash' AS boolean), false) = :replace"
            " ORDER BY created_at DESC LIMIT 5"
        ),
        {
            "customer": customer_id,
            "user": user_id,
            "size": size,
            "filename": filename,
            "fingerprint": fingerprint,
            "batch": batch_id,
            "item": item_id,
            "replace": replace,
        },
    ).mappings()
    return [{"id": str(row["id"]), "parts": row["metadata"].get("parts", {})} for row in rows]


def set_part_hash(
    conn: Connection, customer_id: str, user_id: str, session_id: str, part: int, digest: str
) -> None:
    conn.execute(
        text(
            "UPDATE upload_sessions SET metadata = jsonb_set(metadata, ARRAY['parts', CAST(:part AS text)],"
            " to_jsonb(CAST(:digest AS text)), true),"
            " expires_at = now() + interval '7 days'"
            " WHERE customer_id = :customer AND user_id = :user AND id = :id"
        ),
        {"customer": customer_id, "user": user_id, "id": session_id, "part": str(part), "digest": digest},
    )


# The browser's wait for a complete (lib/api.ts COMPLETE_TIMEOUT_MS): a claim older than this
# is a crashed complete's, taken over.
COMPLETING_SECONDS = 300


def claim(conn: Connection, customer_id: str, user_id: str, session_id: str) -> bool:
    """Review of #564: mark the session as being completed (``metadata.completing``), unless it
    is finished or another complete holds a claim younger than COMPLETING_SECONDS. True when
    this call holds it. One UPDATE on the row: two completes never both hold it."""
    claimed = conn.execute(
        text(
            "UPDATE upload_sessions SET metadata = jsonb_set(metadata, '{completing}', to_jsonb(now()))"
            " WHERE customer_id = :customer AND user_id = :user AND id = :id AND expires_at > now()"
            " AND metadata -> 'completed' IS NULL AND (metadata -> 'completing' IS NULL"
            " OR CAST(metadata ->> 'completing' AS timestamptz) < now() - make_interval(secs => :stale))"
            " RETURNING id"
        ),
        {"customer": customer_id, "user": user_id, "id": session_id, "stale": COMPLETING_SECONDS},
    ).first()
    return claimed is not None


def release(conn: Connection, customer_id: str, user_id: str, session_id: str) -> None:
    """A failed complete's claim, freed for the next one."""
    conn.execute(
        text(
            "UPDATE upload_sessions SET metadata = metadata - 'completing'"
            " WHERE customer_id = :customer AND user_id = :user AND id = :id"
        ),
        {"customer": customer_id, "user": user_id, "id": session_id},
    )


def complete(
    conn: Connection, customer_id: str, user_id: str, session_id: str, reply: dict[str, Any]
) -> None:
    """Production QA T1: the session is kept a day after its file is stored, with the reply, so a
    ``complete`` sent again after a lost reply gets the same document; the expired-session
    purge (jobs/purge.py) removes it then, and its parts if they are still there."""
    conn.execute(
        text(
            "UPDATE upload_sessions"
            " SET metadata = jsonb_set(metadata - 'completing', '{completed}', CAST(:reply AS jsonb)),"
            " expires_at = now() + interval '1 day'"
            " WHERE customer_id = :customer AND user_id = :user AND id = :id"
        ),
        {"customer": customer_id, "user": user_id, "id": session_id, "reply": json.dumps(reply)},
    )


def delete(conn: Connection, customer_id: str, user_id: str, session_id: str) -> None:
    conn.execute(
        text("DELETE FROM upload_sessions WHERE customer_id = :customer AND user_id = :user AND id = :id"),
        {"customer": customer_id, "user": user_id, "id": session_id},
    )


def of_customer(conn: Connection, customer_id: str) -> list[tuple[str, str]]:
    """(storage key, upload id) of every upload session of this customer, open or expired, for
    deleting the organisation (#482). On ``upload_sessions_owner_size``: O(log n + s)."""
    rows = conn.execute(
        text("SELECT storage_key, storage_upload_id FROM upload_sessions WHERE customer_id = :customer"),
        {"customer": customer_id},
    )
    return [(str(key), str(upload_id)) for key, upload_id in rows]


def delete_expired(conn: Connection, customer_id: str, session_id: str, before: datetime) -> None:
    conn.execute(
        text(
            "DELETE FROM upload_sessions WHERE customer_id = :customer AND id = :id AND expires_at <= :before"
        ),
        {"customer": customer_id, "id": session_id, "before": before},
    )


def expired(db: Database, before: datetime, limit: int = 100) -> list[dict[str, object]]:
    statement = text("SELECT * FROM kaagaz_expired_upload_sessions(:before, :limit)")
    with db.system() as conn:
        return [dict(row) for row in conn.execute(statement, {"before": before, "limit": limit}).mappings()]
