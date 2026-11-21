"""Delete forever, granted by an admin (council 4 Oct 2026, PROVISIONAL), through the API.

An admin is never a target, a role change clears the grant and stopping it works at the next
request. No fresh sign-in and no password step is asked for (#744, 22 Nov 2026): the admin
session or the grant is the whole rule, and the page's confirm dialog the check.
"""

from typing import Any

import httpx2 as httpx
import pytest

from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import ANY_ID, CSRF, Api, Signed

pytestmark = pytest.mark.db


def _grant(admin: Signed, user_id: str, allowed: bool = True) -> tuple[int, Any]:
    reply = admin.client.put(
        f"/api/admin/users/{user_id}/delete-forever", json={"allowed": allowed}, headers=CSRF
    )
    body = reply.json()
    return reply.status_code, body["error"]["code"] if "error" in body else body["can_delete_forever"]


def _forever(signed: Signed, document: str) -> tuple[int, str | None]:
    reply = signed.client.delete(f"/api/documents/{document}/permanent", headers=CSRF)
    return reply.status_code, None if reply.status_code == 204 else reply.json()["error"]["code"]


def _granted(api: Api, role: str = "member") -> tuple[Signed, Signed, str]:
    """An admin, a user of ``role`` they allowed, and a document in Trash."""
    admin = api.new_admin()
    user = api.sign_in(admin.customer_id, role)
    assert _grant(admin, user.user_id) == (200, True)
    return admin, user, api.add_document(admin.customer_id, deleted_at=api.clock())


def test_a_granted_user_deletes_forever_with_no_fresh_sign_in(db: Database) -> None:
    api = Api(db)
    admin, member, document = _granted(api, "reviewer")
    assert member.client.get("/api/auth/me").json()["user"]["can_delete_forever"] is True
    assert _forever(member, document) == (204, None)
    rows = api.audit_rows(admin.customer_id)
    assert ("user.delete_forever_on", "user", member.user_id, {}) in rows
    assert rows[-1] == ("document.delete", "document", document, {"versions": 1, "by_grant": True})


def test_an_admin_needs_no_sign_in_and_a_member_without_the_grant_is_refused(
    db: Database,
) -> None:
    api = Api(db)
    admin = api.new_admin()
    document = api.add_document(admin.customer_id, deleted_at=api.clock())
    assert _forever(admin, document) == (204, None)
    assert api.audit_rows(admin.customer_id)[-1][3] == {"versions": 1}  # an admin leaves no grant mark
    plain = api.sign_in(admin.customer_id)
    other = api.add_document(admin.customer_id, deleted_at=api.clock())
    assert _forever(plain, other) == (403, "forbidden")


def test_stopping_the_grant_works_at_the_next_request_and_a_role_change_clears_it(db: Database) -> None:
    api = Api(db)
    admin, member, document = _granted(api)
    assert _grant(admin, member.user_id, allowed=False) == (200, False)
    assert _forever(member, document) == (403, "forbidden")
    assert member.client.get("/api/auth/me").json()["user"]["can_delete_forever"] is False  # still signed in
    assert api.audit_actions(admin.customer_id)[-1] == "user.delete_forever_off"

    assert _grant(admin, member.user_id) == (200, True)
    changed = admin.client.patch(
        f"/api/admin/users/{member.user_id}", json={"role": "reviewer"}, headers=CSRF
    )
    assert changed.json()["can_delete_forever"] is False


def test_admins_are_never_targets(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    other_admin, _ = api.add_user(admin.customer_id, "admin")
    for target in (other_admin, admin.user_id):
        assert _grant(admin, target) == (400, "bad_request")
        assert _grant(admin, target, allowed=False) == (400, "bad_request")
    assert _grant(admin, ANY_ID) == (404, "not_found")
    member, _ = api.add_user(admin.customer_id, "member")
    assert _grant(admin, member, allowed=False) == (200, False)
    assert api.audit_actions(admin.customer_id)[-1] == "user.delete_forever_off"


def test_delete_forever_is_no_company_sign_in_action(db: Database) -> None:
    """#744: starting a fresh sign-in for it is refused (403 ``forbidden``), so nothing can set a proof
    for it; another action's start is not refused for that reason."""
    settings = make_settings(
        keycloak_enabled=True,
        keycloak_issuer="https://id.example.test/realms/kaagaz",
        keycloak_client_id="kaagaz-api",
        keycloak_client_secret="route-test-confidential-secret",
        keycloak_redirect_uri="https://app.example.test/api/auth/keycloak/callback",
        keycloak_required_acr="1",
        keycloak_flow_encryption_key="distinct-route-test-encryption-key-material",
    )
    api = Api(db, settings=settings)
    admin = api.new_admin()

    def start(action: str) -> httpx.Response:
        reply: httpx.Response = admin.client.post(
            f"/api/auth/keycloak/reauth/{action}/start", json={"next": "/documents"}, headers=CSRF
        )
        return reply

    refused = start("delete_forever")
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert start("locks").status_code != 403
