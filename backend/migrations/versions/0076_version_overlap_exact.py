"""Version spotting at 80%, counted exactly, never linked on its own (#521, Tushar, 6 Nov 2026).
NEEDS VRUSHIT'S APPROVAL: schema.

* ``version_suggestions.text_overlap_exact``: true when ``text_overlap`` was counted exactly on
  the two texts' 5-word runs (kaagaz.versions.exact_overlap), false when it is the 64-hash
  estimate (a text over V11's compare limits), NULL when there is no text overlap or the row is
  from before this revision (then it was an estimate). The banner says "about" for an estimate.
* The comments of ``text_overlap`` (exact or estimated now) and ``outcome`` (``auto`` is no
  longer written; the rows linked so before stay as they are, Undo included).

No data change: the new column is NULL on every existing row, and no row's ``outcome`` moves.
The CHECK on ``outcome`` still allows 'auto' for those rows. A nullable column with no default:
no table rewrite.

The rights: ``kaagaz_app`` has SELECT, INSERT and UPDATE on the whole table (0041), which covers
the new column; the limited logins (0067) hold no right of their own and get none here.

The downgrade drops the column and puts 0059's two comments back.

Revision ID: 0076
Revises: 0075 (the storage limit merge)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def _load_0059() -> ModuleType:
    path = Path(__file__).with_name("0059_dictionary_gaps.py")
    spec = importlib.util.spec_from_file_location("migration_0059_for_0076", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0059 = _load_0059()
_literal = _0059._literal
COMMENTS = {
    "text_overlap_exact": "True when text_overlap was counted exactly on the two texts' 5-word runs;"
    " false when it is the estimate from text_minhash (a text too long to count). NULL when"
    " text_overlap is NULL, or for a suggestion from before 0076 (an estimate).",
    "text_overlap": "Share of the two texts' 5-word runs that match, 0 to 1: exact, or estimated from"
    " text_minhash (text_overlap_exact says which). NULL when either has none.",
    "outcome": "suggested (a person decides). auto (linked at once) only on suggestions from before"
    " 0076, which no longer makes it.",
}
COMMENTS_0059 = {
    column: _0059.COLUMNS["version_suggestions"][column] for column in ("text_overlap", "outcome")
}

UPGRADE = [
    "ALTER TABLE version_suggestions ADD COLUMN text_overlap_exact boolean NULL",
    *[
        f"COMMENT ON COLUMN version_suggestions.{column} IS {_literal(text)}"
        for column, text in COMMENTS.items()
    ],
]

DOWNGRADE = [
    "ALTER TABLE version_suggestions DROP COLUMN IF EXISTS text_overlap_exact",
    *[
        f"COMMENT ON COLUMN version_suggestions.{column} IS {_literal(text)}"
        for column, text in COMMENTS_0059.items()
    ],
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
