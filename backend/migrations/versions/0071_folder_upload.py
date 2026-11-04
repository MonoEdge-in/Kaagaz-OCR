"""Upload a whole folder, Testing-Env's shape on production's 0061c (#484, Tushar, 4 Nov 2026).
NEEDS VRUSHIT'S APPROVAL: schema.

Production ran production-with-folder-upload: ``0061c`` (this branch carries its file byte for
byte, a branch off 0061b) made the folder upload tables (``folder_uploads``,
``folder_upload_folders``, ``folder_upload_items``, ``org_upload_limits``), the platform's
``folder_upload_max_*`` columns, the operator's two functions and the ``folders`` comment
(nested at most 100 levels). This revision merges 0061c with 0070 and adds only what
Testing-Env has more, "Add new files only":

* ``folder_uploads.items_counted``: the files registered that weren't already in Kaagaz; the
  file limit is checked against it (``items_registered`` counts every file, against
  ``file_count``). An upload made before it gets its ``items_registered``: under 0061c every
  registered file counted against the limits, as its ``bytes_registered`` still does.
* The comments of ``items_registered``, ``items_counted`` and ``bytes_registered`` (now the
  bytes of the files not already in Kaagaz).

Everything else (columns, CHECKs, failure codes, keys, indexes, grants, row level security,
the functions' bodies and comments) is 0061c's, unchanged. Production (0061c, with uploads):
the release runs 0061 ... 0070, then this. A fresh database, or the test server (0070, no
folder tables): ``upgrade head`` runs 0061c, then this. Either way the same schema
(tests/test_migration_0071_db.py). A column with a constant default: no table rewrite; the
backfill writes each upload row once (one a folder upload).

The downgrade drops ``items_counted`` and puts 0061c's comments back; 0061c's tables and rows
stay, and the database is at 0070 and 0061c.

Revision ID: 0071
Revises: 0070 (Ultra low cost), 0061c (production's folder upload)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0071"
down_revision = ("0070", "0061c")
branch_labels = None
depends_on = None


def _load_0061c() -> ModuleType:
    path = Path(__file__).with_name("0061c_folder_upload.py")
    spec = importlib.util.spec_from_file_location("migration_0061c_for_0071", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0061C = _load_0061c()
_literal = _0061C._literal
COMMENTS = {
    "items_registered": "Files registered so far, those already in Kaagaz too; never above file_count.",
    "items_counted": "Files registered so far that weren't already in Kaagaz; never above the limit.",
    "bytes_registered": "Bytes of the files registered so far that weren't already in Kaagaz;"
    " never above the limit.",
}
COMMENTS_0061C = {
    column: _0061C.COMMENTS["folder_uploads"][1][column]
    for column in ("items_registered", "bytes_registered")
}

UPGRADE = [
    "ALTER TABLE folder_uploads ADD COLUMN items_counted integer NOT NULL DEFAULT 0"
    " CONSTRAINT folder_uploads_items_counted_check CHECK (items_counted >= 0)",
    "UPDATE folder_uploads SET items_counted = items_registered WHERE items_counted <> items_registered",
    *[f"COMMENT ON COLUMN folder_uploads.{column} IS {_literal(text)}" for column, text in COMMENTS.items()],
]

DOWNGRADE = [
    "ALTER TABLE folder_uploads DROP COLUMN IF EXISTS items_counted",
    *[
        f"COMMENT ON COLUMN folder_uploads.{column} IS {_literal(text)}"
        for column, text in COMMENTS_0061C.items()
    ],
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
