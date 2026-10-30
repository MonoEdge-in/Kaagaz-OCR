"""The one OpenID Connect sign-in core for "Ways to sign in" (#457 phase 1), for
``routers/oidc.py``: the authorization code flow with PKCE, the standard library and
``cryptography`` only, no new library. Ported, as plain code, from the Keycloak sign-in's
``kaagaz/api/keycloak.py`` (#438); Keycloak itself stays dropped.

* **One small fixed entry per provider** (``PROVIDERS``): its endpoints, the issuers it may
  name and the one form stored, and its scopes. No discovery fetch. Google is the only entry;
  Microsoft is added only when a customer asks.
* **Every sign-in asks which account**: ``prompt=select_account``, so nobody is signed in
  silently on a shared computer. No refresh token is asked for.
* **The code** is swapped at the token endpoint, server to server, with the PKCE verifier and
  the client's id and secret; the secret goes in that request's body and nowhere else.
* **The ID token** counts only when: RS256, signed by the provider key its ``kid`` names (RSA,
  2048 bits or more; a ``kid`` not in the cached keys fetches them once more, for a new key);
  ``iss`` one of the provider's issuers; ``aud`` this client, and ``azp`` this client when it
  is given or ``aud`` names others; ``exp`` not passed and ``iat`` not ahead, each within
  LEEWAY_SECONDS of the clock; and ``nonce`` the one this browser was given. It gives the
  identity key (provider, issuer in its stored form, ``sub``) and the email, in lower case,
  as a label, with whether the provider verified it.
* **Keys and replies** come through the model client's opener (no proxy from the
  environment, no redirects), within TIMEOUT_SECONDS and MAX_REPLY_BYTES; the key list is
  kept CACHE_SECONDS. Every URL opened is one of the fixed https endpoints.
* **Every failure** is an ``OidcError`` with a short stable code, the only thing logged or
  shown: never a token, a code, a subject or an email.
"""

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kaagaz.ai.openrouter import build_opener
from kaagaz.api.auth import _b64url, _unb64url
from kaagaz.settings import Settings

TIMEOUT_SECONDS = 10.0
MAX_REPLY_BYTES = 1_000_000
CACHE_SECONDS = 300  # a new provider key is seen within 5 minutes
LEEWAY_SECONDS = 60  # clocks a little apart
MIN_KEY_BITS = 2048
MAX_SUBJECT = 255

_OPENER = build_opener()
_cache: dict[str, tuple[float, Any]] = {}  # URL: (when fetched, monotonic seconds; its JSON)


class OidcError(Exception):
    """A step failed. ``code`` says which, in a word the sign-in page explains: ``unavailable``
    (no or a bad reply), ``refused`` (the provider said no), ``bad_token``, ``expired`` or
    ``wrong_nonce``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Provider:
    name: str
    authorize_endpoint: str
    exchange_endpoint: str  # its token endpoint: a code swapped for the ID token
    jwks_uri: str
    issuers: frozenset[str]  # what its ID tokens may name
    issuer: str  # the one form stored
    scopes: str


PROVIDERS = {
    "google": Provider(
        name="google",
        authorize_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        exchange_endpoint="https://oauth2.googleapis.com/token",
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        issuers=frozenset({"https://accounts.google.com", "accounts.google.com"}),
        issuer="https://accounts.google.com",
        scopes="openid email profile",
    ),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Identity:
    provider: str
    issuer: str
    subject: str
    email: str | None  # a label only, lower case
    email_verified: bool


def _client(settings: Settings, name: str) -> tuple[bool, str | None, str | None, str | None]:
    """(switched on, client id, client secret, redirect URI) of the provider ``name``."""
    if name == "google":
        return (
            settings.google_signin_enabled,
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )
    return (False, None, None, None)


def enabled(settings: Settings, name: str) -> bool:
    """The provider exists and its switch is on (settings require its values then)."""
    return name in PROVIDERS and _client(settings, name)[0]


def _configured(settings: Settings, provider: Provider) -> tuple[str, str, str]:
    on, client_id, secret, redirect = _client(settings, provider.name)
    if not (on and client_id and secret and redirect):
        raise OidcError("unavailable")
    return client_id, secret, redirect


def authorize_url(provider: Provider, settings: Settings, *, state: str, nonce: str, verifier: str) -> str:
    """The provider's account chooser for this client, with the state, nonce and PKCE challenge."""
    client_id, _, redirect = _configured(settings, provider)
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": provider.scopes,
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
        "code_challenge": _b64url(hashlib.sha256(verifier.encode("ascii")).digest()),
        "code_challenge_method": "S256",
    }
    return provider.authorize_endpoint + "?" + urllib.parse.urlencode(query)


def id_token_for(provider: Provider, settings: Settings, code: str, verifier: str) -> str:
    """The ID token the token endpoint gives for this code. A code works once."""
    client_id, secret, redirect = _configured(settings, provider)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "code_verifier": verifier,
        "client_id": client_id,
        "client_secret": secret,
    }
    reply = _fetch(provider.exchange_endpoint, urllib.parse.urlencode(form).encode("ascii"))
    token = reply.get("id_token") if isinstance(reply, dict) else None
    if not isinstance(token, str):
        raise OidcError("unavailable")
    return token


def verified_identity(
    provider: Provider, settings: Settings, token: str, *, nonce: str, now: int
) -> Identity:
    """The identity in a genuine ID token for this client and this browser's nonce."""
    client_id, _, _ = _configured(settings, provider)
    try:
        header_part, claims_part, signature_part = token.split(".")
        header = json.loads(_unb64url(header_part))
        claims = json.loads(_unb64url(claims_part))
        signature = _unb64url(signature_part)
    except ValueError:  # also a wrong part count, bad base64, non-ASCII text or bad JSON
        raise OidcError("bad_token") from None
    if not (isinstance(header, dict) and isinstance(claims, dict)) or header.get("alg") != "RS256":
        raise OidcError("bad_token")
    key = _signing_key(provider.jwks_uri, header.get("kid"))
    try:
        key.verify(
            signature, f"{header_part}.{claims_part}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
        )
    except InvalidSignature:
        raise OidcError("bad_token") from None

    audience = claims.get("aud")
    audiences = [audience] if isinstance(audience, str) else audience if isinstance(audience, list) else []
    azp = claims.get("azp")
    if claims.get("iss") not in provider.issuers or client_id not in audiences:
        raise OidcError("bad_token")
    if (azp is not None or len(audiences) > 1) and azp != client_id:
        raise OidcError("bad_token")
    expires, issued = claims.get("exp"), claims.get("iat")
    if type(expires) is not int or type(issued) is not int:  # whole numbers, never a bool
        raise OidcError("bad_token")
    if expires + LEEWAY_SECONDS <= now or issued > now + LEEWAY_SECONDS:
        raise OidcError("expired")
    given = claims.get("nonce")
    if not isinstance(given, str) or not hmac.compare_digest(given.encode(), nonce.encode()):
        raise OidcError("wrong_nonce")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not 0 < len(subject) <= MAX_SUBJECT:
        raise OidcError("bad_token")
    email = claims.get("email")
    email = email.strip().lower() if isinstance(email, str) and email.strip() else None
    return Identity(
        provider=provider.name,
        issuer=provider.issuer,
        subject=subject,
        email=email,
        email_verified=email is not None and claims.get("email_verified") is True,
    )


def _signing_key(jwks_uri: str, kid: object, *, fresh: bool = False) -> rsa.RSAPublicKey:
    """The provider's RSA signing key named ``kid``, else ``bad_token``. Not in the cached keys:
    the keys once more (``fresh``), as the provider may have a new one. The token itself came
    from the token endpoint, never from the browser, so no one can make Kaagaz fetch at will."""
    found = _cached_json(jwks_uri, fresh=fresh)
    keys = found.get("keys") if isinstance(found, dict) else None
    for jwk in keys if isinstance(keys, list) and isinstance(kid, str) else []:
        if not (isinstance(jwk, dict) and jwk.get("kid") == kid and jwk.get("kty") == "RSA"):
            continue
        if jwk.get("use", "sig") != "sig" or jwk.get("alg", "RS256") != "RS256":
            continue
        try:
            n = int.from_bytes(_unb64url(jwk["n"]), "big")
            e = int.from_bytes(_unb64url(jwk["e"]), "big")
            if n.bit_length() >= MIN_KEY_BITS:
                return rsa.RSAPublicNumbers(e, n).public_key()
        except (AttributeError, KeyError, ValueError):  # "n" or "e" missing or not base64url text
            pass
    if not fresh and isinstance(kid, str):
        return _signing_key(jwks_uri, kid, fresh=True)
    raise OidcError("bad_token")


def _cached_json(url: str, *, fresh: bool = False) -> Any:
    now = time.monotonic()
    kept = _cache.get(url)
    if kept is not None and now - kept[0] < CACHE_SECONDS and not fresh:
        return kept[1]
    value = _fetch(url, None)
    _cache[url] = (now, value)
    return value


def _fetch(url: str, data: bytes | None) -> Any:
    """The JSON reply from ``url``, one of a provider's fixed https endpoints: a GET, or a form
    POST of ``data``. An error status is ``refused``; no reply, a slow or a too big one, or one
    that is not JSON, ``unavailable``."""
    if not url.startswith("https://"):
        raise OidcError("unavailable")
    headers = {"Accept": "application/json"}
    if data is not None:  # a POST
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data, headers)  # noqa: S310 - https only, checked above
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as reply:
            body = reply.read(MAX_REPLY_BYTES + 1)
    except urllib.error.HTTPError as error:
        error.close()
        raise OidcError("refused") from None
    except (OSError, ValueError):  # URLError and timeouts are OSErrors
        raise OidcError("unavailable") from None
    if len(body) > MAX_REPLY_BYTES:
        raise OidcError("unavailable")
    try:
        return json.loads(body)
    except ValueError:
        raise OidcError("unavailable") from None
