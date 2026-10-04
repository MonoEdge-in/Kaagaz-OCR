"""Delete forever, granted: ``users.delete_forever_allowed_at`` (council 4 Oct 2026, PROVISIONAL).

An admin may allow chosen members and reviewers to delete forever; this column
holds when, NULL when not. A column, not a permissions table: there is one such
right today. Row level security already covers ``users``, so it never crosses
customers. The app ignores it for an admin and clears it on any role change.
Adding a NULL column with no default rewrites nothing. It is commented (0007's
rule). The downgrade drops it; every grant is lost.

Revision ID: 0012
Revises: 0011 (sound key)
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

COMMENT = (
    "When an admin allowed this member or reviewer to delete documents in Trash forever"
    " (they type their password each time). NULL = not allowed. Ignored for admins."
)


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN delete_forever_allowed_at timestamptz NULL")
    op.execute(f"COMMENT ON COLUMN users.delete_forever_allowed_at IS '{COMMENT}'")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS delete_forever_allowed_at")
