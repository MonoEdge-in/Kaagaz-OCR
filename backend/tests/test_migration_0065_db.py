"""Migration 0065 (#473, Tushar, 2 Nov 2026): who reads scanned pages (``scan_reader``,
openrouter or mistral) and which Mistral model (``mistral_model``), on the platform defaults
and each organisation's settings. Both start NULL (inherit: Gemini through OpenRouter), the
CHECKs refuse anything else, the operator's saves write and audit them (never the
organisation's own log), the app still only reads both tables, the estimate's scan calls
count the priced model's alone, and the downgrade restores 0064 exactly."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer, violation
from tests.test_isolation_ai_settings import NO_DEFAULTS, platform, set_defaults, set_org
from tests.test_migration_0023_db import as_owner, catalog_model
from tests.test_migration_0037 import _load
from tests.test_migration_0064_db import _BODIES as _BODIES_0064
from tests.test_scan_cost_settings_db import spent

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name
fresh = base.fresh
M0065 = _load("0065_scan_reader.py")
_TABLES = ("customer_ai_settings", "platform_ai_settings")
_NEW_COLUMNS = {"scan_reader", "mistral_model"}
# 0064's functions and the new one, Mistral's stop for the console.
_BODIES = text(
    str(_BODIES_0064).replace(
        "'kaagaz_op_org_health')", "'kaagaz_op_org_health', 'kaagaz_op_mistral_status')"
    )
)
MISTRAL = ops.AiChoice(scan_reader="mistral", mistral_model="mistral-ocr-2512")


def _bodies(db: Database) -> list[tuple[str, ...]]:
    with db.system() as conn:
        return [tuple(row) for row in conn.execute(_BODIES)]


def _refused(db: Database, statement: str, params: dict[str, str] | None = None) -> str | None:
    with pytest.raises(DBAPIError) as refused:
        as_owner(db, statement, params)
    return violation(refused.value)[0]


def test_upgrade_adds_columns_null(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0064")
    customer, _ = new_customer(db)
    as_owner(
        db,
        "INSERT INTO customer_ai_settings (customer_id, scan_thinking) VALUES (:c, 'low')",
        {"c": customer},
    )
    migrate(url, "0065")
    for table in _TABLES:
        columns = {row[1]: row for row in base._schema(db)["columns"] if row[0] == table}
        assert set(columns) >= _NEW_COLUMNS
        assert not any(columns[name][3] for name in _NEW_COLUMNS)  # nullable
    for statement in (
        "SELECT DISTINCT scan_reader, mistral_model FROM customer_ai_settings",
        "SELECT DISTINCT scan_reader, mistral_model FROM platform_ai_settings",
    ):
        assert as_owner(db, statement) == [(None, None)]  # the existing rows inherit: Gemini


def test_checks_refuse_unknown_reader_and_model(db: Database) -> None:
    customer, _ = new_customer(db)
    for insert, update, good, bad in (
        (
            "INSERT INTO customer_ai_settings (customer_id, scan_reader) VALUES (:c, :v)",
            "UPDATE platform_ai_settings SET scan_reader = :v",
            "mistral",
            "gemini",
        ),
        (
            "INSERT INTO customer_ai_settings (customer_id, mistral_model) VALUES (:c, :v)",
            "UPDATE platform_ai_settings SET mistral_model = :v",
            "mistral-ocr-4-1",
            "mistral-ocr-latest",
        ),
    ):
        assert _refused(db, insert, {"c": customer, "v": bad}) == "23514"
        assert _refused(db, update, {"v": bad}) == "23514"
        as_owner(db, insert, {"c": customer, "v": good})
        as_owner(db, "DELETE FROM customer_ai_settings WHERE customer_id = :c", {"c": customer})
    names = {row[1] for row in base._schema(db)["constraints"] if row[0] in _TABLES}
    assert {
        f"ai_{side}_settings_{column}" for side in ("customer", "platform") for column in _NEW_COLUMNS
    } <= names


def test_set_org_ai_saves_and_platform_audits_before_after(db: Database, platform: str) -> None:
    customer, _ = new_customer(db)
    assert set_org(db, platform, customer, MISTRAL).code == "done"
    with OperatorDatabase(db.engine).operator() as conn:
        own = ops.org_ai(conn, platform, customer)
    assert own is not None and (own["scan_reader"], own["mistral_model"]) == ("mistral", "mistral-ocr-2512")
    [(details,)] = as_owner(
        db, "SELECT details FROM platform_audit WHERE subject_customer_id = :c", {"c": customer}
    )
    assert (details["scan_reader"], details["mistral_model"]) == ("mistral", "mistral-ocr-2512")

    platform_mistral = ops.AiChoice(
        chat_levels=["low"], chat_default_level="low", dynamic=False, scan_reader="mistral"
    )
    assert set_defaults(db, platform, platform_mistral) == "done"
    [(saved,)] = as_owner(
        db,
        "SELECT details FROM platform_audit WHERE operator_id = :o AND action = 'platform.ai_defaults_set'"
        " ORDER BY id DESC LIMIT 1",
        {"o": platform},
    )
    assert (saved["before"]["scan_reader"], saved["after"]["scan_reader"]) == (None, "mistral")
    assert saved["after"]["mistral_model"] is None
    assert saved["orgs_changed"]["scan_reader"] >= 1  # every organisation that inherits the reader
    assert saved["orgs_changed"]["mistral_model"] == 0  # unchanged
    with OperatorDatabase(db.engine).operator() as conn:
        defaults = ops.ai_defaults(conn, platform)
    assert defaults is not None and defaults["scan_reader"] == "mistral"
    assert defaults["orgs_inheriting_scan_reader"] >= 1 and defaults["orgs_inheriting_mistral_model"] >= 1
    assert set_defaults(db, platform, NO_DEFAULTS) == "done"  # back to inherit: the server's reader
    with OperatorDatabase(db.engine).operator() as conn:
        back = ops.ai_defaults(conn, platform)
    assert back is not None and back["scan_reader"] is None


def test_org_audit_row_strips_reader_fields(db: Database, platform: str) -> None:
    """Org admins never see who reads their pages nor the Mistral model (as the scan cost settings)."""
    customer, _ = new_customer(db)
    assert set_org(db, platform, customer, MISTRAL).code == "done"
    [(details,)] = as_owner(
        db,
        "SELECT details FROM audit_log WHERE customer_id = :c AND action = 'org.ai_settings_set'",
        {"c": customer},
    )
    assert not _NEW_COLUMNS & set(details) and "scan_models" in details


def test_needs_step_up(db: Database, platform: str) -> None:
    customer, _ = new_customer(db)
    assert set_org(db, platform, customer, MISTRAL, step=7).code == "done"
    assert set_org(db, platform, customer, ops.AiChoice(), step=7).code == "step_up"  # the same code
    with OperatorDatabase(db.engine).operator() as conn:
        own = ops.org_ai(conn, platform, customer)
    assert own is not None and own["scan_reader"] == "mistral"
    defaults = ops.AiChoice(
        chat_levels=["low"], chat_default_level="low", dynamic=False, scan_reader="mistral"
    )
    assert set_defaults(db, platform, defaults, step=6) == "step_up"  # an older code


def test_app_role_cannot_write(db: Database) -> None:
    customer, _ = new_customer(db)
    for statement in (
        "UPDATE platform_ai_settings SET scan_reader = 'mistral'",
        "INSERT INTO customer_ai_settings (customer_id, mistral_model) VALUES (:c, 'mistral-ocr-4-1')",
        "UPDATE customer_ai_settings SET scan_reader = 'mistral' WHERE customer_id = :c",
    ):
        with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
            conn.execute(text(statement), {"c": customer})
        assert violation(refused.value)[0] == "42501", statement
    for call in (
        "SELECT * FROM kaagaz_op_mistral_status(gen_random_uuid())",
        "SELECT * FROM kaagaz_op_org_ai(gen_random_uuid(), gen_random_uuid())",
    ):
        with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
            conn.execute(text(call))
        assert violation(refused.value)[0] == "42501", call


def test_scan_cost_calls_count_only_the_model(db: Database, platform: str) -> None:
    """A Mistral organisation's first reads never count in a Gemini model's share read again."""
    customer, _ = new_customer(db)
    lead = catalog_model(db)
    spent(db, customer, lead, calls=4)
    spent(db, customer, "mistral/mistral-ocr-4-1", calls=96, thinking="none", resolution="page")
    spent(db, customer, lead, kind="careful", calls=1, thinking="high")
    with OperatorDatabase(db.engine).operator() as conn:
        row = ops.scan_cost(conn, platform, customer, (lead, "low", "high"), None, ("high", "high"))
    assert row is not None and (row["scan_calls"], row["careful_calls"]) == (4, 1)


def test_down_up_restores_0064_exactly(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0064")
    at_0064, bodies_0064 = base._schema(db), _bodies(db)
    migrate(url, "0065")
    at_0065, bodies_0065 = base._schema(db), _bodies(db)
    assert "kaagaz_op_mistral_status" in [row[0] for row in bodies_0065]
    for name, body, comment, acl in bodies_0065:  # the house pattern: definer, empty path, one role
        assert "SECURITY DEFINER" in body and "SET search_path TO ''" in body
        role, other = (
            ("kaagaz_app", "kaagaz_operator") if "_op_" not in name else ("kaagaz_operator", "kaagaz_app")
        )
        assert comment and f"{role}=X" in acl and other not in acl, name
    base._downgrade(url, "0064")
    assert base._schema(db) == at_0064
    assert _bodies(db) == bodies_0064  # restored exactly, comments and grants too
    migrate(url, "0065")
    assert base._schema(db) == at_0065
    assert _bodies(db) == bodies_0065


def test_single_head() -> None:
    assert (M0065.revision, M0065.down_revision) == ("0065", "0064")
