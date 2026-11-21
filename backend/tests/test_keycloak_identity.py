"""Pure identity, SQL-call and strict OIDC contracts for the Keycloak integration."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import Connection

from kaagaz.api import keycloak_oidc
from kaagaz.api.auth import CurrentUser
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.auth import require_proof
from kaagaz.db import keycloak, whatsapp
from kaagaz.db.whatsapp import Linked
from kaagaz.settings import Settings, SettingsError, _https_url
from tests.settings_factory import make_settings

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


def test_binding_keeps_issuer_and_subject_exact() -> None:
    keycloak.validate_binding("https://id.example.test/realms/Kaagaz", "CaseSensitiveSubject")


@pytest.mark.parametrize(
    ("issuer", "subject"),
    [
        ("", "subject"),
        (" https://id.example.test", "subject"),
        ("https://id.example.test", ""),
        ("https://id.example.test", "subject\nvalue"),
        ("x" * 2049, "subject"),
        ("https://id.example.test", "x" * 256),
    ],
)
def test_binding_rejects_empty_untrimmed_control_or_overlong_values(issuer: str, subject: str) -> None:
    with pytest.raises(ValueError):
        keycloak.validate_binding(issuer, subject)


def test_lookup_calls_only_the_exact_issuer_subject_function() -> None:
    connection = cast(Connection, Mock(spec=Connection))
    execute = cast(Mock, connection.execute)
    execute.return_value.mappings.return_value.first.return_value = None

    assert keycloak.lookup(connection, "https://id.example.test/realm", "opaque-subject") is None

    statement, parameters = execute.call_args.args
    assert "kaagaz_keycloak_lookup(:issuer, :subject)" in str(statement)
    assert parameters == {"issuer": "https://id.example.test/realm", "subject": "opaque-subject"}


def test_whatsapp_channel_check_passes_coexistence_mfa_guard_to_sql() -> None:
    connection = cast(Connection, Mock(spec=Connection))
    execute = cast(Mock, connection.execute)
    execute.return_value.first.return_value = ("sealed-number", "not_allowed")

    result = whatsapp.check(
        connection,
        Linked(
            customer_id="22222222-2222-4222-8222-222222222222",
            user_id="11111111-1111-4111-8111-111111111111",
            link_id="33333333-3333-4333-8333-333333333333",
        ),
        keycloak_enabled=True,
    )

    assert result.refused == "not_allowed"
    statement, parameters = execute.call_args.args
    assert ":keycloak_enabled" in str(statement)
    assert parameters["keycloak_enabled"] is True


def test_finalize_call_has_no_caller_selected_user_or_customer() -> None:
    connection = cast(Connection, Mock(spec=Connection))
    execute = cast(Mock, connection.execute)
    execute.return_value.scalar_one.return_value = 2

    assert (
        keycloak.finalize_link(
            connection,
            "https://id.example.test/realm",
            "opaque-subject",
            now=NOW,
            expected_session_version=1,
        )
        == 2
    )

    statement, parameters = execute.call_args.args
    assert "kaagaz_keycloak_finalize_link(:issuer, :subject, :now, :version)" in str(statement)
    assert parameters == {
        "issuer": "https://id.example.test/realm",
        "subject": "opaque-subject",
        "now": NOW,
        "version": 1,
    }


ISSUER = "https://id.example.test/realms/kaagaz"
CLIENT_ID = "kaagaz-api"
ACR = "1"
PROVIDER_CLAIM = "authentication_provider"
OIDC_NOW = 1_800_000_000


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "keycloak_enabled": True,
        "keycloak_issuer": ISSUER,
        "keycloak_client_id": CLIENT_ID,
        "keycloak_client_secret": "server-side-confidential-secret",
        "keycloak_redirect_uri": "https://app.example.test/api/auth/keycloak/callback",
        "keycloak_required_acr": ACR,
        "keycloak_flow_encryption_key": "a-distinct-keycloak-flow-encryption-secret-value",
    }
    values.update(overrides)
    return make_settings(**values)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private: rsa.RSAPrivateKey, kid: str) -> dict[str, str]:
    numbers = private.public_key().public_numbers()
    size = (numbers.n.bit_length() + 7) // 8
    return {
        "kid": kid,
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(numbers.n.to_bytes(size, "big")),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def _token(
    private: rsa.RSAPrivateKey,
    claims: dict[str, object],
    *,
    kid: str = "key-1",
    typ: str = "JWT",
) -> str:
    header = _b64url(json.dumps({"alg": "RS256", "kid": kid, "typ": typ}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signed = f"{header}.{payload}".encode("ascii")
    signature = private.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def _discovery() -> dict[str, str]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
        "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
        "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
        "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
    }


def _install_fake_provider(
    monkeypatch: pytest.MonkeyPatch, private: rsa.RSAPrivateKey, kid: str = "key-1"
) -> None:
    metadata_url = f"{ISSUER}/.well-known/openid-configuration"
    jwks_url = f"{ISSUER}/protocol/openid-connect/certs"

    def fake(url: str, *, fresh: bool = False) -> object:
        if url == metadata_url:
            return _discovery()
        if url == jwks_url:
            return {"keys": [_jwk(private, kid)]}
        raise AssertionError("OIDC client requested an unconfigured URL")

    monkeypatch.setattr(keycloak_oidc, "_cached_json", fake)


def _identity_claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "azp": CLIENT_ID,
        "iat": OIDC_NOW,
        "exp": OIDC_NOW + 60,
        "auth_time": OIDC_NOW,
        "nonce": "flow-nonce",
        "sub": "opaque-subject",
        "sid": "opaque-keycloak-session",
        "acr": ACR,
        "amr": ["pwd", "otp"],
    }
    claims.update(overrides)
    return claims


def test_authorization_uses_pkce_fresh_login_and_requested_mfa_acr(monkeypatch: pytest.MonkeyPatch) -> None:
    private = _key()
    _install_fake_provider(monkeypatch, private)
    verifier = "an-opaque-pkce-verifier-value"
    parameters = parse_qs(
        urlsplit(
            keycloak_oidc.authorize_url(
                _settings(), state="random-state", nonce="random-nonce", verifier=verifier
            )
        ).query
    )
    assert parameters["code_challenge_method"] == ["S256"]
    assert parameters["code_challenge"] == [_b64url(hashlib.sha256(verifier.encode()).digest())]
    assert parameters["acr_values"] == [ACR]
    assert parameters["max_age"] == ["0"]
    assert parameters["prompt"] == ["login"]
    assert "response_mode" not in parameters


@pytest.mark.parametrize(
    "override",
    [
        {"keycloak_issuer": "http://id.example.test/realms/kaagaz"},
        {"keycloak_issuer": "https://id.example.test:bad/realms/kaagaz"},
        {"keycloak_redirect_uri": "https://app.example.test/other/callback"},
        {"keycloak_redirect_uri": "http://outside.example.test/api/auth/keycloak/callback"},
    ],
)
def test_untrusted_or_inexact_keycloak_endpoints_fail_before_discovery(override: dict[str, str]) -> None:
    with pytest.raises(keycloak_oidc.OidcError, match="not_configured"):
        keycloak_oidc.authorize_url(_settings(**override), state="state", nonce="nonce", verifier="verifier")


def test_keycloak_url_settings_reject_malformed_ports() -> None:
    with pytest.raises(SettingsError, match="KEYCLOAK_ISSUER"):
        _https_url("https://id.example.test:70000/realms/kaagaz", "KEYCLOAK_ISSUER")


def test_compose_alias_is_http_only_for_the_exact_keycloak_issuer() -> None:
    issuer = "http://keycloak.localhost:8081/realms/kaagaz-local"
    assert _https_url(issuer, "KEYCLOAK_ISSUER") == issuer
    with pytest.raises(SettingsError, match="APP_URL"):
        _https_url("http://keycloak.localhost:8081", "APP_URL", "")
    keycloak_oidc._configured(_settings(keycloak_issuer=issuer))


@pytest.mark.parametrize(
    ("updates", "error_code"),
    [
        ({"iss": "https://wrong.example/realm"}, "bad_token"),
        ({"aud": "another-client"}, "bad_token"),
        ({"azp": "another-client"}, "bad_token"),
        ({"acr": "urn:kaagaz:assurance:password"}, "assurance_required"),
        ({"exp": OIDC_NOW - 61}, "expired"),
        ({"iat": OIDC_NOW + 61}, "expired"),
        ({"sub": None}, "bad_token"),
    ],
)
def test_signed_id_token_rejects_wrong_identity_and_assurance_claims(
    monkeypatch: pytest.MonkeyPatch, updates: dict[str, object], error_code: str
) -> None:
    private = _key()
    _install_fake_provider(monkeypatch, private)
    token = _token(private, _identity_claims(**updates))
    with pytest.raises(keycloak_oidc.OidcError, match=error_code):
        keycloak_oidc.verified_identity(
            _settings(), token, nonce="flow-nonce", now=OIDC_NOW, started_at=OIDC_NOW
        )


def test_confidential_code_exchange_keeps_secret_in_basic_auth_only(monkeypatch: pytest.MonkeyPatch) -> None:
    private = _key()
    _install_fake_provider(monkeypatch, private)
    captured: dict[str, object] = {}

    def fake_fetch(url: str, body: bytes | None, *, authorization: str | None = None) -> object:
        captured.update(url=url, body=body, authorization=authorization)
        return {"id_token": "signed-id-token"}

    monkeypatch.setattr(keycloak_oidc, "_fetch_json", fake_fetch)
    assert keycloak_oidc.id_token_for(_settings(), "one-use-code", "pkce-verifier") == "signed-id-token"
    body = cast(bytes, captured["body"]).decode("ascii")
    assert captured["authorization"] == "Basic " + base64.b64encode(
        b"kaagaz-api:server-side-confidential-secret"
    ).decode("ascii")
    assert "server-side-confidential-secret" not in str(captured["url"])
    assert "server-side-confidential-secret" not in body and CLIENT_ID not in body
    assert "code_verifier=pkce-verifier" in body


def test_rs256_identity_accepts_rotated_key_and_uses_amr_despite_a_stale_broker_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = _key()
    previous = _key()
    metadata_url = f"{ISSUER}/.well-known/openid-configuration"
    jwks_url = f"{ISSUER}/protocol/openid-connect/certs"
    refreshes: list[bool] = []

    def fake(url: str, *, fresh: bool = False) -> object:
        if url == metadata_url:
            return _discovery()
        if url == jwks_url:
            refreshes.append(fresh)
            return {"keys": [_jwk(private if fresh else previous, "rotated-key" if fresh else "old-key")]}
        raise AssertionError("OIDC client requested an unconfigured URL")

    monkeypatch.setattr(keycloak_oidc, "_cached_json", fake)
    token = _token(
        private,
        _identity_claims(
            amr=["pwd", "otp"], email="  Person@Example.Test ", email_verified=True, hd="Example.Test"
        ),
        kid="rotated-key",
    )
    identity = keycloak_oidc.verified_identity(
        _settings(), token, nonce="flow-nonce", now=OIDC_NOW, started_at=OIDC_NOW
    )
    assert identity.subject == "opaque-subject"
    assert identity.session_id == "opaque-keycloak-session"
    assert identity.authentication_provider == "local"
    assert refreshes == [False, True]


@pytest.mark.parametrize("value", ["google", "company-password", "local", ["google"], 1, False])
def test_a_configured_provider_claim_with_any_value_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    private = _key()
    _install_fake_provider(monkeypatch, private)
    token = _token(private, _identity_claims(**{PROVIDER_CLAIM: value}))
    with pytest.raises(keycloak_oidc.OidcError, match="organization_provider_policy_unconfigured"):
        keycloak_oidc.verified_identity(
            _settings(), token, nonce="flow-nonce", now=OIDC_NOW, started_at=OIDC_NOW
        )


@pytest.mark.parametrize("claims", [{}, {PROVIDER_CLAIM: ""}, {PROVIDER_CLAIM: None}])
def test_an_absent_or_empty_provider_claim_is_the_local_password_sign_in(
    monkeypatch: pytest.MonkeyPatch, claims: dict[str, object]
) -> None:
    private = _key()
    _install_fake_provider(monkeypatch, private)
    token = _token(private, _identity_claims(**claims))
    identity = keycloak_oidc.verified_identity(
        _settings(), token, nonce="flow-nonce", now=OIDC_NOW, started_at=OIDC_NOW
    )
    assert identity.authentication_provider == "local"


@pytest.mark.parametrize("amr", [["pwd"], ["otp"], [], None, "pwd otp"])
def test_identity_requires_executed_password_and_otp(monkeypatch: pytest.MonkeyPatch, amr: object) -> None:
    private = _key()
    _install_fake_provider(monkeypatch, private)
    token = _token(private, _identity_claims(amr=amr))
    with pytest.raises(keycloak_oidc.OidcError, match="mfa_verify_again"):
        keycloak_oidc.verified_identity(
            _settings(), token, nonce="flow-nonce", now=OIDC_NOW, started_at=OIDC_NOW
        )


def test_identity_requires_auth_time_from_this_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    private = _key()
    _install_fake_provider(monkeypatch, private)
    token = _token(private, _identity_claims(auth_time=OIDC_NOW - 120))
    with pytest.raises(keycloak_oidc.OidcError, match="stale_authentication"):
        keycloak_oidc.verified_identity(
            _settings(), token, nonce="flow-nonce", now=OIDC_NOW, started_at=OIDC_NOW - 30
        )


@pytest.mark.parametrize(
    "events",
    [
        None,
        {},
        {"http://schemas.openid.net/event/backchannel-logout": None},
        {"http://schemas.openid.net/event/backchannel-logout": {"unexpected": True}},
    ],
)
def test_signed_logout_requires_the_exact_present_event_object(
    monkeypatch: pytest.MonkeyPatch, events: object
) -> None:
    private = _key()
    _install_fake_provider(monkeypatch, private)
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "iat": OIDC_NOW,
        "exp": OIDC_NOW + 60,
        "sub": "opaque-subject",
        "sid": "opaque-keycloak-session",
        "jti": "one-time-logout-id",
        "events": events,
    }
    token = _token(private, claims, typ="logout+jwt")
    with pytest.raises(keycloak_oidc.OidcError, match="bad_logout"):
        keycloak_oidc.verified_logout(_settings(), token, now=OIDC_NOW)


def test_signed_logout_rejects_nonce_and_accepts_valid_event(monkeypatch: pytest.MonkeyPatch) -> None:
    private = _key()
    _install_fake_provider(monkeypatch, private)
    event: dict[str, dict[str, object]] = {"http://schemas.openid.net/event/backchannel-logout": {}}
    valid = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "iat": OIDC_NOW,
        "exp": OIDC_NOW + 60,
        "sub": "opaque-subject",
        "sid": "opaque-keycloak-session",
        "jti": "one-time-logout-id",
        "events": event,
    }
    with pytest.raises(keycloak_oidc.OidcError, match="bad_logout"):
        keycloak_oidc.verified_logout(
            _settings(),
            _token(private, valid | {"nonce": "bad"}, typ="logout+jwt"),
            now=OIDC_NOW,
        )
    assert keycloak_oidc.verified_logout(
        _settings(), _token(private, valid, typ="logout+jwt"), now=OIDC_NOW
    ) == (
        ISSUER,
        "opaque-subject",
        "opaque-keycloak-session",
        "one-time-logout-id",
        OIDC_NOW + 60,
    )


def test_a_risky_action_needs_the_exact_action_proof_and_no_password_counts() -> None:
    matching = cast(CurrentUser, SimpleNamespace(auth_authority="keycloak", reauth_action="locks"))
    stale_action = cast(CurrentUser, SimpleNamespace(auth_authority="keycloak", reauth_action="access"))
    legacy = cast(CurrentUser, SimpleNamespace(auth_authority="legacy", reauth_action="locks"))
    require_proof(matching, "locks")
    for refused in (stale_action, legacy):
        with pytest.raises(ApiError) as error:
            require_proof(refused, "locks")
        assert error.value.code == "keycloak_reauth_required"


def test_settings_repr_does_not_include_confidential_keycloak_material() -> None:
    settings = _settings()
    rendered = repr(settings)
    assert "server-side-confidential-secret" not in rendered
    assert settings.keycloak_flow_encryption_key is not None
    assert settings.keycloak_flow_encryption_key not in rendered
