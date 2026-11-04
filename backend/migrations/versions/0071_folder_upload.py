"""Upload a whole folder: the merge of production's 0061c with 0070 (#484, Tushar, 5 Nov 2026).
NEEDS VRUSHIT'S APPROVAL: schema.

Production ran production-with-folder-upload: ``0061c`` (this branch carries its file byte for
byte, a branch off 0061b) made the folder upload tables (``folder_uploads``,
``folder_upload_folders``, ``folder_upload_items``, ``org_upload_limits``), the platform's
``folder_upload_max_*`` columns, the operator's two functions and the ``folders`` comment
(nested at most 100 levels). This revision only merges 0061c with 0070 and changes nothing.

The test server ran this revision as #509 merged it: 0061c's statements, byte for byte, straight
after 0070. A database at that 0071 is where this merge leaves the other two (production: 0061c,
then 0061 ... 0070; a fresh database: 0070, then 0061c), so this revision must never add
anything: such a database would never get it. What Testing-Env has more ("Add new files only")
is 0072's.

The downgrade does nothing: the database is at 0070 and 0061c, 0061c's tables and rows stay.

Revision ID: 0071
Revises: 0070 (Ultra low cost), 0061c (production's folder upload)
"""

revision = "0071"
down_revision = ("0070", "0061c")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
