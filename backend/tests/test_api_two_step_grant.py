"""Two-step sign-in by grant (Tushar, 16 Oct 2026; 0029), through the API.

Until the operator allows it for an organisation, its admins sign in with the password
alone (a code is ignored, a stored one kept), turning it on is refused, and Delete forever
takes a typed password. Once allowed, a right password without the code gets 401
``code_required``. The operator's switch and other customers: test_isolation_two_step_grant.
"""

import base64

import pytest

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, PASSWORD, Api, Signed, wrong_code

pytestmark = pytest.mark.db

_AUDIT = (
    "SELECT action, details FROM audit_log WHERE customer_id = :c AND action LIKE 'org.two_step%' ORDER BY id"
)
_PLATFORM = (
    "SELECT action FROM platform_audit WHERE operator_id = :o AND action LIKE 'org.two_step%' ORDER BY id"
)


def _plain_admin(api: Api, customer_id: str) -> Signed:
    """An admin with two-step off, signed in on the admin screen with the password."""
    user_id, email = api.add_user(customer_id, "admin")
    client = api.client()
    assert api.admin_login(client, email).status_code == 200
    return Signed(client, customer_id, user_id, email, "admin")


def _post(signed: Signed, path: str, body: dict[str, str]) -> tuple[int, str | None]:
    reply = signed.client.post(path, json=body, headers=CSRF)
    return reply.status_code, reply.json()["error"]["code"] if reply.status_code >= 400 else None


def test_without_the_grant_a_stored_code_is_kept_and_never_asked_for(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer(two_step=False)
    admin = api.enrol_admin(customer_id)  # turned on while it was allowed
    api.grant_two_step(customer_id, False)
    before = api.user_state(customer_id, admin.user_id)

    for code in (None, wrong_code(api, admin.secret), "not a code"):
        reply = api.admin_login(api.client(), admin.email, code=code)
        assert reply.status_code == 200, reply.text  # any code is ignored
        assert reply.json()["user"]["two_step_allowed"] is False

    after = api.user_state(customer_id, admin.user_id)
    assert (after["totp_secret_enc"], after["totp_enabled_at"]) == (
        before["totp_secret_enc"],
        before["totp_enabled_at"],
    )
    assert api.audit_rows(customer_id)[-1] == (
        "auth.admin_login",
        "user",
        admin.user_id,
        {"second_factor": False},
    )
    assert api.admin_login(api.client(), admin.email, password="not the password!!").status_code == 401


def test_with_the_grant_a_right_password_without_the_code_gets_code_required_and_counts(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()  # granted
    admin = api.enrol_admin(customer_id)
    client = api.client()
    replies = {
        "no code": api.admin_login(client, admin.email),
        "wrong code": api.admin_login(client, admin.email, code=wrong_code(api, admin.secret)),
        "wrong password": api.admin_login(client, admin.email, password="not the password!!"),
    }
    assert {name: reply.json()["error"]["code"] for name, reply in replies.items()} == {
        "no code": "code_required",
        "wrong code": "bad_credentials",
        "wrong password": "bad_credentials",  # never code_required
    }
    assert api.user_state(customer_id, admin.user_id)["failed_logins"] == 3  # code_required counted too
    signed_in = api.admin_login(client, admin.email, code=api.code(admin.secret))
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["user"]["two_step_allowed"] is True


def test_without_the_grant_two_step_can_not_be_turned_on(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer(two_step=False)
    admin = _plain_admin(api, customer_id)
    start = "/api/auth/admin/2fa/start"
    assert _post(admin, start, {"password": PASSWORD}) == (403, "two_step_not_allowed")
    assert api.user_state(customer_id, admin.user_id)["failed_logins"] == 0  # nothing counted

    api.grant_two_step(customer_id)  # allowed, the key shown, then stopped before the first code
    offer = admin.client.post(start, json={"password": PASSWORD}, headers=CSRF).json()
    api.grant_two_step(customer_id, False)
    code = api.code(base64.b32decode(offer["secret"].replace(" ", "")))
    enrol = {"enrol_token": offer["enrol_token"], "code": code}
    assert _post(admin, "/api/auth/admin/enrol", enrol) == (403, "two_step_not_allowed")
    assert api.user_state(customer_id, admin.user_id)["totp_enabled_at"] is None


def test_without_the_grant_an_admin_deletes_forever_after_typing_their_password(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer(two_step=False)
    admin = _plain_admin(api, customer_id)
    document = api.add_document(customer_id, deleted_at=api.clock())
    path = f"/api/documents/{document}/permanent"

    assert admin.client.delete(path, headers=CSRF).json()["error"]["code"] == "password_confirm_required"
    assert _post(admin, "/api/auth/confirm-password", {"password": "nope"})[0] == 400
    assert _post(admin, "/api/auth/confirm-password", {"password": PASSWORD}) == (204, None)
    assert admin.client.delete(path, headers=CSRF).status_code == 204
    assert api.audit_rows(customer_id)[-1] == ("document.delete", "document", document, {"versions": 1})
    app_admin = api.sign_in(customer_id, "admin")  # an admin's app session never confirms
    assert _post(app_admin, "/api/auth/confirm-password", {"password": PASSWORD}) == (403, "forbidden")
