"""The operator's organisations against PostgreSQL (super admin PR 3, 0016): counts only,
a first admin with a temporary password who signs in to that organisation only, disable
(a fresh code, once) and enable, and exactly one ``platform_audit`` row per write. The
cookies that must not cross: ``test_operator_auth_db.py``'s route walks."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api import totp
from kaagaz.db import keycloak as keycloak_sql
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate
from tests.settings_factory import make_settings
from tests.test_api_admin import ISSUER, KEYCLOAK_ON, _Keycloak
from tests.test_api_support import CSRF, NOW, PDF_BYTES, Api, TestClient, wrong_code
from tests.test_operator_auth_db import _UNLOCK, WRONG, _Operator

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
ORGS = "/api/operator/orgs"
FUNCTIONS = {"kaagaz_op_customers", "kaagaz_op_create_customer", "kaagaz_op_set_customer_status"}
# This operator's rows, sign-ins left out: (action, subject customer, target type, target id).
_PLATFORM = (
    "SELECT action, subject_customer_id::text, target_type, target_id FROM platform_audit"
    " WHERE operator_id = :o AND action <> 'operator.login' ORDER BY id"
)


def _signed_in(api: Api) -> tuple[_Operator, TestClient]:
    operator, client = _Operator(api), api.client()
    assert operator.login(client).status_code == 200
    api.clock.advance(totp.STEP_SECONDS)  # the sign-in used this step's code
    return operator, client


def _platform(operator: _Operator) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in operator.sql(_PLATFORM)]


def test_0016_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0015")
    at_0015 = m0002._schema(database)
    migrate(url, "head")
    at_head = m0002._schema(database)
    assert m0002._names(at_head, "functions") >= FUNCTIONS
    m0002._downgrade(url, "0015")
    assert m0002._schema(database) == at_0015
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_the_list_has_counts_and_names_no_document_or_user(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    a, _ = api.customer("Org A counted")
    member, member_email = api.add_user(a, "member")
    api.add_user(a, "reviewer")
    api.add_document(a, name="secret-plan.pdf", page_count=3, uploaded_by=member)
    api.add_document(a, name="old-trash.pdf", page_count=5, deleted_at=NOW, deleted_by=member)

    reply = client.get(ORGS)
    assert reply.status_code == 200, reply.text
    [org] = [item for item in reply.json()["items"] if item["id"] == a]
    assert (org["name"], org["status"], org["disabled_at"]) == ("Org A counted", "active", None)
    assert org["users"] == {"admins": 1, "reviewers": 1, "members": 1}
    # One current document not in Trash; both files are stored.
    assert (org["documents"], org["pages"], org["storage_bytes"]) == (1, 3, 2 * len(PDF_BYTES))
    assert org["last_activity_at"] is not None
    for hidden in ("secret-plan", "old-trash", member_email, "Test member", "Test reviewer", "admin-"):
        assert hidden not in reply.text
    assert _platform(operator) == []  # a read writes nothing


def test_a_new_organisation_gets_its_admin_a_linked_company_sign_in_account(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    keycloak = _Keycloak(monkeypatch)
    api = Api(db, settings=make_settings(**KEYCLOAK_ON))
    operator, client = _signed_in(api)
    email = f"boss-{uuid.uuid4().hex[:12]}@example.test"
    body = {"name": "Org New", "admin_email": email.upper(), "admin_name": "Boss"}
    created = client.post(ORGS, json=body, headers=CSRF)
    assert created.status_code == 201, created.text
    reply = created.json()
    customer_id = reply["organisation"]["id"]
    assert "temporary_password" not in reply and reply["setup_email_sent"] is True
    assert reply["message"].startswith(f"Created. We emailed {email} a link to set their password")
    assert keycloak.names() == ["create_user", "send_setup_email"]
    with db.system() as conn:  # the admin is linked to exactly the account that was made
        found = keycloak_sql.lookup(conn, ISSUER, keycloak.subject())
    assert found is not None and found.customer_id == customer_id
    taken = client.post(ORGS, json={**body, "name": "Org Probe"}, headers=CSRF)
    assert taken.json()["error"]["message"] == "That e-mail is already in use."  # and nothing is made
    assert keycloak.names() == ["create_user", "send_setup_email"]  # no second account either
    assert _platform(operator) == [
        ("customer.create", customer_id, "customer", customer_id),
        ("customer.create_refused", None, "customer", None),  # the probe leaves a trace, no e-mail
    ]
    assert "Org Probe" not in client.get(ORGS).text


def test_an_email_keycloak_already_has_makes_no_organisation(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    keycloak = _Keycloak(monkeypatch)
    api = Api(db, settings=make_settings(**KEYCLOAK_ON))
    operator, client = _signed_in(api)
    email = f"known-{uuid.uuid4().hex[:12]}@example.test"
    keycloak.existing.add(email)
    reply = client.post(
        ORGS, json={"name": "Org Known", "admin_email": email, "admin_name": "Boss"}, headers=CSRF
    )
    assert reply.status_code == 409
    assert "already has a company sign-in account" in reply.json()["error"]["message"]
    assert keycloak.calls == [] and _platform(operator) == []
    assert "Org Known" not in client.get(ORGS).text


def test_move_everyone_links_each_active_person_once_and_never_an_existing_account(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    keycloak = _Keycloak(monkeypatch)
    api = Api(db, settings=make_settings(**KEYCLOAK_ON))
    _, client = _signed_in(api)
    customer, admin_id = api.customer("Org Move")
    new_id, _ = api.add_user(customer, "member")
    taken_id, taken_email = api.add_user(customer, "reviewer")
    off_id, _ = api.add_user(customer, "member")
    with db.customer(customer) as conn:
        conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": off_id})
    keycloak.existing.add(taken_email)

    summary = client.get(f"{ORGS}/{customer}/sign-in").json()
    assert (summary["connected"], summary["to_move"]) == (0, 3)  # the admin, the member, the reviewer
    moved = client.post(f"{ORGS}/{customer}/move-everyone", headers=CSRF)
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert (body["moved"], body["connected"], len(body["attention"])) == (2, 0, 1)
    assert body["attention"][0]["email"] == taken_email
    assert body["message"] == "2 moved, 0 already connected, 1 need attention."
    assert keycloak.names().count("create_user") == 2  # the admin and the member; not the taken email
    again = client.post(f"{ORGS}/{customer}/move-everyone", headers=CSRF).json()
    assert (again["moved"], again["connected"]) == (0, 2)  # safe to run again: the linked are skipped
    assert [a["email"] for a in again["attention"]] == [taken_email]
    with db.customer(customer) as conn:
        linked = {p.user_id: p.linked for p in keycloak_sql.people(conn, customer, ISSUER)}
    assert linked == {admin_id: True, new_id: True, taken_id: False, off_id: False}
    assert keycloak.names().count("send_setup_email") == 2


def test_disabling_takes_a_fresh_code_once_and_ends_only_that_organisations_sessions(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    a, b = api.new_admin("Org A"), api.new_admin("Org B")
    disable = f"{ORGS}/{a.customer_id}/disable"

    refused = client.post(disable, json={"code": wrong_code(api, operator.secret)}, headers=CSRF)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "step_up_failed")
    assert a.client.get("/api/auth/me").status_code == 200  # nothing changed
    code = api.code(operator.secret)
    done = client.post(disable, json={"code": code}, headers=CSRF)
    assert done.json() == {"organisation": {"id": a.customer_id, "status": "disabled", "changed": True}}
    assert client.post(disable, json={"code": code}, headers=CSRF).status_code == 403  # the same code
    assert a.client.get("/api/auth/me").status_code == 401
    assert b.client.get("/api/auth/me").status_code == 200

    enable = f"{ORGS}/{a.customer_id}/enable"
    assert client.post(enable, headers=CSRF).json()["organisation"]["changed"] is True
    assert client.post(enable, headers=CSRF).json()["organisation"]["changed"] is False
    assert a.client.get("/api/auth/me").status_code == 401  # for good: everyone signs in again
    unknown = f"{ORGS}/{uuid.uuid4()}"
    assert client.post(f"{unknown}/enable", headers=CSRF).status_code == 404
    api.clock.advance(totp.STEP_SECONDS)
    assert (
        client.post(f"{unknown}/disable", json={"code": api.code(operator.secret)}, headers=CSRF).status_code
        == 404
    )

    failed = ("operator.step_up_failed", None, "operator", operator.id)
    assert _platform(operator) == [
        failed,
        ("customer.disable", a.customer_id, "customer", a.customer_id),
        failed,
        ("customer.enable", a.customer_id, "customer", a.customer_id),
    ]
    by_operator = [row for row in api.audit_rows(a.customer_id) if row[3] == {"by": "operator"}]
    assert [row[:3] for row in by_operator] == [
        ("customer.disable", "customer", a.customer_id),
        ("customer.enable", "customer", a.customer_id),
    ]


def test_a_sign_in_lock_never_blocks_a_step_up_and_a_step_up_lock_never_blocks_sign_in(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    a = api.new_admin("Org L")
    limit = api.settings.login_max_failures
    for _ in range(limit):  # anyone who knows the e-mail can lock the sign-in
        assert operator.login(api.client(), password=WRONG).status_code == 401
    disable = f"{ORGS}/{a.customer_id}/disable"
    assert client.post(disable, json={"code": api.code(operator.secret)}, headers=CSRF).status_code == 200

    operator.sql(_UNLOCK)
    for _ in range(limit):
        assert (
            client.post(disable, json={"code": wrong_code(api, operator.secret)}, headers=CSRF).status_code
            == 403
        )
    api.clock.advance(totp.STEP_SECONDS)
    assert client.post(disable, json={"code": api.code(operator.secret)}, headers=CSRF).status_code == 403
    # Changed on purpose (A2, 7 Nov 2026): the address that failed waits out its own lock (the
    # unlock above is the database's), so another address shows the sign-in is not locked.
    assert operator.login(TestClient(api.app, client=("198.51.100.9", 50000))).status_code == 200
    failed = ("operator.step_up_failed", None, "operator", operator.id)
    assert _platform(operator) == [
        ("customer.disable", a.customer_id, "customer", a.customer_id),
        *[failed] * (limit - 1),
        ("operator.step_up_locked", None, "operator", operator.id),
    ]
