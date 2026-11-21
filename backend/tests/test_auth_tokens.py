"""Session and confirm tokens, the sign-in settings and the sign-in dependencies (PRD 1 A1 to A7).

Pure: no database. The dependency tests build a bare FastAPI app whose
``load_user`` is replaced, so they check the wiring of ``current_user``,
``current_user_allow_password_change`` and ``require_admin_session`` without
PostgreSQL (tests/test_users_db.py covers the query behind ``load_user``).
"""

import base64
import dataclasses
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient

from kaagaz.api import auth, errors
from kaagaz.api.auth import (
    CONFIRM_COOKIE,
    CONFIRM_SECONDS,
    SESSION_COOKIE,
    ConfirmClaims,
    CurrentUser,
    SessionClaims,
    new_session,
    read_confirm,
    read_session,
    session_seconds,
    set_session_cookie,
    sign_confirm,
    sign_session,
)
from kaagaz.api.context import AppContext
from kaagaz.api.ports import AnswerLike
from kaagaz.db.engine import Database, make_engine
from kaagaz.ingestion.ports import BlobNotFound
from kaagaz.search.scope import Scope
from kaagaz.settings import Settings, SettingsError
from tests.fakes import MemoryQueue
from tests.settings_factory import make_settings
from tests.test_api_support import ApiFakeStorage

SECRET = "a-test-secret-that-is-long-enough-000000"
UID = "11111111-1111-4111-8111-111111111111"
CID = "22222222-2222-4222-8222-222222222222"
SID = "33333333-3333-4333-8333-333333333333"  # Changed on purpose (#502, 0076): the session id
NOW = 1_800_000_000
ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _session(**changes: Any) -> SessionClaims:
    fields: dict[str, Any] = {
        "user_id": UID,
        "customer_id": CID,
        "role": "admin",
        "scope": "app",
        "session_version": 3,
        "expires_at": NOW + 3600,
        "session_id": SID,
        "auth_source": "legacy_password",
        "assurance": None,
    }
    return SessionClaims(**{**fields, **changes})


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _payload(token: str) -> Any:
    body = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def _signed(payload: dict[str, Any], purpose: bytes, secret: str = SECRET) -> str:
    """A token signed the way the code signs, for any payload: tests _claims on its own."""
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(secret.encode(), purpose + b"\x00" + body.encode("ascii"), hashlib.sha256).digest()
    return body + "." + _b64url(mac)


def _session_payload(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "uid": UID,
        "cid": CID,
        "role": "admin",
        "scope": "app",
        "ver": 3,
        "src": "legacy_password",
        "acr": None,
    }
    payload["exp"] = NOW + 3600
    payload["sid"] = SID
    payload.update(changes)
    return payload


# --- the session token -------------------------------------------------------------------


def test_session_round_trips() -> None:
    assert read_session(sign_session(_session(), SECRET), SECRET, now=NOW) == _session()


def test_session_payload_is_exactly_the_agreed_json() -> None:
    token = sign_session(_session(scope="admin"), SECRET)
    assert _payload(token) == {
        "uid": UID,
        "cid": CID,
        "role": "admin",
        "scope": "admin",
        "ver": 3,
        "exp": NOW + 3600,
        "sid": SID,  # Changed on purpose (#502, 0076)
        "src": "legacy_password",
        "acr": None,
    }


def test_session_mac_is_over_the_session_purpose_and_the_body() -> None:
    token = sign_session(_session(), SECRET)
    body, signature = token.split(".")
    expected = hmac.new(SECRET.encode(), b"session\x00" + body.encode("ascii"), hashlib.sha256).digest()
    assert signature == _b64url(expected)


def test_session_with_another_secret_is_refused() -> None:
    token = sign_session(_session(), "another-secret-that-is-long-enough-00000")
    assert read_session(token, SECRET, now=NOW) is None


def test_session_expiry_is_exclusive() -> None:
    token = sign_session(_session(expires_at=NOW), SECRET)
    assert read_session(token, SECRET, now=NOW) is None
    assert read_session(token, SECRET, now=NOW - 1) is not None


def test_a_changed_payload_is_refused() -> None:
    body, signature = sign_session(_session(), SECRET).split(".")
    forged = _b64url(json.dumps(_session_payload(scope="admin", ver=4)).encode())
    assert read_session(forged + "." + signature, SECRET, now=NOW) is None
    assert read_session(body + "." + signature, SECRET, now=NOW) is not None


@pytest.mark.parametrize("token", ["", ".", "abc", "abc.", ".abc", "a.b.c", "é.é", "!!!.???"])
def test_garbage_tokens_are_refused(token: str) -> None:
    assert read_session(token, SECRET, now=NOW) is None


def test_an_old_sprint_1_cookie_is_refused() -> None:
    """{uid,cid,role,exp} with the MAC over the body alone: every user signs in once more."""
    body = _b64url(
        json.dumps({"uid": UID, "cid": CID, "role": "admin", "exp": NOW + 3600}, sort_keys=True).encode()
    )
    old = body + "." + _b64url(hmac.new(SECRET.encode(), body.encode("ascii"), hashlib.sha256).digest())
    assert read_session(old, SECRET, now=NOW) is None


@pytest.mark.parametrize("missing", ["uid", "cid", "role", "scope", "ver", "exp", "sid", "src", "acr"])
def test_a_session_missing_any_field_is_refused(missing: str) -> None:
    payload = _session_payload()
    del payload[missing]
    assert read_session(_signed(payload, b"session"), SECRET, now=NOW) is None


def test_the_signed_helper_matches_the_code() -> None:
    """So the refusals below are about the field, not a broken signature."""
    assert read_session(_signed(_session_payload(), b"session"), SECRET, now=NOW) == _session()


def test_a_session_with_an_extra_field_is_refused() -> None:
    assert read_session(_signed(_session_payload(sec="x"), b"session"), SECRET, now=NOW) is None


@pytest.mark.parametrize(
    ("source", "assurance"),
    [("unknown", None), ("keycloak", None), ("legacy_mfa", "urn:assurance:mfa")],
)
def test_session_auth_source_and_assurance_must_match(source: str, assurance: str | None) -> None:
    assert (
        read_session(_signed(_session_payload(src=source, acr=assurance), b"session"), SECRET, now=NOW)
        is None
    )


@pytest.mark.parametrize("scope", ["root", "", "APP", "Admin", None, 1, ["app"]])
def test_an_unknown_scope_is_refused(scope: object) -> None:
    assert read_session(_signed(_session_payload(scope=scope), b"session"), SECRET, now=NOW) is None


@pytest.mark.parametrize("role", ["member", "reviewer"])
def test_an_admin_scope_needs_the_admin_role(role: str) -> None:
    assert (
        read_session(_signed(_session_payload(role=role, scope="admin"), b"session"), SECRET, now=NOW) is None
    )
    assert read_session(_signed(_session_payload(role=role, scope="app"), b"session"), SECRET, now=NOW) == (
        _session(role=role)
    )


def test_an_admin_scope_with_the_admin_role_is_accepted() -> None:
    token = _signed(_session_payload(scope="admin"), b"session")
    assert read_session(token, SECRET, now=NOW) == _session(scope="admin")


@pytest.mark.parametrize("ver", [True, False, "3", 3.0, 0, -1, None, [3]])
def test_ver_must_be_a_positive_int_and_not_a_bool(ver: object) -> None:
    assert read_session(_signed(_session_payload(ver=ver), b"session"), SECRET, now=NOW) is None


@pytest.mark.parametrize("exp", [True, str(NOW + 3600), float(NOW + 3600), None])
def test_exp_must_be_an_int(exp: object) -> None:
    assert read_session(_signed(_session_payload(exp=exp), b"session"), SECRET, now=NOW) is None


@pytest.mark.parametrize(
    "changes", [{"uid": "not-a-uuid"}, {"cid": "not-a-uuid"}, {"uid": 1}, {"role": "owner"}]
)
def test_bad_ids_or_roles_are_refused(changes: dict[str, Any]) -> None:
    assert read_session(_signed(_session_payload(**changes), b"session"), SECRET, now=NOW) is None


@pytest.mark.parametrize("payload", [[], "text", 7, None])
def test_a_payload_that_is_not_an_object_is_refused(payload: object) -> None:
    body = _b64url(json.dumps(payload).encode())
    mac = hmac.new(SECRET.encode(), b"session\x00" + body.encode("ascii"), hashlib.sha256).digest()
    assert read_session(body + "." + _b64url(mac), SECRET, now=NOW) is None


# --- lifetimes and the cookie ------------------------------------------------------------------


def test_session_seconds_by_scope() -> None:
    settings = make_settings(session_hours=12, admin_session_minutes=60)
    assert session_seconds("app", settings) == 12 * 3600
    assert session_seconds("admin", settings) == 60 * 60
    with pytest.raises(ValueError):
        session_seconds("root", settings)


@pytest.mark.parametrize(("scope", "seconds"), [("app", 12 * 3600), ("admin", 60 * 60)])
def test_new_session_expires_after_its_scopes_lifetime(scope: str, seconds: int) -> None:
    def started() -> SessionClaims:
        return new_session(
            user_id=UID,
            customer_id=CID,
            role="admin",
            scope=scope,
            session_version=3,
            settings=make_settings(),
            now=NOW,
        )

    claims, other = started(), started()
    assert claims == _session(scope=scope, expires_at=NOW + seconds, session_id=claims.session_id)
    # Changed on purpose (#502, 0076): each session has its own random id, so logout ends one.
    assert str(uuid.UUID(claims.session_id)) == claims.session_id != other.session_id


@pytest.mark.parametrize(("scope", "seconds"), [("app", 12 * 3600), ("admin", 60 * 60)])
def test_cookie_max_age_is_the_tokens_own_lifetime(scope: str, seconds: int) -> None:
    settings = make_settings()
    claims = _session(scope=scope, expires_at=NOW + seconds)
    response = Response()
    set_session_cookie(response, claims, settings, now=NOW)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert f"Max-Age={seconds}" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Secure" not in cookie
    token = cookie.split(";")[0].split("=", 1)[1]
    assert read_session(token, settings.session_secret, now=NOW) == claims


def test_cookie_max_age_is_what_is_left_of_a_re_signed_session() -> None:
    response = Response()
    set_session_cookie(response, _session(expires_at=NOW + 1234), make_settings(), now=NOW)
    assert "Max-Age=1234" in response.headers["set-cookie"]


def test_cookie_is_secure_when_configured() -> None:
    response = Response()
    set_session_cookie(response, _session(), make_settings(cookie_secure=True), now=NOW)
    assert "Secure" in response.headers["set-cookie"]


def test_an_already_expired_session_is_not_set_as_a_cookie() -> None:
    with pytest.raises(ValueError):
        set_session_cookie(Response(), _session(expires_at=NOW), make_settings(), now=NOW)


# --- the confirm token (council 4 Oct 2026) ----------------------------------------------------

# Changed on purpose (A4, 7 Nov 2026): the browser session too.
CONFIRM = {"uid": UID, "cid": CID, "ver": 3, "exp": NOW + CONFIRM_SECONDS, "sid": SID}


def test_confirm_round_trips_and_lasts_5_minutes() -> None:
    claims = ConfirmClaims(
        user_id=UID, customer_id=CID, session_version=3, expires_at=NOW + 300, session_id=SID
    )
    token = sign_confirm(claims, SECRET)
    assert CONFIRM_SECONDS == 300
    assert _payload(token) == CONFIRM
    assert read_confirm(token, SECRET, now=NOW + 299) == claims
    assert read_confirm(token, SECRET, now=NOW + 300) is None
    body, signature = token.split(".")
    expected = hmac.new(SECRET.encode(), b"confirm\x00" + body.encode("ascii"), hashlib.sha256).digest()
    assert signature == _b64url(expected)


def test_a_confirm_token_is_never_a_session_and_the_reverse() -> None:
    confirm = _signed(CONFIRM, b"confirm")
    assert read_session(confirm, SECRET, now=NOW) is None
    assert read_confirm(_signed(CONFIRM, b"session"), SECRET, now=NOW) is None
    assert read_confirm(sign_session(_session(), SECRET), SECRET, now=NOW) is None


def test_a_confirm_token_with_a_missing_bad_or_extra_field_is_refused() -> None:
    payloads = [{key: value for key, value in CONFIRM.items() if key != missing} for missing in CONFIRM]
    bad = ({"ver": True}, {"ver": 0}, {"exp": "1"}, {"uid": "x"}, {"sid": "x"}, {"role": "member"})
    for payload in payloads + [{**CONFIRM, **changes} for changes in bad]:
        assert read_confirm(_signed(payload, b"confirm"), SECRET, now=NOW) is None, payload


# --- the settings ------------------------------------------------------------------------------------


def _example_env(**changes: str | None) -> dict[str, str]:
    """.env.example as a mapping, with a usable SESSION_SECRET and TOTP_ENCRYPTION_KEY
    (both placeholders are too short on purpose)."""
    env: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            env[name.strip()] = value.strip()
    env["SESSION_SECRET"] = "a-laptop-session-secret-for-this-test-0000"
    env["TOTP_ENCRYPTION_KEY"] = "a-laptop-totp-key-for-this-test-not-the-session-0000"
    for changed, new_value in changes.items():
        if new_value is None:
            env.pop(changed, None)
        else:
            env[changed] = new_value
    return env


def test_the_shipped_placeholder_keys_stop_start_up() -> None:
    """Copying .env.example and setting only SESSION_SECRET must not start with a
    publicly known TOTP key: the placeholder is too short on purpose."""
    env: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, value = line.partition("=")
            env.setdefault(name.strip(), value.strip())
    env["SESSION_SECRET"] = "a-laptop-session-secret-for-this-test-0000"
    with pytest.raises(SettingsError, match="TOTP_ENCRYPTION_KEY"):
        Settings.from_env(env)


def test_env_example_lists_the_sign_in_settings() -> None:
    settings = Settings.from_env(_example_env())
    assert settings.login_max_failures == 5
    assert settings.login_lock_minutes == 15
    assert settings.admin_session_minutes == 60
    assert len(settings.totp_encryption_key) >= 32
    assert settings.totp_encryption_key != settings.session_secret


def test_keycloak_defaults_match_stock_realm_and_worker_mfa_policy_is_optional() -> None:
    base = Settings.from_env(_example_env())
    assert base.customer_mfa_required is False
    settings = Settings.from_env(
        _example_env(
            KEYCLOAK_ENABLED="true",
            KEYCLOAK_ISSUER="http://keycloak.localhost:8081/realms/kaagaz-local",
            KEYCLOAK_CLIENT_ID="kaagaz-web",
            KEYCLOAK_CLIENT_SECRET="a-valid-test-keycloak-client-secret-000",
            KEYCLOAK_ADMIN_CLIENT_ID="kaagaz-provisioner",
            KEYCLOAK_ADMIN_CLIENT_SECRET="a-valid-test-keycloak-admin-secret-0000",
            KEYCLOAK_REDIRECT_URI="http://localhost:3000/api/auth/keycloak/callback",
            KEYCLOAK_FLOW_ENCRYPTION_KEY="a-distinct-keycloak-flow-key-for-tests-000",
            CUSTOMER_MFA_REQUIRED="true",
        )
    )
    assert settings.keycloak_required_acr == "1"
    assert settings.customer_mfa_required is True


def _keycloak_env(**changes: str | None) -> dict[str, str]:
    values: dict[str, str | None] = {
        "KEYCLOAK_ENABLED": "true",
        "KEYCLOAK_ISSUER": "http://keycloak.localhost:8081/realms/kaagaz-local",
        "KEYCLOAK_CLIENT_ID": "kaagaz-web",
        "KEYCLOAK_CLIENT_SECRET": "a-valid-test-keycloak-client-secret-000",
        "KEYCLOAK_REDIRECT_URI": "http://localhost:3000/api/auth/keycloak/callback",
        "KEYCLOAK_FLOW_ENCRYPTION_KEY": "a-distinct-keycloak-flow-key-for-tests-000",
        "KEYCLOAK_ADMIN_CLIENT_ID": "kaagaz-provisioner",
        "KEYCLOAK_ADMIN_CLIENT_SECRET": "a-valid-test-keycloak-admin-secret-0000",
        **changes,
    }
    return _example_env(**values)


def test_keycloak_admin_client_is_read_and_its_secret_is_hidden() -> None:
    settings = Settings.from_env(_keycloak_env())
    assert settings.keycloak_admin_client_id == "kaagaz-provisioner"
    assert settings.keycloak_admin_client_secret == "a-valid-test-keycloak-admin-secret-0000"
    assert "keycloak-admin-secret" not in repr(settings)


@pytest.mark.parametrize("name", ["KEYCLOAK_ADMIN_CLIENT_ID", "KEYCLOAK_ADMIN_CLIENT_SECRET"])
def test_keycloak_admin_client_is_required_when_keycloak_is_on(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(_keycloak_env(**{name: None}))


def test_a_short_keycloak_admin_secret_is_refused_without_the_value() -> None:
    short = "only-31-characters-long-0000000"
    with pytest.raises(SettingsError, match="KEYCLOAK_ADMIN_CLIENT_SECRET") as caught:
        Settings.from_env(_keycloak_env(KEYCLOAK_ADMIN_CLIENT_SECRET=short))
    assert short not in str(caught.value)


@pytest.mark.parametrize(
    "name", ["TOTP_ENCRYPTION_KEY", "LOGIN_MAX_FAILURES", "LOGIN_LOCK_MINUTES", "ADMIN_SESSION_MINUTES"]
)
def test_a_missing_sign_in_setting_stops_with_its_name(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(_example_env(**{name: None}))


def test_a_short_totp_key_is_refused_by_name_without_the_value() -> None:
    short = "only-31-characters-long-0000000"
    assert len(short) == 31
    with pytest.raises(SettingsError, match="TOTP_ENCRYPTION_KEY") as caught:
        Settings.from_env(_example_env(TOTP_ENCRYPTION_KEY=short))
    assert short not in str(caught.value)


def test_the_totp_key_must_differ_from_the_session_secret() -> None:
    same = "a-laptop-session-secret-for-this-test-0000"
    with pytest.raises(SettingsError, match="TOTP_ENCRYPTION_KEY") as caught:
        Settings.from_env(_example_env(SESSION_SECRET=same, TOTP_ENCRYPTION_KEY=same))
    assert same not in str(caught.value)
    assert "SESSION_SECRET" in str(caught.value)


@pytest.mark.parametrize("name", ["LOGIN_MAX_FAILURES", "LOGIN_LOCK_MINUTES", "ADMIN_SESSION_MINUTES"])
@pytest.mark.parametrize("value", ["0", "-1", "five"])
def test_sign_in_counts_must_be_at_least_one(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(_example_env(**{name: value}))


def test_the_test_settings_keep_the_two_keys_apart() -> None:
    settings = make_settings()
    assert len(settings.totp_encryption_key) >= 32
    assert settings.totp_encryption_key != settings.session_secret


# --- the dependencies: current_user, the password-change gate, the admin session ----------------------


class _Storage(ApiFakeStorage):
    def get_page_image(self, customer_id: str, document_id: str, page_no: int) -> bytes:
        raise BlobNotFound(document_id)

    def ping(self) -> None:
        return None


class _Queue(MemoryQueue):
    def ping(self) -> None:
        return None

    def depth(self) -> dict[str, int]:
        return {"express": 0, "main": 0}


class _NoAnswers:
    def answer(
        self,
        customer_id: str,
        question: str,
        *,
        scope: Scope,
        history: Any,
        user_id: str,
        memory: Any,
        choice: Any = None,
        pins: Any = (),
        pending: Any = None,
        clarify: Any = True,
        cited: Any = (),
        last_cited: Any = (),
    ) -> AnswerLike:
        raise AssertionError("not used here")

    def remember(self, turns: Any, memory: Any, cited: Any) -> Any:
        raise AssertionError("not used here")


def _user(**changes: Any) -> CurrentUser:
    fields: dict[str, Any] = {
        "user_id": UID,
        "customer_id": CID,
        "email": "admin@example.test",
        "display_name": "Admin",
        "role": "admin",
        "customer_name": "Sharma Traders",
        "scope": "app",
        "session_version": 3,
        "must_change_password": False,
        "totp_enabled": True,  # every admin has two-step sign-in (0035)
        "delete_forever_allowed": False,
    }
    return CurrentUser(**{**fields, **changes})


Lookups = list[tuple[str, str, str]]


def _client(
    monkeypatch: pytest.MonkeyPatch, stored: CurrentUser | None, seen: Lookups | None = None
) -> TestClient:
    """A bare app whose database lookup returns ``stored`` (with the session's scope).
    Each lookup's (customer_id, user_id, scope) is added to ``seen``."""
    lookups: Lookups = [] if seen is None else seen

    def load_user(
        db: Database, customer_id: str, user_id: str, *, scope: str, session_id: str | None = None
    ) -> CurrentUser | None:
        lookups.append((customer_id, user_id, scope))
        if stored is None:
            return None
        fields = {field: getattr(stored, field) for field in CurrentUser.__dataclass_fields__}
        return CurrentUser(**{**fields, "scope": scope, "auth_authority": "keycloak"})

    monkeypatch.setattr(auth, "load_user", load_user)
    settings = make_settings()
    app = FastAPI()
    errors.install(app)
    app.state.kaagaz = AppContext(
        settings=settings,
        db=Database(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app"),
        storage=_Storage(),
        queue=_Queue(),
        answers=_NoAnswers(),
        clock=lambda: datetime.fromtimestamp(NOW, UTC),
    )

    @app.get("/any")
    def any_route(user: auth.User) -> dict[str, Any]:
        return {"id": user.user_id, "scope": user.scope, "ver": user.session_version}

    @app.get("/admin-area", dependencies=[Depends(auth.require_admin_session)])
    def admin_route() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/admin-param")
    def admin_param(user: auth.AdminSession) -> dict[str, str]:
        return {"id": user.user_id}

    @app.delete("/api/documents/forever")
    def forever(user: auth.CanDeleteForever) -> dict[str, str]:
        return {"id": user.user_id}

    return TestClient(app)


def _cookie(client: TestClient, claims: SessionClaims) -> None:
    """The session cookie of a Keycloak sign-in (the only one: any other source is refused)."""
    live = dataclasses.replace(
        claims, auth_source="keycloak", assurance=make_settings().keycloak_required_acr
    )
    client.cookies.set(SESSION_COOKIE, sign_session(live, make_settings().session_secret))


def test_current_user_passes_a_matching_session_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Lookups = []
    client = _client(monkeypatch, _user(role="member"), seen)
    _cookie(client, _session(role="member", expires_at=NOW + 60))
    reply = client.get("/any")
    assert reply.status_code == 200
    assert reply.json() == {"id": UID, "scope": "app", "ver": 3}
    assert seen == [(CID, UID, "app")]


def test_no_cookie_is_401_without_a_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Lookups = []
    client = _client(monkeypatch, _user(), seen)
    reply = client.get("/any")
    assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")
    assert seen == []


@pytest.mark.parametrize("stored_ver", [2, 4])
def test_a_session_version_that_no_longer_matches_is_401(
    monkeypatch: pytest.MonkeyPatch, stored_ver: int
) -> None:
    client = _client(monkeypatch, _user(session_version=stored_ver))
    _cookie(client, _session(expires_at=NOW + 60))
    for path in ("/any", "/admin-area"):
        reply = client.get(path)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), path


def test_a_changed_role_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _user(role="member"))
    _cookie(client, _session(expires_at=NOW + 60))
    assert client.get("/any").status_code == 401


def test_a_user_that_is_gone_or_disabled_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, None)
    _cookie(client, _session(expires_at=NOW + 60))
    assert client.get("/any").status_code == 401


def test_an_admins_app_session_is_signed_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """One sign-in (16 Oct 2026): every admin session has scope "admin". An admin's app
    session from before rested on the password alone, so it ends, on every route."""
    client = _client(monkeypatch, _user())
    _cookie(client, _session(scope="app", expires_at=NOW + 60))
    for path in ("/any", "/admin-area", "/admin-param"):
        reply = client.get(path)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), path


def test_an_admin_session_opens_admin_and_app_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _user())
    _cookie(client, _session(scope="admin", expires_at=NOW + 60))
    assert client.get("/admin-area").json() == {"ok": "yes"}
    assert client.get("/admin-param").json() == {"id": UID}
    assert client.get("/any").json()["scope"] == "admin"


def test_require_admin_session_refuses_a_non_admin_role_even_with_an_admin_scope() -> None:
    with pytest.raises(errors.ApiError) as caught:
        auth.require_admin_session(_user(role="reviewer", scope="admin"))
    assert (caught.value.status, caught.value.code) == (403, "admin_session_required")
    assert auth.require_admin_session(_user(scope="admin")) == _user(scope="admin")


def _forever(client: TestClient, confirm: dict[str, Any] | None = None) -> tuple[int, str | None]:
    if confirm is not None:
        client.cookies.set(CONFIRM_COOKIE, _signed(confirm, b"confirm", make_settings().session_secret))
    reply = client.delete("/api/documents/forever")
    return reply.status_code, reply.json().get("error", {}).get("code")


@pytest.mark.parametrize(
    ("stored", "scope", "expected"),
    [
        # A grant never counts for an admin: an admin's app session ends (16 Oct 2026).
        ({"delete_forever_allowed": True}, "app", (401, "not_signed_in")),
        # Changed on purpose (21 Nov 2026): every admin needs the company sign-in's token, with or
        # without a local two-step code (there is none: Keycloak asks for it at sign-in).
        ({"totp_enabled": False}, "admin", (200, None)),
        ({}, "admin", (200, None)),
        ({"role": "member"}, "app", (403, "forbidden")),
        ({"role": "reviewer", "delete_forever_allowed": True}, "app", (200, None)),
    ],
)
def test_who_may_delete_forever(
    monkeypatch: pytest.MonkeyPatch, stored: dict[str, Any], scope: str, expected: tuple[int, str | None]
) -> None:
    client = _client(monkeypatch, _user(**stored))
    _cookie(client, _session(role=stored.get("role", "admin"), scope=scope, expires_at=NOW + 60))
    assert _forever(client, CONFIRM) == expected


def test_a_granted_member_needs_their_own_fresh_confirm_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _user(role="member", delete_forever_allowed=True))
    _cookie(client, _session(role="member", expires_at=NOW + 60))
    assert _forever(client) == (403, "keycloak_reauth_required")  # no company sign-in yet
    for stale in ({"exp": NOW}, {"uid": CID}, {"ver": 2}):  # expired, another user's, an older session's
        assert _forever(client, {**CONFIRM, **stale}) == (403, "keycloak_reauth_required")
    assert _forever(client, CONFIRM) == (200, None)


def test_keycloak_admin_url_is_optional_checked_and_kept_without_a_trailing_slash() -> None:
    assert Settings.from_env(_keycloak_env()).keycloak_admin_url is None
    assert Settings.from_env(_keycloak_env(KEYCLOAK_ADMIN_URL="")).keycloak_admin_url is None
    ok = Settings.from_env(_keycloak_env(KEYCLOAK_ADMIN_URL="http://keycloak:8080/kc/"))
    assert ok.keycloak_admin_url == "http://keycloak:8080/kc"
    assert Settings.from_env(
        _keycloak_env(KEYCLOAK_ADMIN_URL="https://id.example.test/kc")
    ).keycloak_admin_url
    for bad in (
        "http://evil.example/kc",
        "http://keycloak.evil.example/kc",
        "http://u:p@keycloak:8080/kc",
        "http://keycloak:8080/kc?x=1",
        "http://keycloak:8080/kc#f",
    ):
        with pytest.raises(SettingsError, match="KEYCLOAK_ADMIN_URL"):
            Settings.from_env(_keycloak_env(KEYCLOAK_ADMIN_URL=bad))
    off = Settings.from_env(_example_env(KEYCLOAK_ADMIN_URL="http://evil.example/kc"))  # off: not read
    assert off.keycloak_admin_url is None
