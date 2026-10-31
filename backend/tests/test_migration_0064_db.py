"""The scan cost settings (0064, #463, Tushar, 31 Oct 2026) on a fresh database: up, down and
up again; the new columns and CHECKs (minimal for the scan levels only, low or high for the
picture detail); the app still only reads both settings tables; and the downgrade puts a
minimal scan level back to inherit and restores the four operator functions exactly."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer, violation
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0064 = _load("0064_scan_cost_settings.py")
_BODIES = text(
    "SELECT p.proname, pg_get_functiondef(p.oid), obj_description(p.oid, 'pg_proc'), p.proacl::text"
    " FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace"
    " AND p.proname IN ('kaagaz_op_org_ai', 'kaagaz_op_set_org_ai', 'kaagaz_op_ai_defaults',"
    " 'kaagaz_op_set_ai_defaults', 'kaagaz_op_scan_cost', 'kaagaz_op_start_accuracy_check',"
    " 'kaagaz_op_accuracy_check', 'kaagaz_accuracy_check_claim', 'kaagaz_accuracy_check_finish',"
    " 'kaagaz_op_org_health') ORDER BY 1"
)
_NEW_COLUMNS = {"scan_resolution", "careful_thinking", "careful_resolution", "page_max_output_tokens"}


def _bodies(db: Database) -> list[tuple[str, ...]]:
    with db.system() as conn:
        return [tuple(row) for row in conn.execute(_BODIES)]


def _refused(db: Database, statement: str) -> str | None:
    with pytest.raises(DBAPIError) as refused:
        as_owner(db, statement)
    return violation(refused.value)[0]


def test_0064_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0063")
    at_0063, bodies_0063 = base._schema(db), _bodies(db)
    customer, _ = new_customer(db)
    migrate(url, "0064")
    at_0064, bodies_0064 = base._schema(db), _bodies(db)
    for table in ("customer_ai_settings", "platform_ai_settings"):
        added = {row[1] for row in at_0064["columns"] if row[0] == table} - {
            row[1] for row in at_0063["columns"] if row[0] == table
        }
        assert added == _NEW_COLUMNS
    assert {row[1] for row in at_0064["columns"] if row[0] == "model_usage"} >= {"thinking", "resolution"}
    assert [row[0] for row in bodies_0064] == [
        "kaagaz_accuracy_check_claim",
        "kaagaz_accuracy_check_finish",
        "kaagaz_op_accuracy_check",
        "kaagaz_op_ai_defaults",
        "kaagaz_op_org_ai",
        "kaagaz_op_org_health",
        "kaagaz_op_scan_cost",
        "kaagaz_op_set_ai_defaults",
        "kaagaz_op_set_org_ai",
        "kaagaz_op_start_accuracy_check",
    ]
    for name, body, comment, acl in bodies_0064:  # the house pattern: definer, empty path, one role
        assert "SECURITY DEFINER" in body and "SET search_path TO ''" in body
        role, other = (
            ("kaagaz_app", "kaagaz_operator") if "_op_" not in name else ("kaagaz_operator", "kaagaz_app")
        )
        assert comment and f"{role}=X" in acl and other not in acl, name
    # The budget's month spend leaves the operator's accuracy checks out (only that line changed).
    [health_0063] = [body for name, body, *_ in bodies_0063 if name == "kaagaz_op_org_health"]
    [health_0064] = [body for name, body, *_ in bodies_0064 if name == "kaagaz_op_org_health"]
    assert health_0064 == health_0063.replace(M0064.HEALTH_OLD, M0064.HEALTH_NEW) != health_0063
    accuracy = {row[1]: row for row in at_0064["columns"] if row[0] == "scan_accuracy_checks"}
    assert "word_match" in accuracy and not {"text", "words", "customer_id"} & set(accuracy)
    assert {row[1] for row in at_0064["columns"] if row[0] == "pages"} >= {"read_thinking", "read_resolution"}

    # Minimal for the scan levels (both tables), never for the answer levels; detail low or high.
    as_owner(db, "UPDATE platform_ai_settings SET scan_thinking = 'minimal', careful_thinking = 'minimal'")
    as_owner(
        db,
        "INSERT INTO customer_ai_settings (customer_id, scan_thinking, scan_resolution, careful_thinking,"
        " careful_resolution) VALUES (:c, 'minimal', 'low', 'medium', 'high')",
        {"c": customer},
    )
    assert _refused(db, "UPDATE platform_ai_settings SET chat_default_level = 'minimal'") == "23514"
    assert _refused(db, "UPDATE platform_ai_settings SET scan_resolution = 'medium'") == "23514"
    assert _refused(db, "UPDATE customer_ai_settings SET careful_resolution = 'ultra_high'") == "23514"
    assert _refused(db, "UPDATE customer_ai_settings SET careful_thinking = 'max'") == "23514"

    base._downgrade(url, "0063")
    assert base._schema(db) == at_0063
    assert _bodies(db) == bodies_0063  # restored exactly
    assert as_owner(db, "SELECT scan_thinking FROM platform_ai_settings") == [(None,)]  # minimal: inherit
    assert as_owner(
        db, "SELECT scan_thinking FROM customer_ai_settings WHERE customer_id = :c", {"c": customer}
    ) == [(None,)]
    migrate(url, "0064")
    assert base._schema(db) == at_0064
    assert _bodies(db) == bodies_0064


def test_the_app_only_reads_both_settings_tables(db: Database) -> None:
    """Only the operator's functions write the new fields (the app's grant is SELECT, 0026)."""
    customer, _ = new_customer(db)
    for statement in (
        "UPDATE platform_ai_settings SET scan_resolution = 'low'",
        "INSERT INTO customer_ai_settings (customer_id, careful_thinking) VALUES (:c, 'low')",
        "UPDATE customer_ai_settings SET careful_resolution = 'low' WHERE customer_id = :c",
    ):
        with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
            conn.execute(text(statement), {"c": customer})
        assert violation(refused.value)[0] == "42501", statement  # insufficient_privilege
    privileges = as_owner(
        db,
        "SELECT table_name, privilege_type FROM information_schema.role_table_grants"
        " WHERE grantee = 'kaagaz_app' AND table_name IN ('customer_ai_settings', 'platform_ai_settings')",
    )
    assert sorted(privileges) == [("customer_ai_settings", "SELECT"), ("platform_ai_settings", "SELECT")]
    columns = as_owner(
        db,
        "SELECT count(*) FROM information_schema.column_privileges WHERE grantee = 'kaagaz_app'"
        " AND table_name IN ('customer_ai_settings', 'platform_ai_settings') AND privilege_type <> 'SELECT'",
    )
    assert columns == [(0,)]
