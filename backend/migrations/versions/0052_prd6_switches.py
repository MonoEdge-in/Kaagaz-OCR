"""PRD 6 switches (integration, 24 Oct 2026): three PRD 6 features join the platform (0038) and
organisation (0039) switch lists, so the super admin can turn each off without a deploy.

* ``land_facts``: reading owners, shares and places of land records on upload (D2) and
  answering owner and place questions from them (E).
* ``nlq_clarify``: the clarifying question before an answer (B).
* ``nlq_learning``: Useful marks lifting cited pages for later questions (F3).

As 0041 did for ``version_spotting``: both CHECKs and the operator's four functions that name
the features (0041's helpers, with 0041's lists plus these). No row is on, so each feature runs
until a switch is turned off. The downgrade removes these features' rows and puts 0041's lists
back.

Revision ID: 0052
Revises: 0051
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0052"
down_revision = "0049"  # renumber at integration: down_revision "0051" once F's migrations are in
branch_labels = None
depends_on = None

NEW_FEATURES = ("land_facts", "nlq_clarify", "nlq_learning")


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0052", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0041 = _load("0041_file_management.py")
_ADDED = ", ".join(f"'{name}'" for name in NEW_FEATURES)
PLATFORM_0041 = f"{_0041._PLATFORM_OLD}, '{_0041.NEW_FEATURE}'"
ORG_0041 = f"{_0041._ORG_OLD}, '{_0041.NEW_FEATURE}'"
PLATFORM = f"{PLATFORM_0041}, {_ADDED}"
ORG = f"{ORG_0041}, {_ADDED}"

UPGRADE = [
    *_0041._feature_check("feature_switches", PLATFORM),
    *_0041._feature_check("org_feature_switches", ORG),
    *_0041._switch_functions(PLATFORM, ORG),
]

DOWNGRADE = [
    f"DELETE FROM feature_switches WHERE feature IN ({_ADDED})",
    f"DELETE FROM org_feature_switches WHERE feature IN ({_ADDED})",
    *_0041._switch_functions(PLATFORM_0041, ORG_0041),
    *_0041._feature_check("feature_switches", PLATFORM_0041),
    *_0041._feature_check("org_feature_switches", ORG_0041),
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
