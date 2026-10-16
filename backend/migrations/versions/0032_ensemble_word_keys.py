"""Ensemble's chosen words (scan review UX rules, Tushar, 16 Oct 2026; the council plan of 16
Oct 2026, PR 5): ``ensemble_runs.word_keys``, the flagged words a run reads, so a run started
on one word, some words or the flagged words reads the same words when its worker starts
again or a person retries its pages. NULL for a run on the whole document (every flag and
line of its pages, found when each page is read).

Set once, when the run is made: the app role's column grants on ``ensemble_runs`` (0030)
stay as they are, so it can insert the column and never update it. A new nullable column
with a CHECK: the table is new and small, so the brief lock and scan are nothing. The
downgrade drops the column.

Revision ID: 0032
Revises: 0031 (the super admin's two-step move)
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

MAX_WORDS = 200  # a run's words at most (kaagaz.db.ensemble.MAX_WORDS)

UPGRADE = [
    f"""
    ALTER TABLE ensemble_runs ADD COLUMN word_keys text[] NULL
        CONSTRAINT ensemble_runs_word_keys CHECK (
            cardinality(word_keys) BETWEEN 1 AND {MAX_WORDS}
            AND array_to_string(word_keys, ',') ~ '^[0-9a-f]{{64}}(,[0-9a-f]{{64}})*$'
        )
    """,
    "COMMENT ON COLUMN ensemble_runs.word_keys IS 'The word keys the run reads (kaagaz.extraction.flags"
    ".word_key), 1 to 200, set when it is made. NULL = the whole document: every flag and line of its pages.'",
]

DOWNGRADE = ["ALTER TABLE ensemble_runs DROP COLUMN IF EXISTS word_keys"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
