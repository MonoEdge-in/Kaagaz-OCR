"""The operator's models and AI spend against PostgreSQL (model choice, council 11 Oct 2026,
MC2, MS1, MS2, SP3, SP4; 0023; features 25-27, C10, 0026), through the API: enabling a
model takes a fresh code and a verify-models pass for the current prompt, an
organisation's AI settings and budget, the platform defaults, the outcome of each refusal,
and the owner's verify-models record. A usage reply with no user or
document: ``test_isolation_operator_models.py``. Customer sessions never reach these
routes: ``test_operator_auth_db.py``'s walk covers every /api/operator route."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from kaagaz.api import totp
from kaagaz.cli import _record_verified
from kaagaz.db.engine import Database
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from tests.test_api_support import CSRF, Api, TestClient, wrong_code
from tests.test_operator_auth_db import _Operator, _owner

pytestmark = pytest.mark.db

_MODEL = (
    "INSERT INTO model_catalog (id, name, price_in_usd_per_m, price_out_usd_per_m, providers, efforts,"
    " eligible, verified_prompt_version, verified_at)"
    " VALUES (:id, :name, 2, 12, '{google-vertex}', '{low,high}', :eligible, :version,"
    " CASE WHEN CAST(:version AS text) IS NULL THEN NULL ELSE now() END)"
)
_PLATFORM = (
    "SELECT action, target_id FROM platform_audit WHERE operator_id = :o AND action LIKE :a ORDER BY id"
)


def _model(db: Database, *, eligible: bool = True, verified: bool = True) -> str:
    model_id = f"example/test-{uuid.uuid4().hex[:12]}"
    version = READER_PROMPT_VERSION if verified else None
    _owner(db, _MODEL, {"id": model_id, "name": f"Test {model_id}", "eligible": eligible, "version": version})
    return model_id


def _signed_in(api: Api) -> tuple[_Operator, TestClient]:
    operator, client = _Operator(api), api.client()
    assert operator.login(client).status_code == 200
    api.clock.advance(totp.STEP_SECONDS)  # the sign-in used this step's code
    return operator, client


def _enable(api: Api, operator: _Operator, client: TestClient, model: str, enabled: bool = True) -> Any:
    api.clock.advance(totp.STEP_SECONDS)  # a fresh code each time
    body = {"enabled": enabled, "code": api.code(operator.secret)}
    return client.put(f"/api/operator/models/{model}", json=body, headers=CSRF)


def test_enabling_takes_a_fresh_code_and_a_pass_for_the_current_prompt(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    good, unverified, ineligible = _model(db), _model(db, verified=False), _model(db, eligible=False)

    [listed] = [m for m in client.get("/api/operator/models").json()["items"] if m["id"] == good]
    assert (listed["verified"], listed["enabled_at"], listed["providers"]) == (True, None, ["google-vertex"])

    wrong = client.put(
        f"/api/operator/models/{good}",
        json={"enabled": True, "code": wrong_code(api, operator.secret)},
        headers=CSRF,
    )
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (403, "step_up_failed")
    assert _enable(api, operator, client, unverified).json()["error"]["code"] == "not_verified"
    assert _enable(api, operator, client, ineligible).json()["error"]["code"] == "not_eligible"
    assert _enable(api, operator, client, "example/not-in-the-catalogue").status_code == 404
    done = _enable(api, operator, client, good)
    assert done.json() == {"model": {"id": good, "enabled": True, "changed": True}}
    assert _enable(api, operator, client, good).json()["model"]["changed"] is False

    [listed] = [m for m in client.get("/api/operator/models").json()["items"] if m["id"] == good]
    assert listed["enabled_at"] is not None
    assert operator.sql(_PLATFORM, a="model.%") == [("model.enabled", good)]


# Changed on purpose (features 25-27, 15 Oct 2026): /orgs/{id}/models became /orgs/{id}/ai,
# with the answer models, the levels, Dynamic, Ensemble and a fresh code for every save.
# Changed on purpose (#463, 31 Oct 2026): the scan cost settings too.
FOLLOWS_DEFAULTS: dict[str, Any] = {
    "scan_models": [],
    "scan_default": None,
    "chat_models": [],
    "chat_default": None,
    "scan_thinking": None,
    "scan_resolution": None,
    "careful_thinking": None,
    "careful_resolution": None,
    "page_max_output_tokens": None,
    "scan_reader": None,  # Changed on purpose (#473): who reads the pages, and the Mistral model
    "mistral_model": None,
    "daily_page_limit": None,  # Changed on purpose (#478)
    "chat_levels": None,
    "chat_default_level": None,
    "dynamic": None,
}


def _save(api: Api, operator: _Operator, client: TestClient, path: str, body: dict[str, Any]) -> Any:
    api.clock.advance(totp.STEP_SECONDS)  # a fresh code each time
    return client.put(path, json={**body, "code": api.code(operator.secret)}, headers=CSRF)


def test_an_organisations_ai_settings_and_budget(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    first, second, off = _model(db), _model(db), _model(db)
    for model in (first, second):
        assert _enable(api, operator, client, model).status_code == 200
    customer, _ = api.customer("Org Models")
    path = f"/api/operator/orgs/{customer}/ai"
    got = client.get(path).json()
    assert (got["own"], got["ensemble"], got["ai_budget_usd"], got["max_agents"]) == (
        FOLLOWS_DEFAULTS,
        False,
        None,
        3,
    )

    body = {
        **FOLLOWS_DEFAULTS,
        "scan_models": [second, first],
        "scan_default": second,
        "ensemble": False,
        "ai_budget_usd": 25.5,
    }
    saved = _save(api, operator, client, path, body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["own"]["scan_models"] == sorted([first, second])
    assert (saved.json()["own"]["scan_default"], saved.json()["ai_budget_usd"]) == (second, 25.5)
    assert {**client.get(path).json(), "ensemble_off": False} == saved.json()
    assert operator.sql(_PLATFORM, a="org.ai_settings_set") == [("org.ai_settings_set", customer)]
    [row] = [row for row in api.audit_rows(customer) if row[0] == "org.ai_settings_set"]
    assert row[3]["by"] == "operator" and row[3]["scan_default"] == second
    assert not [key for key in row[3] if "budget" in key or "usd" in key]  # money stays with the operator

    refusals = [
        ({**body, "scan_models": [first, off], "scan_default": first}, "unavailable"),  # off: not enabled
        ({**body, "scan_default": off}, "default_not_allowed"),
        ({**body, "scan_default": None}, "default_not_allowed"),
    ]
    for refused, code in refusals:
        reply = _save(api, operator, client, path, refused)
        assert (reply.status_code, reply.json()["error"]["code"]) == (409, code), reply.text
    assert {**client.get(path).json(), "ensemble_off": False} == saved.json()  # nothing changed
    # Changed on purpose (Tushar, 16 Oct 2026: any number of models): one scanning model with
    # Cost saving and Ensemble saves, Ensemble off and said so.
    one = {**body, "scan_models": [first], "scan_default": first, "dynamic": True, "ensemble": True}
    alone = _save(api, operator, client, path, one)
    assert alone.status_code == 200, alone.text
    assert (alone.json()["own"]["dynamic"], alone.json()["ensemble"], alone.json()["ensemble_off"]) == (
        True,
        False,
        True,
    )
    assert client.get(f"/api/operator/orgs/{uuid.uuid4()}/ai").status_code == 404
    assert _save(api, operator, client, f"/api/operator/orgs/{uuid.uuid4()}/ai", body).status_code == 404

    in_use = _enable(api, operator, client, first, enabled=False)
    assert (in_use.status_code, in_use.json()["error"]["code"]) == (409, "in_use")
    cleared = _save(
        api, operator, client, path, {**FOLLOWS_DEFAULTS, "ensemble": False, "ai_budget_usd": None}
    )
    assert (cleared.json()["own"], cleared.json()["ai_budget_usd"]) == (FOLLOWS_DEFAULTS, None)
    assert _enable(api, operator, client, first, enabled=False).json()["model"]["changed"] is True


def test_operator_rate_limits_are_validated_dynamic_and_audited(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    customer, admin_id = api.customer("RPM settings")
    member_id, _ = api.add_user(customer, "member")
    path = f"/api/operator/orgs/{customer}/model-rates"

    initial = client.get(path)
    assert initial.status_code == 200
    assert initial.json()["organisation_rpm"] is None
    assert {row["id"] for row in initial.json()["users"]} == {admin_id, member_id}

    api.clock.advance(totp.STEP_SECONDS)
    org_saved = client.put(path, json={"rpm": 600, "code": api.code(operator.secret)}, headers=CSRF)
    assert org_saved.status_code == 200, org_saved.text
    assert org_saved.json()["organisation_rpm"] == 600

    user_path = f"/api/operator/orgs/{customer}/users/{member_id}/model-rate"
    api.clock.advance(totp.STEP_SECONDS)
    user_saved = client.put(user_path, json={"rpm": 120, "code": api.code(operator.secret)}, headers=CSRF)
    assert user_saved.status_code == 200, user_saved.text
    assert next(row["rpm"] for row in user_saved.json()["users"] if row["id"] == member_id) == 120

    api.clock.advance(totp.STEP_SECONDS)
    inherited = client.put(user_path, json={"rpm": None, "code": api.code(operator.secret)}, headers=CSRF)
    assert inherited.status_code == 200, inherited.text
    assert next(row["rpm"] for row in inherited.json()["users"] if row["id"] == member_id) is None

    api.clock.advance(totp.STEP_SECONDS)
    org_inherited = client.put(path, json={"rpm": None, "code": api.code(operator.secret)}, headers=CSRF)
    assert org_inherited.status_code == 200, org_inherited.text
    assert org_inherited.json()["organisation_rpm"] is None

    # Fixed (#239, 27 Oct 2026): a body the API refuses is its 400 bad_request (api/errors.py), never 422.
    for refused in (
        client.put(path, json={"rpm": 601, "code": "000000"}, headers=CSRF),
        client.put(user_path, json={"rpm": 121, "code": "000000"}, headers=CSRF),
    ):
        assert (refused.status_code, refused.json()["error"]["code"]) == (400, "bad_request")
    assert (
        client.put(
            user_path, json={"rpm": 60, "code": wrong_code(api, operator.secret)}, headers=CSRF
        ).status_code
        == 403
    )
    assert operator.sql(_PLATFORM, a="model.rpm_set") == [
        ("model.rpm_set", customer),
        ("model.rpm_set", member_id),
        ("model.rpm_set", member_id),
        ("model.rpm_set", customer),
    ]


def test_operator_rate_limit_settings_default_existing_organisations(db: Database) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    customer, _ = api.customer("Existing RPM default")
    row = client.get(f"/api/operator/orgs/{customer}/model-rates")
    assert row.status_code == 200
    assert row.json()["organisation_rpm"] is None


def test_the_platform_defaults_through_the_api(db: Database) -> None:
    """C10, D8: every organisation still on the defaults follows a save; put back after."""
    api = Api(db)
    operator, client = _signed_in(api)
    first, second = _model(db), _model(db)
    for model in (first, second):
        assert _enable(api, operator, client, model).status_code == 200
    api.customer("Org On Defaults")
    got = client.get("/api/operator/ai-defaults").json()
    assert got["orgs_inheriting"]["scan_models"] >= 1
    put_back = {**got["defaults"]}
    body = {**put_back, "scan_models": [first, second], "scan_default": first, "dynamic": True}
    try:
        saved = _save(api, operator, client, "/api/operator/ai-defaults", body)
        assert saved.status_code == 200, saved.text
        assert saved.json()["defaults"]["scan_models"] == sorted([first, second])
        assert saved.json()["defaults"]["dynamic"] is True
        # Changed on purpose (Tushar, 16 Oct 2026: any number of models): Cost saving with one.
        one = {**body, "scan_models": [first]}
        alone = _save(api, operator, client, "/api/operator/ai-defaults", one)
        assert alone.status_code == 200, alone.text
        assert (alone.json()["defaults"]["scan_models"], alone.json()["defaults"]["dynamic"]) == (
            [first],
            True,
        )
    finally:
        assert _save(api, operator, client, "/api/operator/ai-defaults", put_back).status_code == 200


def test_record_writes_a_models_pass_as_the_owner(db: Database) -> None:
    model = _model(db, verified=False)
    _record_verified(db, model, 1290, 830)
    [row] = _owner(
        db,
        "SELECT verified_prompt_version, verified_at IS NOT NULL, tokens_per_page_in, tokens_per_page_out"
        " FROM model_catalog WHERE id = :m",
        {"m": model},
    )
    assert tuple(row) == (READER_PROMPT_VERSION, True, 1290, 830)
    # R10: audited as the other owner commands, with no operator.
    assert [
        tuple(audit)
        for audit in _owner(
            db,
            "SELECT operator_id, action, target_type, target_id FROM platform_audit WHERE target_id = :m",
            {"m": model},
        )
    ] == [(None, "model.verified", "model", model)]
    with db.system() as conn, pytest.raises(ProgrammingError, match="permission denied"):  # never the app
        conn.execute(text("UPDATE model_catalog SET verified_at = now() WHERE id = :m"), {"m": model})


def test_the_catalogue_averages_a_scanned_page_over_every_organisations_ledger(db: Database) -> None:
    """R3, MC3: the operator's estimate a page is the average tokens of the last 30 days of
    non-cached scan calls, every organisation's together: numbers only, no names."""
    api = Api(db)
    _, client = _signed_in(api)
    measured, unmeasured = _model(db), _model(db)
    first, _ = api.customer("Ledger one")
    second, _ = api.customer("Ledger two")
    for customer, tokens in ((first, (4000, 1000)), (second, (2000, 3000))):
        with db.customer(customer) as conn:
            conn.execute(
                text(
                    "INSERT INTO model_usage (customer_id, kind, model, calls, input_tokens, output_tokens,"
                    " reasoning_tokens) VALUES (:c, 'scan', :m, 1, :i, :o, 0)"
                ),
                {"c": customer, "m": measured, "i": tokens[0], "o": tokens[1]},
            )
    items = {item["id"]: item for item in client.get("/api/operator/models").json()["items"]}
    assert (items[measured]["ledger_tokens_in"], items[measured]["ledger_tokens_out"]) == (3000.0, 2000.0)
    assert (items[unmeasured]["ledger_tokens_in"], items[unmeasured]["ledger_tokens_out"]) == (None, None)


_SPEND = (
    "INSERT INTO model_usage (customer_id, kind, model, cached, calls, input_tokens, output_tokens,"
    " reasoning_tokens, cost_usd, cost_estimated, created_at)"
    " VALUES (:c, :kind, :m, :cached, :calls, 1000, 200, 0, CAST(:cost AS numeric), :estimated,"
    " now() - make_interval(days => :ago))"
)


def test_the_catalogue_says_the_real_cost_of_scanning_and_answering_and_never_guesses(db: Database) -> None:
    """0033: per model, every organisation's ledger of the last 30 days together: the cost
    OpenRouter reported for pages read (scan, upgrade, careful, ensemble_scan; a cached
    page is none) and questions answered (chat, ensemble_chat); a NULL or an estimated cost
    is counted as unknown calls, never added; other kinds and older rows are left out; and
    the reply names no organisation."""
    api = Api(db)
    _, client = _signed_in(api)
    model, unused = _model(db), _model(db)
    first, _ = api.customer("Real cost one")
    second, _ = api.customer("Real cost two")
    rows = [  # (customer, kind, cached, calls, cost, estimated, days ago)
        (first, "scan", False, 10, "0.30", False, 0),
        (first, "careful", False, 2, "0.10", False, 0),
        (first, "ensemble_scan", False, 3, None, False, 0),  # no cost reported: unknown
        (first, "upgrade", False, 1, "0.05", True, 0),  # the list price's guess: unknown
        (first, "scan", True, 5, "0", False, 0),  # cached pages: no call, not a page
        (first, "scan", False, 7, "9.99", False, 31),  # outside the 30 days
        (second, "scan", False, 4, "0.20", False, 0),
        (second, "chat", False, 3, "0.06", False, 0),
        (second, "chat", False, 2, "0.04", False, 0),
        (second, "ensemble_chat", False, 1, None, False, 0),
        (second, "embed", False, 50, "1.00", False, 0),  # neither scanning nor answering
        (second, "card", False, 1, "0.50", False, 0),
    ]
    for customer, kind, cached, calls, cost, estimated, ago in rows:
        _owner(
            db,
            _SPEND,
            {
                "c": customer,
                "kind": kind,
                "m": model,
                "cached": cached,
                "calls": calls,
                "cost": cost,
                "estimated": estimated,
                "ago": ago,
            },
        )
    reply = client.get("/api/operator/models")
    items = {item["id"]: item for item in reply.json()["items"]}
    keys = (
        "scan_pages",
        "scan_pages_costed",
        "scan_cost_usd",
        "scan_cost_unknown_calls",
        "answer_questions",
        "answer_questions_costed",
        "answer_cost_usd",
        "answer_cost_unknown_calls",
    )
    assert tuple(items[model][key] for key in keys) == (20, 16, 0.6, 4, 3, 2, 0.1, 1)
    assert tuple(items[unused][key] for key in keys) == (0, 0, None, 0, 0, 0, None, 0)
    assert "Real cost one" not in reply.text and "Real cost two" not in reply.text
