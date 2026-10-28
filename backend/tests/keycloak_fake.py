"""A made-up Keycloak realm for the Keycloak sign-in tests (#435), no network.

``FakeRealm`` stands in for ``kaagaz.api.keycloak``'s opener: it answers the discovery,
the realm's keys and the token endpoint, signs ID tokens RS256 with an RSA key made here
(``cryptography``), and takes each code once, for the PKCE verifier its challenge came
with, as Keycloak does. ``use_realm`` puts it in place for one test.
"""

import base64
import functools
import hashlib
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.message import Message
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kaagaz.api import keycloak
from kaagaz.settings import Settings
from tests.settings_factory import make_settings

ISSUER = "https://id.example.test/realms/kaagaz"
CLIENT_ID = "kaagaz-web"
CLIENT_SECRET = "made-up-client-secret-for-tests"  # not a real secret
REDIRECT_URI = "https://kaagaz.example.test/api/auth/keycloak/callback"
KID = "made-up-key-1"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": ISSUER + "/protocol/openid-connect/auth",
    "token_endpoint": ISSUER + "/protocol/openid-connect/token",
    "jwks_uri": ISSUER + "/protocol/openid-connect/certs",
    "end_session_endpoint": ISSUER + "/protocol/openid-connect/logout",
}


def keycloak_settings(**overrides: Any) -> Settings:
    return make_settings(
        keycloak_enabled=True,
        keycloak_issuer=ISSUER,
        keycloak_client_id=CLIENT_ID,
        keycloak_client_secret=CLIENT_SECRET,
        keycloak_redirect_uri=REDIRECT_URI,
        **overrides,
    )


@functools.cache
def realm_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@functools.cache
def other_key() -> rsa.RSAPrivateKey:
    """Not the realm's: a token it signs must be refused."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def jwk(key: rsa.RSAPrivateKey, kid: str = KID) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    e = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": b64url(n), "e": b64url(e)}


def sign(
    claims: dict[str, Any], *, key: rsa.RSAPrivateKey | None = None, header: dict[str, Any] | None = None
) -> str:
    """A JWT of ``claims``, RS256 with ``key`` (the realm's when None)."""
    head = b64url(json.dumps(header or {"alg": "RS256", "typ": "JWT", "kid": KID}).encode())
    body = b64url(json.dumps(claims).encode())
    signature = (key or realm_key()).sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64url(signature)}"


def claims_for(email: str, nonce: str, now: int, /, **changes: Any) -> dict[str, Any]:
    """An ID token's claims as Keycloak gives them; a change of None leaves that claim out."""
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "azp": CLIENT_ID,
        "sub": "made-up-subject",
        "exp": now + 300,
        "iat": now,
        "nonce": nonce,
        "email": email,
        "email_verified": True,
    }
    claims.update(changes)
    return {name: value for name, value in claims.items() if value is not None}


@dataclass
class FakeRealm:
    """The realm's three endpoints. ``codes`` maps a code to (its ID token, PKCE challenge)."""

    keys: list[dict[str, str]] = field(default_factory=lambda: [jwk(realm_key())])
    discovery: dict[str, Any] = field(default_factory=lambda: dict(DISCOVERY))
    codes: dict[str, tuple[str, str]] = field(default_factory=dict)
    used: set[str] = field(default_factory=set)
    token_forms: list[dict[str, str]] = field(default_factory=list)
    down: bool = False

    def issue(self, id_token: str, challenge: str) -> str:
        """A new code, as Keycloak gives one after the person signed in."""
        code = f"code-{len(self.codes) + 1}"
        self.codes[code] = (id_token, challenge)
        return code

    def open(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        assert timeout == keycloak.TIMEOUT_SECONDS
        if self.down:
            raise urllib.error.URLError("made-up outage")
        url = request.full_url
        if url == ISSUER + "/.well-known/openid-configuration":
            return _json(self.discovery)
        if url == DISCOVERY["jwks_uri"]:
            return _json({"keys": self.keys})
        if url == DISCOVERY["token_endpoint"] and isinstance(request.data, bytes):
            return self._token(request, dict(urllib.parse.parse_qsl(request.data.decode())))
        raise AssertionError(f"unexpected request to {url}")

    def _token(self, request: urllib.request.Request, form: dict[str, str]) -> io.BytesIO:
        self.token_forms.append(form)
        code = form.get("code", "")
        found = self.codes.get(code)
        verifier = form.get("code_verifier", "")
        challenge = b64url(hashlib.sha256(verifier.encode()).digest())
        if (
            found is None
            or code in self.used
            or found[1] != challenge
            or form.get("client_id") != CLIENT_ID
            or form.get("client_secret") != CLIENT_SECRET
            or form.get("redirect_uri") != REDIRECT_URI
            or form.get("grant_type") != "authorization_code"
        ):
            raise urllib.error.HTTPError(request.full_url, 400, "invalid_grant", Message(), io.BytesIO(b"{}"))
        self.used.add(code)
        return _json({"id_token": found[0], "access_token": "made-up", "token_type": "Bearer"})


def _json(value: Any) -> io.BytesIO:
    return io.BytesIO(json.dumps(value).encode())


def use_realm(monkeypatch: pytest.MonkeyPatch, realm: FakeRealm | None = None) -> FakeRealm:
    """``realm`` (a new one when None) as Keycloak, with an empty cache."""
    realm = realm or FakeRealm()
    monkeypatch.setattr(keycloak, "_OPENER", realm)
    monkeypatch.setattr(keycloak, "_cache", {})
    return realm
