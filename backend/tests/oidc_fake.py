"""A made-up Google for the provider sign-in tests (#457 phase 1), no network. Ported from the
Keycloak branch's ``tests/keycloak_fake.py`` (#438).

``FakeGoogle`` stands in for ``kaagaz.api.oidc``'s opener: it answers the key list and the
token endpoint, signs ID tokens RS256 with an RSA key made here (``cryptography``), and takes
each code once, for the PKCE verifier its challenge came with and this client's secret, as
Google does. ``use_google`` puts it in place for one test; ``sign_in`` plays the person
choosing an account at Google's page and gives the callback's query.
"""

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

from kaagaz.api import oidc
from kaagaz.api.auth import _b64url as b64url
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers import oidc as oidc_router
from kaagaz.settings import Settings
from tests.settings_factory import make_settings

ISSUER = "https://accounts.google.com"
CLIENT_ID = "made-up-client.apps.googleusercontent.com"
CLIENT_SECRET = "made-up-google-secret-for-tests"  # not a real secret
REDIRECT_URI = "https://kaagaz.example.test/api/auth/oidc/google/callback"
KID = "made-up-key-1"
GOOGLE = oidc.PROVIDERS["google"]


def google_settings(**overrides: Any) -> Settings:
    return make_settings(
        **{
            "google_signin_enabled": True,
            "google_client_id": CLIENT_ID,
            "google_client_secret": CLIENT_SECRET,
            "google_redirect_uri": REDIRECT_URI,
            **overrides,
        }
    )


@functools.cache
def google_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@functools.cache
def other_key() -> rsa.RSAPrivateKey:
    """Not Google's: a token it signs must be refused."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(key: rsa.RSAPrivateKey, kid: str = KID) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    e = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": b64url(n), "e": b64url(e)}


def sign(
    claims: dict[str, Any], *, key: rsa.RSAPrivateKey | None = None, header: dict[str, Any] | None = None
) -> str:
    """A JWT of ``claims``, RS256 with ``key`` (Google's when None)."""
    head = b64url(json.dumps(header or {"alg": "RS256", "typ": "JWT", "kid": KID}).encode())
    body = b64url(json.dumps(claims).encode())
    signature = (key or google_key()).sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64url(signature)}"


def claims_for(subject: str, email: str, nonce: str, now: int, /, **changes: Any) -> dict[str, Any]:
    """An ID token's claims as Google gives them; a change of None leaves that claim out."""
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "azp": CLIENT_ID,
        "sub": subject,
        "exp": now + 300,
        "iat": now,
        "nonce": nonce,
        "email": email,
        "email_verified": True,
    }
    claims.update(changes)
    return {name: value for name, value in claims.items() if value is not None}


@dataclass
class FakeGoogle:
    """Google's key list and token endpoint. ``codes`` maps a code to (its ID token, PKCE challenge)."""

    keys: list[dict[str, str]] = field(default_factory=lambda: [jwk(google_key())])
    codes: dict[str, tuple[str, str]] = field(default_factory=dict)
    used: set[str] = field(default_factory=set)
    token_forms: list[dict[str, str]] = field(default_factory=list)
    key_fetches: int = 0
    down: bool = False

    def issue(self, id_token: str, challenge: str) -> str:
        """A new code, as Google gives one after the person chose an account."""
        code = f"code-{len(self.codes) + 1}"
        self.codes[code] = (id_token, challenge)
        return code

    def sign_in(
        self, authorize_url: str, subject: str, email: str, now: int, /, **changes: Any
    ) -> dict[str, str]:
        """The person picks this account at Google: the callback's query (state and code), with
        an ID token for the nonce and challenge ``authorize_url`` carries."""
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(authorize_url).query))
        token = sign(claims_for(subject, email, query["nonce"], now, **changes))
        return {"state": query["state"], "code": self.issue(token, query["code_challenge"])}

    def open(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        assert timeout == oidc.TIMEOUT_SECONDS
        if self.down:
            raise urllib.error.URLError("made-up outage")
        url = request.full_url
        if url == GOOGLE.jwks_uri:
            self.key_fetches += 1
            return _json({"keys": self.keys})
        if url == GOOGLE.exchange_endpoint and isinstance(request.data, bytes):
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


def use_google(monkeypatch: pytest.MonkeyPatch, google: FakeGoogle | None = None) -> FakeGoogle:
    """``google`` (a new one when None) as Google, with an empty key cache, and room for every
    callback a test makes from its one address (A1's per-visitor limit is one a process)."""
    google = google or FakeGoogle()
    monkeypatch.setattr(oidc, "_OPENER", google)
    monkeypatch.setattr(oidc, "_cache", {})
    monkeypatch.setattr(oidc_router, "callback_address_limits", RateLimiter(1000, window_seconds=3600))
    return google
