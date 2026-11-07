"""An admin's sign-in: password, 6-digit code, setting up two-step, the admin session.

PRD 1 A1, A2, A6 and A7, with the council's security findings: an enrol token
is not a session and works once, a code works once, a wrong code counts toward
the lockout, and every /api/admin route needs the admin session. Two-step sign-in
is required for every admin (0035, 16 Oct 2026), on the one sign-in (/auth/login):
an admin without it gets the set-up offer and no session; with it, the code is
always needed. /auth/admin/login is gone (410). An admin may turn their own off (0061b,
30 Oct 2026) and then signs in with the password alone, until they turn it on again.
"""

import base64
import json
import logging
import re

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api import totp
from kaagaz.api.auth import SESSION_COOKIE, SessionClaims, sign_session
from kaagaz.api.secret_box import SecretBox
from kaagaz.db.engine import Database
from tests.test_api_support import (
    ANY_ID,
    CSRF,
    PASSWORD,
    Api,
    Signed,
    TestClient,
    call,
    route_calls,
    wrong_code,
)

pytestmark = pytest.mark.db

GROUPED = re.compile(r"[A-Z2-7]{4}( [A-Z2-7]{4}){7}")  # 20 bytes = 32 base32 characters
WRONG = "not the password!!"
ADMIN_SESSION_REFUSED = {
    "error": {
        "code": "admin_session_required",
        "message": "Only admins can use this. Ask your admin if you need it.",
    }
}
ENROL_FIRST = "Set up two-step sign-in to continue. Admins need it to keep your organisation safe."
WRONG_CODE = "That code is not right. Wait for the next code and try again."


def _secret_from(grouped: str) -> bytes:
    return base64.b32decode(grouped.replace(" ", ""))


def _turn_on(client: TestClient, password: str = PASSWORD) -> httpx.Response:
    return client.post("/api/auth/2fa/start", json={"password": password}, headers=CSRF)


def _turn_off(client: TestClient, password: str = PASSWORD, code: str | None = None) -> httpx.Response:
    body = {"password": password} if code is None else {"password": password, "code": code}
    return client.post("/api/auth/2fa/off", json=body, headers=CSRF)


def _session_client(api: Api, customer_id: str, user_id: str, *, scope: str) -> TestClient:
    """A client holding a genuine admin session of this scope, made with the server's key:
    the kind a browser may still hold from before 0035."""
    claims = SessionClaims(
        user_id=user_id,
        customer_id=customer_id,
        role="admin",
        scope=scope,
        session_version=int(api.user_state(customer_id, user_id)["session_version"]),
        expires_at=int(api.clock().timestamp()) + 600,
    )
    client = api.client()
    client.cookies.set(SESSION_COOKIE, sign_session(claims, api.settings.session_secret))
    return client


class _Enrolling:
    """An admin without two-step sign-in whose right password at /auth/login got the
    set-up offer (403 mfa_enrol_required): the new key, and no session."""

    def __init__(self, api: Api) -> None:
        self.api = api
        self.customer_id, _ = api.customer()
        self.user_id, self.email = api.add_user(self.customer_id, "admin")
        self.client = api.client()
        self.reply = api.admin_login(self.client, self.email)
        assert self.reply.status_code == 403, self.reply.text
        body = self.reply.json()["error"]["details"]
        self.token: str = body["enrol_token"]
        self.shown: str = body["secret"]
        self.secret = _secret_from(self.shown)

    def enrol(self, code: str | None = None, *, client: TestClient | None = None) -> httpx.Response:
        return (client or self.client).post(
            "/api/auth/admin/enrol",
            json={"enrol_token": self.token, "code": self.api.code(self.secret) if code is None else code},
            headers=CSRF,
        )


# --- no two-step yet: the set-up offer, no session ------------------------------------------


def test_an_admin_without_2fa_gets_the_set_up_offer_and_no_session(db: Database) -> None:
    api = Api(db)
    enrolling = _Enrolling(api)

    error = enrolling.reply.json()["error"]
    assert (error["code"], error["message"]) == ("mfa_enrol_required", ENROL_FIRST)
    assert set(error["details"]) == {"enrol_token", "secret", "otpauth_uri"}
    assert GROUPED.fullmatch(enrolling.shown)
    assert len(enrolling.secret) == 20
    assert error["details"]["otpauth_uri"] == totp.otpauth_uri(enrolling.secret, enrolling.email)
    assert "set-cookie" not in enrolling.reply.headers
    assert enrolling.client.get("/api/auth/me").status_code == 401
    state = api.user_state(enrolling.customer_id, enrolling.user_id)
    assert state["totp_secret_enc"] is None
    assert state["totp_enabled_at"] is None
    assert state["failed_logins"] == 0  # the right password clears the count

    wrong = api.admin_login(api.client(), enrolling.email, password=WRONG)
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "bad_credentials")
    assert "enrol" not in wrong.text  # only a right password learns that a code must be set up
    assert api.user_state(enrolling.customer_id, enrolling.user_id)["failed_logins"] == 1


def test_turning_on_with_the_code_means_the_code_is_needed_from_then_on(db: Database) -> None:
    """``enrol_totp`` does not change the session version; the reply is a new admin session."""
    api = Api(db)
    enrolling = _Enrolling(api)
    version = api.user_state(enrolling.customer_id, enrolling.user_id)["session_version"]

    reply = enrolling.enrol()

    assert reply.status_code == 200, reply.text
    me = reply.json()
    assert me["user"]["id"] == enrolling.user_id
    assert me["user"]["totp_enabled"] is True
    assert me["session"] == {"scope": "admin"}
    assert f"Max-Age={60 * 60}" in reply.headers["set-cookie"]
    assert enrolling.client.get("/api/admin/users").status_code == 200
    state = api.user_state(enrolling.customer_id, enrolling.user_id)
    assert state["session_version"] == version
    assert state["totp_enabled_at"] == api.clock()
    assert state["totp_last_step"] == api.step()
    sealed = str(state["totp_secret_enc"])
    assert sealed.startswith("v1:")
    assert totp.b32(enrolling.secret) not in sealed
    box = SecretBox(api.settings.totp_encryption_key)
    assert box.open(sealed, aad=enrolling.user_id) == enrolling.secret
    assert api.audit_actions(enrolling.customer_id)[-2:] == ["auth.totp_on", "auth.login"]

    api.clock.advance(totp.STEP_SECONDS)
    for code, error in ((None, "code_required"), (wrong_code(api, enrolling.secret), "bad_credentials")):
        refused = api.admin_login(api.client(), enrolling.email, code=code)
        assert refused.status_code == 401
        assert refused.json()["error"]["code"] == error
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


def test_an_enrolled_admin_without_a_code_is_asked_for_it_and_never_offered_enrolment(
    db: Database,
) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    admin = api.enrol_admin(customer_id)
    before = api.user_state(customer_id, admin.user_id)
    client = api.client()

    for code in (None, "", "   "):
        reply = api.admin_login(client, admin.email, code=code)
        assert reply.status_code == 401
        assert reply.json()["error"]["code"] == "code_required"  # the screen then asks for it
        assert "enrol" not in reply.text
        assert "set-cookie" not in reply.headers

    after = api.user_state(customer_id, admin.user_id)
    assert after["totp_secret_enc"] == before["totp_secret_enc"]
    assert after["totp_enabled_at"] == before["totp_enabled_at"]
    assert after["failed_logins"] == 3  # each missing code is counted


# --- an admin turns their own two-step off, and on again (Tushar, 30 Oct 2026) -------------


def _turned_off(api: Api) -> Signed:
    """A new admin who signed in with a code, then turned two-step sign-in off."""
    signed = api.new_admin()
    assert signed.totp_secret is not None
    api.clock.advance(totp.STEP_SECONDS)  # the sign-in's code works once
    reply = _turn_off(signed.client, code=api.code(signed.totp_secret))
    assert reply.status_code == 200, reply.text
    return signed


def test_turning_off_keeps_this_browser_and_signs_out_every_other(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    assert signed.totp_secret is not None
    api.clock.advance(totp.STEP_SECONDS)
    other = api.client()
    assert api.admin_login(other, signed.email, code=api.code(signed.totp_secret)).status_code == 200
    version = api.user_state(signed.customer_id, signed.user_id)["session_version"]
    api.clock.advance(totp.STEP_SECONDS)

    reply = _turn_off(signed.client, code=api.code(signed.totp_secret))

    assert reply.status_code == 200, reply.text
    me = reply.json()
    assert me["user"]["totp_enabled"] is False
    assert me["user"]["two_step_turned_off"] is True
    assert me["session"] == {"scope": "admin"}
    assert signed.client.get("/api/auth/me").json() == me  # this browser stays in, re-signed
    assert signed.client.get("/api/admin/users").status_code == 200
    assert other.get("/api/auth/me").status_code == 401  # every other browser is signed out
    state = api.user_state(signed.customer_id, signed.user_id)
    assert state["session_version"] == version + 1
    assert (state["totp_secret_enc"], state["totp_enabled_at"], state["totp_last_step"]) == (None, None, None)
    assert state["totp_off_at"] == api.clock()
    assert state["failed_logins"] == 0
    assert api.audit_rows(signed.customer_id)[-1] == ("auth.totp_off", "user", signed.user_id, {})


def test_turned_off_the_admin_signs_in_with_email_and_password_only(db: Database) -> None:
    api = Api(db)
    signed = _turned_off(api)
    client = api.client()

    reply = api.admin_login(client, signed.email)

    assert reply.status_code == 200, reply.text
    assert reply.json()["session"] == {"scope": "admin"}
    assert reply.json()["user"]["two_step_turned_off"] is True
    assert client.get("/api/admin/users").status_code == 200
    assert client.get("/api/documents").status_code == 200
    assert api.admin_login(api.client(), signed.email, code="123456").status_code == 200  # ignored
    wrong = api.admin_login(api.client(), signed.email, password=WRONG)
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "bad_credentials")
    assert api.user_state(signed.customer_id, signed.user_id)["totp_off_at"] is not None  # stays off


def test_turning_off_needs_the_password_and_a_right_code_each_failure_counted(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    assert signed.totp_secret is not None
    used = api.code(signed.totp_secret)  # the sign-in's code: used once already
    api.clock.advance(totp.STEP_SECONDS)
    right = api.code(signed.totp_secret)
    before = api.user_state(signed.customer_id, signed.user_id)

    for password, code, message in (
        (WRONG, right, "Your current password is not right."),
        (PASSWORD, None, "Enter the 6-digit code from your authenticator app."),
        (PASSWORD, "", "Enter the 6-digit code from your authenticator app."),
        (PASSWORD, wrong_code(api, signed.totp_secret), WRONG_CODE),
        (PASSWORD, used, WRONG_CODE),
    ):
        reply = _turn_off(signed.client, password, code)
        assert reply.status_code == 400, (password, code)
        assert reply.json()["error"] == {"code": "bad_request", "message": message}

    after = api.user_state(signed.customer_id, signed.user_id)
    assert after["failed_logins"] == 5
    assert after["locked_until"] is not None  # counted like a sign-in, so the lockout too
    assert {key: after[key] for key in ("totp_secret_enc", "totp_enabled_at", "totp_off_at")} == {
        "totp_secret_enc": before["totp_secret_enc"],
        "totp_enabled_at": before["totp_enabled_at"],
        "totp_off_at": None,
    }
    assert "auth.totp_off" not in api.audit_actions(signed.customer_id)
    assert signed.client.get("/api/admin/users").status_code == 200  # still signed in


def test_turning_off_when_already_off_is_409_and_needs_a_session(db: Database) -> None:
    api = Api(db)
    signed = _turned_off(api)

    reply = _turn_off(signed.client, WRONG, "123456")  # 409 comes before the password check

    assert reply.status_code == 409
    assert reply.json()["error"] == {"code": "conflict", "message": "Two-step sign-in is already off."}
    assert api.user_state(signed.customer_id, signed.user_id)["failed_logins"] == 0
    assert _turn_off(api.client(), code="123456").status_code == 401
    # Changed on purpose (#457 with #460): one route for every role; a member's is off too.
    member = api.sign_in(signed.customer_id, "member")
    assert _turn_off(member.client, code="123456").status_code == 409


def test_turning_back_on_clears_turned_off_and_the_code_is_needed_again(db: Database) -> None:
    api = Api(db)
    signed = _turned_off(api)
    offer = _turn_on(signed.client)
    assert offer.status_code == 200, offer.text
    secret = _secret_from(offer.json()["secret"])

    on = signed.client.post(
        "/api/auth/admin/enrol",
        json={"enrol_token": offer.json()["enrol_token"], "code": api.code(secret)},
        headers=CSRF,
    )

    assert on.status_code == 200, on.text
    assert (on.json()["user"]["totp_enabled"], on.json()["user"]["two_step_turned_off"]) == (True, False)
    state = api.user_state(signed.customer_id, signed.user_id)
    assert (state["totp_enabled_at"], state["totp_off_at"]) == (api.clock(), None)
    assert "auth.totp_on" in api.audit_actions(signed.customer_id)[-2:]
    api.clock.advance(totp.STEP_SECONDS)
    assert api.admin_login(api.client(), signed.email).json()["error"]["code"] == "code_required"
    assert api.admin_login(api.client(), signed.email, code=api.code(secret)).status_code == 200


def test_another_admins_reset_means_set_it_up_again_not_turned_off(db: Database) -> None:
    api = Api(db)
    signed = _turned_off(api)
    other = api.new_admin_of(signed.customer_id)
    listed = {item["id"]: item for item in other.client.get("/api/admin/users").json()["items"]}
    assert (listed[signed.user_id]["totp_enabled"], listed[signed.user_id]["two_step_turned_off"]) == (
        False,
        True,
    )
    assert listed[other.user_id]["two_step_turned_off"] is False

    reset = other.client.post(f"/api/admin/users/{signed.user_id}/reset-2fa", headers=CSRF)

    assert reset.status_code == 200, reset.text
    assert reset.json()["two_step_turned_off"] is False
    assert api.user_state(signed.customer_id, signed.user_id)["totp_off_at"] is None
    assert signed.client.get("/api/auth/me").status_code == 401  # signed out
    again = api.admin_login(api.client(), signed.email)
    assert (again.status_code, again.json()["error"]["code"]) == (403, "mfa_enrol_required")


def test_turned_off_deleting_a_tag_still_needs_two_step_and_delete_forever_a_password(db: Database) -> None:
    # Changed on purpose (#538, Tushar, 7 Nov 2026): Delete forever asks for the password again
    # instead of two-step (test_api_delete_forever_password); deleting a tag is unchanged.
    api = Api(db)
    signed = _turned_off(api)
    document = api.add_document(signed.customer_id, deleted_at=api.clock())
    tag = api.add_tag(signed.customer_id, signed.user_id, "Paid")
    refused = {
        "error": {"code": "two_step_required", "message": "Turn on two-step sign-in in Account to do this."}
    }

    forever = signed.client.delete(f"/api/documents/{document}/permanent", headers=CSRF)
    untag = signed.client.delete(f"/api/tags/{tag}", headers=CSRF)

    assert (forever.status_code, forever.json()["error"]["code"]) == (403, "password_confirm_required")
    assert (untag.status_code, untag.json()) == (403, refused)
    assert signed.client.get(f"/api/documents/{document}").status_code == 200  # still there
    # Allowing a member to delete forever too, pointed where the switch is now (#457 with #460).
    member_id, _ = api.add_user(signed.customer_id, "member")
    allow = signed.client.put(
        f"/api/admin/users/{member_id}/delete-forever", json={"allowed": True}, headers=CSRF
    )
    assert (allow.status_code, allow.json()["error"]["message"]) == (
        403,
        "Turn on two-step sign-in in Account to allow this.",
    )


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
    assert state["locked_until"] is None  # changed on purpose (A2): this address's lock, in memory
    assert "auth.locked" in api.audit_actions(customer_id)


# --- who signs in how ------------------------------------------------------------------------


@pytest.mark.parametrize("enrolled", [True, False])
def test_an_admin_who_must_change_their_password_gets_only_the_password_screen(
    db: Database, enrolled: bool
) -> None:
    """With two-step on, the code first; without it, the new password first (enrolling
    needs a password of their own), and the next sign-in then sets up the code."""
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
    if enrolled:
        assert api.admin_login(api.client(), email).json()["error"]["code"] == "code_required"
    client = api.client()

    reply = api.admin_login(client, email, code=code)

    assert reply.status_code == 200, reply.text
    assert reply.json()["user"]["must_change_password"] is True
    assert reply.json()["session"] == {"scope": "admin"}
    refused = client.get("/api/admin/users")
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "password_change_required")
    wrong = api.admin_login(api.client(), email, password=WRONG, code=code)
    assert wrong.status_code == 401  # a wrong password never learns about the change
    assert (api.user_state(customer_id, user_id)["totp_enabled_at"] is not None) is enrolled


@pytest.mark.parametrize("role", ["member", "reviewer"])
def test_a_member_or_reviewer_signs_in_as_before_and_any_code_is_ignored(db: Database, role: str) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    _, email = api.add_user(customer_id, role)
    client = api.client()

    reply = api.admin_login(client, email, code="123456")

    assert reply.status_code == 200, reply.text
    assert reply.json()["session"] == {"scope": "app"}
    assert client.get("/api/documents").status_code == 200
    assert client.get("/api/admin/users").json() == ADMIN_SESSION_REFUSED


@pytest.mark.parametrize("role", ["admin", "member"])
def test_the_old_admin_sign_in_is_gone_and_checks_nothing(db: Database, role: str) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, role)
    for password in (PASSWORD, WRONG):
        reply = api.client().post(
            "/api/auth/admin/login", json={"email": email, "password": password}, headers=CSRF
        )
        assert (reply.status_code, reply.json()["error"]["code"]) == (410, "gone")
        assert "set-cookie" not in reply.headers
    assert api.user_state(customer_id, user_id)["failed_logins"] == 0
    assert "auth.login_failed" not in api.audit_actions(customer_id)


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


def test_every_admin_route_refuses_a_members_session(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed("member")
    calls = route_calls(api.app, "/api/admin")
    assert {
        ("GET", "/api/admin/users"),
        ("POST", "/api/admin/users"),
        ("PATCH", f"/api/admin/users/{ANY_ID}"),
        ("POST", f"/api/admin/users/{ANY_ID}/unlock"),
        ("POST", f"/api/admin/users/{ANY_ID}/reset-password"),
        ("POST", f"/api/admin/users/{ANY_ID}/reset-2fa"),
        ("PUT", f"/api/admin/users/{ANY_ID}/delete-forever"),
        ("GET", "/api/admin/settings"),
        ("PATCH", "/api/admin/settings"),
        ("GET", "/api/admin/audit"),
        ("GET", "/api/admin/stats"),
    } <= set(calls)

    for method, path in calls:
        reply = call(signed.client, method, path)
        assert reply.status_code == 403, (method, path)
        assert reply.json() == ADMIN_SESSION_REFUSED, (method, path)


def test_an_admin_session_without_2fa_or_an_admins_app_session_opens_nothing(db: Database) -> None:
    """Sessions a browser may still hold from before 0035: an admin session of an admin
    with no code set up gets only the set-up answer; an admin's app session (password
    alone) is signed out."""
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, _ = api.add_user(customer_id, "admin")
    no_code = _session_client(api, customer_id, user_id, scope="admin")
    for path in ("/api/admin/users", "/api/documents"):
        reply = no_code.get(path)
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "mfa_enrol_required"), path
    assert no_code.get("/api/auth/me").status_code == 200  # the screen learns what to show
    assert no_code.post("/api/auth/logout", headers=CSRF).status_code == 204

    enrolled = api.enrol_admin(customer_id)
    app_session = _session_client(api, customer_id, enrolled.user_id, scope="app")
    for path in ("/api/admin/users", "/api/documents", "/api/auth/me"):
        reply = app_session.get(path)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), path


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
    api.clock.advance(totp.STEP_SECONDS)
    replies.append(_turn_off(client, code=api.code(enrolling.secret)))
    assert replies[-1].status_code == 200, replies[-1].text

    secrets = {
        "the secret": totp.b32(enrolling.secret),
        "the grouped secret": enrolling.shown,
        "the otpauth link": enrolling.reply.json()["error"]["details"]["otpauth_uri"],
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
