"""Migration 0037 (any number of scanning models, Tushar, 16 Oct 2026) without a database:
the new bodies drop only the two-model rule and keep every other check, Ensemble is saved
off instead of refused, and the downgrade is 0026's text exactly. Against a real
PostgreSQL: test_isolation_ai_settings.py and test_operator_models_db.py."""

import importlib.util
from pathlib import Path
from types import ModuleType


def _load(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


NEW, OLD = _load("0037_any_number_of_models.py"), _load("0026_ai_modes.py")
CHECKS = (
    "'unavailable'",
    "'not_verified'",
    "'chat_not_verified'",
    "'level_not_supported'",
    "'default_not_allowed'",
    "'step_up'",
    "'signed_out'",
    "'done'",
)


def test_the_new_bodies_lack_the_two_model_rule_and_keep_every_other_check() -> None:
    org, defaults = NEW.UPGRADE
    for body in (org, defaults):
        assert body.lstrip().startswith("CREATE OR REPLACE FUNCTION public.")
        assert "needs_two_models" not in body and "p_dynamic IS TRUE" not in body
        assert "SECURITY DEFINER SET search_path = ''" in body
        assert all(check in body for check in CHECKS)
    assert "'missing'" in org and "'deleting'" in org
    assert "p_ensemble := p_ensemble AND cardinality(v_eff_scan) >= 2;" in org
    # Saved off before the settings row and both audit rows are written.
    assert org.index("p_ensemble :=") < org.index("INSERT INTO public.customer_ai_settings")


def test_only_the_rule_changes_and_the_downgrade_is_0026s_exactly() -> None:
    for signature, new, old_rule, rule in (
        (NEW.ORG, NEW.UPGRADE[0], NEW.ORG_RULE_0026, NEW.ORG_RULE),
        (NEW.DEFAULTS, NEW.UPGRADE[1], NEW.DEFAULTS_RULE_0026, ""),
    ):
        old = OLD.FUNCTIONS[signature]
        assert new == old.replace(old_rule, rule).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    assert [body.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION") for body in NEW.DOWNGRADE] == [
        OLD.FUNCTIONS[NEW.ORG],
        OLD.FUNCTIONS[NEW.DEFAULTS],
    ]


def test_no_colon_word_that_sqlalchemy_would_bind() -> None:
    assert ":" not in NEW.ORG_RULE.replace(":=", "")
