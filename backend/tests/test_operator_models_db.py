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
FOLLOWS_DEFAULTS: dict[str, Any] = {
    "scan_models": [],
    "scan_default": None,
    "chat_models": [],
    "chat_default": None,
    "scan_thinking": None,
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
    assert client.get(path).json() == saved.json()
    assert operator.sql(_PLATFORM, a="org.ai_settings_set") == [("org.ai_settings_set", customer)]
    [row] = [row for row in api.audit_rows(customer) if row[0] == "org.ai_settings_set"]
    assert row[3]["by"] == "operator" and row[3]["scan_default"] == second
    assert not [key for key in row[3] if "budget" in key or "usd" in key]  # money stays with the operator

    refusals = [
        ({**body, "scan_models": [first, off], "scan_default": first}, "unavailable"),  # off: not enabled
        ({**body, "scan_default": off}, "default_not_allowed"),
        ({**body, "scan_default": None}, "default_not_allowed"),
        ({**body, "scan_models": [first], "scan_default": first, "ensemble": True}, "needs_two_models_scan"),
    ]
    for refused, code in refusals:
        reply = _save(api, operator, client, path, refused)
        assert (reply.status_code, reply.json()["error"]["code"]) == (409, code), reply.text
    assert client.get(path).json() == saved.json()  # nothing changed
    assert client.get(f"/api/operator/orgs/{uuid.uuid4()}/ai").status_code == 404
    assert _save(api, operator, client, f"/api/operator/orgs/{uuid.uuid4()}/ai", body).status_code == 404

    in_use = _enable(api, operator, client, first, enabled=False)
    assert (in_use.status_code, in_use.json()["error"]["code"]) == (409, "in_use")
    cleared = _save(
        api, operator, client, path, {**FOLLOWS_DEFAULTS, "ensemble": False, "ai_budget_usd": None}
    )
    assert (cleared.json()["own"], cleared.json()["ai_budget_usd"]) == (FOLLOWS_DEFAULTS, None)
    assert _enable(api, operator, client, first, enabled=False).json()["model"]["changed"] is True


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
        one = {**body, "scan_models": [first]}
        refused = _save(api, operator, client, "/api/operator/ai-defaults", one)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "needs_two_models_scan")
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
