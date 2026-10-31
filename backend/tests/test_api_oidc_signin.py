"""Signing in with a connected Google account (#457 phase 1), against a made-up Google.

Only an approved connection signs in, through the same checks as a password: a disabled
user or organisation, the lock and the attempt count. Without two-step sign-in a member or
reviewer gets an app session; anyone with codes on (always an admin) types the code next.
Every failure lands on /login?oidc_error=<code>; the email never goes in a URL."""

import json
import logging
import urllib.parse
import uuid
from datetime import timedelta

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.api import auth as auth_module
from kaagaz.api.auth import NOTICE_COOKIE, OIDC_COOKIE, PENDING_COOKIE, SESSION_COOKIE
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import oidc as oidc_router
from kaagaz.db import identities
from kaagaz.db.engine import Database
from tests.oidc_fake import ISSUER, FakeGoogle, google_settings, use_google
from tests.settings_factory import make_settings
from tests.test_api_support import ANY_ID, CSRF, PASSWORD, Api, TestClient, wrong_code

pytestmark = pytest.mark.db


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    monkeypatch.setattr(oidc_router, "callback_limits", RateLimiter(1000, window_seconds=3600))
    return use_google(monkeypatch)


def _api(db: Database) -> Api:
    return Api(db, settings=google_settings())


def _now(api: Api) -> int:
    return int(api.clock().timestamp())


def _connect(api: Api, customer_id: str, user_id: str, *, approved: bool = True) -> str:
    """A Google account connected to this user, as Ways to sign in would leave it; its subject."""
    subject = f"made-up-sub-{uuid.uuid4().hex}"
    with api.db.customer(customer_id) as conn:
        identities.add(
            conn,
            customer_id,
            user_id,
            provider="google",
            issuer=ISSUER,
            subject=subject,
            email="ramesh.k@gmail.com",
            approved=approved,
            approved_by=None,
            now=api.clock(),
        )
    return subject


def _start(client: TestClient, next_path: str = "/documents") -> str:
    reply = client.get("/api/auth/oidc/google/start", params={"next": next_path}, follow_redirects=False)
    assert reply.status_code == 303, reply.text
    return reply.headers["location"]


def _sign_in_at_google(
    api: Api, google: FakeGoogle, client: TestClient, subject: str, email: str = "Ramesh.K@Gmail.com"
) -> httpx.Response:
    query = google.sign_in(_start(client), subject, email, _now(api))
    return client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)


def _error(reply: httpx.Response) -> str | None:
    assert reply.status_code == 303, reply.text
    location = urllib.parse.urlsplit(reply.headers["location"])
    assert location.path == "/login"
    return dict(urllib.parse.parse_qsl(location.query)).get("oidc_error")


def test_start_redirects_to_google_and_sets_flow_cookie(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    client = api.client()
    url = _start(client)
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query["prompt"] == "select_account" and query["code_challenge_method"] == "S256"
    assert client.cookies.get(OIDC_COOKIE)
    flow = auth_module.read_flow(client.cookies[OIDC_COOKIE], api.settings.session_secret, _now(api))
    assert flow is not None and (flow.mode, flow.next_path, flow.state) == (
        "signin",
        "/documents",
        query["state"],
    )


def test_member_with_approved_google_gets_app_session(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id)
    client = api.client()

    reply = _sign_in_at_google(api, google, client, subject)

    assert (reply.status_code, reply.headers["location"]) == (303, "/documents")
    assert client.cookies.get(SESSION_COOKIE) and not client.cookies.get(OIDC_COOKIE)
    me = client.get("/api/auth/me").json()
    assert (me["user"]["id"], me["session"]["scope"]) == (member_id, "app")
    assert ("auth.login", "user", member_id, {"via": "google"}) in api.audit_rows(customer_id)
    with db.customer(customer_id) as conn:
        assert [row["last_used_at"] for row in identities.for_user(conn, customer_id, member_id)] == [
            api.clock()
        ]


def test_pending_connection_cannot_sign_in(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id, approved=False)
    client = api.client()
    assert _error(_sign_in_at_google(api, google, client, subject)) == "not_connected"
    assert not client.cookies.get(SESSION_COOKIE)


def test_unknown_google_account_says_not_connected(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    client = api.client()
    assert _error(_sign_in_at_google(api, google, client, "made-up-unknown")) == "not_connected"


def test_not_connected_email_comes_from_notice_not_url(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    client = api.client()
    reply = _sign_in_at_google(api, google, client, "made-up-unknown", "Ramesh.K@Gmail.com")
    assert _error(reply) == "not_connected"
    assert "ramesh" not in reply.headers["location"].lower()
    assert client.cookies.get(NOTICE_COOKIE)
    assert client.get("/api/auth/oidc/notice").json() == {"email": "ramesh.k@gmail.com"}
    assert client.get("/api/auth/oidc/notice").json() == {"email": None}  # once only


def test_admin_needs_code_after_google_then_wrong_code_counted_then_right_code_signs_in(
    db: Database, google: FakeGoogle
) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    admin = api.enrol_admin(customer_id)
    subject = _connect(api, customer_id, admin.user_id)
    client = api.client()

    reply = _sign_in_at_google(api, google, client, subject)
    assert (reply.status_code, reply.headers["location"]) == (303, "/login?step=code&next=%2Fdocuments")
    assert client.cookies.get(PENDING_COOKIE) and not client.cookies.get(SESSION_COOKIE)
    assert client.get("/api/auth/me").status_code == 401  # the second step is not a session

    wrong = client.post("/api/auth/oidc/code", json={"code": wrong_code(api, admin.secret)}, headers=CSRF)
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "bad_credentials")
    # Changed on purpose (#457 review): one attempt per sign-in, the code's, not Google's as well.
    assert api.user_state(customer_id, admin.user_id)["failed_logins"] == 1

    right = client.post("/api/auth/oidc/code", json={"code": api.code(admin.secret)}, headers=CSRF)
    assert right.status_code == 200, right.text
    assert right.json()["session"]["scope"] == "admin"
    assert not client.cookies.get(PENDING_COOKIE)
    assert client.get("/api/admin/users").status_code == 200
    assert api.user_state(customer_id, admin.user_id)["failed_logins"] == 0
    assert ("auth.login", "user", admin.user_id, {"via": "google"}) in api.audit_rows(customer_id)


def test_member_with_a_code_types_it_after_google(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member = api.enrol_user(customer_id, "member")
    subject = _connect(api, customer_id, member.user_id)
    client = api.client()
    reply = _sign_in_at_google(api, google, client, subject)
    assert reply.headers["location"].startswith("/login?step=code")
    right = client.post("/api/auth/oidc/code", json={"code": api.code(member.secret)}, headers=CSRF)
    assert right.status_code == 200, right.text
    assert right.json()["session"]["scope"] == "app"


def test_the_code_step_needs_its_cookie_and_expires(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    admin = api.enrol_admin(customer_id)
    subject = _connect(api, customer_id, admin.user_id)
    client = api.client()
    no_cookie = api.client().post("/api/auth/oidc/code", json={"code": "123456"}, headers=CSRF)
    assert (no_cookie.status_code, no_cookie.json()["error"]["code"]) == (401, "not_signed_in")
    _sign_in_at_google(api, google, client, subject)
    api.clock.advance(auth_module.PENDING_SECONDS)
    late = client.post("/api/auth/oidc/code", json={"code": api.code(admin.secret)}, headers=CSRF)
    assert (late.status_code, late.json()["error"]["code"]) == (401, "not_signed_in")


def test_admin_without_two_step_must_use_password_first(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, admin_id = api.customer()  # an admin with no code set up
    subject = _connect(api, customer_id, admin_id)
    client = api.client()
    assert _error(_sign_in_at_google(api, google, client, subject)) == "password_first"
    assert not client.cookies.get(SESSION_COOKIE) and not client.cookies.get(PENDING_COOKIE)


def test_admin_who_turned_two_step_off_signs_in_with_google_alone(db: Database, google: FakeGoogle) -> None:
    # #457 with #460 (Tushar, 30 Oct 2026): turned off by the admin is not "no code set up yet".
    api = _api(db)
    customer_id, admin_id = api.customer()
    with db.customer(customer_id) as conn:
        turned_off = text("UPDATE users SET totp_off_at = :t WHERE id = :u")
        conn.execute(turned_off, {"t": api.clock(), "u": admin_id})
    subject = _connect(api, customer_id, admin_id)
    client = api.client()

    reply = _sign_in_at_google(api, google, client, subject)

    assert (reply.status_code, reply.headers["location"]) == (303, "/documents")
    assert not client.cookies.get(PENDING_COOKIE)
    me = client.get("/api/auth/me").json()
    assert (me["session"]["scope"], me["user"]["two_step_turned_off"]) == ("admin", True)
    assert client.get("/api/admin/users").status_code == 200
    assert ("auth.login", "user", admin_id, {"via": "google"}) in api.audit_rows(customer_id)


def test_password_first_counts_nothing(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, admin_id = api.customer()
    subject = _connect(api, customer_id, admin_id)
    for _ in range(3):
        assert _error(_sign_in_at_google(api, google, api.client(), subject)) == "password_first"
    assert api.user_state(customer_id, admin_id)["failed_logins"] == 0


def _last_used(api: Api, customer_id: str, user_id: str) -> object:
    with api.db.customer(customer_id) as conn:
        [row] = identities.for_user(conn, customer_id, user_id)
    return row["last_used_at"]


@pytest.mark.parametrize("why", ["disabled", "locked", "pending", "password_first"])
def test_a_refused_sign_in_never_marks_the_connection_used(
    db: Database, google: FakeGoogle, why: str
) -> None:
    api = _api(db)
    customer_id, admin_id = api.customer()
    user_id = admin_id if why == "password_first" else api.add_user(customer_id, "member")[0]
    subject = _connect(api, customer_id, user_id)
    if why == "disabled":
        with db.customer(customer_id) as conn:
            conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": user_id})
    elif why == "locked":
        with db.customer(customer_id) as conn:
            conn.execute(
                text("UPDATE users SET locked_until = :t WHERE id = :u"),
                {"t": api.clock() + timedelta(minutes=10), "u": user_id},
            )
    elif why == "pending":
        with db.engine.begin() as conn:  # as the owner: the app may not change a status
            conn.execute(text("UPDATE customers SET status = 'pending' WHERE id = :c"), {"c": customer_id})
    assert _error(_sign_in_at_google(api, google, api.client(), subject)) is not None
    assert _last_used(api, customer_id, user_id) is None


def test_disabled_user_refused(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id)
    with db.customer(customer_id) as conn:
        conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": member_id})
    client = api.client()
    assert _error(_sign_in_at_google(api, google, client, subject)) == "refused"
    assert not client.cookies.get(SESSION_COOKIE)
    assert ("auth.login_failed", "user", member_id, {"reason": "disabled"}) in api.audit_rows(customer_id)


def test_disabled_customer_refused(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id)
    with db.engine.begin() as conn:  # as the owner: the app may not change a status (0014)
        conn.execute(
            text("UPDATE customers SET status = 'disabled', disabled_at = now() WHERE id = :c"),
            {"c": customer_id},
        )
    client = api.client()
    assert _error(_sign_in_at_google(api, google, client, subject)) == "refused"
    assert not client.cookies.get(SESSION_COOKIE)


def test_locked_account_refused(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id)
    with db.customer(customer_id) as conn:
        conn.execute(
            text("UPDATE users SET locked_until = :t WHERE id = :u"),
            {"t": api.clock() + timedelta(minutes=10), "u": member_id},
        )
    client = api.client()
    assert _error(_sign_in_at_google(api, google, client, subject)) == "locked"
    assert not client.cookies.get(SESSION_COOKIE)


def test_cancel_at_google_returns_to_login_with_cancelled(db: Database, google: FakeGoogle) -> None:
    """Review focus 1: the person presses Cancel at Google."""
    api = _api(db)
    client = api.client()
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(_start(client)).query))["state"]
    reply = client.get(
        "/api/auth/oidc/google/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert _error(reply) == "cancelled"
    assert not client.cookies.get(OIDC_COOKIE)


def test_callback_replayed_says_expired(db: Database, google: FakeGoogle) -> None:
    """Review focus 2: the callback opened twice (Back or refresh): the flow cookie is single use."""
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id)
    client = api.client()
    query = google.sign_in(_start(client), subject, "a@example.com", _now(api))
    first = client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert first.headers["location"] == "/documents"
    again = api.client()  # the cookie is gone; a fresh browser has none either
    second = again.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert _error(second) == "expired"
    assert (
        _error(client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False))
        == "expired"
    )


def test_state_mismatch_says_expired(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id)
    client = api.client()
    query = google.sign_in(_start(client), subject, "a@example.com", _now(api))
    reply = client.get(
        "/api/auth/oidc/google/callback", params={**query, "state": "another-state"}, follow_redirects=False
    )
    assert _error(reply) == "expired"
    assert not client.cookies.get(SESSION_COOKIE) and not client.cookies.get(OIDC_COOKIE)


def test_a_token_for_another_nonce_says_expired(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id)
    client = api.client()
    query = google.sign_in(_start(client), subject, "a@example.com", _now(api), nonce="another-nonce")
    reply = client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert _error(reply) == "expired"


@pytest.mark.parametrize(
    ("wanted", "kept"),
    [
        ("/documents", "/documents"),
        ("/search?q=abc", "/search?q=abc"),
        ("//evil.example.com", "/"),
        ("https://evil.example.com", "/"),
        ("/\\evil.example.com", "/"),
        ("documents", "/"),
        ("/a/../../b%2F", "/"),
    ],
)
def test_next_must_be_a_plain_path(db: Database, google: FakeGoogle, wanted: str, kept: str) -> None:
    api = _api(db)
    client = api.client()
    _start(client, wanted)
    flow = auth_module.read_flow(client.cookies[OIDC_COOKIE], api.settings.session_secret, _now(api))
    assert flow is not None and flow.next_path == kept


def test_operator_email_never_matches(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    operator_email = f"ops-{uuid.uuid4().hex[:8]}@example.com"
    with db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO operators (email, display_name, password_hash) VALUES (:e, 'Ops', 'x')"),
            {"e": operator_email},
        )
    client = api.client()
    assert (
        _error(_sign_in_at_google(api, google, client, "made-up-operator", operator_email)) == "not_connected"
    )
    assert not client.cookies.get(SESSION_COOKIE)


OIDC_PATHS = [
    ("GET", "/api/auth/oidc/google/start"),
    ("GET", "/api/auth/oidc/google/callback"),
    ("GET", "/api/auth/oidc/notice"),
    ("POST", "/api/auth/oidc/code"),
]


@pytest.mark.parametrize(("method", "path"), OIDC_PATHS)
def test_every_route_404_while_off(db: Database, method: str, path: str) -> None:
    api = Api(db, settings=make_settings())
    reply = api.client().request(
        method, path, json=None if method == "GET" else {"code": "123456"}, headers=CSRF
    )
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")


def test_an_unknown_provider_is_404(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    for provider in ("microsoft", "apple", ANY_ID):
        reply = api.client().get(f"/api/auth/oidc/{provider}/start", follow_redirects=False)
        assert reply.status_code == 404, provider


def test_thirty_starts_never_block_a_sign_in(db: Database, google: FakeGoogle) -> None:
    """Behind the proxy every visitor shares one address: a start (only a redirect) is not limited."""
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id)
    client = api.client()
    for _ in range(30):
        _start(api.client())
    assert _sign_in_at_google(api, google, client, subject).headers["location"] == "/documents"


def test_the_site_wide_callback_limit_counts_only_callbacks_with_the_right_state(
    db: Database, google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oidc_router, "callback_limits", RateLimiter(1, window_seconds=3600))
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id)
    stray = api.client()
    for _ in range(5):  # no flow cookie, or the wrong state: never counted
        reply = stray.get(
            "/api/auth/oidc/google/callback", params={"state": "x", "code": "y"}, follow_redirects=False
        )
        assert _error(reply) == "expired"

    assert _sign_in_at_google(api, google, api.client(), subject).headers["location"] == "/documents"
    busy = _sign_in_at_google(api, google, api.client(), subject)
    assert _error(busy) == "busy"  # a redirect the sign-in page explains, never raw 429 JSON


def test_callback_has_no_referrer_header(db: Database, google: FakeGoogle) -> None:
    api = _api(db)
    client = api.client()
    reply = _sign_in_at_google(api, google, client, "made-up-unknown")
    assert reply.headers["referrer-policy"] == "no-referrer"
    assert client.get("/api/health").headers["referrer-policy"] == "same-origin"


def test_the_session_probe_lists_the_providers_that_are_on(db: Database) -> None:
    assert _api(db).client().get("/api/auth/session").json() == {"signed_in": False, "providers": ["google"]}
    assert Api(db).client().get("/api/auth/session").json() == {"signed_in": False, "providers": []}


def test_every_me_reply_lists_the_providers_that_are_on(db: Database) -> None:
    """Signed in too: Account hides "Ways to sign in" while none is on (#457 review)."""
    for api, providers in ((_api(db), ["google"]), (Api(db), [])):
        member = api.new_signed("member")
        assert member.client.get("/api/auth/me").json()["providers"] == providers
        assert member.client.get("/api/auth/session").json()["me"]["providers"] == providers
        body = {"current_password": PASSWORD, "new_password": "a brand new made-up password"}
        changed = member.client.post("/api/auth/password", json=body, headers=CSRF)
        assert changed.json()["providers"] == providers


def test_logs_never_contain_code_token_or_email(
    db: Database, google: FakeGoogle, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    api = _api(db)
    customer_id, _ = api.customer()
    member_id, _ = api.add_user(customer_id, "member")
    subject = _connect(api, customer_id, member_id)
    client = api.client()
    query = google.sign_in(_start(client), subject, "Ramesh.K@Gmail.com", _now(api))
    flow = auth_module.read_flow(client.cookies[OIDC_COOKIE], api.settings.session_secret, _now(api))
    assert flow is not None
    client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    unknown = api.client()
    _sign_in_at_google(api, google, unknown, "made-up-unknown-sub", "sita@example.com")

    # The server's records only: the test client logs the URLs it calls itself.
    server = [record for record in caplog.records if not record.name.startswith("httpx")]
    logged = json.dumps([[record.getMessage(), str(record.__dict__)] for record in server]).lower()
    id_token = google.codes[query["code"]][0]
    never = [
        query["code"],
        query["state"],
        flow.nonce,
        flow.verifier,
        subject,
        "made-up-unknown-sub",
        id_token,
    ]
    for secret in [*never, "ramesh", "sita@example.com", "made-up-google-secret"]:
        assert secret.lower() not in logged, secret


def test_an_unexpected_error_in_the_callback_is_logged_and_redirects_as_unavailable(
    db: Database, google: FakeGoogle, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: object) -> None:
        raise RuntimeError("the database broke")

    monkeypatch.setattr(identities, "lookup", broken)
    caplog.set_level(logging.WARNING, logger="kaagaz.api.routers.oidc")
    api = _api(db)
    client = api.client()
    reply = _sign_in_at_google(api, google, client, "made-up-sub")
    assert _error(reply) == "unavailable"  # a redirect, never raw JSON
    assert not client.cookies.get(OIDC_COOKIE)
    assert '{"event":"oidc_failed","provider":"google","error":"RuntimeError"}' in caplog.messages


@pytest.mark.parametrize(
    ("break_it", "logged"),
    [("refuse", ',"reason":"refused","status":400}'), ("down", ',"reason":"unavailable","status":null}')],
)
def test_a_token_endpoint_failure_logs_why_and_its_status(
    db: Database, google: FakeGoogle, caplog: pytest.LogCaptureFixture, break_it: str, logged: str
) -> None:
    caplog.set_level(logging.WARNING, logger="kaagaz.api.routers.oidc")
    api = _api(db)
    client = api.client()
    query = google.sign_in(_start(client), "made-up-sub", "a@example.com", _now(api))
    if break_it == "refuse":
        google.used.add(query["code"])  # Google answers 400: the code was used
    else:
        google.down = True
    reply = client.get("/api/auth/oidc/google/callback", params=query, follow_redirects=False)
    assert _error(reply) == "unavailable"
    assert '{"event":"oidc_refused","provider":"google","code":"unavailable"' + logged in caplog.messages
