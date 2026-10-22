"""Customer-scoped state for uploads that are still being transferred."""

import json
from datetime import datetime

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


def get(conn: Connection, customer_id: str, user_id: str, session_id: str) -> dict[str, object] | None:
    row = conn.execute(
        text(
            "SELECT id, customer_id, user_id, storage_key, storage_upload_id, file_size, part_count, metadata"
            " FROM upload_sessions WHERE customer_id = :customer AND user_id = :user AND id = :id"
            " AND expires_at > now()"
        ),
        {"customer": customer_id, "user": user_id, "id": session_id},
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def candidates(
    conn: Connection, customer_id: str, user_id: str, filename: str, size: int, fingerprint: str
) -> list[dict[str, object]]:
    rows = conn.execute(
        text(
            "SELECT id, metadata FROM upload_sessions WHERE customer_id = :customer AND user_id = :user"
            " AND file_size = :size AND metadata->>'filename' = :filename"
            " AND metadata->>'fingerprint' = :fingerprint AND expires_at > now()"
            " ORDER BY created_at DESC LIMIT 5"
        ),
        {
            "customer": customer_id,
            "user": user_id,
            "size": size,
            "filename": filename,
            "fingerprint": fingerprint,
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


def delete(conn: Connection, customer_id: str, user_id: str, session_id: str) -> None:
    conn.execute(
        text("DELETE FROM upload_sessions WHERE customer_id = :customer AND user_id = :user AND id = :id"),
        {"customer": customer_id, "user": user_id, "id": session_id},
    )


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
