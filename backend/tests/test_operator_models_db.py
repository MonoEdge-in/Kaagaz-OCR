"""The operator's models and AI spend against PostgreSQL (model choice, council 11 Oct 2026,
MC2, MS1, MS2, SP3, SP4; 0023), through the API: enabling a model takes a fresh code and a
verify-models pass for the current prompt, an organisation's models and budget, the outcome
of each refusal, and the owner's verify-models record. A usage reply with no user or
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


def test_an_organisations_models_default_and_budget(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    first, second, off = _model(db), _model(db), _model(db)
    for model in (first, second):
        assert _enable(api, operator, client, model).status_code == 200
    customer, _ = api.customer("Org Models")
    path = f"/api/operator/orgs/{customer}/models"
    assert client.get(path).json() == {"models": [], "default_scan": None, "ai_budget_usd": None}

    body = {"models": [second, first], "default_scan": second, "ai_budget_usd": 25.5}
    saved = client.put(path, json=body, headers=CSRF)
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"models": sorted([first, second]), "default_scan": second, "ai_budget_usd": 25.5}
    assert client.get(path).json() == saved.json()
    assert operator.sql(_PLATFORM, a="org.models_set") == [("org.models_set", customer)]
    [row] = [row for row in api.audit_rows(customer) if row[0] == "org.models_set"]
    assert row[3]["by"] == "operator" and row[3]["default"] == second

    refusals = [
        ({**body, "models": [first, off], "default_scan": first}, 409, "unavailable"),  # off: not enabled
        ({**body, "default_scan": off}, 400, "default_not_allowed"),
        ({**body, "default_scan": None}, 400, "default_not_allowed"),
    ]
    for refused, status, code in refusals:
        reply = client.put(path, json=refused, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (status, code), reply.text
    assert client.get(path).json() == saved.json()  # nothing changed
    assert client.get(f"/api/operator/orgs/{uuid.uuid4()}/models").status_code == 404
    assert client.put(f"/api/operator/orgs/{uuid.uuid4()}/models", json=body, headers=CSRF).status_code == 404

    in_use = _enable(api, operator, client, first, enabled=False)
    assert (in_use.status_code, in_use.json()["error"]["code"]) == (409, "in_use")
    cleared = client.put(path, json={"models": [], "default_scan": None, "ai_budget_usd": None}, headers=CSRF)
    assert cleared.json() == {"models": [], "default_scan": None, "ai_budget_usd": None}
    assert _enable(api, operator, client, first, enabled=False).json()["model"]["changed"] is True


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
