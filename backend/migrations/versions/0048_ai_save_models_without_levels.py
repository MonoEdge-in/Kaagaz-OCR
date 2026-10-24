"""A model with no thinking levels can be saved for an organisation and as a platform default
again (Tushar, 18 Oct 2026: a model that lists no levels has one mode and takes any level;
``kaagaz.db.models._takes``).

0026's shared check (``_CHECK``) refused it with ``level_not_supported``: with ``efforts``
empty, ``x = ANY (m.efforts)`` is false, so every Qwen and Llama model was refused by
``kaagaz_op_set_org_ai`` and ``kaagaz_op_set_ai_defaults``. Both level checks (answer
levels, scan thinking) now skip a model with no levels; a model whose levels leave out the
one asked for is still refused.

Each body is the one in force (0037's, built from 0026's text; no later migration redefines
them) with only those two lines changed, built from 0037's own text so nothing else drifts;
CREATE OR REPLACE with the same signatures keeps the grants, the comments, SECURITY DEFINER
and the empty search path. The downgrade restores 0037's bodies exactly. No table, column,
index, grant or policy changes.

Revision ID: 0048
Revises: 0047
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def _load_0037() -> ModuleType:
    path = Path(__file__).with_name("0037_any_number_of_models.py")
    spec = importlib.util.spec_from_file_location("migration_0037_for_0048", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_OLD = _load_0037()
# {0026's line: the same line, where a model with no levels takes any level}.
LEVEL_RULE = {
    "WHERE m.id = ANY (v_eff_chat) AND NOT l.level = ANY (m.efforts))": (
        "WHERE m.id = ANY (v_eff_chat) AND cardinality(m.efforts) > 0 AND NOT l.level = ANY (m.efforts))"
    ),
    "WHERE m.id = ANY (v_eff_scan) AND NOT v_eff_thinking = ANY (m.efforts)) THEN": (
        "WHERE m.id = ANY (v_eff_scan) AND cardinality(m.efforts) > 0 AND NOT v_eff_thinking = ANY (m.efforts)) THEN"
    ),
}


def _body(body: str) -> str:
    """0037's ``body`` with the level rule's lines replaced."""
    for old, new in LEVEL_RULE.items():
        if body.count(old) != 1:
            raise RuntimeError("0037's body is not the text 0048 replaces")
        body = body.replace(old, new)
    return body


DOWNGRADE = list(_OLD.UPGRADE)  # kaagaz_op_set_org_ai, kaagaz_op_set_ai_defaults
UPGRADE = [_body(body) for body in DOWNGRADE]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
