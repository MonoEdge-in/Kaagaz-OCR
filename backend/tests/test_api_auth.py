"""Sign-in, sessions, roles, CSRF, the rate limit and the error shape.

The first half needs no database: password hashing, token signing, the
limiter, and requests the API refuses before it touches the database. The
second half (marked ``db``) signs in against a real PostgreSQL.
"""

import base64
import json
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from kaagaz.api.app import create_app
from kaagaz.api.auth import (
    NOTICE_SECONDS,
    OIDC_SECONDS,
    PENDING_SECONDS,
    SESSION_COOKIE,
    ConfirmClaims,
    FlowClaims,
    PendingClaims,
    SessionClaims,
    hash_password,
    read_flow,
    read_notice,
    read_pending,
    read_session,
    sign_confirm,
    sign_flow,
    sign_notice,
    sign_pending,
    sign_session,
    verify_password,
)
from kaagaz.api.middleware import RateLimiter
from kaagaz.db.engine import Database, make_engine
from tests.settings_factory import make_settings
from tests.test_api_support import (
    CSRF,
    NOW,
    PASSWORD,
    Api,
    ApiFakeQueue,
    ApiFakeStorage,
    FakeAnswers,
    Signed,
    TestClient,
)

UID = "11111111-1111-4111-8111-111111111111"
# Changed on purpose (UI quality council, 15 Oct 2026): one clear message, spelt e-mail.
BAD_CREDENTIALS = (
    "E-mail or password is wrong, or the account is locked. Check them, or wait 15 minutes, then try again."
)
OTHER_PASSWORD = "another made-up password"


def _claims(
    customer_id: str, *, user_id: str = UID, role: str = "admin", scope: str = "admin"
) -> SessionClaims:
    return SessionClaims(
        user_id=user_id,
        customer_id=customer_id,
        role=role,
        scope=scope,
        session_version=1,
        expires_at=int(NOW.timestamp()) + 3600,
    )


def _offline_client(**settings: object) -> TestClient:
    """An app whose database is never reached (the URL points nowhere)."""
    db = Database(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app")
    app = create_app(
        make_settings(**settings),
        db=db,
        storage=ApiFakeStorage(),
        queue=ApiFakeQueue(),
        answers=FakeAnswers(),
        clock=lambda: NOW,
    )
    return TestClient(app)


# --- passwords -------------------------------------------------------------------


def test_password_hash_has_the_agreed_format_and_verifies() -> None:
    stored = hash_password("a long enough password")
    parts = stored.split("$")
    assert parts[:4] == ["scrypt", "16384", "8", "1"]
    assert len(base64.b64decode(parts[4])) == 16
    assert len(base64.b64decode(parts[5])) == 32
    assert verify_password("a long enough password", stored)
    assert not verify_password("a long enough passwore", stored)


def test_same_password_gets_a_new_salt_each_time() -> None:
    assert hash_password("same password here") != hash_password("same password here")


@pytest.mark.parametrize(
    "stored",
    ["", "not-a-real-hash", "scrypt$1024$8$1$AAAA$AAAA", "scrypt$16384$8$1$***$AAAA", "bcrypt$x$y$z$a$b"],
)
def test_malformed_or_foreign_hashes_never_verify(stored: str) -> None:
    assert not verify_password("anything", stored)


# --- session tokens (the format itself: tests/test_auth_tokens.py) -------------------------


def test_token_with_a_changed_customer_is_refused() -> None:
    secret = "a-test-secret-that-is-long-enough-000000"
    token = sign_session(_claims("22222222-2222-4222-8222-222222222222"), secret)
    body, signature = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert read_session(token, secret, now=1_000) is not None
    payload["cid"] = "33333333-3333-4333-8333-333333333333"
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    assert read_session(forged + "." + signature, secret, now=1_000) is None


# --- the provider sign-in's flow cookie and second-step token (#457 phase 1) ------------------

SECRET = "a-test-secret-that-is-long-enough-000000"
CID = "22222222-2222-4222-8222-222222222222"


def _flow(**changes: Any) -> FlowClaims:
    values: dict[str, Any] = {
        "provider": "google",
        "mode": "connect",
        "state": "made-up-state",
        "nonce": "made-up-nonce",
        "verifier": "v" * 64,
        "next_path": "/settings/sign-in",
        "user_id": UID,
        "customer_id": CID,
        "session_version": 3,
        "expires_at": 1_000 + OIDC_SECONDS,
    }
    return FlowClaims(**{**values, **changes})


def test_flow_round_trip() -> None:
    connect = _flow()
    signin = _flow(mode="signin", user_id=None, customer_id=None, session_version=None, next_path="/")
    assert read_flow(sign_flow(connect, SECRET), SECRET, now=1_000) == connect
    assert read_flow(sign_flow(signin, SECRET), SECRET, now=1_000) == signin


def test_flow_refused_when_expired_tampered_or_signed_for_another_purpose() -> None:
    token = sign_flow(_flow(), SECRET)
    assert read_flow(token, SECRET, now=1_000 + OIDC_SECONDS) is None  # expired
    assert read_flow(token, "another-secret-that-is-long-enough-0000", now=1_000) is None
    body, signature = token.split(".")
    assert read_flow(body + "x." + signature, SECRET, now=1_000) is None
    confirm = sign_confirm(
        ConfirmClaims(user_id=UID, customer_id=CID, session_version=3, expires_at=2_000, session_id=UID),
        SECRET,
    )
    assert read_flow(confirm, SECRET, now=1_000) is None
    assert read_session(token, SECRET, now=1_000) is None


def test_flow_mode_must_be_signin_or_connect() -> None:
    assert read_flow(sign_flow(_flow(mode="link"), SECRET), SECRET, now=1_000) is None
    # a connect carries its user, a sign-in carries none
    assert read_flow(sign_flow(_flow(user_id=None), SECRET), SECRET, now=1_000) is None
    assert read_flow(sign_flow(_flow(mode="signin"), SECRET), SECRET, now=1_000) is None


def test_pending_round_trip_and_expiry() -> None:
    claims = PendingClaims(
        provider="google", user_id=UID, customer_id=CID, session_version=3, expires_at=1_000 + PENDING_SECONDS
    )
    token = sign_pending(claims, SECRET)
    assert read_pending(token, SECRET, now=1_000) == claims
    assert read_pending(token, SECRET, now=1_000 + PENDING_SECONDS) is None
    assert read_flow(token, SECRET, now=1_000) is None


def test_pending_is_not_a_session() -> None:
    claims = PendingClaims(
        provider="google", user_id=UID, customer_id=CID, session_version=1, expires_at=2_000
    )
    assert read_session(sign_pending(claims, SECRET), SECRET, now=1_000) is None


def test_notice_round_trip_and_expiry() -> None:
    token = sign_notice("ramesh.k@gmail.com", 1_000 + NOTICE_SECONDS, SECRET)
    assert read_notice(token, SECRET, now=1_000) == "ramesh.k@gmail.com"
    assert read_notice(token, SECRET, now=1_000 + NOTICE_SECONDS) is None
    assert read_pending(token, SECRET, now=1_000) is None


# --- rate limiter ----------------------------------------------------------------


def test_rate_limiter_allows_the_bucket_then_refuses_then_refills() -> None:
    now = [0.0]
    limiter = RateLimiter(3, clock=lambda: now[0])
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("b")  # every caller has its own bucket
    now[0] = 20.0  # 3 per minute: one token every 20 seconds
    assert limiter.allow("a")
    assert not limiter.allow("a")


def test_rate_limiter_drops_idle_callers_when_full() -> None:
    now = [0.0]
    limiter = RateLimiter(60, clock=lambda: now[0], max_keys=2)
    limiter.allow("a")
    limiter.allow("b")
    now[0] = 120.0
    assert limiter.allow("c")
    assert limiter.allow("a")  # "a" was dropped as idle and starts full again


def test_rate_limiter_refuses_a_zero_limit() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)


# --- refused before the database ---------------------------------------------------


def test_post_without_csrf_header_is_refused() -> None:
    reply = _offline_client().post("/api/auth/login", json={"email": "a@b.c", "password": "x"})
    assert reply.status_code == 403
    assert reply.json() == {"error": {"code": "csrf", "message": reply.json()["error"]["message"]}}


def test_wrong_csrf_value_is_refused() -> None:
    reply = _offline_client().post(
        "/api/auth/login", json={"email": "a@b.c", "password": "x"}, headers={"X-Requested-With": "other"}
    )
    assert reply.status_code == 403
    assert reply.json()["error"]["code"] == "csrf"


def test_not_signed_in_is_401_with_the_error_shape() -> None:
    reply = _offline_client().get("/api/auth/me")
    assert reply.status_code == 401
    body = reply.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "not_signed_in"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_validation_error_is_400_and_does_not_echo_the_input() -> None:
    reply = _offline_client().post(
        "/api/auth/login", json={"email": "a@b.c", "password": "", "extra": "hunter2-secret"}, headers=CSRF
    )
    assert reply.status_code == 400
    assert reply.json()["error"]["code"] == "bad_request"
    assert "hunter2" not in reply.text


def test_unknown_route_is_404_not_found() -> None:
    reply = _offline_client().get("/api/nothing-here")
    assert reply.status_code == 404
    assert reply.json()["error"]["code"] == "not_found"


def test_every_response_has_request_id_and_security_headers() -> None:
    reply = _offline_client().get("/api/auth/me")
    assert len(reply.headers["X-Request-ID"]) >= 8
    assert reply.headers["X-Content-Type-Options"] == "nosniff"
    assert reply.headers["Referrer-Policy"] == "same-origin"
    assert reply.headers["Cache-Control"] == "no-store"


def test_a_good_incoming_request_id_is_kept_and_a_bad_one_replaced() -> None:
    client = _offline_client()
    assert client.get("/api/auth/me", headers={"X-Request-ID": "abcd-1234-efgh"}).headers["X-Request-ID"] == (
        "abcd-1234-efgh"
    )
    assert (
        client.get("/api/auth/me", headers={"X-Request-ID": "bad id\n"}).headers["X-Request-ID"] != "bad id"
    )


def test_rate_limit_gives_429_with_the_error_shape() -> None:
    client = _offline_client(rate_limit_per_minute=2)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    reply = client.get("/api/auth/me")
    assert reply.status_code == 429
    assert reply.json()["error"]["code"] == "rate_limited"
    assert reply.headers["X-Request-ID"]


def test_signed_out_visitors_behind_caddy_each_have_their_own_bucket() -> None:
    """Production QA S2: uvicorn trusted X-Forwarded-For only from 127.0.0.1, so every signed-out
    visitor came from Caddy's address and shared one bucket: one person could block sign-in for
    every customer. The image trusts the header from anywhere (only Caddy reaches the API's port,
    and Caddy sets the header itself); uvicorn's own middleware is applied here as it runs there."""
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert re.findall(r"^ENV FORWARDED_ALLOW_IPS=(.+)$", dockerfile, re.MULTILINE) == ['"*"']
    app = _offline_client(rate_limit_per_minute=2).app
    client = TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*"))  # type: ignore[arg-type]  # ASGI types differ
    first, second = {"X-Forwarded-For": "203.0.113.7"}, {"X-Forwarded-For": "198.51.100.9"}
    for _ in range(2):
        assert client.get("/api/auth/me", headers=first).status_code == 401
    assert client.get("/api/auth/me", headers=first).status_code == 429  # this visitor: used up
    assert client.get("/api/auth/me", headers=second).status_code == 401  # another: their own


def test_the_sign_in_pages_server_calls_are_keyed_on_the_visitor_it_forwards() -> None:
    """A7 (stability pass, 7 Nov 2026): the login page's server side (frontend
    app/login/signInOptions.ts) calls /auth/session from the frontend container with the visitor's
    address in X-Forwarded-For, so each visitor keeps their own bucket. From outside the header is
    never believed: Caddy, with no trusted_proxies, replaces whatever a visitor sent."""
    caddyfile = (Path(__file__).resolve().parents[2] / "infra" / "caddy" / "Caddyfile").read_text(
        encoding="utf-8"
    )
    assert "trusted_proxies" not in caddyfile
    app = _offline_client(rate_limit_per_minute=1).app
    frontend = TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*"), client=("172.18.0.5", 40000))  # type: ignore[arg-type]  # ASGI types differ
    assert frontend.get("/api/auth/session", headers={"X-Forwarded-For": "203.0.113.7"}).status_code != 429
    assert frontend.get("/api/auth/session", headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 429
    assert frontend.get("/api/auth/session", headers={"X-Forwarded-For": "198.51.100.9"}).status_code != 429


def test_rate_limiter_retry_after_is_the_wait_for_the_next_token() -> None:
    """Vrushit UAT, 25 Oct 2026: a flat "60" made a polite client stall a minute for a
    token that comes back in half a second at 120 a minute."""
    now = [0.0]
    limiter = RateLimiter(120, clock=lambda: now[0])
    while limiter.allow("a"):
        pass
    assert limiter.retry_after("a") == 1
    slow = RateLimiter(3, clock=lambda: now[0])  # one token every 20 s
    while slow.allow("a"):
        pass
    now[0] = 5.0
    assert slow.retry_after("a") == 15


def test_the_documented_limit_is_the_one_the_browser_is_sized_under() -> None:
    """frontend/tests/request-budget.test.ts reads RATE_LIMIT_PER_MINUTE from .env.example and
    checks a bulk upload's polling and upload pace fit well under it. Lowering it here breaks
    this test; making the browser chattier breaks that one."""
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    assert re.findall(r"^RATE_LIMIT_PER_MINUTE=(\d+)$", example, re.MULTILINE) == ["120"]


def test_a_refusal_says_when_to_come_back_and_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="kaagaz.api.rate_limit")
    client = _offline_client(rate_limit_per_minute=2)  # one token every 30 s
    client.get("/api/auth/me")
    client.get("/api/auth/me")
    reply = client.get("/api/auth/me")
    assert reply.status_code == 429
    assert reply.headers["Retry-After"] == "30"
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kaagaz.api.rate_limit"]
    assert len(lines) == 1
    assert lines[0]["event"] == "rate_limited"
    assert lines[0]["path"] == "/api/auth/me"
    assert (lines[0]["bucket"], lines[0]["retry_after"]) == ("general", 30)
    assert [r.levelname for r in caplog.records if r.name == "kaagaz.api.rate_limit"] == ["WARNING"]


def test_upload_calls_and_reads_never_use_up_each_others_budget() -> None:
    """Vrushit UAT, 25 Oct 2026: the batch tray's polling used up the one budget and the
    upload calls were refused ("Too many requests"). Each has its own bucket now."""
    client = _offline_client(rate_limit_per_minute=2)
    for _ in range(2):
        client.get("/api/auth/me")
    assert client.get("/api/auth/me").status_code == 429  # reads: used up
    # Uploads still go.
    assert client.post("/api/documents/upload-sessions/candidates", json={}, headers=CSRF).status_code != 429
    assert (
        client.put("/api/documents/upload-sessions/s1/parts/1", content=b"x", headers=CSRF).status_code != 429
    )
    # Still a limit: the upload bucket is the same size, and a third call is refused.
    assert client.post("/api/documents/upload-sessions", json={}, headers=CSRF).status_code == 429


def test_forged_cookie_is_not_signed_in_without_a_database_lookup() -> None:
    client = _offline_client()
    forged = sign_session(
        _claims("22222222-2222-4222-8222-222222222222"), "someone-elses-secret-long-enough-000000"
    )
    client.cookies.set(SESSION_COOKIE, forged)
    reply = client.get("/api/auth/me")
    assert reply.status_code == 401
    assert reply.json()["error"]["code"] == "not_signed_in"


def test_access_log_has_no_query_string(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="kaagaz.api.access")
    _offline_client().get("/api/documents?q=ABCDE1234F")
    lines = [
        json.loads(record.getMessage()) for record in caplog.records if record.name == "kaagaz.api.access"
    ]
    assert lines and lines[-1]["path"] == "/api/documents"
    assert "ABCDE1234F" not in caplog.text


# --- against the database ------------------------------------------------------------


@pytest.mark.db
def test_login_sets_the_cookie_and_me_works(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer("Sharma Traders")
    user_id, email = api.add_user(customer_id, "reviewer")
    client = api.client()

    reply = client.post("/api/auth/login", json={"email": email.upper(), "password": PASSWORD}, headers=CSRF)

    assert reply.status_code == 200
    assert reply.json() == {
        "access": {
            "management": False,
            "heads": [],
            "in_department": False,
            "walled": False,
        },  # Changed on purpose (PRD 7, #601; walled: PR 11c, #604)
        "user": {
            "id": user_id,
            "email": email,
            "display_name": "Test reviewer",
            "role": "reviewer",
            "must_change_password": False,
            "totp_enabled": False,
            "two_step_turned_off": False,
            "two_step_required": False,  # Changed on purpose (#481, 0068): an admin's only
            "can_delete_forever": False,
            "email_confirmed": True,  # Changed on purpose (#457): true while email is off, no banner
            "google_link_required": False,  # Changed on purpose (#507): the limited session
            "google_unchecked": False,  # Changed on purpose (#507 review): a link not checked yet
        },
        "customer": {"id": customer_id, "name": "Sharma Traders"},
        "session": {"scope": "app"},
        "recent_connections": [],  # Changed on purpose (#457 phase 1): the "Google connected" banner
        "providers": [],  # Changed on purpose (#457 review): the providers switched on, for the menus
    }
    cookie = reply.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie or "SameSite=Lax" in cookie
    assert f"Max-Age={12 * 3600}" in cookie
    assert "Secure" not in cookie
    assert client.get("/api/auth/me").json() == reply.json()
    assert api.audit_actions(customer_id) == ["auth.login"]


@pytest.mark.db
def test_cookie_is_secure_when_configured(db: Database) -> None:
    api = Api(db, settings=make_settings(cookie_secure=True))
    customer_id, _ = api.customer()
    _, email = api.add_user(customer_id, "member")
    reply = api.client().post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF)
    assert "Secure" in reply.headers["set-cookie"]


@pytest.mark.db
def test_bad_credentials_look_the_same_for_every_reason(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    _, email = api.add_user(customer_id, "member")
    disabled_id, disabled_email = api.add_user(customer_id, "member")
    with db.customer(customer_id) as conn:
        conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": disabled_id})
    client = api.client()

    replies = [
        client.post("/api/auth/login", json={"email": email, "password": "wrong password!!"}, headers=CSRF),
        client.post(
            "/api/auth/login", json={"email": "nobody@example.test", "password": PASSWORD}, headers=CSRF
        ),
        client.post("/api/auth/login", json={"email": disabled_email, "password": PASSWORD}, headers=CSRF),
    ]

    assert {reply.status_code for reply in replies} == {401}
    assert len({reply.text for reply in replies}) == 1
    assert replies[0].json() == {"error": {"code": "bad_credentials", "message": BAD_CREDENTIALS}}
    assert all("set-cookie" not in reply.headers for reply in replies)


@pytest.mark.db
def test_a_password_signs_in_only_its_own_user(db: Database) -> None:
    """The helpers' users share one cached hash of PASSWORD, so here B has a different
    password: a login must check the hash of the user it names, never another's."""
    api = Api(db)
    _, a_email = api.add_user(api.customer("Sharma Traders")[0], "member")
    _, b_email = api.add_user(api.customer("Mehta Textiles")[0], "member", password=OTHER_PASSWORD)

    def login(email: str, password: str) -> int:
        body = {"email": email, "password": password}
        return api.client().post("/api/auth/login", json=body, headers=CSRF).status_code

    assert [login(a_email, OTHER_PASSWORD), login(b_email, PASSWORD)] == [401, 401]
    assert [login(a_email, PASSWORD), login(b_email, OTHER_PASSWORD)] == [200, 200]


@pytest.mark.db
def test_logout_clears_the_cookie(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    reply = signed.client.post("/api/auth/logout", headers=CSRF)
    assert reply.status_code == 204
    assert f'{SESSION_COOKIE}=""' in reply.headers["set-cookie"] or "Max-Age=0" in reply.headers["set-cookie"]
    assert signed.client.get("/api/auth/me").status_code == 401
    assert api.audit_actions(signed.customer_id)[-1] == "auth.logout"


@pytest.mark.db
def test_the_session_probe_answers_200_signed_in_or_not(db: Database) -> None:
    """Issue 378: GET /auth/session is /auth/me with no 401 for a visit with no session (no
    cookie, a signed-out one, a disabled user's), so the browser logs no expected error."""
    api = Api(db)
    assert api.client().get("/api/auth/session").json() == {
        "signed_in": False,
        "providers": [],
    }  # no cookie at all
    signed = api.new_signed()
    reply = signed.client.get("/api/auth/session")
    assert reply.status_code == 200
    assert reply.json() == {"signed_in": True, "me": signed.client.get("/api/auth/me").json()}
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": signed.user_id})
    reply = signed.client.get("/api/auth/session")
    assert (reply.status_code, reply.json()) == (200, {"signed_in": False, "providers": []})
    assert signed.client.get("/api/auth/me").status_code == 401  # /auth/me is unchanged


@pytest.mark.db
def test_disabling_a_user_ends_their_session_on_the_next_request(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE users SET disabled = true WHERE id = :u"), {"u": signed.user_id})
    assert signed.client.get("/api/auth/me").status_code == 401


@pytest.mark.db
def test_a_changed_role_ends_the_old_session(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE users SET role = 'member' WHERE id = :u"), {"u": signed.user_id})
    assert signed.client.get("/api/admin/users").status_code == 401
    assert signed.client.get("/api/auth/me").status_code == 401


@pytest.mark.db
def test_an_app_session_lasts_session_hours(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    api.clock.advance(12 * 3600 - 1)
    assert signed.client.get("/api/auth/me").status_code == 200
    api.clock.advance(1)
    assert signed.client.get("/api/auth/me").status_code == 401


@pytest.mark.db
def test_genuine_token_for_a_user_that_does_not_exist_is_refused(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    client = api.client()
    token = sign_session(_claims(customer_id), api.settings.session_secret)
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.db
@pytest.mark.parametrize(
    ("who", "path", "status", "code"),
    [
        ("member", "/api/review", 403, "forbidden"),
        ("member", "/api/admin/users", 403, "admin_session_required"),
        ("reviewer", "/api/review", 200, None),
        ("reviewer", "/api/admin/stats", 403, "admin_session_required"),
        # An admin's app session no longer exists (one sign-in, 0035): test_auth_tokens.
        ("admin session", "/api/review", 200, None),
        ("admin session", "/api/admin/stats", 200, None),
    ],
)
def test_roles_and_the_admin_session_gate_the_routes(
    db: Database, who: str, path: str, status: int, code: str | None
) -> None:
    api = Api(db)
    signed: Signed = api.new_admin() if who == "admin session" else api.new_signed(who)
    reply = signed.client.get(path)
    assert reply.status_code == status
    if code is not None:
        assert reply.json()["error"]["code"] == code


@pytest.mark.db
def test_login_writes_the_sign_in_time_and_resets_the_count(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    user_id, email = api.add_user(customer_id, "member")
    client = api.client()
    client.post("/api/auth/login", json={"email": email, "password": "not the password!!"}, headers=CSRF)
    assert api.user_state(customer_id, user_id)["failed_logins"] == 1

    assert (
        client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF).status_code
        == 200
    )

    assert api.user_state(customer_id, user_id)["failed_logins"] == 0
    with db.customer(customer_id) as conn:
        signed_in_at = conn.execute(text("SELECT last_login_at FROM users WHERE id = :u"), {"u": user_id})
        assert signed_in_at.scalar_one() == api.clock()
    assert api.audit_actions(customer_id) == ["auth.login_failed", "auth.login"]
