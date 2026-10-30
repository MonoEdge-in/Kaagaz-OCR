"""Two-step codes for everyone (#457 phase 3).

Admins keep their required code and cannot turn it off. Members and reviewers may turn one
on (Account, Two-step sign-in: the password, the new key, a first code) and off (the
password and a code). Anyone with codes on types the code after the password, with the
admins' attempt counting, lock and replay check. An admin resets a lost code; an admin of
another organisation cannot. Every change is audited and sends a security notice.
"""

import base64
import logging

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api import totp
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PASSWORD, Api, TestClient, wrong_code

pytestmark = pytest.mark.db

WRONG = "not the password!!"
NEW_PASSWORD = "a brand new made-up password"
CODE_REQUIRED = "Enter the 6-digit code from your authenticator app."


def _email_api(db: Database) -> tuple[Api, MemoryMailer]:
    mailer = MemoryMailer()
    settings = make_settings(email_enabled=True, email_from="no-reply@example.com")
    return Api(db, settings=settings, mailer=mailer), mailer


def _subjects(mailer: MemoryMailer, address: str) -> list[str]:
    return [sent.subject for sent in mailer.sent if sent.to == address]


def _post(client: TestClient, path: str, body: dict[str, str] | None = None) -> httpx.Response:
    return client.post(path, json=body or {}, headers=CSRF)


def _turn_on(api: Api, client: TestClient) -> bytes:
    """Account, Two-step sign-in, Turn on: the password, then the first code. The secret."""
    offer = _post(client, "/api/auth/2fa/start", {"password": PASSWORD})
    assert offer.status_code == 200, offer.text
    assert set(offer.json()) == {"enrol_token", "secret", "otpauth_uri"}
    secret = base64.b32decode(offer.json()["secret"].replace(" ", ""))
    on = _post(
        client,
        "/api/auth/admin/enrol",
        {"enrol_token": offer.json()["enrol_token"], "code": api.code(secret)},
    )
    assert on.status_code == 200, on.text
    assert on.json()["user"]["totp_enabled"] is True
    assert on.json()["session"] == {"scope": "app"}  # a member's session, never an admin's
    return secret


@pytest.mark.parametrize("role", ["member", "reviewer"])
def test_a_member_or_reviewer_turns_codes_on_and_off(db: Database, role: str) -> None:
    api, mailer = _email_api(db)
    customer_id, _ = api.customer()
    signed = api.sign_in(customer_id, role)
    other_browser = api.client()
    assert api.admin_login(other_browser, signed.email).status_code == 200

    wrong = _post(signed.client, "/api/auth/2fa/start", {"password": WRONG})
    assert (wrong.status_code, wrong.json()["error"]["message"]) == (
        400,
        "Your current password is not right.",
    )
    assert api.user_state(customer_id, signed.user_id)["failed_logins"] == 1  # counted like a sign-in
    secret = _turn_on(api, signed.client)
    assert api.user_state(customer_id, signed.user_id)["failed_logins"] == 0
    assert signed.client.get("/api/auth/me").json()["user"]["totp_enabled"] is True
    again = _post(signed.client, "/api/auth/2fa/start", {"password": PASSWORD})
    assert again.status_code == 409
    assert _subjects(mailer, signed.email) == ["Two-step sign-in is on"]

    # From now on the code is asked after the password.
    asked = api.admin_login(api.client(), signed.email)
    assert (asked.status_code, asked.json()["error"]["code"]) == (401, "code_required")
    api.clock.advance(totp.STEP_SECONDS)
    signed_in = api.admin_login(api.client(), signed.email, code=api.code(secret))
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["session"] == {"scope": "app"}

    # Turning off needs the password and a code.
    api.clock.advance(totp.STEP_SECONDS)
    for body, message in (
        ({"password": PASSWORD}, CODE_REQUIRED),
        ({"password": PASSWORD, "code": wrong_code(api, secret)}, "That code is not right."),
        ({"password": WRONG, "code": api.code(secret)}, "Your current password is not right."),
    ):
        refused = _post(signed.client, "/api/auth/2fa/off", body)
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["message"].startswith(message)
    assert api.user_state(customer_id, signed.user_id)["totp_enabled_at"] is not None
    off = _post(signed.client, "/api/auth/2fa/off", {"password": PASSWORD, "code": api.code(secret)})
    assert off.status_code == 200, off.text
    assert off.json()["user"]["totp_enabled"] is False
    assert signed.client.get("/api/auth/me").status_code == 200  # this browser stays signed in
    assert other_browser.get("/api/auth/me").status_code == 401  # every other one is signed out
    state = api.user_state(customer_id, signed.user_id)
    assert (state["totp_secret_enc"], state["totp_enabled_at"], state["totp_last_step"]) == (None, None, None)
    assert _post(signed.client, "/api/auth/2fa/off", {"password": PASSWORD}).status_code == 409
    assert api.admin_login(api.client(), signed.email).status_code == 200  # the password alone again

    actions = api.audit_actions(customer_id)
    assert actions.index("auth.totp_on") < actions.index("auth.totp_off")
    assert _subjects(mailer, signed.email) == ["Two-step sign-in is on", "Two-step sign-in is off"]


def test_an_admin_cannot_turn_theirs_off(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    assert admin.totp_secret is not None
    before = api.user_state(admin.customer_id, admin.user_id)

    reply = _post(
        admin.client, "/api/auth/2fa/off", {"password": PASSWORD, "code": api.code(admin.totp_secret)}
    )

    assert (reply.status_code, reply.json()["error"]) == (
        403,
        {
            "code": "two_step_required",
            "message": "Admins keep two-step sign-in on to keep your organisation safe.",
        },
    )
    assert api.user_state(admin.customer_id, admin.user_id) == before  # nothing counted or used


def test_a_member_with_codes_types_the_code_and_wrong_codes_lead_to_the_lock(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    member = api.enrol_user(customer_id, "member")
    client = api.client()

    missing = api.admin_login(client, member.email)
    assert (missing.status_code, missing.json()["error"]["code"]) == (401, "code_required")
    assert "set-cookie" not in missing.headers
    assert api.user_state(customer_id, member.user_id)["failed_logins"] == 1  # counted
    for _ in range(api.settings.login_max_failures - 1):
        wrong = api.admin_login(client, member.email, code=wrong_code(api, member.secret))
        assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "bad_credentials")
    right = api.admin_login(client, member.email, code=api.code(member.secret))

    assert right.status_code == 401  # locked
    state = api.user_state(customer_id, member.user_id)
    assert state["locked_until"] is not None
    assert "auth.locked" in api.audit_actions(customer_id)
    reasons = {details.get("reason") for action, _, _, details in api.audit_rows(customer_id)}
    assert {"code_required", "wrong_code"} <= reasons


def test_a_members_code_works_once(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    member = api.enrol_user(customer_id, "member")
    code = api.code(member.secret)
    assert api.admin_login(api.client(), member.email, code=code).status_code == 200

    replayed = api.admin_login(api.client(), member.email, code=code)

    assert (replayed.status_code, replayed.json()["error"]["code"]) == (401, "bad_credentials")
    assert "set-cookie" not in replayed.headers
    assert api.user_state(customer_id, member.user_id)["failed_logins"] == 1


def test_a_reviewer_without_codes_signs_in_as_before(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    _, email = api.add_user(customer_id, "reviewer")
    reply = api.admin_login(api.client(), email)
    assert reply.status_code == 200, reply.text
    assert reply.json()["session"] == {"scope": "app"}
    assert reply.json()["user"]["totp_enabled"] is False


def test_change_password_needs_the_code_for_a_member_with_codes(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    member = api.enrol_user(customer_id, "member")
    client = api.client()
    assert api.admin_login(client, member.email, code=api.code(member.secret)).status_code == 200
    api.clock.advance(totp.STEP_SECONDS)
    body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}

    missing = _post(client, "/api/auth/password", body)
    assert (missing.status_code, missing.json()["error"]["message"]) == (400, CODE_REQUIRED)
    changed = _post(client, "/api/auth/password", {**body, "code": api.code(member.secret)})
    assert changed.status_code == 200, changed.text


def test_an_admin_resets_a_members_code(db: Database) -> None:
    api, mailer = _email_api(db)
    admin = api.new_admin()
    member = api.enrol_user(admin.customer_id, "member")
    member_client = api.client()
    assert api.admin_login(member_client, member.email, code=api.code(member.secret)).status_code == 200
    listed = {user["id"]: user for user in admin.client.get("/api/admin/users").json()["items"]}
    assert listed[member.user_id]["totp_enabled"] is True

    reset = _post(admin.client, f"/api/admin/users/{member.user_id}/reset-2fa")

    assert reset.status_code == 200, reset.text
    assert reset.json()["totp_enabled"] is False
    assert member_client.get("/api/auth/me").status_code == 401  # every session ends
    assert api.admin_login(api.client(), member.email).status_code == 200  # the password alone
    assert api.audit_actions(admin.customer_id).count("auth.totp_reset") == 1  # Changed on purpose (#457)
    assert _subjects(mailer, member.email) == ["Your Kaagaz two-step sign-in was reset"]


def test_an_admin_of_another_organisation_cannot_reset_a_members_code(db: Database) -> None:
    api = Api(db)
    a_customer, _ = api.customer("Sharma Traders")
    member = api.enrol_user(a_customer, "member")
    before = api.user_state(a_customer, member.user_id)
    a_audit = api.audit_rows(a_customer)
    b_admin = api.new_admin("Mehta Textiles")

    reply = _post(b_admin.client, f"/api/admin/users/{member.user_id}/reset-2fa")

    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert api.user_state(a_customer, member.user_id) == before
    assert api.audit_rows(a_customer) == a_audit


UNREADABLE = "Your two-step code can't be checked right now. Ask your admin to reset it."


def test_an_unreadable_two_step_secret_says_so_logs_an_error_and_counts_nothing(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """A secret that no longer opens (a changed key, a damaged row) is not the person's fault:
    the sign-in, after a right password, says so, and nothing counts toward the lock."""
    api = Api(db)
    customer_id, _ = api.customer()
    member = api.enrol_user(customer_id, "member")
    with db.customer(customer_id) as conn:
        conn.execute(
            text("UPDATE users SET totp_secret_enc = 'damaged' WHERE id = :u"), {"u": member.user_id}
        )
    caplog.set_level(logging.ERROR, logger="kaagaz.api.routers.auth")

    for _ in range(api.settings.login_max_failures + 1):
        reply = api.admin_login(api.client(), member.email, code=api.code(member.secret))
        assert (reply.status_code, reply.json()["error"]) == (
            403,
            {"code": "two_step_unreadable", "message": UNREADABLE},
        )
    assert api.user_state(customer_id, member.user_id)["failed_logins"] == 0
    assert '{"event":"totp_unreadable"}' in caplog.messages
    wrong = api.admin_login(api.client(), member.email, password=WRONG, code="123456")
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "bad_credentials")  # says nothing
