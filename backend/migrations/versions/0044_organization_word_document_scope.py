"""Scope viewer word suggestions to the document they came from.

Revision ID: 0044
Revises: 0043
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

UPGRADE = [
    "ALTER TABLE organization_word_suggestions ADD COLUMN document_id uuid NULL",
    "ALTER TABLE organization_word_suggestions ADD COLUMN page_no integer NULL",
    "ALTER TABLE organization_word_suggestions ADD CONSTRAINT organization_word_suggestions_document_fk"
    " FOREIGN KEY (customer_id, document_id) REFERENCES documents(customer_id, id) ON DELETE CASCADE",
    "ALTER TABLE organization_word_suggestions ADD CONSTRAINT"
    " organization_word_suggestions_document_page_check"
    " CHECK ((document_id IS NULL) = (page_no IS NULL) AND (page_no IS NULL OR page_no >= 1))",
    "DROP INDEX organization_word_suggestions_active_meaning",
    "CREATE UNIQUE INDEX organization_word_suggestions_active_global_meaning"
    " ON organization_word_suggestions (customer_id, word_fold, meaning_fold)"
    " WHERE status IN ('pending', 'approved') AND document_id IS NULL",
    "CREATE UNIQUE INDEX organization_word_suggestions_active_document_meaning"
    " ON organization_word_suggestions (customer_id, document_id, word_fold, meaning_fold)"
    " WHERE status IN ('pending', 'approved') AND document_id IS NOT NULL",
    "COMMENT ON COLUMN organization_word_suggestions.document_id IS"
    " 'When set, approval applies only to this customer document.'",
    "COMMENT ON COLUMN organization_word_suggestions.page_no IS 'Page where the viewer correction was made.'",
]

DOWNGRADE = [
    "DROP INDEX organization_word_suggestions_active_document_meaning",
    "DROP INDEX organization_word_suggestions_active_global_meaning",
    "CREATE UNIQUE INDEX organization_word_suggestions_active_meaning"
    " ON organization_word_suggestions (customer_id, word_fold, meaning_fold)"
    " WHERE status IN ('pending', 'approved')",
    "ALTER TABLE organization_word_suggestions DROP CONSTRAINT"
    " organization_word_suggestions_document_page_check",
    "ALTER TABLE organization_word_suggestions DROP CONSTRAINT organization_word_suggestions_document_fk",
    "ALTER TABLE organization_word_suggestions DROP COLUMN page_no",
    "ALTER TABLE organization_word_suggestions DROP COLUMN document_id",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
