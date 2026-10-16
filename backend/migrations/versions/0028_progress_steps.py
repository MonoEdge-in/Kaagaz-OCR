"""Progress steps the worker writes (stuck reading, 16 Oct 2026): ``documents.progress``'s
CHECK (0004) allowed five steps, each with done and total, so two writes the worker makes
were refused and the refusal (an IntegrityError, a bug to the worker) ended the worker
in the middle of the job:

* ``{"step": "waiting"}`` (PRD 5 Q6), written when the reader stopped a try: every Gemini
  timeout or outage ended the worker instead of letting the document wait, and the stale
  "Reading page 1 of 1" stayed on screen while the job came back try after try.
* ``rereading`` with done and total (Dynamic's Careful, feature 26).

The CHECK now allows ``rereading`` among the steps, and ``{"step": "waiting"}`` alone.
It is added NOT VALID (a brief lock) and then validated, which reads the table without
blocking writes; every stored value already passes it. The downgrade clears the two new
shapes first, then puts 0004's CHECK back.

Revision ID: 0028
Revises: 0027 (party folders)
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

CHECK = "documents_progress_check"  # 0004's inline CHECK, by its default name

# The steps with a count, as kaagaz.pipeline.Step (tests/test_migration_0028.py keeps them the same).
STEPS = ("scanning", "preparing", "reading", "rereading", "understanding", "indexing")
STEPS_0004 = ("scanning", "preparing", "reading", "understanding", "indexing")
WAITING = "waiting"  # kaagaz.db.documents.WAITING_FOR_READER


def _counted(steps: tuple[str, ...]) -> str:
    """0004's shape: an object with step, done and total, the step one of ``steps``."""
    names = ", ".join(f"'{step}'" for step in steps)
    return f"""(
        jsonb_typeof(progress) = 'object'
        AND progress ? 'step' AND progress ? 'done' AND progress ? 'total'
        AND jsonb_typeof(progress -> 'step') = 'string'
        AND progress ->> 'step' IN ({names})
        AND jsonb_typeof(progress -> 'done') = 'number' AND progress ->> 'done' ~ '^[0-9]{{1,9}}$'
        AND jsonb_typeof(progress -> 'total') = 'number' AND progress ->> 'total' ~ '^[0-9]{{1,9}}$'
    )"""


COMMENT_0007 = (
    "Processing progress, JSON {step, done, total}. In the reading step done and total count pages."
)
COMMENT_NOW = (
    "Processing progress, JSON {step, done, total}. In the reading and rereading steps done and"
    " total count pages. Just {step} with the step waiting while the reader is down."
)

UPGRADE = [
    f"ALTER TABLE documents DROP CONSTRAINT {CHECK}",
    f"ALTER TABLE documents ADD CONSTRAINT {CHECK}"
    f' CHECK ({_counted(STEPS)} OR progress = \'{{"step": "{WAITING}"}}\'::jsonb) NOT VALID',
    f"ALTER TABLE documents VALIDATE CONSTRAINT {CHECK}",
    f"COMMENT ON COLUMN documents.progress IS '{COMMENT_NOW}'",
]

DOWNGRADE = [
    f"UPDATE documents SET progress = NULL WHERE progress ->> 'step' IN ('rereading', '{WAITING}')",
    f"ALTER TABLE documents DROP CONSTRAINT {CHECK}",
    f"ALTER TABLE documents ADD CONSTRAINT {CHECK} CHECK {_counted(STEPS_0004)}",
    f"COMMENT ON COLUMN documents.progress IS '{COMMENT_0007}'",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
