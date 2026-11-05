"""Folder upload's "Add new files only" (#484, Tushar, 5 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema.

Moved out of 0071: the test server had already run 0071 as #509 merged it (the folder tables,
straight after 0070), so an 0071 edited in place never reached it. 0071 is now only the merge of
0070 and production's 0061c; this revision adds what Testing-Env has more, on every database:

* ``folder_uploads.items_counted``: the files registered that weren't already in Kaagaz; the
  file limit is checked against it (``items_registered`` counts every file, against
  ``file_count``). An upload made before it gets its ``items_registered``: before it every
  registered file counted against the limits, as its ``bytes_registered`` still does.
* The comments of ``items_registered``, ``items_counted`` and ``bytes_registered`` (now the
  bytes of the files not already in Kaagaz).

The test server (#509's 0071), production (0061c, then 0061 ... 0071) and a fresh database all
reach 0071 with 0061c's folder tables, so all three end on the same schema
(tests/test_migration_0071_db.py). A column with a constant default: no table rewrite; the
backfill writes each upload row once (one a folder upload).

The downgrade drops ``items_counted`` and puts 0061c's comments back; the tables and their rows
stay.

Revision ID: 0072
Revises: 0071 (the merge of 0070 and 0061c)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def _load_0061c() -> ModuleType:
    path = Path(__file__).with_name("0061c_folder_upload.py")
    spec = importlib.util.spec_from_file_location("migration_0061c_for_0072", path)
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
