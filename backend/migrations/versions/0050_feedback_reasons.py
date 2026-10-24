"""Useful and Not useful (PRD 6 package F1): why an answer was not useful, whether the
person was told their admin sees it, and whether an admin has handled it.

``feedback`` gains four columns; nothing else changes, and no row is rewritten:

* ``reason_codes``: what went wrong, the boxes the person ticked (``REASONS``), none on a
  Useful rating (a CHECK); the typed ``reason`` (at most 500 characters) stays beside it.
* ``notice_shown``: the form said "Your company admin can see this answer, your question,
  your reason and your name." when it was sent (decision 7, 24 Oct 2026): the admin's
  Answer quality page lists only these, so nothing given before the notice is shown.
* ``handled_at``, ``handled_by``: an admin marked it handled. ``handled_by`` points at the
  user with (customer_id, id) ON DELETE SET NULL (handled_by), so deleting that admin keeps
  the row and the time (a CHECK: no one without a time).

A partial index (customer_id, created_at) of the Not useful rows given after the notice
serves the admin's page. The app's grants on ``feedback`` (0001) already cover all of it.

The downgrade refuses (check_violation) while any row holds a reason code, the notice or a
handled mark: dropping them would lose what people said. It then drops the index, the
columns and their constraints, and puts 0007's comments back.

Revision ID: 0050
Revises: 0049 (land facts)
"""

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "10s"  # as env.py's
# PRD 6: the reasons, in the order the form shows them (backend/kaagaz/db/chat.py REASONS).
REASONS = ("wrong_documents", "document_missing", "wrong_figure", "did_not_understand", "other")
_REASONS_SQL = "ARRAY[" + ", ".join(f"'{code}'" for code in REASONS) + "]::text[]"


def _literal(comment: str) -> str:
    return "'" + comment.replace("'", "''") + "'"


TABLE_COMMENT = (
    "Useful (up) or Not useful (down) on an answer, one per user and message. A new rating"
    " replaces the old one, with its reasons, and clears a handled mark."
)
OLD_TABLE_COMMENT = "Thumbs up or down on an answer, one per user and message. A new vote replaces the old."
COLUMNS = {
    "rating": "up (Useful) or down (Not useful).",
    "reason_codes": (
        "What went wrong, the boxes ticked on Not useful: wrong_documents, document_missing,"
        " wrong_figure, did_not_understand, other. Empty on Useful."
    ),
    "notice_shown": (
        "true when the form told the person their company admin can see the answer, the"
        " question, the reason and their name. Only these reach the admin's Answer quality page."
    ),
    "handled_at": "When an admin marked it handled. NULL = open.",
    "handled_by": "→ users.id (same customer), the admin who marked it handled. NULL when open or that user is gone.",
}
OLD_RATING_COMMENT = "up or down."

UPGRADE = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    f"""
    ALTER TABLE feedback
        ADD COLUMN reason_codes text[] NOT NULL DEFAULT '{{}}',
        ADD COLUMN notice_shown boolean NOT NULL DEFAULT false,
        ADD COLUMN handled_at timestamptz NULL,
        ADD COLUMN handled_by uuid NULL,
        ADD CONSTRAINT feedback_reason_codes_known CHECK (reason_codes <@ {_REASONS_SQL}),
        ADD CONSTRAINT feedback_reasons_on_down CHECK (rating = 'down' OR cardinality(reason_codes) = 0),
        ADD CONSTRAINT feedback_handled_by_has_time CHECK (handled_by IS NULL OR handled_at IS NOT NULL),
        ADD CONSTRAINT feedback_handled_by_fk FOREIGN KEY (customer_id, handled_by)
            REFERENCES users (customer_id, id) ON DELETE SET NULL (handled_by)
    """,
    "CREATE INDEX feedback_not_useful ON feedback (customer_id, created_at DESC)"
    " WHERE rating = 'down' AND notice_shown",
    f"COMMENT ON TABLE feedback IS {_literal(TABLE_COMMENT)}",
    *[f"COMMENT ON COLUMN feedback.{column} IS {_literal(comment)}" for column, comment in COLUMNS.items()],
]

DOWNGRADE = [
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM feedback WHERE cardinality(reason_codes) > 0 OR notice_shown
                   OR handled_at IS NOT NULL) THEN
            RAISE EXCEPTION 'feedback holds reasons, the notice or a handled mark; downgrading would lose them'
                USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    "DROP INDEX IF EXISTS feedback_not_useful",
    """
    ALTER TABLE feedback
        DROP CONSTRAINT IF EXISTS feedback_handled_by_fk,
        DROP CONSTRAINT IF EXISTS feedback_handled_by_has_time,
        DROP CONSTRAINT IF EXISTS feedback_reasons_on_down,
        DROP CONSTRAINT IF EXISTS feedback_reason_codes_known,
        DROP COLUMN IF EXISTS handled_by,
        DROP COLUMN IF EXISTS handled_at,
        DROP COLUMN IF EXISTS notice_shown,
        DROP COLUMN IF EXISTS reason_codes
    """,
    f"COMMENT ON TABLE feedback IS {_literal(OLD_TABLE_COMMENT)}",
    f"COMMENT ON COLUMN feedback.rating IS {_literal(OLD_RATING_COMMENT)}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
