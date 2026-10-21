"""Customer-shared words, submitted by members and decided by reviewers."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Connection, RowMapping, text

MAX_ORGANIZATION_WORDS = 500


@dataclass(frozen=True, slots=True)
class Suggestion:
    id: str
    word: str
    meaning: str
    status: Literal["pending", "approved", "rejected"]
    submitted_by: str
    created_at: datetime


def approved(conn: Connection, customer_id: str) -> list[Suggestion]:
    rows = conn.execute(
        text(
            "SELECT id, word, meaning, status, submitted_by, created_at FROM organization_word_suggestions"
            " WHERE customer_id = :c AND status = 'approved' ORDER BY word_fold, meaning_fold, id LIMIT 500"
        ),
        {"c": customer_id},
    ).mappings()
    return [_suggestion(row) for row in rows]


def visible(conn: Connection, customer_id: str, user_id: str, *, reviewer: bool) -> list[Suggestion]:
    rows = conn.execute(
        text(
            "SELECT id, word, meaning, status, submitted_by, created_at FROM organization_word_suggestions"
            " WHERE customer_id = :c AND"
            " (status = 'approved' OR submitted_by = :u OR (:reviewer AND status = 'pending'))"
            " ORDER BY status, word_fold, meaning_fold, id LIMIT 500"
        ),
        {"c": customer_id, "u": user_id, "reviewer": reviewer},
    ).mappings()
    return [_suggestion(row) for row in rows]


def submit(
    conn: Connection,
    customer_id: str,
    user_id: str,
    *,
    word: str,
    word_fold: str,
    meaning: str,
    meaning_fold: str,
) -> Suggestion | Literal["too_many_words"] | None:
    conn.execute(text("SELECT id FROM customers WHERE id = :c FOR NO KEY UPDATE"), {"c": customer_id})
    duplicate = conn.execute(
        text(
            "SELECT 1 FROM organization_word_suggestions WHERE customer_id = :c"
            " AND word_fold = :wf AND meaning_fold = :mf AND status IN ('pending', 'approved')"
        ),
        {"c": customer_id, "wf": word_fold, "mf": meaning_fold},
    ).first()
    if duplicate is not None:
        return None
    active = conn.execute(
        text(
            "SELECT count(*) FROM organization_word_suggestions WHERE customer_id = :c"
            " AND status IN ('pending', 'approved')"
        ),
        {"c": customer_id},
    ).scalar_one()
    if active >= MAX_ORGANIZATION_WORDS:
        return "too_many_words"
    row = conn.execute(
        text(
            "INSERT INTO organization_word_suggestions (customer_id, submitted_by, word, word_fold,"
            " meaning, meaning_fold)"
            " VALUES (:c, :u, :w, :wf, :m, :mf) ON CONFLICT DO NOTHING"
            " RETURNING id, word, meaning, status, submitted_by, created_at"
        ),
        {"c": customer_id, "u": user_id, "w": word, "wf": word_fold, "m": meaning, "mf": meaning_fold},
    ).mappings().first()
    return None if row is None else _suggestion(row)


def decide(
    conn: Connection,
    customer_id: str,
    suggestion_id: str,
    reviewer_id: str,
    *,
    status: Literal["approved", "rejected"],
) -> Suggestion | None:
    row = conn.execute(
        text(
            "UPDATE organization_word_suggestions SET status = :status, decided_by = :u, decided_at = now()"
            " WHERE customer_id = :c AND id = :id AND status = 'pending' AND submitted_by <> :u"
            " RETURNING id, word, meaning, status, submitted_by, created_at"
        ),
        {"c": customer_id, "id": suggestion_id, "u": reviewer_id, "status": status},
    ).mappings().first()
    return None if row is None else _suggestion(row)


def _suggestion(row: RowMapping) -> Suggestion:
    return Suggestion(
        id=str(row["id"]),
        word=row["word"],
        meaning=row["meaning"],
        status=row["status"],
        submitted_by=str(row["submitted_by"]),
        created_at=row["created_at"],
    )
