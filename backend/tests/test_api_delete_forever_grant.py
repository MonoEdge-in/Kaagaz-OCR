"""Delete forever, granted by an admin (council 4 Oct 2026, PROVISIONAL), through the API.

An admin is never a target, a role change clears the grant and stopping it works at the next
request. A granted user first does a fresh company sign-in for ``delete_forever`` (Keycloak, 21 Nov
2026; ``Api.prove`` leaves what its callback leaves): a 5-minute confirm cookie bound to them, their
session version and this browser's session. There is no password step.
"""

from typing import Any

import pytest

from kaagaz.api.auth import CONFIRM_COOKIE, CONFIRM_PATH, SESSION_COOKIE
from kaagaz.db.engine import Database
from tests.test_api_support import ANY_ID, CSRF, Api, Signed

pytestmark = pytest.mark.db
PATH = CONFIRM_PATH


def _grant(admin: Signed, user_id: str, allowed: bool = True) -> tuple[int, Any]:
    reply = admin.client.put(
        f"/api/admin/users/{user_id}/delete-forever", json={"allowed": allowed}, headers=CSRF
    )
    body = reply.json()
    return reply.status_code, body["error"]["code"] if "error" in body else body["can_delete_forever"]


def _confirm(signed: Signed) -> int:
    """The step before deleting forever: a fresh company sign-in for ``delete_forever`` (``Api.prove``).
    204 as the password step it replaced answered, so a caller reads as before."""
    Api.of(signed).prove(signed, "delete_forever")
    return 204


def _forever(signed: Signed, document: str) -> tuple[int, str | None]:
    reply = signed.client.delete(f"/api/documents/{document}/permanent", headers=CSRF)
    return reply.status_code, None if reply.status_code == 204 else reply.json()["error"]["code"]


def _granted(api: Api, role: str = "member") -> tuple[Signed, Signed, str]:
    """An admin, a user of ``role`` they allowed, and a document in Trash."""
    admin = api.new_admin()
    user = api.sign_in(admin.customer_id, role)
    assert _grant(admin, user.user_id) == (200, True)
    return admin, user, api.add_document(admin.customer_id, deleted_at=api.clock())


def test_a_granted_user_deletes_forever_after_a_company_sign_in(db: Database) -> None:
    api = Api(db)
    admin, member, document = _granted(api, "reviewer")
    assert member.client.get("/api/auth/me").json()["user"]["can_delete_forever"] is True
    assert _forever(member, document) == (403, "keycloak_reauth_required")

    api.prove(member, "delete_forever")

    assert _forever(member, document) == (204, None)
    rows = api.audit_rows(admin.customer_id)
    assert ("user.delete_forever_on", "user", member.user_id, {}) in rows
    assert rows[-1] == ("document.delete", "document", document, {"versions": 1, "by_grant": True})


def test_an_admin_needs_the_company_sign_in_too_and_a_member_without_the_grant_is_refused(
    db: Database,
) -> None:
    api = Api(db)
    admin = api.new_admin()
    document = api.add_document(admin.customer_id, deleted_at=api.clock())
    assert _forever(admin, document) == (403, "keycloak_reauth_required")
    api.prove(admin, "delete_forever")
    assert _forever(admin, document) == (204, None)
    assert api.audit_rows(admin.customer_id)[-1][3] == {"versions": 1}  # an admin leaves no grant mark
    plain = api.sign_in(admin.customer_id)
    other = api.add_document(admin.customer_id, deleted_at=api.clock())
    api.prove(plain, "delete_forever")  # the sign-in alone is no grant
    assert _forever(plain, other) == (403, "forbidden")


def test_stopping_the_grant_works_at_the_next_request_and_a_role_change_clears_it(db: Database) -> None:
    api = Api(db)
    admin, member, document = _granted(api)
    api.prove(member, "delete_forever")
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


def test_only_a_fresh_confirm_token_of_this_user_and_session_counts(db: Database) -> None:
    api = Api(db)
    admin, member, document = _granted(api)
    colleague = api.sign_in(admin.customer_id)
    assert _grant(admin, colleague.user_id) == (200, True)
    session = colleague.client.cookies[SESSION_COOKIE]
    colleague.client.cookies.set(CONFIRM_COOKIE, session, path=PATH)  # wrong purpose
    assert _forever(colleague, document) == (403, "keycloak_reauth_required")
    api.prove(member, "delete_forever")
    colleague.client.cookies.delete(CONFIRM_COOKIE, path=PATH)
    theirs = member.client.cookies.get(CONFIRM_COOKIE, path=PATH)
    assert theirs
    colleague.client.cookies.set(CONFIRM_COOKIE, theirs, path=PATH)  # another user's
    assert _forever(colleague, document) == (403, "keycloak_reauth_required")

    api.clock.advance(300)  # 5 minutes later
    assert _forever(member, document) == (403, "keycloak_reauth_required")
    api.prove(member, "delete_forever")
    api.mint_session(member.client, member.customer_id, member.user_id)  # signed in again: a new session
    assert _forever(member, document) == (403, "keycloak_reauth_required")
