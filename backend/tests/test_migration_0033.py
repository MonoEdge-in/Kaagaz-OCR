"""Migration 0033 (the operator's real cost, 16 Oct 2026) without a database: it counts only
kinds the ledger writes, splits scanning from answering, takes the cost OpenRouter reported
(never the list price's estimate), keeps the 30 days of kaagaz.db.models, and only the
operator role may run it. Against a real PostgreSQL: test_operator_models_db.py."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from kaagaz.ai.usage import KINDS
from kaagaz.db.models import LEDGER_DAYS


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0033_operator_real_cost.py"
    spec = importlib.util.spec_from_file_location("migration_0033", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _kinds(listed: str) -> set[str]:
    return set(re.findall(r"'([a-z_]+)'", listed))


def test_it_counts_scanning_and_answering_kinds_the_ledger_writes_and_nothing_else() -> None:
    migration = _migration()
    scan, answer = _kinds(migration.SCAN_KINDS), _kinds(migration.ANSWER_KINDS)
    assert scan == {"scan", "upgrade", "careful", "ensemble_scan"}
    assert answer == {"chat", "ensemble_chat"}
    assert scan | answer <= set(KINDS)


def test_the_cost_is_what_openrouter_reported_and_an_estimate_counts_as_unknown() -> None:
    new = _migration().NEW
    assert "x.cost_usd IS NOT NULL AND NOT x.cost_estimated AS charged" in new
    # Every cost sum is over charged rows only; every unknown count is over the rest.
    assert re.findall(r"sum\(r\.cost_usd\) FILTER \(WHERE ([^)]*)\)", new) == [
        "r.scan AND r.charged",
        "NOT r.scan AND r.charged",
    ]
    assert "r.scan AND NOT r.charged" in new and "NOT r.scan AND NOT r.charged" in new
    assert "price_in_usd_per_m *" not in new and "price_out_usd_per_m *" not in new


def test_it_reads_the_last_30_days_through_each_organisations_index_and_skips_cached_pages() -> None:
    migration = _migration()
    assert migration.LEDGER_DAYS == LEDGER_DAYS == 30
    assert "WHERE u.customer_id = c.id AND u.created_at >= now() - make_interval(days => 30)" in migration.NEW
    assert "AND NOT u.cached" in migration.NEW


def test_only_the_operator_role_may_run_it_after_either_direction() -> None:
    migration = _migration()
    for create, comment in ((migration.NEW, migration.NEW_COMMENT), (migration.OLD, migration.OLD_COMMENT)):
        statements = migration._replace(create, comment)
        assert "REVOKE ALL ON FUNCTION public.kaagaz_op_models(uuid) FROM PUBLIC" in statements
        assert "GRANT EXECUTE ON FUNCTION public.kaagaz_op_models(uuid) TO kaagaz_operator" in statements
        assert sum("GRANT" in statement for statement in statements) == 1
        assert "SECURITY DEFINER SET search_path = ''" in create
