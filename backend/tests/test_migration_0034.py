"""Migration 0034 (the Overview's real figures, 16 Oct 2026) without a database: the
downgrade is 0026's function exactly, the new pages and costs count only kinds the ledger
writes, first reads apart from re-reads, the cost OpenRouter reported only, and only the
operator role may run it. Against a real PostgreSQL: test_operator_insights_db.py."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from kaagaz.ai.usage import KINDS


def _load(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _kinds(listed: str) -> set[str]:
    return set(re.findall(r"'([a-z_]+)'", listed))


def test_the_downgrade_restores_0026s_function_exactly() -> None:
    old, new = _load("0026_ai_modes.py"), _load("0034_overview_real_figures.py")
    assert new._daily(False) == old._daily(old.DOCUMENT_KINDS, old.QUESTION_KINDS)


def test_first_reads_and_re_reads_never_mix_and_are_ledger_kinds() -> None:
    migration = _load("0034_overview_real_figures.py")
    first, again = _kinds(migration.FIRST_READ_KINDS), _kinds(migration.REREAD_KINDS)
    assert (first, again) == ({"scan", "upgrade"}, {"careful", "ensemble_scan"})
    assert _kinds(migration.ANSWER_KINDS) == {"chat", "ensemble_chat"}
    assert first | again | _kinds(migration.ANSWER_KINDS) <= set(KINDS)


def test_only_the_reported_cost_is_summed_and_the_rest_counted_as_calls() -> None:
    new = _load("0034_overview_real_figures.py")._daily(True)
    charged = "u.cost_usd IS NOT NULL AND NOT u.cost_estimated"
    real_sums = re.findall(r"sum\(u\.cost_usd\) FILTER \(WHERE ([^\n]*)\),?\n", new)[-3:]
    assert all(sum_filter.endswith(charged) for sum_filter in real_sums)
    assert new.count(f"NOT ({charged})") == 3  # the three unknown-call counts
    assert "price_in_usd_per_m" not in new


def test_only_the_operator_role_may_run_it_after_either_direction() -> None:
    migration = _load("0034_overview_real_figures.py")
    function = "FUNCTION public.kaagaz_op_daily(uuid, date, date, text)"
    for real in (True, False):
        statements = migration._replace(real)
        assert f"REVOKE ALL ON {function} FROM PUBLIC" in statements
        grants = [statement for statement in statements if "GRANT" in statement]
        assert grants == [f"GRANT EXECUTE ON {function} TO kaagaz_operator"]
