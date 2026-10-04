"""The scan reader an upload chose: ``documents.ocr_choice`` (PRD 4 #17, council 4 Oct 2026, PROVISIONAL).

NULL is Auto, the usual way. Otherwise it is the id of a reader the server
offered (``kaagaz.reading.ocr.ocr_choices``), checked by the api on upload and
again by the worker, which reads with Auto when it doesn't have it. The CHECK is
the id's shape only, never a fixed list, so a new reader needs no migration.
Row level security already covers ``documents``, so it never crosses customers.
Adding a NULL column with no default rewrites nothing. It is commented (0007's
rule). The downgrade drops it; every choice is lost and documents read again
with Auto.

Revision ID: 0013
Revises: 0012 (delete forever grant)
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

COMMENT = (
    "The scan reader the uploader chose for this file, by its id (lower-case letters, digits"
    " and underscores). NULL = Auto, the usual reader. A reader the server no longer has is read with Auto."
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE documents ADD COLUMN ocr_choice text NULL"
        " CONSTRAINT documents_ocr_choice_shape CHECK (ocr_choice ~ '^[a-z0-9_]{1,40}$')"
    )
    op.execute(f"COMMENT ON COLUMN documents.ocr_choice IS '{COMMENT}'")


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS ocr_choice")
