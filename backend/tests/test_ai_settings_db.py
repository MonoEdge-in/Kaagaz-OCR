"""AI settings by task, the feature switches and the platform test model (0038) against
PostgreSQL: the API's shape and role checks, the lock and the ceiling, the audit rows, and
each behaviour it wires (Ask at None and capped, a scan at None, auto-filing per person,
a switched-off feature). The rule itself: test_ai_settings (no database).

The switches and the test model belong to no customer: every test that sets one puts it back.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api import totp
from kaagaz.db import models
from kaagaz.db.engine import Database
from kaagaz.worker_main import _scanning_on
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api, Signed, TestClient
from tests.test_autofile_db import file, organisation, ready, state, upload
from tests.test_migration_0023_db import allow, catalog_model
from tests.test_operator_auth_db import _Operator, _owner

pytestmark = pytest.mark.db

GEMINI = make_settings(ocr_engine="gemini")
MONEY_OR_MODEL = re.compile(r"usd|price|budget|cost|spend|\$|₹|google/|anthropic/", re.IGNORECASE)
TASKS = ["scan", "ask", "ensemble", "autofile"]


@contextmanager
def switched_off(db: Database, feature: str) -> Iterator[None]:
    """``feature`` off for every organisation (as the operator's function leaves it), then on."""
    _owner(db, "INSERT INTO feature_switches (feature, enabled) VALUES (:f, false)", {"f": feature})
    try:
        yield
    finally:
        _owner(db, "DELETE FROM feature_switches WHERE feature = :f", {"f": feature})


def settings_of(signed: Signed) -> dict[str, Any]:
    reply = signed.client.get("/api/ai-settings")
    assert reply.status_code == 200, reply.text
    return {task["task"]: task for task in reply.json()["tasks"]} | {"_": reply.json()}


def put(signed: Signed, path: str, tasks: dict[str, Any]) -> Any:
    return signed.client.put(f"/api/ai-settings/{path}", json={"tasks": tasks}, headers=CSRF)


def test_the_shape_names_no_model_and_nothing_saved_is_todays_behaviour(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed()
    reply = member.client.get("/api/ai-settings")
    body = reply.json()
    assert [task["task"] for task in body["tasks"]] == TASKS
    assert body["can_edit_organisation"] is False
    assert not MONEY_OR_MODEL.search(reply.text)
    for task in body["tasks"]:
        assert set(task) == {"task", "effective", "mine", "organisation", "locked", "allowed", "available"}
        assert (task["mine"], task["organisation"], task["locked"]) == (None, None, False)
    tasks = {task["task"]: task for task in body["tasks"]}
    assert tasks["ensemble"] == {
        **tasks["ensemble"],
        "effective": "off",
        "allowed": ["off"],
        "available": False,
    }
    assert tasks["ask"]["effective"] == "low"  # the platform's default level (0026 seeds low)
    admin = api.admin_sign_in(member.customer_id)
    assert admin.client.get("/api/ai-settings").json()["can_edit_organisation"] is True


def test_a_member_saves_and_clears_their_own_choice_audited(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed()
    saved = put(member, "me", {"ask": "none"})
    assert saved.status_code == 200, saved.text
    ask = {task["task"]: task for task in saved.json()["tasks"]}["ask"]
    assert (ask["mine"], ask["effective"]) == ("none", "none")
    assert put(member, "me", {"ask": None}).json()["tasks"][1]["mine"] is None
    rows = [row for row in api.audit_rows(member.customer_id) if row[0] == "ai_settings.mine"]
    assert [row[3] for row in rows] == [
        {"task": "ask", "old": None, "new": "none", "locked": False},
        {"task": "ask", "old": "none", "new": None, "locked": False},
    ]
    colleague = api.sign_in(member.customer_id)
    assert settings_of(colleague)["ask"]["mine"] is None  # one person's choice is theirs alone


def test_a_locked_task_is_409_and_a_level_not_allowed_is_400(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id)
    done = put(admin, "organisation", {"ask": {"level": "low", "locked": True}})
    assert done.status_code == 200, done.text
    refused = put(member, "me", {"ask": "none"})
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "locked")
    shown = settings_of(member)["ask"]
    assert (shown["organisation"], shown["locked"], shown["effective"]) == ("low", True, "low")
    for tasks in ({"ensemble": "on"}, {"scan": "on"}, {"ask": "max"}):  # not granted, wrong kind, ceiling
        reply = put(member, "me", tasks)
        assert (reply.status_code, reply.json()["error"]["code"]) == (400, "not_allowed"), tasks
    assert [row for row in api.audit_rows(admin.customer_id) if row[0] == "ai_settings.mine"] == []
    [row] = [row for row in api.audit_rows(admin.customer_id) if row[0] == "ai_settings.organisation"]
    assert row[3] == {"task": "ask", "old": None, "new": "low", "locked": True}


def test_only_an_admin_sets_the_organisations_defaults(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed()
    reply = put(member, "organisation", {"ask": {"level": "none", "locked": True}})
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "forbidden")
    extra = member.client.put("/api/ai-settings/me", json={"tasks": {}, "x": 1}, headers=CSRF)
    assert extra.status_code == 400  # bodies forbid extra fields


def test_ask_at_none_drafts_with_no_model_and_a_level_caps_the_request(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed()
    assert member.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF).status_code == 200
    assert api.answers.choices[-1] is not models.NO_MODEL  # nothing saved: as before
    put(member, "me", {"ask": "none"})
    assert member.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF).status_code == 200
    assert api.answers.choices[-1] is models.NO_MODEL
    put(member, "me", {"ask": "low"})
    body = {"question": "totals?", "thinking": "high"}
    member.client.post("/api/chat", json=body, headers=CSRF)
    choice = api.answers.choices[-1]
    assert choice is not None and choice.thinking == "low"  # the request's own level is capped


def test_switched_off_features_are_403_feature_off(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed()
    document = api.add_document(member.customer_id, name="a.pdf", uploaded_by=member.user_id)
    with switched_off(db, "ask"):
        reply = member.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "feature_off")
        assert settings_of(member)["ask"]["available"] is False
    with switched_off(db, "my_words"):
        assert member.client.get("/api/me/words").json()["error"]["code"] == "feature_off"
    with switched_off(db, "review"):
        reply = member.client.get(f"/api/documents/{document}/flags")
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "feature_off")
    with switched_off(db, "self_signup"):
        assert api.client().get("/api/auth/signup").json() == {"enabled": False}
    assert api.client().get("/api/auth/signup").json() == {"enabled": True}
    assert member.client.get("/api/me/words").status_code == 200  # back on


def test_a_scan_at_none_is_read_locally_and_low_is_automatic(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed()
    lead = catalog_model(db)
    allow(db, member.customer_id, [lead], lead)
    with db.customer(member.customer_id) as conn:
        document = insert_document(conn, member.customer_id, uploaded_by=member.user_id)
        conn.execute(
            text("UPDATE documents SET read_requested_by = :u, read_mode = 'single' WHERE id = :d"),
            {"u": member.user_id, "d": document},
        )

    def reading() -> models.Reading:
        with db.customer(member.customer_id) as conn:
            return models.resolve_scan(conn, member.customer_id, document, settings=GEMINI)

    assert reading().mode == "single"  # nothing saved: the document's own mode
    put(member, "me", {"scan": "none"})
    assert reading().mode == "local"
    put(member, "me", {"scan": "low"})
    low = reading()  # Automatic, which the platform's Dynamic switch (seeded off) still decides
    assert (low.mode, low.notes[-1]) == ("single", "mode_revoked")


def test_auto_filing_follows_the_uploaders_own_choice(db: Database) -> None:
    customer, _ = organisation(db, on=False)  # granted, the admin's switch off
    api = Api(db)
    member = api.sign_in(customer)
    other = api.sign_in(customer)
    put(member, "me", {"autofile": "on"})
    mine, theirs = upload(db, customer, member.user_id), upload(db, customer, other.user_id)
    assert state(db, customer, mine)[0] == "pending"  # an "on" row makes a new upload wait for filing
    for document in (mine, theirs):
        ready(db, customer, document)
    assert (file(db, customer, mine), file(db, customer, theirs)) == ("filed", None)
    assert state(db, customer, mine) == ("filed", "Invoices")  # the folder is made when needed
    with switched_off(db, "autofile"):
        again = upload(db, customer, member.user_id)
        ready(db, customer, again)
        assert file(db, customer, again) == "off"


# --- the super admin's switches and test model ---------------------------------------------------


def _operator(api: Api) -> tuple[_Operator, TestClient]:
    operator, client = _Operator(api), api.client()
    assert operator.login(client).status_code == 200
    return operator, client


def _code(api: Api, operator: _Operator) -> str:
    api.clock.advance(totp.STEP_SECONDS)  # a fresh code each time
    return api.code(operator.secret)


def test_a_feature_switch_takes_a_fresh_code_and_is_audited(db: Database) -> None:
    api = Api(db)
    operator, client = _operator(api)
    listed = client.get("/api/operator/feature-switches").json()
    assert [item["feature"] for item in listed] == [
        "scan_ai", "ask", "ensemble", "autofile", "cost_saving", "review", "my_words", "self_signup",
        "version_spotting",  # Changed on purpose (PRD 1, 0041)
        "land_facts", "nlq_clarify", "nlq_learning",  # Changed on purpose (PRD 6, 0052)
    ]  # fmt: skip
    assert all(item["enabled"] and item["organisations_using"] >= 0 for item in listed)
    wrong = client.put(
        "/api/operator/feature-switches/ask", json={"enabled": False, "code": "000000"}, headers=CSRF
    )
    assert wrong.status_code == 403
    try:
        off = client.put(
            "/api/operator/feature-switches/ask",
            json={"enabled": False, "code": _code(api, operator)},
            headers=CSRF,
        )
        assert off.status_code == 200, off.text
        assert {item["feature"]: item["enabled"] for item in off.json()}["ask"] is False
        member = api.new_signed()
        assert member.client.post("/api/chat", json={"question": "q"}, headers=CSRF).status_code == 403
    finally:
        on = client.put(
            "/api/operator/feature-switches/ask",
            json={"enabled": True, "code": _code(api, operator)},
            headers=CSRF,
        )
        assert on.status_code == 200, on.text
    audited = operator.sql(
        "SELECT action, details->>'enabled' FROM platform_audit WHERE operator_id = :o"
        " AND action LIKE 'feature.%' ORDER BY id"
    )
    assert [tuple(row) for row in audited] == [("feature.off", "false"), ("feature.on", "true")]
    unknown = client.put(
        "/api/operator/feature-switches/nope", json={"enabled": False, "code": "1"}, headers=CSRF
    )
    assert unknown.status_code in (400, 422)


# --- per-organisation feature switches (0039) -------------------------------------------------------

# Changed on purpose (PRD 1, 0041): version_spotting; (PRD 6, 0052): the last three.
ORG_FEATURES = [
    "scan_ai",
    "ask",
    "ensemble",
    "autofile",
    "cost_saving",
    "review",
    "my_words",
    "version_spotting",
    "land_facts",
    "nlq_clarify",
    "nlq_learning",
]


def _org_switch(
    api: Api, operator: _Operator, client: TestClient, customer: str, feature: str, on: bool
) -> Any:
    return client.put(
        f"/api/operator/orgs/{customer}/feature-switches/{feature}",
        json={"enabled": on, "code": _code(api, operator)},
        headers=CSRF,
    )


def test_an_organisation_switch_takes_a_fresh_code_is_audited_and_blocks_only_that_organisation(
    db: Database,
) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _operator(api)
    mine, other = api.new_signed(), api.new_signed()
    path = f"/api/operator/orgs/{mine.customer_id}/feature-switches"
    listed = client.get(path).json()
    assert listed == [{"feature": f, "enabled": True, "platform_enabled": True} for f in ORG_FEATURES]
    using = {
        item["feature"]: item["organisations_using"]
        for item in client.get("/api/operator/feature-switches").json()
    }
    wrong = client.put(f"{path}/ask", json={"enabled": False, "code": "000000"}, headers=CSRF)
    assert wrong.status_code == 403
    off = _org_switch(api, operator, client, mine.customer_id, "ask", False)
    assert off.status_code == 200, off.text
    assert {item["feature"]: item["enabled"] for item in off.json()}["ask"] is False
    assert _org_switch(api, operator, client, mine.customer_id, "my_words", False).status_code == 200

    reply = mine.client.post("/api/chat", json={"question": "totals?"}, headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "feature_off")
    assert (settings_of(mine)["ask"]["available"], settings_of(other)["ask"]["available"]) == (False, True)
    assert mine.client.get("/api/me/words").json()["error"]["code"] == "feature_off"
    assert other.client.get("/api/me/words").status_code == 200  # another organisation: untouched
    after = {
        item["feature"]: item["organisations_using"]
        for item in client.get("/api/operator/feature-switches").json()
    }
    assert after["ask"] == using["ask"] - 1  # an organisation switched off is not counted

    assert _org_switch(api, operator, client, mine.customer_id, "my_words", True).status_code == 200
    assert mine.client.get("/api/me/words").status_code == 200  # back on
    audited = operator.sql(
        "SELECT action, subject_customer_id::text, target_id, details->>'enabled' FROM platform_audit"
        " WHERE operator_id = :o AND action LIKE 'feature.org%' ORDER BY id"
    )
    assert [tuple(row) for row in audited] == [
        ("feature.org_off", mine.customer_id, "ask", "false"),
        ("feature.org_off", mine.customer_id, "my_words", "false"),
        ("feature.org_on", mine.customer_id, "my_words", "true"),
    ]
    assert client.get(f"/api/operator/orgs/{uuid.uuid4()}/feature-switches").status_code == 404
    signup = client.put(f"{path}/self_signup", json={"enabled": False, "code": "1"}, headers=CSRF)
    assert signup.status_code in (400, 422)  # sign-up has no organisation switch


def test_the_platform_switch_off_still_wins_over_an_organisation_switch_on(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    _, client = _operator(api)
    member = api.new_signed()
    document = api.add_document(member.customer_id, name="a.pdf", uploaded_by=member.user_id)
    path = f"/api/operator/orgs/{member.customer_id}/feature-switches"
    _owner(
        db,
        "INSERT INTO org_feature_switches (customer_id, feature, enabled) VALUES (:c, 'review', true)",
        {"c": member.customer_id},
    )
    with switched_off(db, "review"):
        review = {item["feature"]: item for item in client.get(path).json()}["review"]
        assert review == {"feature": "review", "enabled": True, "platform_enabled": False}
        reply = member.client.get(f"/api/documents/{document}/flags")
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "feature_off")
    assert member.client.get(f"/api/documents/{document}/flags").status_code == 200


def test_the_worker_and_the_ceiling_follow_each_organisations_own_switch(db: Database) -> None:
    """Scanning with AI hands back only that organisation's jobs; Cost saving and Ensemble
    come off its ceiling only (``effective_settings``)."""
    mine, other = new_customer(db)[0], new_customer(db)[0]
    for feature in ("scan_ai", "cost_saving", "ensemble"):
        _owner(
            db,
            "INSERT INTO org_feature_switches (customer_id, feature, enabled) VALUES (:c, :f, false)",
            {"c": mine, "f": feature},
        )
    for customer in (mine, other):
        _owner(
            db,
            "INSERT INTO customer_ai_settings (customer_id, ensemble, dynamic) VALUES (:c, true, true)",
            {"c": customer},
        )
    assert (_scanning_on(db, mine), _scanning_on(db, other)) == (False, True)
    for customer, on in ((mine, False), (other, True)):
        with db.customer(customer) as conn:
            ceiling = models.effective_settings(conn, customer)
        assert (ceiling["dynamic"], ceiling["ensemble"]) == (on, on), customer


def test_the_test_model_reaches_every_organisation_and_undo_restores_each_exactly(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _operator(api)
    own, test = catalog_model(db), catalog_model(db)
    first, second = api.new_signed(), api.new_signed()
    allow(db, first.customer_id, [own], own)

    def scan_list(customer: str) -> list[str]:
        with db.customer(customer) as conn:
            return [row["id"] for row in models.usable_models(conn, customer, settings=GEMINI)]

    before = {customer: scan_list(customer) for customer in (first.customer_id, second.customer_id)}
    bad = client.post(
        "/api/operator/test-model",
        json={"scan_model": "google/not-in-the-catalogue", "code": _code(api, operator)},
        headers=CSRF,
    )
    assert (bad.status_code, bad.json()["error"]["code"]) == (409, "unavailable")
    try:
        started = client.post(
            "/api/operator/test-model",
            json={"scan_model": test, "note": "cost check", "code": _code(api, operator)},
            headers=CSRF,
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert (body["active"], body["scan_model"], body["chat_model"]) == (True, test, None)
        assert body["since"] == {"pages": 0, "questions": 0, "cost_usd": None, "avg_seconds_per_page": None}
        for customer in before:
            assert scan_list(customer) == [test]
        with db.customer(first.customer_id) as conn:  # real spend rows only, the estimated left out
            for cost, estimated in ((0.5, False), (9.0, True)):
                conn.execute(
                    text(
                        "INSERT INTO model_usage (customer_id, kind, model, calls, input_tokens,"
                        " output_tokens, reasoning_tokens, cost_usd, cost_estimated)"
                        " VALUES (:c, 'scan', :m, 1, 10, 5, 0, :x, :e)"
                    ),
                    {"c": first.customer_id, "m": test, "x": cost, "e": estimated},
                )
        since = client.get("/api/operator/test-model").json()["since"]
        assert (since["pages"], since["cost_usd"]) == (2, 0.5)
        with switched_off(db, "ask"):  # a switch still wins over the test model
            assert first.client.post("/api/chat", json={"question": "q"}, headers=CSRF).status_code == 403
    finally:
        undone = client.request(
            "DELETE", "/api/operator/test-model", json={"code": _code(api, operator)}, headers=CSRF
        )
        assert undone.status_code == 200, undone.text
    assert undone.json()["active"] is False
    assert {customer: scan_list(customer) for customer in before} == before
    actions = operator.sql(
        "SELECT action FROM platform_audit WHERE operator_id = :o"
        " AND action LIKE 'platform.test_model%' ORDER BY id"
    )
    assert [row[0] for row in actions] == ["platform.test_model_start", "platform.test_model_undo"]
