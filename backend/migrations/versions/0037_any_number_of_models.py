"""Any number of scanning models (Tushar, 16 Oct 2026: "I can select as I want"): the super
admin may tick one, two or three scanning models, whatever Cost saving and Ensemble are.

* ``kaagaz_op_set_org_ai``: Cost saving (``dynamic``) no longer needs two scanning models
  (with one, its careful re-reads use that same model: ``kaagaz.db.models.resolve_scan``).
  Ensemble still compares two scanning models, so with fewer than two in effect the save
  succeeds (``done``) and Ensemble is saved off, in the settings row and in both audit
  rows; the API sees it off and tells the console. An own answer list of one no longer
  refuses Ensemble either (Ask answers with one model then: ``resolve_chat``).
* ``kaagaz_op_set_ai_defaults``: Cost saving with one scanning model is allowed.

So ``needs_two_models_scan`` and ``needs_two_models_chat`` are no longer returned. Each
body is 0026's (no later migration redefines them) with only the rule's lines replaced,
built from 0026's own text so nothing else drifts; CREATE OR REPLACE with the same
signatures keeps the grants, the comments, SECURITY DEFINER and the empty search path.
The downgrade restores 0026's bodies exactly. No table, column, index, grant or policy
changes, and no lock: the old API keeps working between ``alembic upgrade`` and the
restart (it only stops getting the two refusals).

Revision ID: 0037
Revises: 0036 (ranked sources)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def _load_0026() -> ModuleType:
    path = Path(__file__).with_name("0026_ai_modes.py")
    spec = importlib.util.spec_from_file_location("migration_0026_for_0037", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_OLD = _load_0026()
ORG = (
    "kaagaz_op_set_org_ai(uuid, uuid, text[], text, text[], text, text, text[], text, boolean, boolean,"
    " numeric, text, text, bigint)"
)
DEFAULTS = (
    "kaagaz_op_set_ai_defaults(uuid, text[], text, text[], text, text, text[], text, boolean, text, text,"
    " bigint)"
)

ORG_RULE_0026 = """        IF (p_ensemble OR p_dynamic IS TRUE) AND cardinality(v_eff_scan) < 2 THEN
            outcome := 'needs_two_models_scan';
            RETURN;
        END IF;
        IF p_ensemble AND cardinality(v_chat) = 1 THEN
            outcome := 'needs_two_models_chat';
            RETURN;
        END IF;
"""
# Ensemble compares two scanning models; with fewer in effect it is saved off, never refused.
ORG_RULE = """        -- Any number of models (Tushar, 16 Oct 2026). Ensemble needs a second scanning model
        -- in effect, else it is saved off (the API tells the console).
        p_ensemble := p_ensemble AND cardinality(v_eff_scan) >= 2;
"""
DEFAULTS_RULE_0026 = """        IF p_dynamic AND cardinality(v_scan) < 2 THEN
            outcome := 'needs_two_models_scan';
            RETURN;
        END IF;
"""
CREATE = "    CREATE FUNCTION public."
REPLACE = "    CREATE OR REPLACE FUNCTION public."


def _body(signature: str, old: str, new: str | None) -> str:
    """0026's ``signature`` as CREATE OR REPLACE, with ``old`` replaced by ``new`` (None: as 0026)."""
    body = _OLD.FUNCTIONS[signature]
    if body.count(CREATE) != 1 or body.count(old) != 1:
        raise RuntimeError(f"0026's {signature} is not the text 0037 replaces")
    body = body.replace(CREATE, REPLACE)
    return body if new is None else body.replace(old, new)


UPGRADE = [_body(ORG, ORG_RULE_0026, ORG_RULE), _body(DEFAULTS, DEFAULTS_RULE_0026, "")]
DOWNGRADE = [_body(ORG, ORG_RULE_0026, None), _body(DEFAULTS, DEFAULTS_RULE_0026, None)]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
