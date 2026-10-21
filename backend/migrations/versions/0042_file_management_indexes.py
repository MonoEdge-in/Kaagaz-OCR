"""File management indexes found by the council (Tushar, 20 Oct 2026). 0041 was already
merged and deployed (#208), so these go in their own migration rather than into 0041.

Spotting's "never suggested twice" and the banner probe pending suggestions by family;
the list and dashboard probe suggestions by document; the owner filter reads documents by
owner. For Vrushit's later review.
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

UPGRADE = [
    "CREATE INDEX IF NOT EXISTS version_suggestions_pending_family"
    " ON version_suggestions (customer_id, candidate_family_id) WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS version_suggestions_document ON version_suggestions (customer_id, document_id)",
    "CREATE INDEX IF NOT EXISTS documents_owner_id ON documents (customer_id, owner_id)",
]

DOWNGRADE = [
    "DROP INDEX IF EXISTS documents_owner_id",
    "DROP INDEX IF EXISTS version_suggestions_document",
    "DROP INDEX IF EXISTS version_suggestions_pending_family",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
