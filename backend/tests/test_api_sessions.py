"""Ending sessions and temporary passwords (PRD 1 A4, A5 and A7).

A session is a signed cookie with the user's ``session_version`` in it as
``ver``. The version goes up on a password change, "Sign out everywhere",
disabling a user, a 2FA reset and a password set by an admin, and a cookie
whose ``ver`` no longer matches gets a 401. A user with a temporary password
can only change it, sign out or ask who they are, until they set a new one.
"""

import base64
import hashlib
import hmac
import json
import re

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api import totp
from kaagaz.api.auth import SESSION_COOKIE, SessionClaims, sign_session, verify_password
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, EMAIL_ROUTES, NOW, PASSWORD, Api, TestClient, call, route_calls

pytestmark = pytest.mark.db

NEW_PASSWORD = "a brand new password"
ACTION = re.compile(r"^[a-z_.]{1,64}$")
PASSWORD_CHANGE_REFUSED = {
    "error": {"code": "password_change_required", "message": "Set a new password to continue."}
}


def _login(client: TestClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=CSRF)


def _change_password(client: TestClient, current: str, new: str, code: str | None = None) -> httpx.Response:
    body = {"current_password": current, "new_password": new}
    if code is not None:
        body["code"] = code
    return client.post("/api/auth/password", json=body, headers=CSRF)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# --- password change ---------------------------------------------------------------------


def test_a_password_change_signs_out_other_browsers_but_keeps_this_one(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    other = api.client()
    assert _login(other, signed.email).status_code == 200
    version = api.user_state(signed.customer_id, signed.user_id)["session_version"]

    reply = _change_password(signed.client, PASSWORD, NEW_PASSWORD)

    assert reply.status_code == 200, reply.text
    assert reply.json()["user"]["must_change_password"] is False
    assert reply.json()["session"] == {"scope": "app"}
    assert reply.headers["set-cookie"].startswith(f"{SESSION_COOKIE}=")
    assert api.user_state(signed.customer_id, signed.user_id)["session_version"] > version
    assert signed.client.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    assert _login(api.client(), signed.email).status_code == 401
    assert _login(api.client(), signed.email, NEW_PASSWORD).status_code == 200
    assert "auth.password_change" in api.audit_actions(signed.customer_id)


def test_a_password_change_keeps_the_admin_session_and_its_end(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    api.clock.advance(30 * 60)
    assert admin.totp_secret is not None

    # Changed on purpose (#457): with two-step sign-in on, the code too.
    reply = _change_password(admin.client, PASSWORD, NEW_PASSWORD, api.code(admin.totp_secret))

    assert reply.status_code == 200, reply.text
    assert reply.json()["session"] == {"scope": "admin"}
    assert f"Max-Age={30 * 60}" in reply.headers["set-cookie"]  # what was left, not a fresh hour
    assert admin.client.get("/api/admin/users").status_code == 200
    api.clock.advance(30 * 60)
    assert admin.client.get("/api/admin/users").status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"current_password": "not the password!!", "new_password": NEW_PASSWORD},
        {"current_password": PASSWORD, "new_password": "too short"},
        {"current_password": PASSWORD, "new_password": PASSWORD},
        {"current_password": PASSWORD},
        {"current_password": PASSWORD, "new_password": NEW_PASSWORD, "extra": "x"},
    ],
)
def test_a_password_change_needs_the_right_current_and_a_good_new_one(
    db: Database, body: dict[str, str]
) -> None:
    api = Api(db)
    signed = api.new_signed()
    stored = api.user_state(signed.customer_id, signed.user_id)

    reply = signed.client.post("/api/auth/password", json=body, headers=CSRF)

    assert reply.status_code == 400
    assert reply.json()["error"]["code"] == "bad_request"
    after = api.user_state(signed.customer_id, signed.user_id)
    assert after["password_hash"] == stored["password_hash"]
    assert after["session_version"] == stored["session_version"]
    assert signed.client.get("/api/auth/me").status_code == 200


def test_a_password_change_needs_a_session(db: Database) -> None:
    reply = _change_password(Api(db).client(), PASSWORD, NEW_PASSWORD)
    assert reply.status_code == 401


# --- sign out everywhere, disable, 2FA reset ------------------------------------------------------


def test_sign_out_everywhere_ends_every_session(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    other = api.client()
    assert _login(other, signed.email).status_code == 200

    reply = signed.client.post("/api/auth/logout-everywhere", headers=CSRF)

    assert reply.status_code == 204
    assert "Max-Age=0" in reply.headers["set-cookie"] or f'{SESSION_COOKIE}=""' in reply.headers["set-cookie"]
    assert signed.client.get("/api/auth/me").status_code == 401
    assert other.get("/api/auth/me").status_code == 401
    assert _login(api.client(), signed.email).status_code == 200  # the password still works
    assert "auth.logout_everywhere" in api.audit_actions(signed.customer_id)


def test_disable_then_enable_does_not_bring_old_cookies_back(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    path = f"/api/admin/users/{member.user_id}"

    assert admin.client.patch(path, json={"disabled": True}, headers=CSRF).status_code == 200
    assert admin.client.patch(path, json={"disabled": False}, headers=CSRF).status_code == 200

    assert member.client.get("/api/auth/me").status_code == 401
    assert _login(api.client(), member.email).status_code == 200


def test_a_2fa_reset_ends_the_admins_sessions(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    colleague = api.admin_sign_in(admin.customer_id)
    colleague_app = api.client()
    # Changed on purpose (#239, 27 Oct 2026): an admin signs in with a code on the one sign-in
    # (cbcfc50, 16 Oct); the next step's, as this step's was used.
    api.clock.advance(totp.STEP_SECONDS)
    assert colleague.totp_secret is not None
    code = api.code(colleague.totp_secret)
    assert api.admin_login(colleague_app, colleague.email, code=code).status_code == 200

    reply = admin.client.post(f"/api/admin/users/{colleague.user_id}/reset-2fa", headers=CSRF)

    assert reply.status_code == 200, reply.text
    assert colleague.client.get("/api/auth/me").status_code == 401
    assert colleague_app.get("/api/auth/me").status_code == 401


def test_a_cookie_with_another_ver_is_refused(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    version = int(api.user_state(signed.customer_id, signed.user_id)["session_version"])
    assert version == 1
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE users SET session_version = 2 WHERE id = :u"), {"u": signed.user_id})

    for ver, status in ((version, 401), (3, 401), (2, 200)):
        claims = SessionClaims(
            user_id=signed.user_id,
            customer_id=signed.customer_id,
            role="member",
            scope="app",
            session_version=ver,
            expires_at=int(NOW.timestamp()) + 3600,
        )
        client = api.client()
        client.cookies.set(SESSION_COOKIE, sign_session(claims, api.settings.session_secret))
        assert client.get("/api/auth/me").status_code == status, ver


def test_an_old_cookie_without_scope_or_ver_is_refused(db: Database) -> None:
    """Sprint 1's cookie, {uid, cid, role, exp}: every user signs in once more."""
    api = Api(db)
    signed = api.new_signed()
    secret = api.settings.session_secret.encode()
    payload = {"uid": signed.user_id, "cid": signed.customer_id, "role": "member", "exp": 2_000_000_000}
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    old_mac = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    new_mac = hmac.new(secret, b"session\x00" + body.encode("ascii"), hashlib.sha256).digest()

    for token in (body + "." + _b64url(old_mac), body + "." + _b64url(new_mac)):
        client = api.client()
        client.cookies.set(SESSION_COOKIE, token)
        reply = client.get("/api/auth/me")
        assert reply.status_code == 401
        assert reply.json()["error"]["code"] == "not_signed_in"


# --- temporary passwords and the must-change gate -------------------------------------------------


def _must_change_client(api: Api) -> tuple[TestClient, str, str, str]:
    """A member created by an admin, signed in with the password the admin chose."""
    admin = api.new_admin()
    email = f"new-{admin.user_id[:8]}@example.test"
    created = admin.client.post(
        "/api/admin/users",
        json={"email": email, "display_name": "New Person", "role": "member", "password": PASSWORD},
        headers=CSRF,
    )
    assert created.status_code == 201, created.text
    assert created.json()["must_change_password"] is True
    client = api.client()
    login = _login(client, email)
    assert login.status_code == 200, login.text
    assert login.json()["user"]["must_change_password"] is True
    return client, admin.customer_id, created.json()["id"], email


def test_must_change_blocks_every_route_except_password_logout_and_me(db: Database) -> None:
    api = Api(db)
    client, _, _, _ = _must_change_client(api)
    open_to_all = {
        ("GET", "/api/health"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/admin/login"),
        ("POST", "/api/auth/admin/enrol"),
        ("GET", "/api/auth/signup"),
        ("POST", "/api/auth/signup"),
    }
    # Changed on purpose (issue 378, 27 Oct 2026): GET /auth/session is /auth/me's quiet twin.
    allowed = {
        ("POST", "/api/auth/password"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/me"),
        ("GET", "/api/auth/session"),
    }
    # Changed on purpose (#457): the email routes are 404 while email is off (tests/test_api_email.py).
    email_off = {(method, path) for method, path in route_calls(api.app) if path in EMAIL_ROUTES}
    calls = [call for call in route_calls(api.app) if call not in email_off]
    assert open_to_all | allowed <= set(calls) and len(email_off) == len(EMAIL_ROUTES)

    # Fixed (#239, 27 Oct 2026): the operator's routes (0015) take an operator session, so a
    # customer's session gets their 401 before this gate; tests/test_operator_auth_db.py too.
    operator = [c for c in calls if c[1].startswith("/api/operator") and c != ("POST", "/api/operator/login")]
    gated = [
        (method, path)
        for method, path in calls
        if (method, path) not in open_to_all | allowed and not path.startswith("/api/operator")
    ]
    assert len(gated) > 20 and len(operator) > 10
    for method, path in gated:
        reply = call(client, method, path)
        assert reply.status_code == 403, (method, path)
        assert reply.json() == PASSWORD_CHANGE_REFUSED, (method, path)
    for method, path in operator:
        reply = call(client, method, path)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), (method, path)

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["must_change_password"] is True
    assert client.get("/api/auth/session").json() == {"signed_in": True, "me": me.json()}


def test_setting_a_new_password_lifts_the_gate(db: Database) -> None:
    api = Api(db)
    client, customer_id, user_id, email = _must_change_client(api)
    assert client.get("/api/documents").status_code == 403

    reply = _change_password(client, PASSWORD, NEW_PASSWORD)

    assert reply.status_code == 200, reply.text
    assert reply.json()["user"]["must_change_password"] is False
    assert client.get("/api/documents").status_code == 200
    assert api.user_state(customer_id, user_id)["must_change_password"] is False
    assert _login(api.client(), email, NEW_PASSWORD).json()["user"]["must_change_password"] is False


def test_logout_works_while_a_password_change_is_due(db: Database) -> None:
    api = Api(db)
    client, _, _, _ = _must_change_client(api)
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_the_temporary_password_is_shown_once_and_stored_hashed(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    path = f"/api/admin/users/{member.user_id}/reset-password"

    reply = admin.client.post(path, headers=CSRF)

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert set(body) == {"user", "temporary_password"}
    temporary = body["temporary_password"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{16}", temporary)
    assert body["user"]["id"] == member.user_id
    assert body["user"]["must_change_password"] is True
    stored = str(api.user_state(admin.customer_id, member.user_id)["password_hash"])
    assert stored.startswith("scrypt$")
    assert temporary not in stored
    assert verify_password(temporary, stored)
    assert member.client.get("/api/auth/me").status_code == 401  # the old session ended
    assert temporary not in admin.client.get("/api/admin/users").text
    assert temporary not in admin.client.get("/api/admin/audit").text

    again = admin.client.post(path, headers=CSRF).json()["temporary_password"]
    assert again != temporary
    assert _login(api.client(), member.email, temporary).status_code == 401
    signed_in = _login(api.client(), member.email, again)
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["must_change_password"] is True


# --- the audit trail ----------------------------------------------------------------------------------


def test_every_auth_event_is_audited_with_a_valid_action(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member_id, member_email = api.add_user(admin.customer_id, "member")
    client = api.client()
    for _ in range(api.settings.login_max_failures):
        _login(client, member_email, "not the password!!")
    admin.client.post(f"/api/admin/users/{member_id}/unlock", headers=CSRF)
    temporary = admin.client.post(f"/api/admin/users/{member_id}/reset-password", headers=CSRF).json()[
        "temporary_password"
    ]
    assert _login(client, member_email, temporary).status_code == 200
    assert _change_password(client, temporary, NEW_PASSWORD).status_code == 200
    colleague = api.admin_sign_in(admin.customer_id)
    admin.client.post(f"/api/admin/users/{colleague.user_id}/reset-2fa", headers=CSRF)
    assert client.post("/api/auth/logout-everywhere", headers=CSRF).status_code == 204
    enrolling_id, enrolling_email = api.add_user(admin.customer_id, "admin")
    enrolling = api.client()
    refused = api.admin_login(enrolling, enrolling_email)  # 2FA not set up: the offer, no session
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "mfa_enrol_required")
    offer = refused.json()["error"]["details"]
    secret = base64.b32decode(offer["secret"].replace(" ", ""))
    enrolled = enrolling.post(
        "/api/auth/admin/enrol",
        json={"enrol_token": offer["enrol_token"], "code": api.code(secret)},
        headers=CSRF,
    )
    assert enrolled.status_code == 200, enrolled.text
    assert admin.client.post("/api/auth/logout", headers=CSRF).status_code == 204

    with db.customer(admin.customer_id) as conn:
        rows = conn.execute(
            text("SELECT action, target_type, target_id, details FROM audit_log ORDER BY id")
        ).all()
    actions = [str(row[0]) for row in rows]
    assert all(ACTION.fullmatch(action) for action in actions)
    assert not ACTION.fullmatch("user.reset_2fa")  # why the 2FA reset is "user.reset_totp"
    assert {
        "auth.login",
        "auth.login_failed",
        "auth.locked",
        "auth.password_change",
        "auth.logout_everywhere",
        "auth.logout",
        "auth.totp_on",
        "user.unlock",
        "user.reset_password",
        "user.reset_totp",
    } <= set(actions)
    for action, target_type, target_id, _ in rows:
        if str(action).startswith(("auth.", "user.")):
            assert target_type == "user", action
            assert target_id in {admin.user_id, member_id, colleague.user_id, enrolling_id}, action
