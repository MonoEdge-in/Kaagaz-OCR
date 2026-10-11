"""Customer separation of the organisations' models and budget, and the operator's model
functions (model choice, council 11 Oct 2026, MC2, MS1, MS2, MS5, SP3, SP4; 0023).

Customer A never reads B's allowed models or budget, and B's models never count for A's
documents. The app writes none of it: not an organisation's models, not a budget, not a
catalogue model's enabled or verified columns, and it runs none of the operator's
functions. Those act only for an enabled operator whose two-step secret is confirmed;
they refuse a model that is unknown, not eligible, not enabled or not verified for the
reader's prompt, and a default that is not one of the models; they write one
``platform_audit`` row (and, for an organisation's models, one row in its own log);
switching a model on or off takes a fresh code; and their rows carry no user and no
document (the signatures are pinned in test_isolation_schema).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.db.models import resolve, usable_models
from tests.db_support import insert_document, new_customer, violation
from tests.settings_factory import make_settings
from tests.test_migration_0023_db import PROMPT, add_usage, allow, as_owner, catalog_model, new_operator

pytestmark = pytest.mark.db

SETTINGS = make_settings()
_SET_ORG = text(
    "SELECT outcome, CAST(result_id AS text) FROM kaagaz_op_set_org_models("
    "CAST(:o AS uuid), CAST(:c AS uuid), CAST(:m AS text[]), CAST(:d AS text), CAST(:b AS numeric),"
    " CAST(:p AS text))"
)
_SET_ENABLED = text(
    "SELECT kaagaz_op_set_model_enabled(CAST(:o AS uuid), CAST(:m AS text), CAST(:on AS boolean),"
    " CAST(:p AS text), CAST(:s AS bigint))"
)
_ORG = text("SELECT * FROM kaagaz_op_org_models(CAST(:o AS uuid), CAST(:c AS uuid))")
_CATALOG = text("SELECT * FROM kaagaz_op_models(CAST(:o AS uuid))")
_USAGE = text("SELECT * FROM kaagaz_op_usage(CAST(:o AS uuid), :start, :end)")
OP_CALLS = (
    "SELECT * FROM kaagaz_op_models(gen_random_uuid())",
    "SELECT kaagaz_op_set_model_enabled(gen_random_uuid(), 'a/b', true, 'g2', 1)",
    "SELECT * FROM kaagaz_op_org_models(gen_random_uuid(), gen_random_uuid())",
    "SELECT * FROM kaagaz_op_set_org_models(gen_random_uuid(), gen_random_uuid(), ARRAY['a/b'], 'a/b', 1,"
    " 'g2')",
    "SELECT * FROM kaagaz_op_usage(gen_random_uuid(), now() - interval '1 day', now())",
)
USAGE_COLUMNS = [
    "customer_id",
    "customer_name",
    "model",
    "calls",
    "cached_calls",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_usd",
    "cost_estimated",
]


def _operator(db: Database) -> OperatorDatabase:
    return OperatorDatabase(db.engine)


def _set_org(
    db: Database,
    operator: str,
    customer: str,
    models: list[str],
    default: str | None,
    budget: float | None = None,
) -> tuple[str, str | None]:
    params = {"o": operator, "c": customer, "m": models, "d": default, "b": budget, "p": PROMPT}
    with _operator(db).operator() as conn:
        outcome, result = conn.execute(_SET_ORG, params).one()
    return str(outcome), result


def _set_enabled(db: Database, operator: str, model: str, on: bool | None, step: int | None) -> str:
    with _operator(db).operator() as conn:
        params = {"o": operator, "m": model, "on": on, "p": PROMPT, "s": step}
        return str(conn.execute(_SET_ENABLED, params).scalar_one())


def _allowed(db: Database, customer: str) -> list[tuple[Any, ...]]:
    return as_owner(
        db,
        "SELECT model, is_default_scan FROM customer_models WHERE customer_id = :c ORDER BY model",
        {"c": customer},
    )


# --- customer A and customer B -----------------------------------------------------------


def test_a_customer_never_reads_another_customers_models_or_budget(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    mine, theirs = catalog_model(db, name="Mine"), catalog_model(db, name="Theirs")
    allow(db, a, [mine], mine)
    allow(db, b, [theirs], theirs)
    as_owner(db, "UPDATE customers SET ai_budget_usd = 50 WHERE id = :c", {"c": b})
    with db.customer(a) as conn:
        rows = conn.execute(text("SELECT CAST(customer_id AS text), model FROM customer_models")).all()
        budget = conn.execute(text("SELECT ai_budget_usd FROM customers WHERE id = :b"), {"b": b}).all()
        their_list = usable_models(conn, b, settings=SETTINGS)
        my_list = usable_models(conn, a, settings=SETTINGS)
    assert [tuple(row) for row in rows] == [(a, mine)]
    assert (budget, list(their_list)) == ([], [])
    assert [model["id"] for model in my_list] == [mine]


def test_another_customers_models_never_count_for_a_document(db: Database) -> None:
    """A's upload asked for a model only B is allowed: A's default reads it."""
    a, a_admin = new_customer(db)
    b, _ = new_customer(db)
    mine, theirs = catalog_model(db), catalog_model(db)
    allow(db, a, [mine], mine)
    allow(db, b, [theirs], theirs)
    with db.customer(a) as conn:
        document = insert_document(conn, a, uploaded_by=a_admin)
        conn.execute(text("UPDATE documents SET read_model = :m WHERE id = :d"), {"m": theirs, "d": document})
        assert resolve(conn, a, document, settings=SETTINGS, prompt_version=PROMPT) == (mine, "google-vertex")
    with db.customer(b) as conn:  # B's transaction can't even see A's document
        assert resolve(conn, b, document, settings=SETTINGS, prompt_version=PROMPT) == (
            SETTINGS.ocr_model,
            "google-vertex",
        )


# --- what the app may not do -----------------------------------------------------------------

_APP_REFUSED = (
    "INSERT INTO customer_models (customer_id, model, is_default_scan) VALUES (:c, :m, true)",
    "UPDATE customer_models SET is_default_scan = false WHERE customer_id = :c",
    "DELETE FROM customer_models WHERE customer_id = :c",
    "UPDATE customers SET ai_budget_usd = 100 WHERE id = :c",
    "UPDATE model_catalog SET enabled_at = now() WHERE id = :m",
    "UPDATE model_catalog SET enabled_by = NULL, enabled_at = NULL WHERE id = :m",
    "UPDATE model_catalog SET verified_prompt_version = 'g9', verified_at = now() WHERE id = :m",
    "UPDATE model_catalog SET tokens_per_page_in = 1 WHERE id = :m",
    "INSERT INTO model_catalog (id, name, price_in_usd_per_m, price_out_usd_per_m, verified_prompt_version,"
    " verified_at) VALUES ('test-app/forged', 'Forged', 0, 0, 'g2', now())",
    "DELETE FROM model_catalog WHERE id = :m",
)


@pytest.mark.parametrize("statement", _APP_REFUSED)
def test_the_app_writes_no_organisations_models_budget_or_catalogue_switch(
    db: Database, statement: str
) -> None:
    customer, _ = new_customer(db)
    model = catalog_model(db)
    allow(db, customer, [model], model)
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(statement), {"c": customer, "m": model})
    assert violation(refused.value)[0] == "42501"  # insufficient_privilege
    assert _allowed(db, customer) == [(model, True)]


def test_the_app_refresh_may_add_and_update_a_catalogue_model(db: Database) -> None:
    """The refresh columns only (MC1); the operator's switch stays as it was."""
    model = f"test-{uuid.uuid4().hex[:10]}/refreshed"
    with db.system() as conn:
        conn.execute(
            text(
                "INSERT INTO model_catalog (id, name, price_in_usd_per_m, price_out_usd_per_m, providers,"
                " efforts, eligible) VALUES (:m, 'New', 1, 2, ARRAY['google-vertex'], ARRAY['low'], true)"
            ),
            {"m": model},
        )
        conn.execute(
            text(
                "UPDATE model_catalog SET name = 'Renamed', eligible = false, seen_at = now() WHERE id = :m"
            ),
            {"m": model},
        )
    assert as_owner(
        db, "SELECT name, eligible, enabled_at FROM model_catalog WHERE id = :m", {"m": model}
    ) == [("Renamed", False, None)]


@pytest.mark.parametrize("call", OP_CALLS)
def test_the_app_runs_none_of_the_operators_model_functions(db: Database, call: str) -> None:
    customer, _ = new_customer(db)
    for begin in (db.system, lambda: db.customer(customer)):
        with pytest.raises(DBAPIError) as refused, begin() as conn:
            conn.execute(text(call))
        assert violation(refused.value)[0] == "42501", begin


# --- the operator's functions ------------------------------------------------------------


def test_an_organisations_models_refuse_what_the_platform_does_not_offer(db: Database) -> None:
    operator = new_operator(db)
    customer, _ = new_customer(db)
    good, other = catalog_model(db), catalog_model(db)
    refused = {
        "unavailable": [
            "test-none/never-listed",
            catalog_model(db, eligible=False),
            catalog_model(db, enabled=False),
        ],
        "not_verified": [catalog_model(db, verified=None), catalog_model(db, verified="g0")],
    }
    for outcome, models in refused.items():
        for model in models:
            assert _set_org(db, operator, customer, [good, model], good) == (outcome, customer), model
    for models, default in (([good], other), ([good], None), ([], good)):
        assert _set_org(db, operator, customer, models, default) == ("default_not_allowed", customer)
    assert _allowed(db, customer) == []
    assert as_owner(
        db, "SELECT count(*) FROM platform_audit WHERE subject_customer_id = :c", {"c": customer}
    ) == [(0,)]


def test_an_organisations_models_and_budget_are_replaced_and_audited_in_both_logs(db: Database) -> None:
    operator = new_operator(db)
    customer, _ = new_customer(db)
    good, other = catalog_model(db), catalog_model(db)

    assert _set_org(db, operator, customer, [other, good, other], good, 12.5) == ("done", customer)
    assert _allowed(db, customer) == sorted([(good, True), (other, False)])
    with _operator(db).operator() as conn:
        org = conn.execute(_ORG, {"o": operator, "c": customer}).mappings().one()
    assert (sorted(org["models"]), org["default_scan"], float(org["ai_budget_usd"])) == (
        sorted([good, other]),
        good,
        12.5,
    )
    logged = as_owner(
        db,
        "SELECT actor_user_id, target_id, details FROM audit_log"
        " WHERE customer_id = :c AND action = 'org.models_set'",
        {"c": customer},
    )
    assert logged == [
        (
            None,
            customer,
            {"by": "operator", "models": sorted([good, other]), "default": good, "budget_usd": 12.5},
        )
    ]
    platform = "SELECT operator_id, action, target_type, target_id FROM platform_audit"
    platform += " WHERE subject_customer_id = :c"
    assert as_owner(db, platform, {"c": customer}) == [
        (uuid.UUID(operator), "org.models_set", "customer", customer)
    ]

    assert _set_org(db, operator, customer, [], None) == ("done", customer)  # back to OCR_MODEL, no budget
    assert _allowed(db, customer) == []
    with _operator(db).operator() as conn:
        cleared = conn.execute(_ORG, {"o": operator, "c": customer}).mappings().one()
    assert (cleared["models"], cleared["default_scan"], cleared["ai_budget_usd"]) == ([], None, None)

    with pytest.raises(DBAPIError) as refused:
        _set_org(db, operator, customer, [good], good, 0)
    assert violation(refused.value) == ("23514", "customers_ai_budget_usd_check")


def test_an_organisations_models_need_an_enabled_operator_and_a_customer_not_being_deleted(
    db: Database,
) -> None:
    customer, _ = new_customer(db)
    good = catalog_model(db)
    for operator in (new_operator(db, disabled=True), new_operator(db, confirmed=False), str(uuid.uuid4())):
        assert _set_org(db, operator, customer, [good], good) == ("signed_out", None)
        with _operator(db).operator() as conn:
            assert conn.execute(_ORG, {"o": operator, "c": customer}).all() == []
            assert conn.execute(_CATALOG, {"o": operator}).all() == []
    operator = new_operator(db)
    assert _set_org(db, operator, str(uuid.uuid4()), [good], good) == ("missing", None)
    as_owner(
        db,
        "UPDATE customers SET status = 'deleting', disabled_at = now(), delete_after = now() WHERE id = :c",
        {"c": customer},
    )
    assert _set_org(db, operator, customer, [good], good) == ("deleting", customer)
    assert _allowed(db, customer) == []


def test_switching_a_model_on_or_off_takes_a_fresh_code_and_keeps_the_rules(db: Database) -> None:
    operator = new_operator(db)
    model = catalog_model(db, enabled=False)
    assert _set_enabled(db, operator, model, True, None) == "step_up"  # no code
    assert _set_enabled(db, operator, model, True, 100) == "done"
    assert _set_enabled(db, operator, model, True, 100) == "step_up"  # the same code again
    assert _set_enabled(db, operator, model, True, 101) == "unchanged"
    assert (
        _set_enabled(db, operator, catalog_model(db, eligible=False, enabled=False), True, 102)
        == "not_eligible"
    )
    assert (
        _set_enabled(db, operator, catalog_model(db, verified="g0", enabled=False), True, 103)
        == "not_verified"
    )
    assert (
        _set_enabled(db, operator, catalog_model(db, verified=None, enabled=False), True, 104)
        == "not_verified"
    )
    assert _set_enabled(db, operator, "test-none/never-listed", True, 105) == "missing"

    customer, _ = new_customer(db)
    allow(db, customer, [model], model)
    assert (
        _set_enabled(db, operator, model, False, 106) == "in_use"
    )  # remove it from every organisation first
    as_owner(db, "DELETE FROM customer_models WHERE customer_id = :c", {"c": customer})
    assert _set_enabled(db, operator, model, False, 107) == "done"
    assert _set_enabled(db, new_operator(db, disabled=True), model, True, 108) == "step_up"
    with pytest.raises(DBAPIError) as refused:
        _set_enabled(db, operator, model, None, 109)
    assert violation(refused.value)[0] == "23514"

    assert as_owner(db, "SELECT enabled_at, enabled_by FROM model_catalog WHERE id = :m", {"m": model}) == [
        (None, None)
    ]
    rows = "SELECT action, target_type, target_id FROM platform_audit WHERE operator_id = :o ORDER BY id"
    assert as_owner(db, rows, {"o": operator}) == [
        ("model.enabled", "model", model),
        ("model.disabled", "model", model),
    ]


def test_the_catalogue_and_the_spend_the_operator_sees_carry_no_user_or_document(db: Database) -> None:
    operator = new_operator(db)
    model = catalog_model(db, name="Seen")
    now = datetime.now(UTC)
    customers = []
    for cost in (1, 2):
        customer, admin = new_customer(db, name=f"Org spend {cost}")
        customers.append(customer)
        with db.customer(customer) as conn:
            document = insert_document(conn, customer, display_name="secret-plan.pdf", uploaded_by=admin)
            add_usage(
                conn, customer, user_id=admin, document_id=document, model=model, cost_usd=cost, calls=2
            )
            add_usage(conn, customer, model=model, cached=True, calls=3, cost_usd=0)
            add_usage(
                conn, customer, model=model, cost_usd=50, created_at=now - timedelta(days=40)
            )  # outside
    with _operator(db).operator() as conn:
        catalogue = conn.execute(_CATALOG, {"o": operator}).mappings().all()
        spend = conn.execute(
            _USAGE, {"o": operator, "start": now - timedelta(days=1), "end": now + timedelta(days=1)}
        )
        rows = spend.mappings().all()
    assert [row["name"] for row in catalogue if row["id"] == model] == ["Seen"]
    assert "enabled_by" not in catalogue[0]
    ours = [row for row in rows if str(row["customer_id"]) in customers]
    assert [list(row.keys()) for row in ours] == [USAGE_COLUMNS] * 2
    assert sorted(
        (str(row["customer_id"]), row["calls"], row["cached_calls"], float(row["cost_usd"])) for row in ours
    ) == sorted([(customers[0], 5, 3, 1.0), (customers[1], 5, 3, 2.0)])
    assert "secret-plan" not in str(rows)
