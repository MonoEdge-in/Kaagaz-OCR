"""A deleted document's runs go with it (Vrushit's decision: "if a document is
deleted, instantly forget everything about it").

0004 kept ``processing_runs`` rows when their document was deleted, with
``document_id`` set NULL, so the pages-per-month numbers outlived a purge
(AR12, PROVISIONAL). Now ``document_id`` is NOT NULL and its (customer_id,
document_id) foreign key deletes the runs with the document (ON DELETE
CASCADE), like every other table that points at documents. The runs already
left without a document are deleted first: they belong to documents deleted
before this.

Both directions lock documents, then processing_runs, the order the app takes
them in (``runs.start_run`` locks the documents row, then writes a run), so
they queue behind the app instead of deadlocking with it; migrations/env.py
sets lock_timeout, so a busy table makes them fail fast. Both are locked in
ACCESS EXCLUSIVE mode first (as 0004 does), since dropping and adding the key
lock documents too: no lock is raised half way.

The downgrade puts back 0004's nullable column and its SET NULL key, under the
same constraint name. The runs deleted by the upgrade do not come back.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

FOREIGN_KEY = "processing_runs_customer_id_document_id_fkey"  # 0004's key, by PostgreSQL's name for it
LOCK = "LOCK TABLE documents, processing_runs IN ACCESS EXCLUSIVE MODE"

UPGRADE = [
    LOCK,
    # Runs as the table owner, and 0004 has no FORCE ROW LEVEL SECURITY, so this sees
    # every customer's runs: a run without a document belongs to no document to keep.
    "DELETE FROM processing_runs WHERE document_id IS NULL",
    "ALTER TABLE processing_runs ALTER COLUMN document_id SET NOT NULL",
    f"ALTER TABLE processing_runs DROP CONSTRAINT {FOREIGN_KEY}",
    f"""
    ALTER TABLE processing_runs ADD CONSTRAINT {FOREIGN_KEY}
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE CASCADE
    """,
]

DOWNGRADE = [
    LOCK,
    f"ALTER TABLE processing_runs DROP CONSTRAINT {FOREIGN_KEY}",
    f"""
    ALTER TABLE processing_runs ADD CONSTRAINT {FOREIGN_KEY}
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE SET NULL (document_id)
    """,
    "ALTER TABLE processing_runs ALTER COLUMN document_id DROP NOT NULL",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
