"""Delete forever, granted by an admin (council 4 Oct 2026, PROVISIONAL), through the API.

Allowing needs the admin's own two-step sign-in, an admin is never a target, a
role change clears the grant and stopping it works at the next request. A granted
user types their password first (/auth/confirm-password, counted like a sign-in)
for a 5-minute confirm cookie bound to them and to their session version.
"""

from typing import Any

import pytest

from kaagaz.api.auth import CONFIRM_COOKIE, SESSION_COOKIE
from kaagaz.db.engine import Database
from tests.test_api_support import ANY_ID, CSRF, PASSWORD, Api, Signed

pytestmark = pytest.mark.db


def _grant(admin: Signed, user_id: str, allowed: bool = True) -> tuple[int, Any]:
    reply = admin.client.put(
        f"/api/admin/users/{user_id}/delete-forever", json={"allowed": allowed}, headers=CSRF
    )
    body = reply.json()
    return reply.status_code, body["error"]["code"] if "error" in body else body["can_delete_forever"]


def _confirm(signed: Signed, password: str = PASSWORD) -> int:
    return signed.client.post(
        "/api/auth/confirm-password", json={"password": password}, headers=CSRF
    ).status_code


def _forever(signed: Signed, document: str) -> tuple[int, str | None]:
    reply = signed.client.delete(f"/api/documents/{document}/permanent", headers=CSRF)
    return reply.status_code, None if reply.status_code == 204 else reply.json()["error"]["code"]


def _granted(api: Api, role: str = "member") -> tuple[Signed, Signed, str]:
    """An admin (two-step on), a user of ``role`` they allowed, and a document in Trash."""
    admin = api.new_admin()
    user = api.sign_in(admin.customer_id, role)
    assert _grant(admin, user.user_id) == (200, True)
    return admin, user, api.add_document(admin.customer_id, deleted_at=api.clock())


def test_a_granted_user_deletes_forever_after_typing_their_password(db: Database) -> None:
    api = Api(db)
    admin, member, document = _granted(api, "reviewer")
    assert member.client.get("/api/auth/me").json()["user"]["can_delete_forever"] is True
    assert _forever(member, document) == (403, "password_confirm_required")

    confirmed = member.client.post("/api/auth/confirm-password", json={"password": PASSWORD}, headers=CSRF)

    assert confirmed.status_code == 204
    cookie = confirmed.headers["set-cookie"].lower()
    for part in (f"{CONFIRM_COOKIE}=", "httponly", "max-age=300", "path=/api/documents", "samesite=strict"):
        assert part in cookie
    assert _forever(member, document) == (204, None)
    rows = api.audit_rows(admin.customer_id)
    assert ("user.delete_forever_on", "user", member.user_id, {}) in rows
    assert rows[-1] == ("document.delete", "document", document, {"versions": 1, "by_grant": True})


def test_stopping_the_grant_works_at_the_next_request_and_a_role_change_clears_it(db: Database) -> None:
    api = Api(db)
    admin, member, document = _granted(api)
    assert _confirm(member) == 204
    assert _grant(admin, member.user_id, allowed=False) == (200, False)
    assert _forever(member, document) == (403, "forbidden")
    assert member.client.get("/api/auth/me").json()["user"]["can_delete_forever"] is False  # still signed in
    assert api.audit_actions(admin.customer_id)[-1] == "user.delete_forever_off"

    assert _grant(admin, member.user_id) == (200, True)
    changed = admin.client.patch(
        f"/api/admin/users/{member.user_id}", json={"role": "reviewer"}, headers=CSRF
    )
    assert changed.json()["can_delete_forever"] is False


def test_admins_are_never_targets_and_allowing_needs_two_step(db: Database) -> None:
    """The council's critical finding: a password-only admin must not route round
    two-step by allowing themselves or an account they made."""
    api = Api(db)
    admin = api.new_admin()
    other_admin, _ = api.add_user(admin.customer_id, "admin")
    for target in (other_admin, admin.user_id):
        assert _grant(admin, target) == (400, "bad_request")
        assert _grant(admin, target, allowed=False) == (400, "bad_request")
    assert _grant(admin, ANY_ID) == (404, "not_found")

    plain_id, email = api.add_user(admin.customer_id, "admin")
    # A password-only admin has no session at all since 0035 (MFA required), so has nothing
    # to allow with; the gate itself: test_auth_tokens and test_api_admin_sign_in.
    refused = api.admin_login(api.client(), email)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "mfa_enrol_required")
    member, _ = api.add_user(admin.customer_id, "member")
    assert _grant(admin, plain_id) == (400, "bad_request")
    assert _grant(admin, member, allowed=False) == (200, False)
    assert api.audit_actions(admin.customer_id)[-1] == "user.delete_forever_off"


def test_confirm_password_is_only_for_grant_holders_and_counts_wrong_passwords(db: Database) -> None:
    api = Api(db)
    admin, member, _document = _granted(api)
    for signed in (api.sign_in(admin.customer_id), admin):
        assert _confirm(signed) == 403
        assert api.user_state(admin.customer_id, signed.user_id)["failed_logins"] == 0
    assert _confirm(member, "not the password") == 400
    assert api.user_state(admin.customer_id, member.user_id)["failed_logins"] == 1
    assert _confirm(member) == 204
    assert api.user_state(admin.customer_id, member.user_id)["failed_logins"] == 0


def test_only_a_fresh_confirm_token_of_this_user_and_session_counts(db: Database) -> None:
    api = Api(db)
    admin, member, document = _granted(api)
    colleague = api.sign_in(admin.customer_id)
    assert _grant(admin, colleague.user_id) == (200, True)
    colleague.client.cookies.set(CONFIRM_COOKIE, colleague.client.cookies[SESSION_COOKIE])  # wrong purpose
    assert _forever(colleague, document) == (403, "password_confirm_required")
    assert _confirm(member) == 204
    colleague.client.cookies.delete(CONFIRM_COOKIE)
    colleague.client.cookies.set(CONFIRM_COOKIE, member.client.cookies[CONFIRM_COOKIE])  # another user's
    assert _forever(colleague, document) == (403, "password_confirm_required")

    api.clock.advance(300)  # 5 minutes later
    assert _forever(member, document) == (403, "password_confirm_required")
    assert _confirm(member) == 204
    passwords = {"current_password": PASSWORD, "new_password": "a brand new password"}
    changed = member.client.post("/api/auth/password", json=passwords, headers=CSRF)
    assert changed.status_code == 200  # this browser stays in, on a new session version
    assert _forever(member, document) == (403, "password_confirm_required")
