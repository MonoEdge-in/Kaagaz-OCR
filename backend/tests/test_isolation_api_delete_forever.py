"""Delete forever, granted (council 4 Oct 2026), never crosses customers: B's admin
can't allow or stop it for A's users (404, no audit row), B's granted member gets
404 on A's document, with no sign-in step to hide behind (#744, 22 Nov 2026).
The control: A's own granted member then deletes it."""

import pytest

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api

pytestmark = pytest.mark.db


def test_a_grant_never_crosses_customers(db: Database) -> None:
    api = Api(db)
    a = api.admin_sign_in(api.customer("Sharma Traders")[0])
    b = api.admin_sign_in(api.customer("Mehta Textiles")[0])
    a_member = api.sign_in(a.customer_id)
    b_member = api.sign_in(b.customer_id)
    binned = api.add_document(a.customer_id, deleted_at=api.clock())

    for allowed in (True, False):
        reply = b.client.put(
            f"/api/admin/users/{a_member.user_id}/delete-forever", json={"allowed": allowed}, headers=CSRF
        )
        assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert a_member.client.get("/api/auth/me").json()["user"]["can_delete_forever"] is False
    for customer_id in (a.customer_id, b.customer_id):
        assert not [action for action in api.audit_actions(customer_id) if "delete_forever" in action]

    for admin, member in ((a, a_member), (b, b_member)):
        grant = f"/api/admin/users/{member.user_id}/delete-forever"
        assert admin.client.put(grant, json={"allowed": True}, headers=CSRF).status_code == 200
    # Changed on purpose (#744, 22 Nov 2026): B's granted member needs no proof, so the 404 is the
    # customer boundary alone, and A's document stays.
    assert b_member.client.delete(f"/api/documents/{binned}/permanent", headers=CSRF).status_code == 404
    assert api.storage.has(a.customer_id, binned)

    assert a_member.client.delete(f"/api/documents/{binned}/permanent", headers=CSRF).status_code == 204
