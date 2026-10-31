"""Only the super admin controls the scan cost settings, and one organisation's never reach
another's (#463, Tushar, 31 Oct 2026; 0064).

* No customer session reaches the operator's routes, the estimate included; the app role
  writes neither settings table (test_migration_0064_db) and runs none of the operator's
  functions (test_isolation_ai_settings).
* An organisation's own routes neither take nor show the new fields: /api/ai-settings refuses
  an extra key and stores nothing; an upload's extra form fields are never read.
* A member or admin can't raise thinking above the operator's level: High and Max are offered
  only when the scan level is high (``ai_settings.scan_ceiling``), so with minimal they are
  refused.
* An operator's save for organisation A changes nothing of B's, and A's estimate shares only
  A's own calls (the cost a page is every organisation's ledger, by design)."""

from typing import Any

import pytest

from kaagaz.db import models
from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_model_choice import upload
from tests.test_api_support import CSRF, Api
from tests.test_isolation_ai_settings import platform, set_org
from tests.test_operator_models_db import _signed_in
from tests.test_scan_cost_settings_db import SETTINGS, levels, reading_of, scan_model, spent

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name
GEMINI = make_settings(ocr_engine="gemini")
SAVED = ops.AiChoice(scan_resolution="low", careful_thinking="minimal", careful_resolution="low")
NEW_FIELDS = ("scan_resolution", "careful_thinking", "careful_resolution", "page_max_output_tokens")


def test_no_customer_session_reaches_the_estimate(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin("Admin No Estimate")
    member = api.sign_in(admin.customer_id)
    for signed in (admin, member):
        for body in ({}, {"customer_id": signed.customer_id}):
            reply = signed.client.post("/api/operator/scan-cost", json=body, headers=CSRF)
            assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")
    assert api.client().post("/api/operator/scan-cost", json={}, headers=CSRF).status_code == 401


def test_an_organisations_own_routes_neither_take_nor_show_the_new_fields(
    db: Database, platform: str
) -> None:
    api = Api(db, settings=GEMINI)
    admin = api.new_admin("Own Routes Org")
    member = api.sign_in(admin.customer_id)
    shown = member.client.get("/api/ai-settings")
    assert shown.status_code == 200
    assert not any(field in shown.text for field in (*NEW_FIELDS, "resolution", "minimal", "tokens"))
    for signed, path, body in (
        (member, "me", {"tasks": {}, "scan_resolution": "high"}),
        (admin, "organisation", {"tasks": {}, "careful_thinking": "high"}),
        (admin, "organisation", {"tasks": {}, "page_max_output_tokens": 65536}),
        (member, "me", {"tasks": {}, "page_max_output_tokens": 65536}),
        (admin, "organisation", {"tasks": {"scan": {"level": "low", "careful_resolution": "high"}}}),
    ):
        reply = signed.client.put(f"/api/ai-settings/{path}", json=body, headers=CSRF)
        assert reply.status_code == 400, body
    assert levels(reading_of(db, admin.customer_id)) == (None, None, None)  # nothing stored

    # An upload's extra form fields are never read: the document reads at the organisation's.
    assert set_org(db, platform, admin.customer_id, SAVED).code == "done"
    sent = upload(member, scan_resolution="high", careful_thinking="high", careful_resolution="high")
    assert sent.status_code in (200, 201), sent.text
    document = sent.json()["document"]["id"]
    with db.customer(admin.customer_id) as conn:
        reading = models.resolve_scan(conn, admin.customer_id, document, settings=SETTINGS)
    assert levels(reading) == ("low", "minimal", "low")


def test_a_member_or_admin_never_raises_thinking_above_the_operators_level(
    db: Database, platform: str
) -> None:
    api = Api(db, settings=GEMINI)
    admin = api.new_admin("No Raising Org")
    member = api.sign_in(admin.customer_id)
    lead = scan_model(db)
    assert (
        set_org(db, platform, admin.customer_id, ops.AiChoice([lead], lead, scan_thinking="minimal")).code
        == "done"
    )
    scan = {task["task"]: task for task in member.client.get("/api/ai-settings").json()["tasks"]}["scan"]
    assert "high" not in scan["allowed"] and "max" not in scan["allowed"]
    for signed, path, tasks in (
        (member, "me", {"scan": "high"}),
        (member, "me", {"scan": "max"}),
        (admin, "organisation", {"scan": {"level": "max", "locked": True}}),
    ):
        reply = signed.client.put(f"/api/ai-settings/{path}", json={"tasks": tasks}, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (400, "not_allowed"), tasks
    assert reading_of(db, admin.customer_id).thinking == "minimal"


def test_a_save_for_one_organisation_never_touches_another(db: Database, platform: str) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    a, b = api.customer("Org Cost A")[0], api.customer("Org Cost B")[0]
    lead = scan_model(db)
    assert set_org(db, platform, a, ops.AiChoice([lead], lead, dynamic=True, **_fields(SAVED))).code == "done"
    assert levels(reading_of(db, a)) == ("low", "minimal", "low")
    assert levels(reading_of(db, b)) == (None, None, None)
    with db.customer(b) as conn:
        effective = dict(models.effective_settings(conn, b))
    assert {field: effective[field] for field in NEW_FIELDS} == dict.fromkeys(NEW_FIELDS)

    # A's share read again is A's alone; B's calls never count in it.
    spent(db, a, lead, calls=10, thinking="minimal")
    spent(db, a, lead, kind="careful", calls=1, thinking="minimal", resolution="low")
    spent(db, b, lead, calls=1000, thinking="minimal")
    spent(db, b, lead, kind="careful", calls=900, thinking="minimal", resolution="low")
    # Changed on purpose (#463 review, U2): the draft's Cost saving is sent (null: the platform's).
    body = {"customer_id": a, **_fields(SAVED), "scan_thinking": None, "dynamic": True}
    reply = client.post("/api/operator/scan-cost", json=body, headers=CSRF).json()
    assert reply["re_read"]["share"] == pytest.approx(0.1)


def _fields(choice: ops.AiChoice) -> dict[str, Any]:
    return {field: getattr(choice, field) for field in NEW_FIELDS}
