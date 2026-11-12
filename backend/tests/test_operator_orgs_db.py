"""The operator's organisations against PostgreSQL (super admin PR 3, 0016): counts only,
a first admin with a temporary password who signs in to that organisation only, disable
(a fresh code, once) and enable, and exactly one ``platform_audit`` row per write. The
cookies that must not cross: ``test_operator_auth_db.py``'s route walks."""

import uuid
from typing import Any

import pytest

from kaagaz.api import totp
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate
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


def test_a_new_organisation_whose_first_admin_must_change_the_temporary_password(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    email = f"boss-{uuid.uuid4().hex[:12]}@example.test"
    body = {"name": "Org New", "admin_email": email.upper(), "admin_name": "Boss"}
    created = client.post(ORGS, json=body, headers=CSRF)
    assert created.status_code == 201, created.text
    customer_id, temporary = created.json()["organisation"]["id"], created.json()["temporary_password"]
    taken = client.post(ORGS, json={**body, "name": "Org Probe"}, headers=CSRF)
    assert taken.json()["error"]["message"] == "That e-mail is already in use."  # and nothing is made

    admin = api.client()
    signed = admin.post("/api/auth/login", json={"email": email, "password": temporary}, headers=CSRF)
    assert signed.status_code == 200, signed.text
    assert signed.json()["customer"]["id"] == customer_id  # this organisation only
    assert (signed.json()["user"]["role"], signed.json()["user"]["must_change_password"]) == ("admin", True)
    assert admin.get("/api/documents").json()["error"]["code"] == "password_change_required"
    assert _platform(operator) == [
        ("customer.create", customer_id, "customer", customer_id),
        ("customer.create_refused", None, "customer", None),  # the probe leaves a trace, no e-mail
    ]
    assert "Org Probe" not in client.get(ORGS).text


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
