"""Ultra low cost's ceiling up to 55 paise (#488, Tushar, 5 Nov 2026; NEEDS VRUSHIT'S APPROVAL:
schema and operator functions).

The most one read of a page may cost was 20 to 40 paise (0070); it is now 20 to 55. The default
(NULL everywhere) stays 40 paise (kaagaz.db.models.ULC_DEFAULT_PAISE), so nothing reads
differently until the super admin chooses a higher ceiling.

* ``customer_ai_settings`` and ``platform_ai_settings``: the named ``*_ulc_ceiling_paise``
  CHECKs (0070's) take ``BETWEEN 20 AND 55``; the column comments say so.
* ``kaagaz_op_set_org_ai`` and ``kaagaz_op_set_ai_defaults``, the two saves that refuse a
  ceiling outside the range (``ceiling_out_of_range``): 0070's text with only the range changed,
  exactly once each (``_changed``). Their signatures do not change, so CREATE OR REPLACE keeps the
  owner, grants, comment, SECURITY DEFINER and empty search path. The two reads never check it.

Each CHECK is dropped and added again: PostgreSQL reads both tables once to validate it (one
platform row and one row an organisation with settings; no rewrite).

The downgrade lowers any ceiling above 40 to 40 (the most 0070 takes; Ultra low cost then reads
for at most 40 paise a page), then puts 0070's CHECKs, comments and function bodies back exactly.

Revision ID: 0073
Revises: 0072 (folder upload's "Add new files only")
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def _load_0070() -> ModuleType:
    path = Path(__file__).with_name("0070_ultra_low_cost.py")
    spec = importlib.util.spec_from_file_location("migration_0070_for_0073", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0070 = _load_0070()
_literal = _0070._literal
CEILING_LOW, CEILING_HIGH = 20, 55  # kaagaz.db.models.ULC_CEILING_PAISE
OLD_HIGH = _0070.CEILING_HIGH  # 40
DEFAULT_PAISE = 40  # kaagaz.db.models.ULC_DEFAULT_PAISE: unchanged
TABLES = _0070._PREFIXES  # {table: its constraints' prefix}
SAVES = (_0070.SET_ORG_AI, _0070.SET_AI_DEFAULTS)

_RANGE = (
    f"p_ulc_ceiling_paise NOT BETWEEN {CEILING_LOW} AND {OLD_HIGH} THEN",
    f"p_ulc_ceiling_paise NOT BETWEEN {CEILING_LOW} AND {CEILING_HIGH} THEN",
)
# 0070's bodies (CREATE FUNCTION) as CREATE OR REPLACE: the same signature, so nothing else moves.
OLD = {
    signature: _0070.NEW[signature].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    for signature in SAVES
}
NEW = {signature: _0070._changed(body, [_RANGE]) for signature, body in OLD.items()}

COMMENTS = {
    f"{table}.ulc_ceiling_paise": f"The most one read of a page may cost while ultra_low_cost reads,"
    f" {CEILING_LOW} to {CEILING_HIGH} paise; a page that would cost more is read locally. NULL, " + inherits
    for table, inherits in (
        ("customer_ai_settings", "the platform default."),
        ("platform_ai_settings", f"{DEFAULT_PAISE} paise."),
    )
}
OLD_COMMENTS = {
    column: comment for column, comment in _0070.COMMENTS.items() if column.endswith(".ulc_ceiling_paise")
}


def _check(table: str, high: int) -> str:
    """``table``'s ceiling CHECK, under its 0070 name, up to ``high`` paise."""
    name = f"{TABLES[table]}_ulc_ceiling_paise"
    return (
        f"ALTER TABLE {table} DROP CONSTRAINT {name},"
        f" ADD CONSTRAINT {name} CHECK (ulc_ceiling_paise BETWEEN {CEILING_LOW} AND {high})"
    )


UPGRADE = [
    *[_check(table, CEILING_HIGH) for table in TABLES],
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
    *NEW.values(),
]

DOWNGRADE = [
    *OLD.values(),
    # The most 0070 takes: a higher ceiling reads for at most 40 paise a page again.
    *[
        f"UPDATE {table} SET ulc_ceiling_paise = {OLD_HIGH} WHERE ulc_ceiling_paise > {OLD_HIGH}"
        for table in TABLES
    ],
    *[_check(table, OLD_HIGH) for table in TABLES],
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in OLD_COMMENTS.items()],
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
