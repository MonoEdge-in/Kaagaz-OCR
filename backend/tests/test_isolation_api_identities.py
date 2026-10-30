"""Connected accounts (#457 phase 1) never cross organisations through the API: organisation
B's admin gets 404 on A's connection and user ids for approve, decline and remove, never
sees A's waiting connections, and A's connection is left exactly as it was. A member can
never remove someone else's connection either."""

import pytest

from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import oidc as oidc_router
from kaagaz.db.engine import Database
from tests.oidc_fake import FakeGoogle, use_google
from tests.test_api_admin_identities import _pending, _waiting
from tests.test_api_oidc_connect import _api, _mine
from tests.test_api_support import CSRF, PASSWORD

pytestmark = pytest.mark.db


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    monkeypatch.setattr(oidc_router, "callback_limits", RateLimiter(1000, window_seconds=3600))
    monkeypatch.setattr(oidc_router, "connect_limits", RateLimiter(1000, window_seconds=3600))
    monkeypatch.setattr(oidc_router, "remove_limits", RateLimiter(1000, window_seconds=3600))
    return use_google(monkeypatch)


def test_another_organisations_admin_cannot_see_approve_decline_or_remove(
    db: Database, google: FakeGoogle
) -> None:
    api = _api(db)
    admin_a = api.new_admin("Org A identities")
    admin_b = api.new_admin("Org B identities")
    member_a = _waiting(api, google, admin_a)
    [row] = _pending(admin_a)

    assert _pending(admin_b) == []
    for path in (
        f"/api/admin/identities/{row['id']}/approve",
        f"/api/admin/identities/{row['id']}/decline",
        f"/api/admin/users/{member_a.user_id}/remove-connections",
    ):
        reply = admin_b.client.post(path, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found"), path
    users_b = {item["id"] for item in admin_b.client.get("/api/admin/users").json()["items"]}
    assert member_a.user_id not in users_b

    member_b = api.sign_in(admin_b.customer_id, "member")
    reply = member_b.client.post(
        f"/api/auth/identities/{row['id']}/remove", json={"password": PASSWORD}, headers=CSRF
    )
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert member_b.client.get("/api/auth/identities").json()["identities"] == []

    assert _mine(api, member_a) == [("google", "ramesh.k@gmail.com", "pending")]
    assert [item["id"] for item in _pending(admin_a)] == [row["id"]]
