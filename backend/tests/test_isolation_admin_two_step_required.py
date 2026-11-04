"""Require two-step sign-in for admins (#481, Tushar, 3 Nov 2026; 0068), against PostgreSQL.

* Off for every organisation until the super admin turns it on, with a fresh code, for one
  organisation; audited in ``platform_audit`` and that organisation's own log.
* While on, that organisation's admin who turned two-step off themselves (0061b) is sent to
  set it up at sign-in, can't use the admin screens meanwhile, and an admin can't turn it off.
* Another organisation is untouched: its admin keeps signing in on the password alone.
"""

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api import totp
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, PASSWORD, Api, TestClient, call, route_calls, wrong_code
from tests.test_operator_auth_db import _Operator, _owner
from tests.test_operator_orgs_db import ORGS, _platform, _signed_in

pytestmark = pytest.mark.db

_TURNED_OFF = (
    "UPDATE users SET totp_secret_enc = NULL, totp_enabled_at = NULL, totp_last_step = NULL,"
    " totp_off_at = now() WHERE customer_id = :c AND id = :u"
)


def _code(api: Api, operator: _Operator) -> str:
    """A code of a step not used yet."""
    api.clock.advance(totp.STEP_SECONDS)
    return api.code(operator.secret)


def _set(api: Api, operator: _Operator, client: TestClient, customer: str, required: bool) -> httpx.Response:
    body = {"required": required, "code": _code(api, operator)}
    return client.put(f"{ORGS}/{customer}/admin-two-step", json=body, headers=CSRF)


def _turned_off_admin(api: Api, customer: str) -> tuple[str, str]:
    """An admin of ``customer`` who turned their own two-step sign-in off: (id, e-mail)."""
    user_id, email = api.add_user(customer, "admin")
    with api.db.customer(customer) as conn:
        conn.execute(text(_TURNED_OFF), {"c": customer, "u": user_id})
    return user_id, email


def _sign_in(api: Api, email: str) -> tuple[TestClient, httpx.Response]:
    client = api.client()
    return client, client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF)


def test_only_the_super_admin_turns_it_on_for_one_organisation_with_a_fresh_code(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    a, _ = api.customer("Org A two-step")
    b, _ = api.customer("Org B two-step")
    path = f"{ORGS}/{a}/admin-two-step"
    assert client.get(path).json() == {"required": False}  # off by default
    bad = client.put(path, json={"required": True, "code": wrong_code(api, operator.secret)}, headers=CSRF)
    assert (bad.status_code, bad.json()["error"]["code"]) == (403, "step_up_failed")
    assert client.put(path, json={"required": True}, headers=CSRF).status_code == 400  # no code

    reply = _set(api, operator, client, a, True)
    assert (reply.status_code, reply.json()) == (200, {"required": True})
    assert _set(api, operator, client, a, True).json() == {"required": True}  # unchanged: no new rows
    assert client.get(f"{ORGS}/{b}/admin-two-step").json() == {"required": False}  # B untouched
    missing = "00000000-0000-4000-8000-000000000000"
    assert _set(api, operator, client, missing, True).status_code == 404
    assert client.get(f"{ORGS}/{missing}/admin-two-step").status_code == 404
    assert _set(api, operator, client, a, False).json() == {"required": False}

    row = ("customer", a)
    assert [entry for entry in _platform(operator) if entry[0].startswith("org.admin_two_step")] == [
        ("org.admin_two_step_on", a, *row),
        ("org.admin_two_step_off", a, *row),
    ]
    assert [entry[0] for entry in api.audit_rows(a) if entry[3] == {"by": "operator"}] == [
        "org.admin_two_step_on",
        "org.admin_two_step_off",
    ]
    assert api.audit_rows(b) == []


def test_while_on_an_admin_who_turned_it_off_sets_it_up_and_other_organisations_are_untouched(
    db: Database,
) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    a, _ = api.customer("Org A required")
    b, _ = api.customer("Org B required")
    a_admin, a_email = _turned_off_admin(api, a)
    _, b_email = _turned_off_admin(api, b)
    a_client, signed_in = _sign_in(api, a_email)
    assert signed_in.status_code == 200, signed_in.text  # off: the password alone, as before
    assert a_client.get("/api/admin/stats").status_code == 200

    assert _set(api, operator, client, a, True).status_code == 200
    refused = a_client.get("/api/admin/stats")  # the session in hand: no admin screen until set up
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "mfa_enrol_required")
    me = a_client.get("/api/auth/me").json()["user"]
    assert (me["two_step_turned_off"], me["two_step_required"]) == (False, True)  # the screen's gate
    _, again = _sign_in(api, a_email)
    assert (again.status_code, again.json()["error"]["code"]) == (403, "mfa_enrol_required")
    assert set(again.json()["error"]["details"]) >= {"enrol_token", "secret", "otpauth_uri"}

    b_client, b_signed_in = _sign_in(api, b_email)  # B: still the password alone
    assert b_signed_in.status_code == 200, b_signed_in.text
    assert b_client.get("/api/auth/me").json()["user"]["two_step_required"] is False

    assert _set(api, operator, client, a, False).status_code == 200
    _, off_again = _sign_in(api, a_email)
    assert off_again.status_code == 200, off_again.text  # off again: as the admin left it
    assert _owner(db, "SELECT totp_off_at IS NOT NULL FROM users WHERE id = :u", {"u": a_admin}) == [(True,)]


def test_every_admin_route_refuses_until_set_up(db: Database) -> None:
    """Walks the app's routes, so a future /api/admin route on another dependency is caught."""
    api = Api(db)
    operator, client = _signed_in(api)
    a, _ = api.customer("Org A every route")
    _, email = _turned_off_admin(api, a)
    a_client, signed_in = _sign_in(api, email)
    assert signed_in.status_code == 200, signed_in.text
    assert _set(api, operator, client, a, True).status_code == 200
    routes = route_calls(api.app, "/api/admin/")
    assert len(routes) > 10, routes
    for method, path in routes:
        reply = call(a_client, method, path)
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "mfa_enrol_required"), (
            method,
            path,
        )


def test_while_on_the_admin_screen_shows_no_admin_as_turned_off(db: Database) -> None:
    """The users list and a one-user reply follow sign-in's rule: off counts only while not required."""
    api = Api(db)
    operator, client = _signed_in(api)
    a, _ = api.customer("Org A users list")
    off_id, _ = _turned_off_admin(api, a)
    admin = api.admin_sign_in(a)

    def listed() -> bool:
        items = admin.client.get("/api/admin/users").json()["items"]
        return bool(next(item for item in items if item["id"] == off_id)["two_step_turned_off"])

    assert listed() is True
    assert _set(api, operator, client, a, True).status_code == 200
    assert listed() is False
    unlocked = admin.client.post(f"/api/admin/users/{off_id}/unlock", headers=CSRF)
    assert (unlocked.status_code, unlocked.json()["two_step_turned_off"]) == (200, False)


def test_while_on_an_admin_cant_turn_their_own_two_step_off(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    a, _ = api.customer("Org A keep on")
    admin = api.admin_sign_in(a)
    assert _set(api, operator, client, a, True).status_code == 200
    api.clock.advance(totp.STEP_SECONDS)
    assert admin.totp_secret is not None
    body = {"password": PASSWORD, "code": api.code(admin.totp_secret)}
    reply = admin.client.post("/api/auth/2fa/off", json=body, headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "two_step_required")
    assert "requires two-step sign-in for admins" in reply.json()["error"]["message"]
    assert api.user_state(a, admin.user_id)["totp_enabled_at"] is not None  # still on
    assert admin.client.get("/api/auth/me").json()["user"]["two_step_required"] is True

    member = api.enrol_user(a, "member")  # a member's own code is theirs to turn off
    member_client = api.client()
    login = {"email": member.email, "password": PASSWORD, "code": api.code(member.secret)}
    assert member_client.post("/api/auth/login", json=login, headers=CSRF).status_code == 200
    api.clock.advance(totp.STEP_SECONDS)
    off = {"password": PASSWORD, "code": api.code(member.secret)}
    assert member_client.post("/api/auth/2fa/off", json=off, headers=CSRF).status_code == 200
    assert member_client.get("/api/auth/me").json()["user"]["two_step_required"] is False
