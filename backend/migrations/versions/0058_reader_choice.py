"""The reader choice (#398): every account chooses how its scans are read, as the scan level of
the AI settings by task (0038): ``none`` "Local reader only", the new ``save`` "Save cost" (the
local reader keeps clean printed pages, the AI reads the rest; ``kaagaz.db.ai_settings``), and
``low``, ``high`` or ``max`` "AI reads every page".

* ``ai_task_settings``: its two level CHECKs also take ``save``, for the scan task only. No row
  is written or changed (nothing saved is still today's reading), and the table keeps its
  ``customer_only`` policy (0038) and grants.

The CHECKs are replaced in one transaction on a table of a few rows a person (an organisation's
defaults and its members' own choices), so the brief lock is short. The downgrade reads a saved
``save`` as ``low`` (Automatic, every page by the AI, as with PAGE_ROUTING off) and puts the
CHECKs and the column's comment back exactly as 0038 made them.

Revision ID: 0058
Revises: 0057 (read again regions)
"""

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

_DROP = [
    "ALTER TABLE ai_task_settings DROP CONSTRAINT ai_task_settings_level_check",
    "ALTER TABLE ai_task_settings DROP CONSTRAINT ai_task_settings_level_fits_task",
]


def _comment(text: str) -> str:
    return "COMMENT ON COLUMN ai_task_settings.level IS '" + text.replace("'", "''") + "'"


UPGRADE = [
    *_DROP,
    "ALTER TABLE ai_task_settings ADD CONSTRAINT ai_task_settings_level_check"
    " CHECK (level IN ('none', 'save', 'low', 'high', 'max', 'on', 'off'))",
    """
    ALTER TABLE ai_task_settings ADD CONSTRAINT ai_task_settings_level_fits_task CHECK (
        CASE WHEN task = 'scan' THEN level IN ('none', 'save', 'low', 'high', 'max')
             WHEN task = 'ask' THEN level IN ('none', 'low', 'high', 'max')
             ELSE level IN ('off', 'on') END)
    """,
    _comment(
        "none, low, high or max (scan, ask); save too for scan, the reader choice Save cost (#398);"
        " off or on (ensemble, autofile)."
    ),
]

# 0038's own CHECKs and comment, as it wrote them.
DOWNGRADE = [
    "UPDATE ai_task_settings SET level = 'low' WHERE level = 'save'",
    *_DROP,
    "ALTER TABLE ai_task_settings ADD CONSTRAINT ai_task_settings_level_check"
    " CHECK (level IN ('none', 'low', 'high', 'max', 'on', 'off'))",
    """
    ALTER TABLE ai_task_settings ADD CONSTRAINT ai_task_settings_level_fits_task CHECK (
        CASE WHEN task IN ('scan', 'ask') THEN level IN ('none', 'low', 'high', 'max')
             ELSE level IN ('off', 'on') END)
    """,
    _comment("none, low, high or max (scan, ask); off or on (ensemble, autofile)."),
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
