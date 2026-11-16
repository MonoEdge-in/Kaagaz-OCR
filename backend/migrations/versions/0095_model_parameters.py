"""Each model's supported parameters (#647, 16 Nov 2026; NEEDS VRUSHIT'S APPROVAL: schema and one
operator function). It follows PRD 7 (0083 to 0093) and search (0094, #646); none of those
changes ``kaagaz_op_models``, so its rebuild from 0070's text still holds. It must not run from
the test server while it shares production's database (#577).

On 15 Nov 2026 every Vertex endpoint of gemini-3.5-flash-lite stopped listing ``temperature``,
and every Ask call was 404 ``no_endpoints`` (#631 added one retry without it). The daily
catalogue refresh already reads each endpoint's ``supported_parameters``; now it keeps them.

* ``model_catalog.parameters`` (new): the union of the supported parameters of the model's
  zero-data-retention endpoints with status 0 of a provider in OPENROUTER_PROVIDERS that take
  structured outputs, the ones a call can land on (``kaagaz.jobs.catalog``). ``text[] NULL`` with a named CHECK on each name's shape and count, as
  0023's on ``providers`` and ``efforts``. NULL: not read yet, and the client sends as before.
  Every row is NULL until the next refresh; the app and the worker role may write it, as the
  other refresh columns (0083: the worker role has the app's rights).
* ``kaagaz_op_models`` (0070's) returns it last, for the operator's Models screen: 0070's text
  with that change, dropped and created again (its columns change) with 0033's comment and grants.

The downgrade puts 0070's function back and drops the column (and its grants with it). Lost on
downgrade: the stored parameters, which the next refresh writes again.

Revision ID: 0095
Revises: 0094 (bounded search)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{name[:4]}_for_0095", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0070 = _load("0070_ultra_low_cost.py")
_0033 = _0070._0033
ROLES = ("kaagaz_app", "kaagaz_worker")  # 0083: the worker role has the app's rights
PARAMETER = "[a-z][a-z0-9_]{0,63}"  # kaagaz.jobs.catalog.PARAMETER
MAX_PARAMETERS = 64  # kaagaz.jobs.catalog.MAX_PARAMETERS
COMMENT = (
    "The parameters its approved endpoints take (supported_parameters of its zero-data-retention"
    " endpoints with status 0 in OPENROUTER_PROVIDERS that take structured outputs, together). The"
    " client sends an optional one"
    " (temperature) only when listed here. NULL: not read yet, sent as before."
)

MODELS_NEW = _0070._changed(
    _0070.MODELS_NEW,
    [
        ("price_image_usd numeric)", "price_image_usd numeric, parameters text[])"),
        (
            "m.price_request_usd, m.price_image_usd\n",
            "m.price_request_usd, m.price_image_usd, m.parameters\n",
        ),
    ],
)

# 0023's CHECK on the names joined by commas, and as many names after the join as before, so an
# empty name or one holding a comma is refused too.
UPGRADE = [
    f"ALTER TABLE model_catalog ADD COLUMN parameters text[] NULL CONSTRAINT ai_model_catalog_parameters"
    f" CHECK (cardinality(parameters) <= {MAX_PARAMETERS} AND array_position(parameters, NULL) IS NULL"
    f" AND array_to_string(parameters, ',') ~ '^({PARAMETER}(,{PARAMETER})*)?$'"
    " AND cardinality(string_to_array(array_to_string(parameters, ','), ',')) = cardinality(parameters))",
    f"COMMENT ON COLUMN model_catalog.parameters IS {_0070._literal(COMMENT)}",
    *[f"GRANT INSERT (parameters), UPDATE (parameters) ON model_catalog TO {role}" for role in ROLES],
    *_0033._replace(MODELS_NEW, _0033.NEW_COMMENT),
]

DOWNGRADE = [
    *_0033._replace(_0070.MODELS_NEW, _0033.NEW_COMMENT),
    # Its grants go with it.
    "ALTER TABLE model_catalog DROP COLUMN parameters",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
