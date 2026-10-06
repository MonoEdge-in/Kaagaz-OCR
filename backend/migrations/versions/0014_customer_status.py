"""Organisation status, and the app may no longer change it (super admin PR 1, council 6 Oct 2026, PROVISIONAL).

Change plan: Outside-Folder/docs/plans/2026-10-06-super-admin.md (awaiting
Vrushit's approval).

1. Three ``customers`` columns: ``status`` (active, disabled or deleting; every
   existing customer is active), ``disabled_at`` and ``delete_after``. Two CHECKs
   keep them together: ``disabled_at`` is set exactly when the status is not
   active, and ``delete_after`` exactly when it is deleting (PR 4 sets that).
   A NOT NULL column with a constant default rewrites nothing (PostgreSQL 11 on);
   the CHECKs read every customers row once, a few rows.
2. The grant fix the council found: since 0001, ``kaagaz_app`` may UPDATE the
   whole ``customers`` row, so one bug on the customer side could switch a
   disabled organisation back on. Now it may update ``max_upload_mb`` only, the
   one column the app writes (``db/admin.py``, the admin's upload limit). The
   folder tree's row lock (``db/folders.py``, FOR NO KEY UPDATE) needs UPDATE on
   at least one column, which this keeps. Only the table owner (the CLI's
   disable-customer and enable-customer) changes the status.

Row level security on ``customers`` is unchanged (``id = kaagaz_current_customer()``).
Each new column is commented (0007's rule).

The downgrade refuses while any customer is not active, because dropping the
status would switch it back on: run ``enable-customer`` first (0006's pattern).
Then it drops the columns and gives the app UPDATE on the whole row again.

Revision ID: 0014
Revises: 0013 (ocr choice)
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"

COMMENTS = {
    "status": (
        "active (its users sign in and its files are read), disabled (no sign-in, no session,"
        " no reading; changed only by the platform operator) or deleting (disabled, and"
        " removed for good after delete_after)."
    ),
    "disabled_at": "When the customer was disabled. NULL while it is active.",
    "delete_after": (
        "When a customer that is being deleted is removed for good, with its users, files and rows."
        " NULL unless the status is deleting."
    ),
}

UPGRADE = [
    """
    ALTER TABLE customers
        ADD COLUMN status text NOT NULL DEFAULT 'active'
            CONSTRAINT customers_status_check CHECK (status IN ('active', 'disabled', 'deleting')),
        ADD COLUMN disabled_at timestamptz NULL,
        ADD COLUMN delete_after timestamptz NULL,
        ADD CONSTRAINT customers_disabled_at_check CHECK ((status = 'active') = (disabled_at IS NULL)),
        ADD CONSTRAINT customers_delete_after_check CHECK ((status = 'deleting') = (delete_after IS NOT NULL))
    """,
    *[f"COMMENT ON COLUMN customers.{column} IS '{comment}'" for column, comment in COMMENTS.items()],
    f"REVOKE UPDATE ON customers FROM {APP_ROLE}",
    f"GRANT UPDATE (max_upload_mb) ON customers TO {APP_ROLE}",
]

DOWNGRADE = [
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM customers WHERE status <> 'active') THEN
            RAISE EXCEPTION 'a customer is disabled or being deleted; enable it before downgrading'
                USING ERRCODE = 'check_violation';
        END IF;
    END
    $$
    """,
    "ALTER TABLE customers DROP COLUMN delete_after, DROP COLUMN disabled_at, DROP COLUMN status",
    f"REVOKE UPDATE (max_upload_mb) ON customers FROM {APP_ROLE}",
    f"GRANT UPDATE ON customers TO {APP_ROLE}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
