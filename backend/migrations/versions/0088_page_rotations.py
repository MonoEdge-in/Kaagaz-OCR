"""Page turns saved for everyone (PRD 7 section 14 step 2, #600, Tushar, 14 Nov 2026).
NEEDS VRUSHIT'S APPROVAL: schema.

* ``page_rotations``: one row per page of one file version turned for everyone, the clockwise turn
  on top of the file's own /Rotate (90, 180 or 270). No row: not turned, so 0 is never stored.
* Every key holds the organisation (as 0084's tables). Rows go with their version (CASCADE from
  ``documents``); a new version is a new id and starts with none. No key to ``pages``: a PDF shows
  its pages before its ``pages`` rows exist, and a Read again rewrites them.
* ``set_by`` points at the person with NO ACTION: a user is never deleted, and
  ``kaagaz_finish_customer_delete`` refuses while a document is left, so the rows are gone first.
* Row level security with ``customer_only``; the app and the worker role read, add and delete
  (0083's equality test), never update: a save deletes the pages it changes and adds them again.
  The primary key serves the read by document and the deletes; no other index.

Cost: catalog only. The three keys hold ``customers``, ``documents`` and ``users`` SHARE ROW
EXCLUSIVE for a moment while the empty table is created (reads go on, writes wait).

The downgrade drops the table; its rows are lost. PR 9's per-person database rule must list
``page_rotations`` among the content tables.

Revision ID: 0088
Revises: 0087
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0088", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_literal = _load("0068_audit_keeping.py")._literal
ROLES = ("kaagaz_app", "kaagaz_worker")
POLICY = "customer_id = kaagaz_current_customer()"
CREATE = """
    CREATE TABLE page_rotations (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        document_id uuid NOT NULL,
        page_no integer NOT NULL CONSTRAINT page_rotations_page_no CHECK (page_no >= 1),
        turn smallint NOT NULL CONSTRAINT page_rotations_turn CHECK (turn IN (90, 180, 270)),
        set_by uuid NOT NULL,
        set_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, document_id, page_no),
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, set_by) REFERENCES users (customer_id, id)
    )
    """
TABLE_COMMENT = "A page of one file version turned for everyone (PRD 7 section 14). No row: not turned."
COMMENTS = {
    "customer_id": "The organisation.",
    "document_id": "documents.id (same customer) of the version; each version has its own turns, and they"
    " go with it.",
    "page_no": "The page, from 1.",
    "turn": "Clockwise degrees on top of the file's own turn: 90, 180 or 270.",
    "set_by": "users.id (same customer) who saved it.",
    "set_at": "When it was saved.",
}

UPGRADE = [
    CREATE,
    f"COMMENT ON TABLE page_rotations IS {_literal(TABLE_COMMENT)}",
    *[f"COMMENT ON COLUMN page_rotations.{column} IS {_literal(text)}" for column, text in COMMENTS.items()],
    "ALTER TABLE page_rotations ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY customer_only ON page_rotations USING ({POLICY}) WITH CHECK ({POLICY})",
    "COMMENT ON POLICY customer_only ON page_rotations IS 'A row is seen and written only by its own customer.'",
    *[f"GRANT SELECT, INSERT, DELETE ON page_rotations TO {role}" for role in ROLES],
]
DOWNGRADE = ["DROP TABLE IF EXISTS page_rotations"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
