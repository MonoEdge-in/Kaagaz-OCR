"""A trashed folder's parent may change (A13, stability pass, 7 Nov 2026). NEEDS VRUSHIT'S
APPROVAL: a grant.

A folder deleted with ``contents=keep&to=X`` (#537) moves what is inside it to X, but a folder of
it already in Trash (#542) kept the deleted folder as its ``parent_id``, so its Restore came back
at the top level instead of in X. The app now repoints those rows to X
(``kaagaz.db.folders.delete_folder_with_contents``), which needs UPDATE on ``parent_id`` alone: a
column grant, so the app still never changes a trashed folder's name, kind, delete or owner.

No table change and no row written. The downgrade revokes the grant.

Revision ID: 0082
Revises: 0081
"""

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"


def upgrade() -> None:
    op.execute(f"GRANT UPDATE (parent_id) ON trashed_folders TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE (parent_id) ON trashed_folders FROM {APP_ROLE}")
