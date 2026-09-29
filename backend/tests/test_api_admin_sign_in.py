"""Signing in on the admin screen: password, 6-digit code, 2FA on and off, the admin session.

PRD 1 A1, A2, A6 and A7, with the council's security findings: an enrol token
is not a session and works once, a code works once, a wrong code counts toward
the lockout, and every /api/admin route needs the admin session (an admin's own
app session included). Two-step sign-in is each admin's own choice: while it
is off the password alone signs in; once it is on, the code is always needed.
"""

import base64
import json
import logging
import re

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api import totp
from kaagaz.api.auth import SESSION_COOKIE
from kaagaz.api.secret_box import SecretBox
from kaagaz.db.engine import Database
from tests.test_api_support import (
    ANY_ID,
    CSRF,
    PASSWORD,
    Api,
    TestClient,
    call,
    route_calls,
    wrong_code,
)

pytestmark = pytest.mark.db

GROUPED = re.compile(r"[A-Z2-7]{4}( [A-Z2-7]{4}){7}")  # 20 bytes = 32 base32 characters
WRONG = "not the password!!"
ADMIN_SESSION_REFUSED = {
    "error": {"code": "admin_session_required", "message": "Sign in on the admin screen to use this."}
}


def _secret_from(grouped: str) -> bytes:
    return base64.b32decode(grouped.replace(" ", ""))


def _turn_on(client: TestClient, password: str = PASSWORD) -> httpx.Response:
    return client.post("/api/auth/admin/2fa/start", json={"password": password}, headers=CSRF)


def _turn_off(client: TestClient, password: str = PASSWORD) -> httpx.Response:
    return client.post("/api/auth/admin/2fa/off", json={"password": password}, headers=CSRF)


class _Enrolling:
    """An admin with 2FA off, signed in on the admin screen with the password
    alone, who has pressed "Turn on" (``/2fa/start`` with the current password)
    and holds the new key."""

    def __init__(self, api: Api) -> None:
        self.api = api
        self.customer_id, _ = api.customer()
        self.user_id, self.email = api.add_user(self.customer_id, "admin")
        self.client = api.client()
        signed_in = api.admin_login(self.client, self.email)
        assert signed_in.status_code == 200, signed_in.text
        self.reply = _turn_on(self.client)
        assert self.reply.status_code == 200, self.reply.text
        body = self.reply.json()
        self.token: str = body["enrol_token"]
        self.shown: str = body["secret"]
        self.secret = _secret_from(self.shown)

    def enrol(self, code: str | None = None, *, client: TestClient | None = None) -> httpx.Response:
        return (client or self.client).post(
            "/api/auth/admin/enrol",
            json={"enrol_token": self.token, "code": self.api.code(self.secret) if code is None else code},
            headers=CSRF,
        )


# --- 2FA off: the password alone -----------------------------------------------------------


def test_an_admin_with_2fa_off_signs_in_with_the_password_alone(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "admin")
    client = api.client()

    reply = api.admin_login(client, email)

    assert reply.status_code == 200, reply.text
    me = reply.json()
    assert me["user"]["id"] == user_id
    assert me["user"]["totp_enabled"] is False
    assert me["session"] == {"scope": "admin"}
    assert "enrol" not in reply.text
    assert f"Max-Age={60 * 60}" in reply.headers["set-cookie"]
    assert client.get("/api/admin/users").status_code == 200
    assert api.audit_rows(customer_id)[-1] == ("auth.admin_login", "user", user_id, {"second_factor": False})
    assert api.user_state(customer_id, user_id)["failed_logins"] == 0


# --- turning 2FA on ----------------------------------------------------------------------


def test_turn_on_shows_a_new_key_and_stores_nothing_yet(db: Database) -> None:
    api = Api(db)
    enrolling = _Enrolling(api)

    body = enrolling.reply.json()
    assert set(body) == {"enrol_token", "secret", "otpauth_uri"}
    assert GROUPED.fullmatch(enrolling.shown)
    assert len(enrolling.secret) == 20
    assert body["otpauth_uri"] == totp.otpauth_uri(enrolling.secret, enrolling.email)
    assert "set-cookie" not in enrolling.reply.headers
    state = api.user_state(enrolling.customer_id, enrolling.user_id)
    assert state["totp_secret_enc"] is None
    assert state["totp_enabled_at"] is None
    assert state["failed_logins"] == 0  # the right password clears the count


def test_turn_on_with_a_wrong_or_no_password_shows_no_key(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "admin")
    client = api.client()
    assert api.admin_login(client, email).status_code == 200

    wrong = _turn_on(client, WRONG)
    missing = client.post("/api/auth/admin/2fa/start", headers=CSRF)

    assert wrong.status_code == 400
    assert wrong.json()["error"] == {"code": "bad_request", "message": "Your current password is not right."}
    assert missing.status_code == 400
    for reply in (wrong, missing):
        assert "secret" not in reply.text
        assert "enrol_token" not in reply.text
    assert api.user_state(customer_id, user_id)["failed_logins"] == 1  # the wrong one, counted
    assert api.audit_rows(customer_id)[-1][3] == {"reason": "wrong_password"}

    for _ in range(api.settings.login_max_failures - 1):
        assert _turn_on(client, WRONG).status_code == 400
    locked = _turn_on(client)
    assert locked.status_code == 400
    assert locked.json()["error"]["message"] == "Too many wrong passwords. Try again in 15 minutes."
    assert "secret" not in locked.text


def test_turning_on_with_the_code_means_the_code_is_needed_from_then_on(db: Database) -> None:
    """``enrol_totp`` does not change the session version, so every browser of
    this admin stays signed in; the reply is a new admin session here."""
    api = Api(db)
    enrolling = _Enrolling(api)
    other = api.client()
    assert api.admin_login(other, enrolling.email).status_code == 200
    version = api.user_state(enrolling.customer_id, enrolling.user_id)["session_version"]

    reply = enrolling.enrol()

    assert reply.status_code == 200, reply.text
    me = reply.json()
    assert me["user"]["id"] == enrolling.user_id
    assert me["user"]["totp_enabled"] is True
    assert me["session"] == {"scope": "admin"}
    assert f"Max-Age={60 * 60}" in reply.headers["set-cookie"]
    assert enrolling.client.get("/api/admin/users").status_code == 200
    assert other.get("/api/admin/users").status_code == 200
    state = api.user_state(enrolling.customer_id, enrolling.user_id)
    assert state["session_version"] == version
    assert state["totp_enabled_at"] == api.clock()
    assert state["totp_last_step"] == api.step()
    sealed = str(state["totp_secret_enc"])
    assert sealed.startswith("v1:")
    assert totp.b32(enrolling.secret) not in sealed
    box = SecretBox(api.settings.totp_encryption_key)
    assert box.open(sealed, aad=enrolling.user_id) == enrolling.secret
    assert api.audit_actions(enrolling.customer_id)[-2:] == ["auth.totp_on", "auth.admin_login"]

    api.clock.advance(totp.STEP_SECONDS)
    for code in (None, wrong_code(api, enrolling.secret)):
        refused = api.admin_login(api.client(), enrolling.email, code=code)
        assert refused.status_code == 401
        assert refused.json()["error"]["code"] == "bad_credentials"
    assert api.admin_login(api.client(), enrolling.email, code=api.code(enrolling.secret)).status_code == 200


def test_turn_on_when_2fa_is_already_on_is_409(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()

    reply = _turn_on(signed.client, WRONG)  # 409 comes before the password check

    assert reply.status_code == 409
    assert reply.json()["error"]["code"] == "conflict"
    assert "secret" not in reply.text
    assert api.user_state(signed.customer_id, signed.user_id)["failed_logins"] == 0


def test_enrol_sign_out_and_sign_in_again_with_the_code(db: Database) -> None:
    """The PRD's exit test, first line."""
    api = Api(db)
    enrolling = _Enrolling(api)
    assert enrolling.enrol().status_code == 200
    assert enrolling.client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert enrolling.client.get("/api/auth/me").status_code == 401

    api.clock.advance(totp.STEP_SECONDS)
    again = api.admin_login(enrolling.client, enrolling.email, code=api.code(enrolling.secret))

    assert again.status_code == 200, again.text
    assert again.json()["session"] == {"scope": "admin"}
    assert enrolling.client.get("/api/admin/users").status_code == 200


def test_an_enrol_token_cannot_enrol_twice(db: Database) -> None:
    api = Api(db)
    enrolling = _Enrolling(api)
    assert enrolling.enrol().status_code == 200
    stored = api.user_state(enrolling.customer_id, enrolling.user_id)["totp_secret_enc"]

    api.clock.advance(totp.STEP_SECONDS)
    second = enrolling.enrol(client=api.client())

    assert second.status_code == 401
    assert second.json()["error"]["code"] == "not_signed_in"
    assert "set-cookie" not in second.headers
    state = api.user_state(enrolling.customer_id, enrolling.user_id)
    assert state["totp_secret_enc"] == stored
    assert state["failed_logins"] == 0  # a replayed token is not a guess: it can't lock the admin out


def test_an_enrol_token_from_before_a_session_change_does_not_enrol(db: Database) -> None:
    """The token carries the session version: a password reset in between voids it."""
    api = Api(db)
    enrolling = _Enrolling(api)
    with db.customer(enrolling.customer_id) as conn:
        conn.execute(
            text("UPDATE users SET session_version = session_version + 1 WHERE id = :u"),
            {"u": enrolling.user_id},
        )

    reply = enrolling.enrol()

    assert reply.status_code == 401
    assert reply.json()["error"]["code"] == "not_signed_in"
    state = api.user_state(enrolling.customer_id, enrolling.user_id)
    assert state["totp_enabled_at"] is None
    assert state["failed_logins"] == 0  # a stale token is not a guess: nothing is counted
    assert "auth.login_failed" not in api.audit_actions(enrolling.customer_id)


def test_an_expired_or_changed_enrol_token_is_refused(db: Database) -> None:
    api = Api(db)
    enrolling = _Enrolling(api)
    body, _, signature = enrolling.token.partition(".")
    changed = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + signature
    reply = enrolling.client.post(
        "/api/auth/admin/enrol",
        json={"enrol_token": changed, "code": api.code(enrolling.secret)},
        headers=CSRF,
    )
    assert reply.status_code == 401
    assert reply.json()["error"]["code"] == "not_signed_in"

    api.clock.advance(10 * 60)  # the token lasts 10 minutes
    expired = enrolling.enrol()
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "not_signed_in"
    assert api.user_state(enrolling.customer_id, enrolling.user_id)["totp_enabled_at"] is None


def test_a_wrong_enrol_code_is_refused_and_counts(db: Database) -> None:
    api = Api(db)
    enrolling = _Enrolling(api)

    reply = enrolling.enrol(wrong_code(api, enrolling.secret))

    assert reply.status_code == 401
    assert reply.json()["error"]["code"] == "bad_credentials"
    state = api.user_state(enrolling.customer_id, enrolling.user_id)
    assert state["totp_enabled_at"] is None
    assert state["failed_logins"] == 1
    assert enrolling.enrol().status_code == 200  # the right code still works after a slip


def test_the_enrol_token_is_not_a_session_and_a_session_is_not_an_enrol_token(db: Database) -> None:
    api = Api(db)
    enrolling = _Enrolling(api)
    client = api.client()
    client.cookies.set(SESSION_COOKIE, enrolling.token)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/admin/users").status_code == 401

    signed = api.admin_sign_in(enrolling.customer_id)
    session_token = signed.client.cookies[SESSION_COOKIE]
    api.clock.advance(totp.STEP_SECONDS)
    reply = api.client().post(
        "/api/auth/admin/enrol",
        json={"enrol_token": session_token, "code": api.code(enrolling.secret)},
        headers=CSRF,
    )
    assert reply.status_code == 401
    assert reply.json()["error"]["code"] == "not_signed_in"


def test_an_enrolled_admin_without_a_code_is_never_offered_enrolment(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    admin = api.enrol_admin(customer_id)
    before = api.user_state(customer_id, admin.user_id)
    client = api.client()

    for code in (None, ""):
        reply = api.admin_login(client, admin.email, code=code)
        assert reply.status_code == 401
        assert reply.json()["error"]["code"] == "bad_credentials"
        assert "enrol" not in reply.text
        assert "set-cookie" not in reply.headers

    after = api.user_state(customer_id, admin.user_id)
    assert after["totp_secret_enc"] == before["totp_secret_enc"]
    assert after["totp_enabled_at"] == before["totp_enabled_at"]


# --- turning 2FA off ---------------------------------------------------------------------


def test_turn_off_with_a_wrong_password_is_400_and_counts_toward_the_lockout(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    before = api.user_state(signed.customer_id, signed.user_id)

    reply = _turn_off(signed.client, WRONG)

    assert reply.status_code == 400
    assert reply.json()["error"] == {"code": "bad_request", "message": "Your current password is not right."}
    state = api.user_state(signed.customer_id, signed.user_id)
    assert state["failed_logins"] == 1
    assert state["totp_enabled_at"] == before["totp_enabled_at"]
    assert state["session_version"] == before["session_version"]
    assert api.audit_rows(signed.customer_id)[-1][3] == {"reason": "wrong_password"}

    for _ in range(api.settings.login_max_failures - 1):
        assert _turn_off(signed.client, WRONG).status_code == 400
    locked = _turn_off(signed.client)
    assert locked.status_code == 400
    assert locked.json()["error"]["message"] == "Too many wrong passwords. Try again in 15 minutes."
    assert api.user_state(signed.customer_id, signed.user_id)["totp_enabled_at"] is not None


def test_turn_off_ends_the_other_sessions_and_keeps_this_one(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    assert signed.totp_secret is not None
    api.clock.advance(totp.STEP_SECONDS)
    other = api.client()
    assert api.admin_login(other, signed.email, code=api.code(signed.totp_secret)).status_code == 200
    version = api.user_state(signed.customer_id, signed.user_id)["session_version"]

    reply = _turn_off(signed.client)

    assert reply.status_code == 200, reply.text
    me = reply.json()
    assert me["user"]["id"] == signed.user_id
    assert me["user"]["totp_enabled"] is False
    assert me["session"] == {"scope": "admin"}
    # Re-signed with the new version; the session keeps its end time.
    assert f"Max-Age={60 * 60 - totp.STEP_SECONDS}" in reply.headers["set-cookie"]
    assert signed.client.get("/api/admin/users").status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    state = api.user_state(signed.customer_id, signed.user_id)
    assert (state["totp_secret_enc"], state["totp_enabled_at"], state["totp_last_step"]) == (None, None, None)
    assert state["session_version"] == version + 1
    assert state["failed_logins"] == 0
    assert api.audit_rows(signed.customer_id)[-1] == ("auth.totp_off", "user", signed.user_id, {})
    assert api.admin_login(api.client(), signed.email).status_code == 200  # the password alone again


def test_turn_off_when_2fa_is_already_off_is_409_and_not_counted(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "admin")
    client = api.client()
    assert api.admin_login(client, email).status_code == 200
    before = api.user_state(customer_id, user_id)

    reply = _turn_off(client)

    assert reply.status_code == 409
    assert reply.json()["error"]["code"] == "conflict"
    assert api.user_state(customer_id, user_id) == before


def test_turn_on_and_off_need_an_admin_session(db: Database) -> None:
    api = Api(db)
    app_session = api.new_signed("admin")  # an admin, signed in on the normal screen
    before = api.user_state(app_session.customer_id, app_session.user_id)

    for path in ("/api/auth/admin/2fa/start", "/api/auth/admin/2fa/off"):
        body = {"password": PASSWORD}
        refused = app_session.client.post(path, json=body, headers=CSRF)
        assert refused.status_code == 403, path
        assert refused.json() == ADMIN_SESSION_REFUSED, path
        assert api.client().post(path, json=body, headers=CSRF).status_code == 401, path

    assert api.user_state(app_session.customer_id, app_session.user_id) == before


# --- codes -------------------------------------------------------------------------------


def test_the_same_code_twice_is_refused(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    signed = api.admin_sign_in(customer_id)
    assert signed.totp_secret is not None

    again = api.admin_login(api.client(), signed.email, code=api.code(signed.totp_secret))

    assert again.status_code == 401
    assert again.json()["error"]["code"] == "bad_credentials"
    assert "set-cookie" not in again.headers
    assert api.user_state(customer_id, signed.user_id)["failed_logins"] == 1


def test_a_code_one_step_off_works_once_and_two_steps_off_never(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    admin = api.enrol_admin(customer_id)

    for offset in (-2, 2):
        assert (
            api.admin_login(api.client(), admin.email, code=api.code(admin.secret, offset)).status_code == 401
        )
    assert api.admin_login(api.client(), admin.email, code=api.code(admin.secret, -1)).status_code == 200
    assert api.admin_login(api.client(), admin.email, code=api.code(admin.secret, 1)).status_code == 200
    # Step +1 is now the last one used, so the code of this very step is refused.
    assert api.admin_login(api.client(), admin.email, code=api.code(admin.secret)).status_code == 401
    assert api.user_state(customer_id, admin.user_id)["totp_last_step"] == api.step(1)


def test_a_code_typed_with_a_space_in_the_middle_works(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    admin = api.enrol_admin(customer_id)
    code = api.code(admin.secret)
    reply = api.admin_login(api.client(), admin.email, code=f"{code[:3]} {code[3:]}")
    assert reply.status_code == 200, reply.text


def test_wrong_codes_count_toward_the_lockout(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    admin = api.enrol_admin(customer_id)
    client = api.client()

    for _ in range(api.settings.login_max_failures):
        assert api.admin_login(client, admin.email, code=wrong_code(api, admin.secret)).status_code == 401
    right = api.admin_login(client, admin.email, code=api.code(admin.secret))

    assert right.status_code == 401
    state = api.user_state(customer_id, admin.user_id)
    assert state["failed_logins"] == api.settings.login_max_failures
    assert state["locked_until"] is not None
    assert "auth.locked" in api.audit_actions(customer_id)


# --- who may use the admin screen ----------------------------------------------------------


@pytest.mark.parametrize("enrolled", [True, False])
def test_an_admin_who_must_change_their_password_gets_403(db: Database, enrolled: bool) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    if enrolled:
        admin = api.enrol_admin(customer_id)
        user_id, email, code = admin.user_id, admin.email, api.code(admin.secret)
    else:
        user_id, email = api.add_user(customer_id, "admin")
        code = None
    with db.customer(customer_id) as conn:
        conn.execute(text("UPDATE users SET must_change_password = true WHERE id = :u"), {"u": user_id})
    client = api.client()

    reply = api.admin_login(client, email, code=code)

    assert reply.status_code == 403
    assert reply.json() == {
        "error": {"code": "password_change_required", "message": "Set a new password to continue."}
    }
    assert "set-cookie" not in reply.headers
    wrong = api.admin_login(client, email, password=WRONG, code=code)
    assert wrong.status_code == 401  # a wrong password never learns about the change
    assert (api.user_state(customer_id, user_id)["totp_enabled_at"] is not None) is enrolled


@pytest.mark.parametrize("role", ["member", "reviewer"])
def test_a_non_admin_on_the_admin_screen_gets_the_plain_401(db: Database, role: str) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, role)
    client = api.client()

    right = api.admin_login(client, email)
    wrong = api.admin_login(client, email, password=WRONG)

    assert right.status_code == wrong.status_code == 401
    assert right.text == wrong.text
    assert right.json()["error"]["code"] == "bad_credentials"
    assert "set-cookie" not in right.headers
    # Nothing counted and nothing audited: the admin screen can't lock a member out.
    for _ in range(10):
        api.admin_login(client, email, password=WRONG)
    assert api.user_state(customer_id, user_id)["failed_logins"] == 0
    assert "auth.login_failed" not in api.audit_actions(customer_id)
    signed_in = api.client().post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF
    )
    assert signed_in.status_code == 200  # still not locked


# --- the admin session -------------------------------------------------------------------------


def test_the_admin_session_lasts_60_minutes(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    admin = api.enrol_admin(customer_id)
    client = api.client()
    reply = api.admin_login(client, admin.email, code=api.code(admin.secret))
    assert reply.status_code == 200
    assert f"Max-Age={60 * 60}" in reply.headers["set-cookie"]

    api.clock.advance(60 * 60 - 1)
    assert client.get("/api/admin/users").status_code == 200
    assert client.get("/api/documents").status_code == 200
    api.clock.advance(1)
    assert client.get("/api/admin/users").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_every_admin_route_refuses_an_app_session(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed("admin")  # an admin, signed in on the normal screen
    calls = route_calls(api.app, "/api/admin")
    assert {
        ("GET", "/api/admin/users"),
        ("POST", "/api/admin/users"),
        ("PATCH", f"/api/admin/users/{ANY_ID}"),
        ("POST", f"/api/admin/users/{ANY_ID}/unlock"),
        ("POST", f"/api/admin/users/{ANY_ID}/reset-password"),
        ("POST", f"/api/admin/users/{ANY_ID}/reset-2fa"),
        ("GET", "/api/admin/settings"),
        ("PATCH", "/api/admin/settings"),
        ("GET", "/api/admin/audit"),
        ("GET", "/api/admin/stats"),
    } <= set(calls)

    for method, path in calls:
        reply = call(signed.client, method, path)
        assert reply.status_code == 403, (method, path)
        assert reply.json() == ADMIN_SESSION_REFUSED, (method, path)


def test_an_admins_app_session_can_use_documents_chat_and_review(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed("admin")

    me = signed.client.get("/api/auth/me").json()
    assert me["user"]["role"] == "admin"
    assert me["session"] == {"scope": "app"}  # so the screen shows no Admin menu
    document = api.add_document(signed.customer_id, uploaded_by=signed.user_id)
    assert signed.client.get("/api/documents").status_code == 200
    assert signed.client.get(f"/api/documents/{document}").status_code == 200
    assert signed.client.post("/api/chat", json={"question": "total?"}, headers=CSRF).status_code == 200
    assert signed.client.get("/api/review").status_code == 200


def test_an_admin_session_can_use_the_app_too(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    assert admin.client.get("/api/documents").status_code == 200
    assert admin.client.post("/api/chat", json={"question": "total?"}, headers=CSRF).status_code == 200
    assert admin.client.get("/api/review").status_code == 200
    assert admin.client.get("/api/auth/me").json()["session"] == {"scope": "admin"}


# --- secrets stay secret ---------------------------------------------------------------------


def test_no_secret_reaches_logs_audit_or_the_api(db: Database, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    api = Api(db)
    enrolling = _Enrolling(api)
    replies: list[httpx.Response] = [enrolling.enrol(wrong_code(api, enrolling.secret)), enrolling.enrol()]
    client = enrolling.client
    member_id, _ = api.add_user(enrolling.customer_id, "member")
    reset = client.post(f"/api/admin/users/{member_id}/reset-password", headers=CSRF)
    temporary = reset.json()["temporary_password"]
    replies += [
        client.get("/api/admin/users"),
        client.get("/api/admin/audit"),
        client.get("/api/auth/me"),
        client.post(f"/api/admin/users/{member_id}/unlock", headers=CSRF),
        client.post("/api/auth/logout", headers=CSRF),
    ]
    api.clock.advance(totp.STEP_SECONDS)
    replies.append(api.admin_login(client, enrolling.email, code=api.code(enrolling.secret)))
    sealed = str(api.user_state(enrolling.customer_id, enrolling.user_id)["totp_secret_enc"])
    replies += [_turn_off(client, WRONG), _turn_off(client)]

    secrets = {
        "the secret": totp.b32(enrolling.secret),
        "the grouped secret": enrolling.shown,
        "the otpauth link": enrolling.reply.json()["otpauth_uri"],
        "the sealed secret": sealed,
        "the enrol token": enrolling.token,
        "the password": PASSWORD,
        "the temporary password": temporary,
    }
    with db.customer(enrolling.customer_id) as conn:
        audit_rows = [
            json.dumps([str(value) for value in row])
            for row in conn.execute(
                text("SELECT action, target_type, target_id, details FROM audit_log")
            ).all()
        ]
    bodies = [reply.text for reply in replies]
    for name, secret in secrets.items():
        assert secret not in caplog.text, name
        assert all(secret not in row for row in audit_rows), name
        assert all(secret not in body for body in bodies), name
    for body in bodies:
        assert "totp_secret" not in body
        assert "scrypt$" not in body
