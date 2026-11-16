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
    document_id: str | None
    page_no: int | None


def approved(conn: Connection, customer_id: str) -> list[Suggestion]:
    """The organisation's approved words, and those made on a page of a document the person can
    see, in any scope (PRD 7, #602, decision 9; changed on purpose: a word made on a page helped
    inside its document only). One the person can't see never helps or is echoed, so a hidden
    scope id answers as a random one (understood_words)."""
    rows = conn.execute(
        text(
            "SELECT id, word, meaning, status, submitted_by, created_at, document_id, page_no"
            " FROM organization_word_suggestions WHERE customer_id = :c AND status = 'approved'"
            " AND (document_id IS NULL OR EXISTS (SELECT 1 FROM visible_documents d"
            " WHERE d.customer_id = :c AND d.id = document_id))"
            " ORDER BY word_fold, meaning_fold, id LIMIT 500"
        ),
        {"c": customer_id},
    ).mappings()
    return [_suggestion(row) for row in rows]


def visible(conn: Connection, customer_id: str, user_id: str, *, reviewer: bool) -> list[Suggestion]:
    rows = conn.execute(
        text(
            "SELECT id, word, meaning, status, submitted_by, created_at, document_id, page_no"
            " FROM organization_word_suggestions"
            " WHERE customer_id = :c AND"
            " (status = 'approved' OR submitted_by = :u OR (:reviewer AND status = 'pending'))"
            # PRD 7 (#599): one made on a page shows only while its document is one the person sees.
            " AND (document_id IS NULL OR EXISTS (SELECT 1 FROM visible_documents d"
            " WHERE d.customer_id = :c AND d.id = document_id))"
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
    document_id: str | None = None,
    page_no: int | None = None,
) -> Suggestion | Literal["too_many_words"] | None:
    conn.execute(text("SELECT id FROM customers WHERE id = :c FOR NO KEY UPDATE"), {"c": customer_id})
    duplicate = conn.execute(
        text(
            "SELECT 1 FROM organization_word_suggestions WHERE customer_id = :c"
            # #602, decision 9: the organisation's own word, or the same document's.
            " AND (document_id IS NULL OR document_id IS NOT DISTINCT FROM CAST(:doc AS uuid))"
            " AND word_fold = :wf AND meaning_fold = :mf AND status IN ('pending', 'approved')"
        ),
        {"c": customer_id, "doc": document_id, "wf": word_fold, "mf": meaning_fold},
    ).first()
    if duplicate is not None:
        return None
    active = conn.execute(
        text(
            # PRD 7 (#606): the whole organisation's (0091), so the cap can't fail open.
            "SELECT kaagaz_word_suggestions_count() WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
        ),
        {"c": customer_id},
    ).scalar_one()
    if active >= MAX_ORGANIZATION_WORDS:
        return "too_many_words"
    row = (
        conn.execute(
            text(
                "INSERT INTO organization_word_suggestions (customer_id, submitted_by, word, word_fold,"
                " meaning, meaning_fold, document_id, page_no)"
                " VALUES (:c, :u, :w, :wf, :m, :mf, CAST(:doc AS uuid), :page) ON CONFLICT DO NOTHING"
                " RETURNING id, word, meaning, status, submitted_by, created_at, document_id, page_no"
            ),
            {
                "c": customer_id,
                "u": user_id,
                "w": word,
                "wf": word_fold,
                "m": meaning,
                "mf": meaning_fold,
                "doc": document_id,
                "page": page_no,
            },
        )
        .mappings()
        .first()
    )
    return None if row is None else _suggestion(row)


def decide(
    conn: Connection,
    customer_id: str,
    suggestion_id: str,
    reviewer_id: str,
    *,
    status: Literal["approved", "rejected"],
) -> Suggestion | None:
    row = (
        conn.execute(
            text(
                "UPDATE organization_word_suggestions SET status = :status,"
                " decided_by = :u, decided_at = now()"
                " WHERE customer_id = :c AND id = :id AND status = 'pending' AND submitted_by <> :u"
                # PRD 7 (#599): one made on a page of a file the person can't see is not theirs to decide.
                " AND (document_id IS NULL OR EXISTS (SELECT 1 FROM visible_documents d"
                " WHERE d.customer_id = :c AND d.id = document_id))"
                " RETURNING id, word, meaning, status, submitted_by, created_at, document_id, page_no"
            ),
            {"c": customer_id, "id": suggestion_id, "u": reviewer_id, "status": status},
        )
        .mappings()
        .first()
    )
    return None if row is None else _suggestion(row)


def _suggestion(row: RowMapping) -> Suggestion:
    return Suggestion(
        id=str(row["id"]),
        word=row["word"],
        meaning=row["meaning"],
        status=row["status"],
        submitted_by=str(row["submitted_by"]),
        created_at=row["created_at"],
        document_id=None if row["document_id"] is None else str(row["document_id"]),
        page_no=row["page_no"],
    )
