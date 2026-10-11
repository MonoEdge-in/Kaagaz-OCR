"""Model choice and AI spend across organisations, for the operator (council 11 Oct 2026,
SP3, MS2; 0023), against PostgreSQL: the operator's usage reply holds organisations and
models only, never a user's or a document's id or name; one organisation's models never
show in another's; and a customer admin's session reaches none of these routes."""

import pytest

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api
from tests.test_operator_auth_db import _owner
from tests.test_operator_models_db import _enable, _model, _signed_in

pytestmark = pytest.mark.db

_USAGE = (
    "INSERT INTO model_usage (customer_id, user_id, document_id, kind, model, calls, input_tokens,"
    " output_tokens, reasoning_tokens, cost_usd, cost_estimated, created_at)"
    " VALUES (:c, :u, :d, 'scan', :m, :calls, 1000, 200, 50, :cost, false, :at)"
)


def test_usage_is_per_organisation_and_model_and_never_names_a_user_or_a_document(db: Database) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    model = _model(db)
    a, _ = api.customer("Org Spend A")
    b, _ = api.customer("Org Spend B")
    member, member_email = api.add_user(a, "member")
    document = api.add_document(a, name="secret-ledger.pdf", uploaded_by=member)
    for customer, user, doc, calls, cost in (
        (a, member, document, 3, "0.5"),
        (a, None, None, 1, "0.25"),
        (b, None, None, 2, None),
    ):
        _owner(
            db,
            _USAGE,
            {"c": customer, "u": user, "d": doc, "m": model, "calls": calls, "cost": cost, "at": api.clock()},
        )

    reply = client.get("/api/operator/usage")  # this month so far, on the app's clock
    assert reply.status_code == 200, reply.text
    rows = {row["organisation_id"]: row for row in reply.json()["items"] if row["model"] == model}
    assert rows[a]["calls"] == 4 and rows[a]["cost_usd"] == 0.75 and rows[a]["input_tokens"] == 2000
    assert rows[a]["organisation_name"] == "Org Spend A"
    assert (rows[b]["calls"], rows[b]["cost_usd"]) == (2, None)
    for row in rows.values():
        assert not {key for key in row if "user" in key or "document" in key}
    for hidden in (member, member_email, document, "secret-ledger", "Test member"):
        assert hidden not in reply.text


def test_one_organisations_models_never_show_in_anothers(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    model = _model(db)
    assert _enable(api, operator, client, model).status_code == 200
    a, _ = api.customer("Org Models A")
    b, _ = api.customer("Org Models B")
    body = {"models": [model], "default_scan": model, "ai_budget_usd": 10}
    assert client.put(f"/api/operator/orgs/{a}/models", json=body, headers=CSRF).status_code == 200
    assert client.get(f"/api/operator/orgs/{b}/models").json() == {
        "models": [],
        "default_scan": None,
        "ai_budget_usd": None,
    }


def test_a_customer_admins_session_reaches_no_model_or_spend_route(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin("Org Admin Only")
    for method, path in (
        ("GET", "/api/operator/models"),
        ("GET", f"/api/operator/orgs/{admin.customer_id}/models"),
        ("PUT", f"/api/operator/orgs/{admin.customer_id}/models"),
        ("GET", "/api/operator/usage"),
    ):
        body: dict[str, object] = {"models": [], "default_scan": None, "ai_budget_usd": None}
        reply = admin.client.request(method, path, headers=CSRF, json=body)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), path
