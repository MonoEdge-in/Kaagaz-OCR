"""Migration 0070 (#488, Tushar, 4 Nov 2026): Ultra low cost. ``scan_reader`` takes
``ultra_low_cost`` on both AI settings tables, ``ulc_ceiling_paise`` (20 to 40, NULL: inherit)
comes with it, NULL for every existing row, so no organisation's reading changes on release
day; the operator's four AI functions save, check, audit and count it (never the
organisation's own log); ``kaagaz_op_ulc_cost`` and the catalogue's page reads count the
``ulc_scan`` ledger kind, and so do the Overview, the model board and the test model's cost, each
live body changed only by its kind lists (SPEND); up, down (Ultra low cost back to NULL) and up
again, with rows."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer, violation
from tests.test_isolation_ai_settings import platform, set_defaults, set_org
from tests.test_migration_0023_db import as_owner, catalog_model
from tests.test_migration_0037 import _load
from tests.test_migration_0066_db import _BODIES as _BODIES_0066

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name
fresh = base.fresh
M0070 = _load("0070_ultra_low_cost.py")
_TABLES = ("customer_ai_settings", "platform_ai_settings")
_BODIES = text(
    str(_BODIES_0066).replace(
        "'kaagaz_customer_ids_held')", "'kaagaz_customer_ids_held', 'kaagaz_op_ulc_cost', 'kaagaz_op_models')"
    )
)
ULC = ops.AiChoice(scan_reader="ultra_low_cost", ulc_ceiling_paise=30)
PLATFORM_ULC = ops.AiChoice(
    chat_levels=["low"], chat_default_level="low", dynamic=False, scan_reader="ultra_low_cost"
)
_DEFINITION = text(
    "SELECT pg_get_functiondef(p.oid), pg_get_userbyid(p.proowner), obj_description(p.oid, 'pg_proc'),"
    " coalesce(p.proacl::text, '') FROM pg_proc p WHERE p.oid = CAST(:f AS regprocedure)"
)


def _spend(db: Database) -> dict[str, tuple[str, ...]]:
    """SPEND's functions: (definition, owner, comment, grants)."""
    with db.system() as conn:
        return {each: tuple(conn.execute(_DEFINITION, {"f": f"public.{each}"}).one()) for each in M0070.SPEND}


def _bodies(db: Database) -> list[tuple[str, ...]]:
    with db.system() as conn:
        return [tuple(row) for row in conn.execute(_BODIES)]


def _refused(db: Database, statement: str, params: dict[str, object] | None = None) -> str | None:
    with pytest.raises(DBAPIError) as refused:
        as_owner(db, statement, params)
    return violation(refused.value)[0]


def test_the_revision_follows_0069a() -> None:
    assert (M0070.revision, M0070.down_revision) == ("0070", "0069a")


def test_upgrade_starts_every_ceiling_empty_and_changes_no_reader(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0069a")
    customer, _ = new_customer(db)
    as_owner(
        db,
        "INSERT INTO customer_ai_settings (customer_id, scan_reader) VALUES (:c, 'mistral')",
        {"c": customer},
    )
    migrate(url, "0070")
    assert as_owner(db, "SELECT scan_reader, ulc_ceiling_paise FROM customer_ai_settings") == [
        ("mistral", None)
    ]
    assert as_owner(db, "SELECT scan_reader, ulc_ceiling_paise FROM platform_ai_settings") == [(None, None)]
    for table in _TABLES:
        [column] = [row for row in base._schema(db)["columns"] if row[:2] == (table, "ulc_ceiling_paise")]
        assert column[2:4] == ("smallint", False)  # nullable


def test_the_checks_take_ultra_low_cost_and_a_ceiling_of_20_to_55_paise(db: Database) -> None:
    # Changed on purpose (#488, 0073): up to 55 paise (was 40); test_migration_0073_db.
    customer, _ = new_customer(db)
    insert = (
        "INSERT INTO customer_ai_settings (customer_id, scan_reader, ulc_ceiling_paise) VALUES (:c, :r, :p)"
    )
    for reader, paise in (("gemini", None), ("ultra_low_cost", 19), ("ultra_low_cost", 56)):
        assert _refused(db, insert, {"c": customer, "r": reader, "p": paise}) == "23514"
        assert (
            _refused(db, "UPDATE platform_ai_settings SET ulc_ceiling_paise = :p", {"p": paise or 0})
            == "23514"
        )
    for paise in (20, 55):
        as_owner(db, insert, {"c": customer, "r": "ultra_low_cost", "p": paise})
        as_owner(db, "DELETE FROM customer_ai_settings WHERE customer_id = :c", {"c": customer})
    names = {row[1] for row in base._schema(db)["constraints"] if row[0] in _TABLES}
    assert {f"ai_{side}_settings_ulc_ceiling_paise" for side in ("customer", "platform")} <= names


def test_the_catalogue_keeps_a_price_a_request_and_an_image_null_when_not_known(
    db: Database, platform: str
) -> None:
    """#488 review (R3): OpenRouter's pricing.request and pricing.image, dollars a call, at least 0;
    NULL: not known. The refresh (the app) writes them; the operator's catalogue shows them."""
    schema = base._schema(db)
    columns = {row[1]: row[2:4] for row in schema["columns"] if row[0] == "model_catalog"}
    assert columns["price_request_usd"] == columns["price_image_usd"] == ("numeric", False)  # nullable
    names = {row[1] for row in schema["constraints"] if row[0] == "model_catalog"}
    assert {"ai_model_catalog_price_request_usd", "ai_model_catalog_price_image_usd"} <= names
    model = catalog_model(db)
    assert as_owner(
        db, "SELECT price_request_usd, price_image_usd FROM model_catalog WHERE id = :m", {"m": model}
    ) == [(None, None)]
    for update in (
        "UPDATE model_catalog SET price_request_usd = -0.000001 WHERE id = :m",
        "UPDATE model_catalog SET price_image_usd = -0.000001 WHERE id = :m",
    ):
        assert _refused(db, update, {"m": model}) == "23514"
    comments = as_owner(
        db,
        "SELECT col_description('model_catalog'::regclass, attnum) FROM pg_attribute"
        " WHERE attrelid = 'model_catalog'::regclass AND attname IN ('price_request_usd', 'price_image_usd')",
    )
    assert len(comments) == 2 and all(comment and "NULL" in comment for (comment,) in comments)
    with db.system() as conn:  # the app role, as the refresh writes
        conn.execute(
            text("UPDATE model_catalog SET price_request_usd = 0, price_image_usd = 0.001238 WHERE id = :m"),
            {"m": model},
        )
    with OperatorDatabase(db.engine).operator() as conn:
        [row] = [row for row in ops.list_models(conn, platform) if row["id"] == model]
    assert (row["price_request_usd"], float(row["price_image_usd"])) == (0, 0.001238)


def test_the_org_save_keeps_the_ceiling_audits_it_for_the_operator_only_and_refuses_one_outside(
    db: Database, platform: str
) -> None:
    customer, _ = new_customer(db)
    for paise in (19, 56):  # Changed on purpose (#488, 0073): up to 55
        outcome = set_org(
            db, platform, customer, ops.AiChoice(scan_reader="ultra_low_cost", ulc_ceiling_paise=paise)
        )
        assert outcome.code == "ceiling_out_of_range"
    assert set_org(db, platform, customer, ULC).code == "done"
    with OperatorDatabase(db.engine).operator() as conn:
        own = ops.org_ai(conn, platform, customer)
    assert own is not None and (own["scan_reader"], own["ulc_ceiling_paise"]) == ("ultra_low_cost", 30)
    [(operators,)] = as_owner(
        db, "SELECT details FROM platform_audit WHERE subject_customer_id = :c", {"c": customer}
    )
    assert (operators["scan_reader"], operators["ulc_ceiling_paise"]) == ("ultra_low_cost", 30)
    [(own_log,)] = as_owner(
        db,
        "SELECT details FROM audit_log WHERE customer_id = :c AND action = 'org.ai_settings_set'",
        {"c": customer},
    )
    assert not {"scan_reader", "ulc_ceiling_paise"} & set(own_log) and "scan_models" in own_log


def test_the_defaults_save_audits_before_and_after_and_counts_the_organisations_inheriting(
    db: Database, platform: str
) -> None:
    customer, _ = new_customer(db)  # inherits every field
    with OperatorDatabase(db.engine).operator() as conn:
        before = ops.ai_defaults(conn, platform)
    assert before is not None and before["orgs_inheriting_ulc_ceiling_paise"] >= 1
    refused = ops.AiChoice(chat_levels=["low"], chat_default_level="low", dynamic=False, ulc_ceiling_paise=56)
    assert set_defaults(db, platform, refused) == "ceiling_out_of_range"
    chosen = ops.AiChoice(
        chat_levels=["low"],
        chat_default_level="low",
        dynamic=False,
        scan_reader="ultra_low_cost",
        ulc_ceiling_paise=25,
    )
    assert set_defaults(db, platform, chosen) == "done"
    [(saved,)] = as_owner(
        db,
        "SELECT details FROM platform_audit WHERE operator_id = :o AND action = 'platform.ai_defaults_set'"
        " ORDER BY id DESC LIMIT 1",
        {"o": platform},
    )
    assert (saved["before"]["ulc_ceiling_paise"], saved["after"]["ulc_ceiling_paise"]) == (None, 25)
    assert saved["after"]["scan_reader"] == "ultra_low_cost"
    assert saved["orgs_changed"]["ulc_ceiling_paise"] >= 1 and saved["orgs_changed"]["scan_reader"] >= 1
    with OperatorDatabase(db.engine).operator() as conn:
        defaults = ops.ai_defaults(conn, platform)
    assert defaults is not None and (defaults["scan_reader"], defaults["ulc_ceiling_paise"]) == (
        "ultra_low_cost",
        25,
    )
    with OperatorDatabase(db.engine).operator() as conn:
        own = ops.org_ai(conn, platform, customer)
    assert own is not None and (own["scan_reader"], own["ulc_ceiling_paise"]) == (None, None)  # it follows


def test_head_down_to_0069a_puts_ultra_low_cost_back_to_null_and_up_again(
    fresh: tuple[str, Database],
) -> None:
    url, db = fresh
    migrate(url, "0069a")
    at_0069a, bodies_0069a, spend_0069a = base._schema(db), _bodies(db), _spend(db)
    migrate(url, "0070")
    at_0070, bodies_0070, spend_0070 = base._schema(db), _bodies(db), _spend(db)
    for function, changes in M0070.SPEND.items():  # only the kind lists change, nothing else
        body, *rest = spend_0069a[function]
        for (old, new), times in changes:
            assert body.count(old) == times, function
            body = body.replace(old, new)
        assert spend_0070[function] == (body, *rest)
        assert "ulc_scan" in body and "SECURITY DEFINER" in body and "SET search_path TO ''" in body
    assert "kaagaz_op_ulc_cost" in [row[0] for row in bodies_0070]
    for name, body, comment, acl in bodies_0070:  # the house pattern: definer, empty path, one role
        assert "SECURITY DEFINER" in body and "SET search_path TO ''" in body, name
        role, other = (
            ("kaagaz_app", "kaagaz_operator") if "_op_" not in name else ("kaagaz_operator", "kaagaz_app")
        )
        assert comment and f"{role}=X" in acl and other not in acl, name
        assert not any(grant.startswith("=") for grant in acl.strip("{}").split(",")), name  # no PUBLIC
    customer, _ = new_customer(db)
    as_owner(
        db,
        "INSERT INTO customer_ai_settings (customer_id, scan_reader, ulc_ceiling_paise)"
        " VALUES (:c, 'ultra_low_cost', 20)",
        {"c": customer},
    )
    as_owner(db, "UPDATE platform_ai_settings SET scan_reader = 'ultra_low_cost', ulc_ceiling_paise = 40")
    as_owner(
        db,
        "INSERT INTO model_usage (customer_id, kind, model, cached, calls, pages, input_tokens,"
        " output_tokens, reasoning_tokens, cost_usd, limit_hits)"
        " VALUES (:c, 'ulc_scan', 'google/x', false, 1, 1, 10, 10, 0,"
        " 0.004, 1)",
        {"c": customer},
    )

    base._downgrade(url, "0069a")
    assert base._schema(db) == at_0069a
    assert _bodies(db) == bodies_0069a  # restored exactly, comments and grants too
    assert _spend(db) == spend_0069a
    assert as_owner(
        db, "SELECT scan_reader FROM customer_ai_settings WHERE customer_id = :c", {"c": customer}
    ) == [(None,)]  # back to Gemini, as every organisation read before
    assert as_owner(db, "SELECT scan_reader FROM platform_ai_settings") == [(None,)]
    assert as_owner(db, "SELECT count(*) FROM model_usage WHERE kind = 'ulc_scan'") == [(1,)]  # kept
    migrate(url, "0070")
    assert base._schema(db) == at_0070
    assert _bodies(db) == bodies_0070
    assert _spend(db) == spend_0070
