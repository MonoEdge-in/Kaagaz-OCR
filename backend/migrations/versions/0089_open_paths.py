"""Nothing opens to everyone by accident (#594, Tushar, 14 Nov 2026). NEEDS VRUSHIT'S APPROVAL:
schema.

* ``version_suggestions.previous_held_for``: who the file was held for when a link took it into its
  family (``kaagaz.db.versions.link``), so its Undo holds it for that person again instead of
  leaving it open to everyone. Keyed to ``users`` with the organisation, as
  ``documents_held_for_fk``. Rows linked before hold NULL, which is right: until #594 nothing in
  the app held a file. The table-wide grant (0041) covers the app and the worker role.

Cost: the key is added NOT VALID (0084's pattern), then validated in the same transaction: the
column is new, so the check reads ``version_suggestions`` once and finds nothing, while it and
``users`` are locked against writes for that moment. No table is rewritten.

The downgrade drops the column; what it held is lost (an Undo then leaves a held file in no folder,
as before #594).

Revision ID: 0089
Revises: 0088
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0089", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_literal = _load("0068_audit_keeping.py")._literal
KEY = "version_suggestions_previous_held_for_fk"
HELD_COMMENT = (
    "users.id (same customer) the document was held for before it was linked (none: not held),"
    " whom Undo holds it for again when it goes back to no folder (#594)"
)

UPGRADE = [
    "ALTER TABLE version_suggestions ADD COLUMN previous_held_for uuid NULL,"
    f" ADD CONSTRAINT {KEY} FOREIGN KEY (customer_id, previous_held_for) REFERENCES users (customer_id, id)"
    " NOT VALID",
    f"ALTER TABLE version_suggestions VALIDATE CONSTRAINT {KEY}",
    f"COMMENT ON COLUMN version_suggestions.previous_held_for IS {_literal(HELD_COMMENT)}",
]
DOWNGRADE = ["ALTER TABLE version_suggestions DROP COLUMN IF EXISTS previous_held_for"]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
