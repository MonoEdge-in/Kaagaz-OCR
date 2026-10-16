"""Customer B's admin setting up two-step sign-in at sign-in (and being refused turning it
off, 0031) never touches customer A: A's admins (2FA on and not yet set up) stay exactly as
they were, A's session still works and A's audit log gets no row, though B uses the same
password as A's admins.
"""

import base64

import pytest

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, PASSWORD, Api

pytestmark = pytest.mark.db


def test_b_setting_up_two_step_never_touches_a(db: Database) -> None:
    api = Api(db)
    a_customer, _ = api.customer("Sharma Traders")
    b_customer, _ = api.customer("Mehta Textiles")
    a = api.admin_sign_in(a_customer)
    a_plain_id, _ = api.add_user(a_customer, "admin")
    b_id, b_email = api.add_user(b_customer, "admin")
    a_users = (a.user_id, a_plain_id)
    before = {user: api.user_state(a_customer, user) for user in a_users}
    a_audit = api.audit_rows(a_customer)

    b = api.client()
    offer = api.admin_login(b, b_email)
    assert offer.status_code == 403, offer.text
    details = offer.json()["error"]["details"]
    secret = base64.b32decode(details["secret"].replace(" ", ""))
    on = b.post(
        "/api/auth/admin/enrol",
        json={"enrol_token": details["enrol_token"], "code": api.code(secret)},
        headers=CSRF,
    )
    assert on.status_code == 200, on.text
    off = b.post("/api/auth/admin/2fa/off", json={"password": PASSWORD}, headers=CSRF)
    assert off.status_code == 403, off.text

    assert on.json()["user"]["id"] == b_id
    assert on.json()["customer"]["id"] == b_customer
    assert {user: api.user_state(a_customer, user) for user in a_users} == before
    assert api.audit_rows(a_customer) == a_audit
    assert a.client.get("/api/admin/users").status_code == 200  # A's admin session is untouched
