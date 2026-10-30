"""Admin, People and Settings for connected accounts (#457 phase 1): approve or decline a
member's Google account, remove a person's connections, the auto-approval switch and who
signs in how. Against a made-up Google."""

import uuid
from typing import Any

import pytest

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import oidc as oidc_router
from kaagaz.db import identities
from kaagaz.db.engine import Database
from tests.oidc_fake import ISSUER, FakeGoogle, use_google
from tests.settings_factory import make_settings
from tests.test_api_email import BrokenMailer
from tests.test_api_oidc_connect import _api, _connect, _mine
from tests.test_api_oidc_signin import _sign_in_at_google
from tests.test_api_support import ANY_ID, CSRF, PASSWORD, Api, Signed, call

pytestmark = pytest.mark.db


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    monkeypatch.setattr(oidc_router, "callback_limits", RateLimiter(1000, window_seconds=3600))
    monkeypatch.setattr(oidc_router, "connect_limits", RateLimiter(1000, window_seconds=3600))
    monkeypatch.setattr(oidc_router, "remove_limits", RateLimiter(1000, window_seconds=3600))
    return use_google(monkeypatch)


def _waiting(api: Api, google: FakeGoogle, admin: Signed, email: str = "ramesh.k@gmail.com") -> Signed:
    """A member of the admin's organisation whose Google account waits for approval."""
    member = api.sign_in(admin.customer_id, "member")
    _connect(api, google, member, email)
    return member


def _pending(admin: Signed) -> list[dict[str, Any]]:
    reply = admin.client.get("/api/admin/identities", params={"status": "pending"})
    assert reply.status_code == 200, reply.text
    items: list[dict[str, Any]] = reply.json()["items"]
    return items


def _google_sign_in_works(api: Api, google: FakeGoogle, subject: str) -> bool:
    reply = _sign_in_at_google(api, google, api.client(), subject)
    return reply.headers["location"] == "/documents"


def test_pending_list_then_approve_then_member_can_sign_in(db: Database, google: FakeGoogle) -> None:
    mailer = MemoryMailer()
    api = _api(db, mailer)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    subject = f"made-up-sub-{member.user_id}"
    with db.customer(admin.customer_id) as conn:  # its key, to sign in with below
        identities.add(
            conn,
            admin.customer_id,
            member.user_id,
            provider="google",
            issuer=ISSUER,
            subject=subject,
            email="ramesh.k@gmail.com",
            approved=False,
            approved_by=None,
            now=api.clock(),
        )
    [row] = _pending(admin)
    assert {key: row[key] for key in ("user_id", "display_name", "user_email", "provider", "email")} == {
        "user_id": member.user_id,
        "display_name": "Test member",
        "user_email": member.email,
        "provider": "google",
        "email": "ramesh.k@gmail.com",
    }
    assert set(row) == {"id", "user_id", "display_name", "user_email", "provider", "email", "linked_at"}
    assert not _google_sign_in_works(api, google, subject)

    approved = admin.client.post(f"/api/admin/identities/{row['id']}/approve", headers=CSRF)
    assert approved.status_code == 200, approved.text
    assert approved.json() == {"items": []}
    assert _mine(api, member) == [("google", "ramesh.k@gmail.com", "approved")]
    assert _google_sign_in_works(api, google, subject)
    audit = [r for r in api.audit_rows(admin.customer_id) if r[0] == "auth.identity_approved"]
    assert audit == [
        ("auth.identity_approved", "user", member.user_id, {"provider": "google", "identity_id": row["id"]})
    ]
    assert [m.subject for m in mailer.sent if m.to == member.email][-1] == "Your Google account was approved"
    again = admin.client.post(f"/api/admin/identities/{row['id']}/approve", headers=CSRF)
    assert again.status_code == 404  # only a waiting one


def test_decline_removes_it(db: Database, google: FakeGoogle) -> None:
    mailer = MemoryMailer()
    api = _api(db, mailer)
    admin = api.new_admin()
    member = _waiting(api, google, admin)
    [row] = _pending(admin)
    declined = admin.client.post(f"/api/admin/identities/{row['id']}/decline", headers=CSRF)
    assert declined.status_code == 200, declined.text
    assert declined.json() == {"items": []}
    assert _mine(api, member) == []
    assert (
        "auth.identity_declined",
        "user",
        member.user_id,
        {"provider": "google", "identity_id": row["id"]},
    ) in (api.audit_rows(admin.customer_id))
    assert [m.subject for m in mailer.sent if m.to == member.email][-1] == "Your Google account was declined"


def test_remove_connections_signs_member_out_and_keeps_password(db: Database, google: FakeGoogle) -> None:
    mailer = MemoryMailer()
    api = _api(db, mailer)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    _connect(api, google, member, member.email)  # approved
    reply = admin.client.post(f"/api/admin/users/{member.user_id}/remove-connections", headers=CSRF)
    assert reply.status_code == 200, reply.text
    assert reply.json()["signs_in_with"] == ["password"]
    assert _mine(api, member) == []
    assert member.client.get("/api/auth/me").status_code == 401  # signed out
    assert api.admin_login(api.client(), member.email).status_code == 200  # the password still works
    assert ("auth.identity_unlinked", "user", member.user_id, {"count": 1}) in api.audit_rows(
        admin.customer_id
    )
    assert [m.subject for m in mailer.sent if m.to == member.email][-1] == "A Google account was disconnected"


def test_reset_password_also_clears_connections(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    _connect(api, google, member, member.email)
    assert len(_mine(api, member)) == 1
    reply = admin.client.post(f"/api/admin/users/{member.user_id}/reset-password", headers=CSRF)
    assert reply.status_code == 200, reply.text
    assert _mine(api, member) == []


def test_auto_approve_toggle_is_audited(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    admin = api.new_admin()
    assert admin.client.get("/api/admin/settings").json()["auto_approve_connections"] is False
    on = admin.client.patch("/api/admin/settings", json={"auto_approve_connections": True}, headers=CSRF)
    assert on.status_code == 200, on.text
    assert on.json()["auto_approve_connections"] is True
    unchanged = admin.client.patch(
        "/api/admin/settings", json={"auto_approve_connections": True}, headers=CSRF
    )
    assert unchanged.status_code == 200
    off = admin.client.patch("/api/admin/settings", json={"auto_approve_connections": False}, headers=CSRF)
    assert off.json()["auto_approve_connections"] is False
    actions = [r[0] for r in api.audit_rows(admin.customer_id) if r[0].startswith("settings.auto_approve")]
    assert actions == ["settings.auto_approve_on", "settings.auto_approve_off"]


def test_users_list_shows_signs_in_with(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    admin = api.new_admin()
    connected = api.sign_in(admin.customer_id, "member")
    _connect(api, google, connected, connected.email)  # approved
    waiting = _waiting(api, google, admin)
    items = {item["id"]: item for item in admin.client.get("/api/admin/users").json()["items"]}
    assert items[connected.user_id]["signs_in_with"] == ["password", "google"]
    assert items[waiting.user_id]["signs_in_with"] == ["password"]  # not until approved
    assert items[admin.user_id]["signs_in_with"] == ["password"]


def test_member_cannot_use_admin_identity_routes(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    member = api.new_signed("member")
    for method, path in [
        ("GET", "/api/admin/identities"),
        ("POST", f"/api/admin/identities/{ANY_ID}/approve"),
        ("POST", f"/api/admin/identities/{ANY_ID}/decline"),
        ("POST", f"/api/admin/users/{ANY_ID}/remove-connections"),
    ]:
        reply = call(member.client, method, path)
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "admin_session_required"), path


def test_admin_identity_routes_are_404_while_off(db: Database) -> None:
    api = Api(db, settings=make_settings())
    admin = api.new_admin()
    for method, path in [
        ("GET", "/api/admin/identities"),
        ("POST", f"/api/admin/identities/{ANY_ID}/approve"),
        ("POST", f"/api/admin/identities/{ANY_ID}/decline"),
        ("POST", f"/api/admin/users/{admin.user_id}/remove-connections"),
    ]:
        reply = call(admin.client, method, path)
        assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found"), path
    assert admin.client.get("/api/admin/users").json()["items"][0]["signs_in_with"] == ["password"]


def test_unknown_ids_are_404(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    admin = api.new_admin()
    for path in (
        f"/api/admin/identities/{ANY_ID}/approve",
        f"/api/admin/identities/{ANY_ID}/decline",
        f"/api/admin/users/{ANY_ID}/remove-connections",
    ):
        assert admin.client.post(path, headers=CSRF).status_code == 404, path


def test_a_password_still_signs_in_after_every_disconnect(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    _connect(api, google, member, member.email)
    identity_id = member.client.get("/api/auth/identities").json()["identities"][0]["id"]
    member.client.post(
        f"/api/auth/identities/{identity_id}/remove", json={"password": PASSWORD}, headers=CSRF
    )
    assert api.admin_login(api.client(), member.email).status_code == 200


def test_approve_signs_the_member_out_elsewhere(db: Database, google: FakeGoogle) -> None:
    """The spec: approving, like connecting and disconnecting, raises the person's session version."""
    api = _api(db)
    admin = api.new_admin()
    member = _waiting(api, google, admin)
    [row] = _pending(admin)
    assert member.client.get("/api/auth/me").status_code == 200
    assert admin.client.post(f"/api/admin/identities/{row['id']}/approve", headers=CSRF).status_code == 200
    assert member.client.get("/api/auth/me").status_code == 401
    assert api.admin_login(api.client(), member.email).status_code == 200  # the password still works


def test_approve_refuses_when_the_email_became_another_users_sign_in_email(
    db: Database, google: FakeGoogle
) -> None:
    """The one outside account rule holds at approval too: someone was given that email since."""
    api = _api(db)
    admin = api.new_admin()
    email = f"sita-{uuid.uuid4().hex[:8]}@example.com"  # made up: e-mails are unique across runs
    member = _waiting(api, google, admin, email)
    [row] = _pending(admin)
    created = admin.client.post(
        "/api/admin/users",
        json={
            "email": email.upper(),
            "display_name": "Sita",
            "role": "member",
            "password": PASSWORD,
        },
        headers=CSRF,
    )
    assert created.status_code == 201, created.text
    reply = admin.client.post(f"/api/admin/identities/{row['id']}/approve", headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["message"]) == (
        409,
        "This Google account already belongs to a Kaagaz user.",
    )
    assert _mine(api, member) == [("google", email, "pending")]


def test_a_notice_that_breaks_never_breaks_approve_decline_or_remove(
    db: Database, google: FakeGoogle
) -> None:
    api = _api(db, BrokenMailer())
    admin = api.new_admin()
    first = _waiting(api, google, admin)
    _waiting(api, google, admin, "sita@example.com")
    approve, decline = _pending(admin)
    for path in (
        f"/api/admin/identities/{approve['id']}/approve",
        f"/api/admin/identities/{decline['id']}/decline",
        f"/api/admin/users/{first.user_id}/remove-connections",
    ):
        reply = admin.client.post(path, headers=CSRF)
        assert reply.status_code == 200, (path, reply.text)
