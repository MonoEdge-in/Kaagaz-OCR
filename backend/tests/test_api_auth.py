"""Sign-in, sessions, roles, CSRF, the rate limit and the error shape.

The first half needs no database: password hashing, token signing, the
limiter, and requests the API refuses before it touches the database. The
second half (marked ``db``) signs in against a real PostgreSQL.
"""

import base64
import json

import pytest
from sqlalchemy import text

from kaagaz.api.app import create_app
from kaagaz.api.auth import (
    SESSION_COOKIE,
    SessionClaims,
    hash_password,
    read_session,
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
BAD_CREDENTIALS = "Email or password is wrong, or the account is locked. Try again in 15 minutes."


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
        "user": {
            "id": user_id,
            "email": email,
            "display_name": "Test reviewer",
            "role": "reviewer",
            "must_change_password": False,
        },
        "customer": {"id": customer_id, "name": "Sharma Traders"},
        "session": {"scope": "app"},
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
def test_logout_clears_the_cookie(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    reply = signed.client.post("/api/auth/logout", headers=CSRF)
    assert reply.status_code == 204
    assert f'{SESSION_COOKIE}=""' in reply.headers["set-cookie"] or "Max-Age=0" in reply.headers["set-cookie"]
    assert signed.client.get("/api/auth/me").status_code == 401
    assert api.audit_actions(signed.customer_id)[-1] == "auth.logout"


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
        ("admin", "/api/review", 200, None),
        ("admin", "/api/admin/stats", 403, "admin_session_required"),
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
