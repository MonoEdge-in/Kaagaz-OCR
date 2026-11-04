"""Connecting a Google account from Account, Ways to sign in (#457 phase 1), against a made-up
Google: the proof first (password, and the code for anyone with codes on), the one outside
account per Kaagaz user rule, admin approval or approval at once, link-CSRF both ways, the
session version, the audit row, the per-user limit and the emails."""

import urllib.parse
import uuid
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api.auth import OIDC_COOKIE, SESSION_COOKIE
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import oidc as oidc_router
from kaagaz.db import identities
from kaagaz.db.engine import Database
from tests.oidc_fake import ISSUER, FakeGoogle, google_settings, use_google
from tests.settings_factory import make_settings
from tests.test_api_email import BrokenMailer, FailingMailer
from tests.test_api_support import ANY_ID, CSRF, PASSWORD, Api, Signed, TestClient, wrong_code

pytestmark = pytest.mark.db
SITE = "https://kaagaz.example.com"
WAYS = "/settings/sign-in"


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    monkeypatch.setattr(oidc_router, "callback_limits", RateLimiter(1000, window_seconds=3600))
    monkeypatch.setattr(oidc_router, "connect_limits", RateLimiter(1000, window_seconds=3600))
    monkeypatch.setattr(oidc_router, "remove_limits", RateLimiter(1000, window_seconds=3600))
    return use_google(monkeypatch)


def _api(db: Database, mailer: MemoryMailer | None = None) -> Api:
    if mailer is None:
        return Api(db, settings=google_settings())
    settings = google_settings(email_enabled=True, email_from="no-reply@example.com", app_url=SITE)
    return Api(db, settings=settings, mailer=mailer)


def _start(client: TestClient, password: str = PASSWORD, code: str | None = None) -> httpx.Response:
    body: dict[str, str] = {"password": password}
    if code is not None:
        body["code"] = code
    return client.post("/api/auth/oidc/google/connect", json=body, headers=CSRF)


def _connect(
    api: Api,
    google: FakeGoogle,
    signed: Signed,
    email: str = "ramesh.k@gmail.com",
    *,
    subject: str | None = None,
    code: str | None = None,
    **claims: Any,
) -> httpx.Response:
    """Connect from Ways to sign in, then choose this Google account: the callback's reply."""
    started = _start(signed.client, code=code)
    assert started.status_code == 200, started.text
    query = google.sign_in(
        started.json()["url"], subject or _subject(), email, int(api.clock().timestamp()), **claims
    )
    return signed.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)


def _subject() -> str:
    return f"made-up-sub-{uuid.uuid4().hex}"


def _landed(reply: httpx.Response) -> dict[str, str]:
    """The Ways to sign in page's query the callback sent the browser to."""
    assert reply.status_code == 303, reply.text
    location = urllib.parse.urlsplit(reply.headers["location"])
    assert location.path == WAYS, reply.headers["location"]
    return dict(urllib.parse.parse_qsl(location.query))


def _mine(api: Api, signed: Signed) -> list[tuple[str, str | None, str]]:
    with api.db.customer(signed.customer_id) as conn:
        rows = identities.for_user(conn, signed.customer_id, signed.user_id)
    return [(row["provider"], row["email_at_link"], row["status"]) for row in rows]


def _admin_code(api: Api, admin: Signed) -> str:
    """A fresh code: the admin's sign-in just used this step's."""
    assert admin.totp_secret is not None
    api.clock.advance(30)
    return api.code(admin.totp_secret)


# --- proof first ------------------------------------------------------------------------------


def test_connect_needs_password(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    member = api.new_signed("member")
    reply = _start(member.client, password="not the password!!")
    assert (reply.status_code, reply.json()["error"]["message"]) == (
        400,
        "Your current password is not right.",
    )
    assert api.user_state(member.customer_id, member.user_id)["failed_logins"] == 1
    assert not member.client.cookies.get(OIDC_COOKIE)
    assert ("auth.login_failed", "user", member.user_id, {"reason": "wrong_password"}) in api.audit_rows(
        member.customer_id
    )


def test_admin_connect_needs_password_and_code(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    admin = api.new_admin()
    assert admin.totp_secret is not None
    no_code = _start(admin.client)
    assert (no_code.status_code, no_code.json()["error"]["message"]) == (
        400,
        "Enter the 6-digit code from your authenticator app.",
    )
    wrong = _start(admin.client, code=wrong_code(api, admin.totp_secret))
    assert wrong.status_code == 400
    right = _start(admin.client, code=_admin_code(api, admin))
    assert right.status_code == 200, right.text
    assert right.json()["url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert admin.client.cookies.get(OIDC_COOKIE)


def test_member_with_codes_on_needs_the_code_too(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member = api.enrol_user(customer_id, "member")
    client = api.client()
    signed_in = api.admin_login(client, member.email, code=api.code(member.secret))
    assert signed_in.status_code == 200, signed_in.text
    assert _start(client).status_code == 400
    api.clock.advance(30)
    assert _start(client, code=api.code(member.secret)).status_code == 200


# --- approval ------------------------------------------------------------------------------------


def test_member_same_email_google_is_approved_at_once(db: Database, google: FakeGoogle) -> None:
    """Review focus 5: Google's Ramesh@Gmail.com is the Kaagaz ramesh@gmail.com."""
    mailer = MemoryMailer()
    api = _api(db, mailer)
    member = api.new_signed("member")
    reply = _connect(api, google, member, member.email.upper())
    assert _landed(reply) == {"connected": "google"}
    assert _mine(api, member) == [("google", member.email, "approved")]
    assert [sent.subject for sent in mailer.sent if sent.to == member.email] == [
        "A Google account was connected"
    ]


@pytest.mark.parametrize("changes", [{}, {"email_verified": False}])
def test_member_different_email_waits_for_approval(
    db: Database, google: FakeGoogle, changes: dict[str, Any]
) -> None:
    mailer = MemoryMailer()
    api = _api(db, mailer)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    email = "ramesh.k@gmail.com" if not changes else member.email  # unverified: even the same email waits
    reply = _connect(api, google, member, email, **changes)
    assert _landed(reply) == {"waiting": "google"}
    assert _mine(api, member) == [("google", email, "pending")]
    to_admin = [(sent.subject, sent.text) for sent in mailer.sent if sent.to == admin.email]
    assert len(to_admin) == 1 and to_admin[0][0] == "Waiting for your approval"
    assert f"{SITE}/admin" in to_admin[0][1] and email not in to_admin[0][1]
    assert [sent.subject for sent in mailer.sent if sent.to == member.email] == [
        "A Google account is waiting for approval"
    ]


def test_auto_approve_on_approves_at_once(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    with db.customer(admin.customer_id) as conn:
        conn.execute(
            text("UPDATE customers SET auto_approve_connections = true WHERE id = :c"),
            {"c": admin.customer_id},
        )
    assert _landed(_connect(api, google, member)) == {"connected": "google"}
    assert _mine(api, member) == [("google", "ramesh.k@gmail.com", "approved")]


def test_admin_own_connection_is_approved(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    admin = api.new_admin()
    reply = _connect(api, google, admin, code=_admin_code(api, admin))
    assert _landed(reply) == {"connected": "google"}
    assert _mine(api, admin) == [("google", "ramesh.k@gmail.com", "approved")]


def test_a_new_admin_cant_skip_the_reapproval_by_connecting_again(db: Database, google: FakeGoogle) -> None:
    """#507 review: promotion puts the connection back to waiting. Cancelling it and connecting
    again is not approved at once (an admin's own, the same email, auto-approval) for 30 days
    after the ``auth.identity_reapproval``: another admin approves it."""
    api = _api(db)
    first = api.new_admin()
    customer = first.customer_id
    member = api.enrol_user(customer, "member")
    with db.customer(customer) as conn:
        identities.add(
            conn,
            customer,
            member.user_id,
            provider="google",
            issuer=ISSUER,
            subject=_subject(),
            email=member.email,
            approved=True,
            approved_by=None,
            now=api.clock(),
        )
        conn.execute(
            text("UPDATE customers SET auto_approve_connections = true WHERE id = :c"), {"c": customer}
        )
    promoted = first.client.patch(f"/api/admin/users/{member.user_id}", json={"role": "admin"}, headers=CSRF)
    assert promoted.status_code == 200, promoted.text
    client = api.client()
    assert api.admin_login(client, member.email, code=api.code(member.secret)).status_code == 200
    signed = Signed(client, customer, member.user_id, member.email, "admin", member.secret)
    [waiting] = client.get("/api/auth/identities").json()["identities"]
    cancelled = client.post(f"/api/auth/identities/{waiting['id']}/remove", json={}, headers=CSRF)
    assert cancelled.status_code == 200, cancelled.text

    reply = _connect(api, google, signed, member.email, code=_admin_code(api, signed))
    assert _landed(reply) == {"waiting": "google"}
    assert _mine(api, signed) == [("google", member.email, "pending")]
    [again] = first.client.get("/api/admin/identities").json()["items"]
    assert first.client.post(f"/api/admin/identities/{again['id']}/approve", headers=CSRF).status_code == 200

    with db.engine.begin() as conn:  # 31 days pass (the owner: the app can't change the log)
        conn.execute(
            text(
                "UPDATE audit_log SET created_at = created_at - interval '31 days'"
                " WHERE customer_id = :c AND action = 'auth.identity_reapproval'"
            ),
            {"c": customer},
        )
    [mine] = client.get("/api/auth/identities").json()["identities"]
    body = {"password": PASSWORD}
    assert (
        client.post(f"/api/auth/identities/{mine['id']}/remove", json=body, headers=CSRF).status_code == 200
    )
    reply = _connect(api, google, signed, member.email, code=_admin_code(api, signed))
    assert _landed(reply) == {"connected": "google"}


# --- one outside account, one Kaagaz user -------------------------------------------------------


def test_already_connected_elsewhere_says_taken(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    first = api.new_signed("member")
    subject = _subject()
    assert _landed(_connect(api, google, first, subject=subject)) == {"waiting": "google"}
    other = api.new_signed("member")  # another organisation
    reply = _connect(api, google, other, subject=subject)
    assert _landed(reply) == {"error": "taken"}
    assert _mine(api, other) == []


def test_google_email_is_another_users_sign_in_email_says_taken(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    elsewhere = api.new_signed("member")  # another organisation's user
    member = api.new_signed("member")
    reply = _connect(api, google, member, elsewhere.email.upper())
    assert _landed(reply) == {"error": "taken"}  # names no one
    assert elsewhere.email not in reply.headers["location"]
    assert _mine(api, member) == []


def test_second_google_for_same_user_says_has_provider(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    member = api.new_signed("member")
    assert _landed(_connect(api, google, member)) == {"waiting": "google"}
    assert _landed(_connect(api, google, member, "another@example.com")) == {"error": "has_provider"}
    assert _mine(api, member) == [("google", "ramesh.k@gmail.com", "pending")]


# --- link-CSRF and sessions ------------------------------------------------------------------------


def test_connect_callback_after_sign_out_or_as_other_user_links_nothing(
    db: Database, google: FakeGoogle
) -> None:
    """Review focus 3: the session ended, or another user signed in in another tab."""
    api = _api(db)
    member = api.new_signed("member")
    now = int(api.clock().timestamp())

    # signed out before the callback
    started = _start(member.client)
    query = google.sign_in(started.json()["url"], _subject(), "ramesh.k@gmail.com", now)
    member.client.post("/api/auth/logout", headers=CSRF)
    reply = member.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert _landed(reply) == {"error": "signed_out"}

    # another user signed in in this browser before the callback
    member = api.sign_in(member.customer_id, "member")
    other = api.sign_in(member.customer_id, "member")
    started = _start(member.client)
    query = google.sign_in(started.json()["url"], _subject(), "ramesh.k@gmail.com", now)
    for cookie in member.client.cookies.jar:  # this browser's flow, now beside the other's session
        if cookie.name == OIDC_COOKIE:
            other.client.cookies.jar.set_cookie(cookie)
    assert other.client.cookies.get(SESSION_COOKIE) and other.client.cookies.get(OIDC_COOKIE)
    reply = other.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert _landed(reply) == {"error": "signed_out"}

    # a sign-in started by someone else (their flow cookie) never links to this session
    victim = api.sign_in(member.customer_id, "member")
    attacker = api.sign_in(member.customer_id, "member")
    started = _start(attacker.client)
    query = google.sign_in(started.json()["url"], _subject(), "attacker@example.com", now)
    reply = victim.client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert reply.headers["location"] == "/login?oidc_error=expired"  # no flow cookie in the victim's browser

    for signed in (member, other, victim, attacker):
        assert _mine(api, signed) == []


def test_connect_bumps_session_version_and_keeps_this_browser(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    member = api.new_signed("member")
    elsewhere = api.client()
    assert api.admin_login(elsewhere, member.email).status_code == 200
    before = api.user_state(member.customer_id, member.user_id)["session_version"]
    assert _landed(_connect(api, google, member)) == {"waiting": "google"}
    assert api.user_state(member.customer_id, member.user_id)["session_version"] == before + 1
    assert member.client.get("/api/auth/me").status_code == 200
    assert elsewhere.get("/api/auth/me").status_code == 401


def test_audit_identity_linked_has_no_email_or_sub(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    member = api.new_signed("member")
    subject = _subject()
    _connect(api, google, member, subject=subject)
    with db.customer(member.customer_id) as conn:
        identity_id = str(identities.for_user(conn, member.customer_id, member.user_id)[0]["id"])
    rows = [row for row in api.audit_rows(member.customer_id) if row[0] == "auth.identity_linked"]
    assert rows == [
        (
            "auth.identity_linked",
            "user",
            member.user_id,
            # Changed on purpose (#507): the email's domain, never the email itself.
            {"provider": "google", "identity_id": identity_id, "status": "pending", "domain": "gmail.com"},
        )
    ]
    assert subject not in str(api.audit_rows(member.customer_id))
    assert "ramesh" not in str(api.audit_rows(member.customer_id))


def test_connect_rate_limited_per_user(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    monkeypatch.setattr(oidc_router, "connect_limits", RateLimiter(2, window_seconds=3600))
    api = _api(db)
    member = api.new_signed("member")
    replies = [_start(member.client) for _ in range(3)]
    assert [reply.status_code for reply in replies] == [200, 200, 429]
    assert replies[2].headers["retry-after"]
    assert (
        api.new_signed("member")
        .client.post("/api/auth/oidc/google/connect", json={"password": PASSWORD}, headers=CSRF)
        .status_code
        == 200
    )  # another user has their own allowance


def test_a_failing_mailer_never_breaks_the_connect(db: Database, google: FakeGoogle) -> None:
    api = _api(db, FailingMailer())
    member = api.new_signed("member")
    assert _landed(_connect(api, google, member)) == {"waiting": "google"}


def test_cancel_at_google_while_connecting_goes_back_to_ways_to_sign_in(
    db: Database, google: FakeGoogle
) -> None:
    api = _api(db)
    member = api.new_signed("member")
    started = _start(member.client)
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(started.json()["url"]).query))["state"]
    reply = member.client.get(
        "/api/auth/oidc/google/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert _landed(reply) == {"error": "cancelled"}
    assert _mine(api, member) == []


def test_connect_is_404_while_off(db: Database) -> None:
    api = Api(db, settings=make_settings())
    member = api.new_signed("member")
    reply = member.client.post("/api/auth/oidc/google/connect", json={"password": PASSWORD}, headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert api.user_state(member.customer_id, member.user_id)["failed_logins"] == 0  # nothing read


# --- a person's own connections (Ways to sign in) -------------------------------------------------


def test_list_shows_only_mine(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    member = api.new_signed("member")
    colleague = api.sign_in(member.customer_id, "member")
    _connect(api, google, member)
    _connect(api, google, colleague, "sita@example.com")
    listed = member.client.get("/api/auth/identities")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["providers"] == ["google"]
    assert [(row["provider"], row["email"], row["status"]) for row in body["identities"]] == [
        ("google", "ramesh.k@gmail.com", "pending")
    ]
    assert set(body["identities"][0]) == {"id", "provider", "email", "status", "linked_at", "last_used_at"}


def test_remove_needs_password_and_keeps_password_sign_in_working(db: Database, google: FakeGoogle) -> None:
    mailer = MemoryMailer()
    api = _api(db, mailer)
    member = api.new_signed("member")
    _connect(api, google, member, member.email)  # approved at once: the user's own email
    identity_id = member.client.get("/api/auth/identities").json()["identities"][0]["id"]
    path = f"/api/auth/identities/{identity_id}/remove"

    wrong = member.client.post(path, json={"password": "not the password!!"}, headers=CSRF)
    assert wrong.status_code == 400
    assert len(_mine(api, member)) == 1

    elsewhere = api.client()
    assert api.admin_login(elsewhere, member.email).status_code == 200
    removed = member.client.post(path, json={"password": PASSWORD}, headers=CSRF)
    assert removed.status_code == 200, removed.text
    rules = {"google_only": False, "domains": []}  # Changed on purpose (#507): the rules for Ways to sign in
    assert removed.json() == {"providers": ["google"], "identities": [], "rules": rules}
    assert _mine(api, member) == []
    assert member.client.get("/api/auth/me").status_code == 200  # this browser stays in
    assert elsewhere.get("/api/auth/me").status_code == 401  # every other one is signed out
    assert api.admin_login(api.client(), member.email).status_code == 200  # the password still works
    rows = [row for row in api.audit_rows(member.customer_id) if row[0] == "auth.identity_unlinked"]
    assert rows == [
        ("auth.identity_unlinked", "user", member.user_id, {"provider": "google", "identity_id": identity_id})
    ]
    to_member = [sent.subject for sent in mailer.sent if sent.to == member.email]
    assert to_member[-1] == "A Google account was disconnected"


def test_cancelling_a_waiting_request_needs_no_password_but_a_disconnect_does(
    db: Database, google: FakeGoogle
) -> None:
    """A waiting connection never signed in, so cancelling it takes nothing away (#457 review)."""
    api = _api(db)
    member = api.new_signed("member")
    _connect(api, google, member)  # waiting
    [waiting] = member.client.get("/api/auth/identities").json()["identities"]
    cancelled = member.client.post(f"/api/auth/identities/{waiting['id']}/remove", json={}, headers=CSRF)
    assert cancelled.status_code == 200, cancelled.text
    assert _mine(api, member) == []
    assert api.user_state(member.customer_id, member.user_id)["failed_logins"] == 0

    _connect(api, google, member, member.email)  # approved at once
    [approved] = member.client.get("/api/auth/identities").json()["identities"]
    path = f"/api/auth/identities/{approved['id']}/remove"
    refused = member.client.post(path, json={}, headers=CSRF)
    assert (refused.status_code, refused.json()["error"]) == (
        400,
        {"code": "bad_request", "message": "Type your Kaagaz password to continue."},
    )
    assert len(_mine(api, member)) == 1
    assert member.client.post(path, json={"password": PASSWORD}, headers=CSRF).status_code == 200


def test_remove_someone_elses_is_404(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    member = api.new_signed("member")
    colleague = api.sign_in(member.customer_id, "member")
    _connect(api, google, colleague)
    identity_id = colleague.client.get("/api/auth/identities").json()["identities"][0]["id"]
    reply = member.client.post(
        f"/api/auth/identities/{identity_id}/remove", json={"password": PASSWORD}, headers=CSRF
    )
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert len(_mine(api, colleague)) == 1


def test_me_has_recent_connections(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    member = api.new_signed("member")
    assert member.client.get("/api/auth/me").json()["recent_connections"] == []
    _connect(api, google, member, member.email)  # approved
    linked_at = member.client.get("/api/auth/identities").json()["identities"][0]["linked_at"]
    me = member.client.get("/api/auth/me").json()
    assert me["recent_connections"] == [{"provider": "google", "linked_at": linked_at}]
    assert (
        member.client.get("/api/auth/session").json()["me"]["recent_connections"] == me["recent_connections"]
    )
    api.clock.advance(15 * 24 * 3600)
    member = api.sign_in(member.customer_id, "member")
    assert member.client.get("/api/auth/me").json()["recent_connections"] == []


def test_a_waiting_connection_is_not_recent(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    member = api.new_signed("member")
    _connect(api, google, member)  # waiting
    assert member.client.get("/api/auth/me").json()["recent_connections"] == []


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/api/auth/identities"), ("POST", f"/api/auth/identities/{ANY_ID}/remove")]
)
def test_identity_routes_are_404_while_off(db: Database, method: str, path: str) -> None:
    reply = (
        Api(db, settings=make_settings())
        .client()
        .request(method, path, json=None if method == "GET" else {"password": PASSWORD}, headers=CSRF)
    )
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")


def test_disconnect_rate_limited_per_user(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    monkeypatch.setattr(oidc_router, "remove_limits", RateLimiter(2, window_seconds=3600))
    api = _api(db)
    member = api.new_signed("member")
    path = f"/api/auth/identities/{ANY_ID}/remove"
    replies = [member.client.post(path, json={"password": PASSWORD}, headers=CSRF) for _ in range(3)]
    assert [reply.status_code for reply in replies] == [404, 404, 429]
    assert replies[2].headers["retry-after"]


def test_a_notice_that_breaks_never_breaks_connect_or_disconnect(db: Database, google: FakeGoogle) -> None:
    """The member's notice and the admins' "Waiting for your approval" go after the reply."""
    api = _api(db, BrokenMailer())
    member = api.new_signed("member")
    reply = _connect(api, google, member)
    assert _landed(reply) == {"waiting": "google"}
    assert SESSION_COOKIE in reply.headers.get("set-cookie", "")
    assert member.client.get("/api/auth/me").status_code == 200
    [identity] = member.client.get("/api/auth/identities").json()["identities"]
    removed = member.client.post(
        f"/api/auth/identities/{identity['id']}/remove", json={"password": PASSWORD}, headers=CSRF
    )
    assert removed.status_code == 200, removed.text
    assert SESSION_COOKIE in removed.headers.get("set-cookie", "")


@pytest.mark.parametrize(("has_provider", "error"), [(True, "has_provider"), (False, "taken")])
def test_a_connect_that_loses_a_race_says_which_rule_it_met(
    db: Database, google: FakeGoogle, monkeypatch: pytest.MonkeyPatch, has_provider: bool, error: str
) -> None:
    """Two connects at once: the unique key the second insert met says which refusal it is."""

    def lost(*args: object, **kwargs: object) -> str:
        raise identities.IdentityTaken(has_provider=has_provider)

    monkeypatch.setattr(identities, "add", lost)
    api = _api(db)
    member = api.new_signed("member")
    assert _landed(_connect(api, google, member)) == {"error": error}
