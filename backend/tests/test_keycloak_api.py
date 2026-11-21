"""Focused HTTP contracts for the Keycloak callback and replay boundary."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import Connection
from sqlalchemy.engine import Engine
from starlette.requests import Request
from starlette.testclient import TestClient

from kaagaz.api import auth, keycloak_oidc
from kaagaz.api.app import create_app
from kaagaz.api.auth import CurrentUser, User, new_session
from kaagaz.api.routers import keycloak as keycloak_router
from kaagaz.db import access as access_sql
from kaagaz.db import keycloak
from kaagaz.db.engine import Database
from kaagaz.settings import Settings
from tests.settings_factory import make_settings

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)


class _NoDatabase:
    """No SQL is expected in these route tests; database calls are replaced at the boundary."""

    engine = cast(Engine, object())

    @contextmanager
    def system(self) -> Iterator[Connection]:
        yield cast(Connection, object())

    @contextmanager
    def user(self, customer_id: str, user_id: str) -> Iterator[Connection]:
        del customer_id, user_id
        yield cast(Connection, object())


def _client(*, settings: Settings | None = None, expose_auth_user: bool = False) -> TestClient:
    configured = settings or make_settings(
        keycloak_enabled=True,
        keycloak_issuer="https://id.example.test/realms/kaagaz",
        keycloak_client_id="kaagaz-api",
        keycloak_client_secret="route-test-confidential-secret",
        keycloak_redirect_uri="https://app.example.test/api/auth/keycloak/callback",
        keycloak_required_acr="1",
        keycloak_flow_encryption_key="distinct-route-test-encryption-key-material",
    )
    app = create_app(configured, db=cast(Database, _NoDatabase()), clock=lambda: NOW)
    if expose_auth_user:

        def auth_user(user: User) -> dict[str, object]:
            return {"id": user.user_id, "authority": user.auth_authority, "totp_enabled": user.totp_enabled}

        app.add_api_route("/test/auth-user", auth_user, methods=["GET"])
    return TestClient(
        app,
        base_url="https://app.example.test",
        client=("198.51.100.77", 50000),
        follow_redirects=False,
    )


def _user(*, role: str, authority: str, totp_enabled: bool) -> CurrentUser:
    return CurrentUser(
        user_id="11111111-1111-4111-8111-111111111111",
        customer_id="22222222-2222-4222-8222-222222222222",
        email="person@example.test",
        display_name="Person",
        role=role,
        customer_name="Customer",
        scope="admin" if role == "admin" else "app",
        session_version=1,
        must_change_password=False,
        totp_enabled=totp_enabled,
        delete_forever_allowed=False,
        seen_at=NOW,
        auth_authority=authority,
    )


def _set_session(
    client: TestClient,
    settings: Settings,
    user: CurrentUser,
    source: str,
    assurance: str | None,
) -> None:
    claims = new_session(
        user_id=user.user_id,
        customer_id=user.customer_id,
        role=user.role,
        scope=user.scope,
        session_version=user.session_version,
        settings=settings,
        now=int(NOW.timestamp()),
        idle_hours=None,
        auth_source=source,
        assurance=assurance,
    )
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(claims, settings.session_secret))


def test_login_callback_state_is_one_use_and_callback_needs_no_csrf_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states: list[str] = []
    destinations: list[str] = []
    consumed = 0
    login_calls = 0
    flow = keycloak.FlowRow(
        nonce="flow-nonce-value-1234567890123456",
        sealed_verifier="sealed-verifier-value-that-is-long-enough",
        purpose="login",
        next_path="/",
        customer_id=None,
        user_id=None,
        session_version=None,
        local_session_id=None,
        action=None,
        started_at=NOW,
    )

    monkeypatch.setattr(
        keycloak,
        "create_flow",
        lambda *args, **kwargs: destinations.append(kwargs["next_path"]),
    )
    monkeypatch.setattr(keycloak_oidc, "open_verifier", lambda *args: "pkce-verifier")

    def authorize(settings: object, *, state: str, nonce: str, verifier: str, max_age: int = 0) -> str:
        del settings, nonce, verifier, max_age
        states.append(state)
        return f"https://id.example.test/authorize?state={state}"

    monkeypatch.setattr(keycloak_oidc, "authorize_url", authorize)

    def consume(conn: Connection, state_hash: bytes, *, now: datetime) -> keycloak.FlowRow | None:
        nonlocal consumed
        del conn, now
        assert state_hash == hashlib.sha256(states[0].encode("ascii")).digest()
        consumed += 1
        return flow if consumed == 1 else None

    monkeypatch.setattr(keycloak, "consume_flow", consume)
    monkeypatch.setattr(keycloak_oidc, "id_token_for", lambda *args: "opaque-test-token")
    monkeypatch.setattr(
        keycloak_oidc,
        "verified_identity",
        lambda *args, **kwargs: keycloak_oidc.Identity(
            issuer="https://id.example.test/realms/kaagaz",
            subject="opaque-subject",
            session_id="opaque-sid",
            auth_time=int(NOW.timestamp()),
            acr="urn:kaagaz:assurance:mfa",
            authentication_provider="local",
        ),
    )

    def login(context: object, consumed_flow: keycloak.FlowRow, identity: object) -> RedirectResponse:
        nonlocal login_calls
        del context, identity
        login_calls += 1
        return RedirectResponse(consumed_flow.next_path, status_code=303)

    monkeypatch.setattr(keycloak_router, "_login", login)
    with _client() as client:
        started = client.get("/api/auth/keycloak/start?next=https%3A%2F%2Fevil.example")
        assert started.status_code == 303
        state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
        assert states == [state]
        assert destinations == ["/"]
        assert client.cookies.get(keycloak_router.STATE_COOKIE) == state

        wrong_issuer = client.get(
            "/api/auth/keycloak/callback",
            params={"state": state, "code": "one-use-code", "iss": "https://wrong.example"},
        )
        assert wrong_issuer.status_code == 400
        assert consumed == 0

        duplicate = client.get(f"/api/auth/keycloak/callback?state={state}&state={state}&code=one-use-code")
        assert duplicate.status_code == 400 and consumed == 0
        malformed = client.get(f"/api/auth/keycloak/callback?state={state}&code=%2")
        assert malformed.status_code == 400 and consumed == 0
        oversized = client.get(
            "/api/auth/keycloak/callback",
            params={"state": state, "code": "x" * 4097},
        )
        assert oversized.status_code == 400 and consumed == 0

        first = client.get(
            "/api/auth/keycloak/callback",
            params={
                "state": state,
                "code": "one-use-code",
                "iss": "https://id.example.test/realms/kaagaz",
            },
        )
        assert first.status_code == 303 and first.headers["location"] == "/"
        assert first.headers["cache-control"] == "no-store"

        client.cookies.set(keycloak_router.STATE_COOKIE, state)
        replay = client.get(
            "/api/auth/keycloak/callback",
            params={
                "state": state,
                "code": "one-use-code",
                "iss": "https://id.example.test/realms/kaagaz",
            },
        )
        assert replay.status_code == 303
        assert replay.headers["location"] == "/login?keycloak_error=expired"
        assert login_calls == 1
        assert consumed == 2


def test_keycloak_login_error_uses_frontend_key_and_keeps_safe_mfa_retry_code() -> None:
    flow = keycloak.FlowRow(
        nonce="nonce",
        sealed_verifier="sealed",
        purpose="login",
        next_path="/",
        customer_id=None,
        user_id=None,
        session_version=None,
        local_session_id=None,
        action=None,
        started_at=NOW,
    )
    refused = keycloak_router._failure(flow, "mfa_verify_again")
    assert refused.headers["location"] == "/login?keycloak_error=mfa_verify_again"
    assert keycloak_router._safe_error("unexpected-provider-detail") == "refused"


def test_invalid_signed_logout_is_rejected_without_csrf_or_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid(*args: object, **kwargs: object) -> object:
        raise keycloak_oidc.OidcError("bad_logout")

    monkeypatch.setattr(keycloak_oidc, "verified_logout", invalid)
    with _client() as client:
        response = client.post(
            "/api/auth/keycloak/backchannel-logout",
            data={"logout_token": "untrusted-token"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_logout"


def test_keycloak_admin_without_local_totp_passes_real_auth_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(keycloak_enabled=True, keycloak_required_acr="1")
    admin = replace(_user(role="admin", authority="keycloak", totp_enabled=False), two_step_required=True)
    client = _client(settings=settings, expose_auth_user=True)
    _set_session(client, settings, admin, "keycloak", "1")
    monkeypatch.setattr(auth, "load_user", lambda *args, **kwargs: admin)
    monkeypatch.setattr(access_sql, "me", lambda conn: (False, [], False, False))
    with client:
        response = client.get("/test/auth-user")
        me = client.get("/api/auth/me").json()
    assert response.status_code == 200
    assert response.json() == {"id": admin.user_id, "authority": "keycloak", "totp_enabled": False}
    assert me["user"]["auth_source"] == "keycloak"
    assert me["user"]["two_step_required"] is False
    assert me["keycloak_enabled"] is True


def test_bound_flows_reject_another_user_or_browser_and_reauth_rejects_an_unknown_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(keycloak_enabled=True, keycloak_required_acr="1")
    legacy = _user(role="member", authority="keycloak", totp_enabled=False)
    claims = new_session(
        user_id=legacy.user_id,
        customer_id=legacy.customer_id,
        role=legacy.role,
        scope=legacy.scope,
        session_version=legacy.session_version,
        settings=settings,
        now=int(NOW.timestamp()),
        idle_hours=None,
        auth_source="keycloak",
        assurance="1",
    )
    token = auth.sign_session(claims, settings.session_secret)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/auth/keycloak/callback",
            "query_string": b"",
            "headers": [(b"cookie", f"{auth.SESSION_COOKIE}={token}".encode("ascii"))],
        }
    )
    client = _client(settings=settings)
    context = cast(FastAPI, client.app).state.kaagaz
    monkeypatch.setattr(keycloak_router, "_load_active", lambda *args: legacy)
    flow = keycloak.FlowRow(
        nonce="nonce",
        sealed_verifier="sealed",
        purpose="reauth",
        next_path="/documents",
        customer_id=legacy.customer_id,
        user_id=legacy.user_id,
        session_version=legacy.session_version,
        local_session_id=claims.session_id,
        action=None,
        started_at=NOW,
    )
    assert keycloak_router._bound_user(request, context, flow)[0] == legacy
    with pytest.raises(ValueError, match="browser session changed"):
        keycloak_router._bound_user(request, context, replace(flow, local_session_id="another-browser"))
    with pytest.raises(ValueError, match="browser session changed"):
        keycloak_router._bound_user(
            request, context, replace(flow, user_id="33333333-3333-4333-8333-333333333333")
        )

    keycloak_admin = _user(role="admin", authority="keycloak", totp_enabled=False)
    bad_action = replace(flow, purpose="reauth", action="unknown", next_path="/sensitive")
    refused = keycloak_router._reauth(
        context, bad_action, cast(keycloak_oidc.Identity, object()), keycloak_admin, claims
    )
    assert refused.headers["location"] == "/sensitive?reauth_error=refused"


def test_session_says_signed_out_with_no_providers_and_whether_keycloak_is_on() -> None:
    assert _client().get("/api/auth/session").json() == {"signed_in": False, "keycloak_enabled": True}


def test_the_start_passes_only_an_allowed_account_action_to_keycloak(monkeypatch: pytest.MonkeyPatch) -> None:
    """Change password and Two-step sign-in open Keycloak's own pages (``kc_action``); no other
    value is passed on, and with none the sign-in is as before."""
    monkeypatch.setattr(keycloak, "create_flow", lambda *args, **kwargs: None)
    monkeypatch.setattr(keycloak_router, "_open_verifier", lambda *args: "pkce-verifier")
    monkeypatch.setattr(
        keycloak_oidc, "authorize_url", lambda *args, **kwargs: "https://id.example.test/auth?state=s"
    )
    with _client() as client:
        plain = client.get("/api/auth/keycloak/start?next=/documents")
        assert plain.headers["location"] == "https://id.example.test/auth?state=s"
        for action in ("UPDATE_PASSWORD", "CONFIGURE_TOTP"):
            started = client.get(f"/api/auth/keycloak/start?action={action}")
            assert started.status_code == 303
            assert parse_qs(urlsplit(started.headers["location"]).query)["kc_action"] == [action]
        for refused in ("VERIFY_EMAIL", "update_password", "UPDATE_PASSWORD%20CONFIGURE_TOTP", ""):
            reply = client.get(f"/api/auth/keycloak/start?action={refused}")
            assert (reply.status_code, reply.json()["error"]["code"]) == (400, "bad_request"), refused


def test_the_logout_url_is_given_to_the_page_and_needs_no_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        keycloak_oidc, "logout_url", lambda settings: "https://id.example.test/logout?client_id=x"
    )
    with _client() as client:
        response = client.get("/api/auth/keycloak/logout-url")
    assert response.status_code == 200
    assert response.json() == {"url": "https://id.example.test/logout?client_id=x"}
    assert response.headers["cache-control"] == "no-store"


def test_the_logout_url_is_not_found_while_company_sign_in_is_off() -> None:
    with _client(settings=make_settings()) as client:
        assert client.get("/api/auth/keycloak/logout-url").status_code == 404


def test_the_logout_url_says_unavailable_when_keycloak_cannot_be_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(settings: object) -> str:
        raise keycloak_oidc.OidcError("provider_unavailable")

    monkeypatch.setattr(keycloak_oidc, "logout_url", down)
    with _client() as client:
        assert client.get("/api/auth/keycloak/logout-url").status_code == 502
