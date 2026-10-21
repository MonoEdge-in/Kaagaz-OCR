"""Organization word suggestions (NB-32): members submit, reviewers and admins decide.

Approved words are available to everyone in that customer. Existing personal words stay
private and unchanged.

Revision ID: 0043
Revises: 0042
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"

UPGRADE = [
    """
    CREATE TABLE organization_word_suggestions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
        submitted_by uuid NOT NULL,
        word text NOT NULL CHECK (char_length(word) BETWEEN 1 AND 40),
        word_fold text NOT NULL CHECK (char_length(word_fold) >= 1),
        meaning text NOT NULL CHECK (char_length(meaning) BETWEEN 1 AND 80),
        meaning_fold text NOT NULL CHECK (char_length(meaning_fold) >= 1),
        status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
        decided_by uuid NULL,
        decided_at timestamptz NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (customer_id, id),
        FOREIGN KEY (customer_id, submitted_by) REFERENCES users(customer_id, id),
        FOREIGN KEY (customer_id, decided_by) REFERENCES users(customer_id, id),
        CHECK ((status = 'pending' AND decided_by IS NULL AND decided_at IS NULL)
            OR (status <> 'pending' AND decided_by IS NOT NULL AND decided_at IS NOT NULL))
    )
    """,
    "CREATE UNIQUE INDEX organization_word_suggestions_active_meaning"
    " ON organization_word_suggestions (customer_id, word_fold, meaning_fold)"
    " WHERE status IN ('pending', 'approved')",
    "CREATE INDEX organization_word_suggestions_pending"
    " ON organization_word_suggestions (customer_id, created_at, id) WHERE status = 'pending'",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON organization_word_suggestions TO {APP_ROLE}",
    "ALTER TABLE organization_word_suggestions ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY customer_only ON organization_word_suggestions"
    " USING (customer_id = kaagaz_current_customer())"
    " WITH CHECK (customer_id = kaagaz_current_customer())",
    "COMMENT ON TABLE organization_word_suggestions IS 'Organization vocabulary: members may submit;"
    " reviewers and admins decide; maximum 500 pending or approved entries per customer.'",
    "COMMENT ON COLUMN organization_word_suggestions.id IS 'Suggestion id.'",
    "COMMENT ON COLUMN organization_word_suggestions.customer_id IS 'The customer that owns this"
    " organization vocabulary.'",
    "COMMENT ON COLUMN organization_word_suggestions.submitted_by IS 'The customer user who suggested the word.'",
    "COMMENT ON COLUMN organization_word_suggestions.word IS 'The suggested word as typed, 1 to 40 characters.'",
    "COMMENT ON COLUMN organization_word_suggestions.word_fold IS 'Unicode-folded word used to find duplicates.'",
    "COMMENT ON COLUMN organization_word_suggestions.meaning IS 'The suggested meaning in English, 1 to 80"
    " characters.'",
    "COMMENT ON COLUMN organization_word_suggestions.meaning_fold IS 'Unicode-folded meaning used to find duplicates.'",
    "COMMENT ON COLUMN organization_word_suggestions.status IS 'pending for review, approved for all"
    " users, or rejected.'",
    "COMMENT ON COLUMN organization_word_suggestions.decided_by IS 'Reviewer or admin who approved or"
    " rejected this suggestion.'",
    "COMMENT ON COLUMN organization_word_suggestions.decided_at IS 'When the suggestion was approved or rejected.'",
    "COMMENT ON COLUMN organization_word_suggestions.created_at IS 'When the suggestion was submitted.'",
]

DOWNGRADE = [
    "DROP TABLE organization_word_suggestions",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
