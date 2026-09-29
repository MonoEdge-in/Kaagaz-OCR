"""Job attempts on documents: how many times the worker has tried each one (PRD 1 follow-ups).

The worker writes ``attempts`` (this try's number) and ``max_attempts``
(JOB_MAX_ATTEMPTS when it tried) before each try, so the file list can say
"Try 2 of 5" and "We tried 5 times". Both are display only: the queue's
receive count, not this column, decides when a job is given up.

Adding two columns with constant defaults is a catalogue change in PostgreSQL
11 and later: no table rewrite, so the documents lock is brief.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    ALTER TABLE documents
        ADD COLUMN attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        ADD COLUMN max_attempts integer NULL CHECK (max_attempts >= 1)
    """,
]

DOWNGRADE = [
    "ALTER TABLE documents DROP COLUMN IF EXISTS max_attempts, DROP COLUMN IF EXISTS attempts",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
