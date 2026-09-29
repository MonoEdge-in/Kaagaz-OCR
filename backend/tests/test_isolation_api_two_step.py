"""Customer B's admin turning their own 2FA off and on never touches customer A:
A's admins (2FA on and off) stay exactly as they were, A's session still works
and A's audit log gets no row, though B uses the same password as A's admins.
"""

import base64

import pytest

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, PASSWORD, Api

pytestmark = pytest.mark.db


def test_b_turning_2fa_off_and_on_never_touches_a(db: Database) -> None:
    api = Api(db)
    a_customer, _ = api.customer("Sharma Traders")
    b_customer, _ = api.customer("Mehta Textiles")
    a = api.admin_sign_in(a_customer)
    a_plain_id, _ = api.add_user(a_customer, "admin")
    b = api.admin_sign_in(b_customer)
    a_users = (a.user_id, a_plain_id)
    before = {user: api.user_state(a_customer, user) for user in a_users}
    a_audit = api.audit_rows(a_customer)

    off = b.client.post("/api/auth/admin/2fa/off", json={"password": PASSWORD}, headers=CSRF)
    assert off.status_code == 200, off.text
    start = b.client.post("/api/auth/admin/2fa/start", headers=CSRF)
    assert start.status_code == 200, start.text
    secret = base64.b32decode(start.json()["secret"].replace(" ", ""))
    on = b.client.post(
        "/api/auth/admin/enrol",
        json={"enrol_token": start.json()["enrol_token"], "code": api.code(secret)},
        headers=CSRF,
    )
    assert on.status_code == 200, on.text

    for reply in (off, on):
        assert reply.json()["user"]["id"] == b.user_id
        assert reply.json()["customer"]["id"] == b_customer
    assert {user: api.user_state(a_customer, user) for user in a_users} == before
    assert api.audit_rows(a_customer) == a_audit
    assert a.client.get("/api/admin/users").status_code == 200  # A's admin session is untouched
