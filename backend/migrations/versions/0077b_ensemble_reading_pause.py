"""Ensemble held by the reading pause (#520, Tushar, 6 Nov 2026). NEEDS VRUSHIT'S APPROVAL:
schema (one function's body).

A queued Ensemble run the worker picks up while its organisation's reading is paused stays
queued with the pause's code in ``ensemble_runs.error_code`` (``paused_daily_limit`` or
``paused_budget``; the column, its check and the app's UPDATE on it are 0030's). The release
sweep finds the organisations to look at through ``kaagaz_customer_ids_held()`` (0066: owner
rights, ids only), which now also gives those with a held run. Only its body and comment
change (CREATE OR REPLACE), so its owner, grants, SECURITY DEFINER and empty search path stay.
The held runs are found on 0030's index of the active runs (``status IN ('queued', 'running')``).

The downgrade puts 0066's body and comment back. A run held then stays queued until someone
stops it (Stop works on a queued run), as nothing looks for it.

Revision ID: 0077b
Revises: 0077a (ended sessions)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0077b"
down_revision = "0077a"
branch_labels = None
depends_on = None


def _load_0066() -> ModuleType:
    path = Path(__file__).with_name("0066_reading_pause.py")
    spec = importlib.util.spec_from_file_location("migration_0066_for_0076", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0066 = _load_0066()
CUSTOMERS_HELD = _0066.CUSTOMERS_HELD

CUSTOMERS_HELD_SQL = """
    CREATE OR REPLACE FUNCTION public.kaagaz_customer_ids_held()
    RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS
    $$ SELECT d.customer_id FROM public.documents d WHERE d.reading_held IS NOT NULL
       UNION
       SELECT r.customer_id FROM public.ensemble_runs r
       WHERE r.status = 'queued' AND r.error_code IN ('paused_daily_limit', 'paused_budget') $$
"""
CUSTOMERS_HELD_COMMENT = (
    "The worker's release sweep: the organisations with a document or a queued Ensemble run held by a"
    " reading pause. Ids only."
)

UPGRADE = [
    CUSTOMERS_HELD_SQL,
    f"COMMENT ON FUNCTION public.{CUSTOMERS_HELD} IS {_0066._literal(CUSTOMERS_HELD_COMMENT)}",
]
DOWNGRADE = [
    _0066.CUSTOMERS_HELD_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"),
    f"COMMENT ON FUNCTION public.{CUSTOMERS_HELD} IS {_0066._literal(_0066.CUSTOMERS_HELD_COMMENT)}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
