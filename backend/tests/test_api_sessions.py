"""Ending sessions (PRD 1 A4, A5 and A7).

A session is a signed cookie with the user's ``session_version`` in it as
``ver``. The version goes up on "Sign out everywhere", disabling a user, a 2FA reset and a password
set by an admin, and a cookie whose ``ver`` no longer matches gets a 401. Keycloak is the only
sign-in (21 Nov 2026), so there is no password change here: the person does it on Keycloak's page.
"""

import base64
import hashlib
import hmac
import json
import re

import pytest
from sqlalchemy import text

from kaagaz.api.auth import SESSION_COOKIE, SessionClaims, sign_session
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, NOW, Api

pytestmark = pytest.mark.db

ACTION = re.compile(r"^[a-z_.]{1,64}$")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# --- password change ---------------------------------------------------------------------


# --- sign out everywhere, disable, 2FA reset ------------------------------------------------------


def test_sign_out_everywhere_ends_every_session(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    other = api.client()
    api.mint_session(other, signed.customer_id, signed.user_id)

    reply = signed.client.post("/api/auth/logout-everywhere", headers=CSRF)

    assert reply.status_code == 204
    assert "Max-Age=0" in reply.headers["set-cookie"] or f'{SESSION_COOKIE}=""' in reply.headers["set-cookie"]
    assert signed.client.get("/api/auth/me").status_code == 401
    assert other.get("/api/auth/me").status_code == 401
    api.mint_session(api.client(), signed.customer_id, signed.user_id)  # and a new sign-in works
    assert "auth.logout_everywhere" in api.audit_actions(signed.customer_id)


def test_disable_then_enable_does_not_bring_old_cookies_back(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    path = f"/api/admin/users/{member.user_id}"

    assert admin.client.patch(path, json={"disabled": True}, headers=CSRF).status_code == 200
    assert admin.client.patch(path, json={"disabled": False}, headers=CSRF).status_code == 200

    assert member.client.get("/api/auth/me").status_code == 401


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
            auth_source="keycloak",
            assurance=api.settings.keycloak_required_acr,
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


# --- the audit trail ----------------------------------------------------------------------------------


# --- the audit trail ----------------------------------------------------------------------------------


def test_every_auth_event_is_audited_with_a_valid_action(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    admin.client.post(f"/api/admin/users/{member.user_id}/unlock", headers=CSRF)
    other = api.sign_in(admin.customer_id, "member")
    assert other.client.post("/api/auth/logout-everywhere", headers=CSRF).status_code == 204
    assert admin.client.post("/api/auth/logout", headers=CSRF).status_code == 204

    with db.customer(admin.customer_id) as conn:
        rows = conn.execute(
            text("SELECT action, target_type, target_id, details FROM audit_log ORDER BY id")
        ).all()
    actions = [str(row[0]) for row in rows]
    assert all(ACTION.fullmatch(action) for action in actions)
    assert {
        "auth.logout_everywhere",
        "auth.logout",
        "user.unlock",
    } <= set(actions)
    for action, target_type, target_id, _ in rows:
        if str(action).startswith(("auth.", "user.")):
            assert target_type == "user", action
            assert target_id in {admin.user_id, member.user_id, other.user_id}, action


# --- an old password cookie ----------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["legacy_password", "legacy_mfa", "legacy_google", "legacy_google_mfa"])
def test_a_cookie_of_the_old_sign_in_is_refused_whatever_the_settings(db: Database, source: str) -> None:
    """Keycloak is the only sign-in (21 Nov 2026): a cookie from before it must not keep working
    after the deploy, whether or not Keycloak is switched on, and whoever the person is."""
    api = Api(db)
    signed = api.new_signed()
    for authority in ("legacy", "keycloak"):
        with api.db.engine.begin() as conn:
            conn.execute(
                text("UPDATE users SET auth_authority = :a WHERE id = :u"),
                {"a": authority, "u": signed.user_id},
            )
        claims = SessionClaims(
            user_id=signed.user_id,
            customer_id=signed.customer_id,
            role="member",
            scope="app",
            session_version=1,
            expires_at=int(NOW.timestamp()) + 3600,
            auth_source=source,
        )
        client = api.client()
        client.cookies.set(SESSION_COOKIE, sign_session(claims, api.settings.session_secret))
        reply = client.get("/api/auth/me")
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), authority
    assert signed.client.get("/api/auth/me").status_code == 200  # the Keycloak session still works
