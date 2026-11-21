"""Strict OIDC client for the one explicitly configured Keycloak issuer.

Discovery is fetched only from the configured issuer. Its authorization, token and JWKS endpoints
must exactly match Keycloak's realm endpoints; redirects, proxies, non-RS256 keys, identity claims
other than ``iss``/``sub``/``sid``, and unconfigured provider or organization claims are refused.
The PKCE verifier and nonce stay server-side in a one-use database flow.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from kaagaz.ai.openrouter import build_opener
from kaagaz.api.auth import _b64url, _unb64url
from kaagaz.settings import Settings

TIMEOUT_SECONDS = 10.0
MAX_REPLY_BYTES = 1_000_000
CACHE_SECONDS = 300
CLOCK_SKEW_SECONDS = 60
MAX_TOKEN_AGE_SECONDS = 600
MIN_KEY_BITS = 2048
MAX_SUBJECT = 255
MAX_SID = 255
# Must match the realm's "Broker identity provider session note" mapper claim.
PROVIDER_CLAIM = "authentication_provider"

_OPENER = build_opener()
_cache: dict[str, tuple[float, Any]] = {}


class OidcError(Exception):
    """A fixed, safe failure code; no provider reply or token is exposed."""

    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class Identity:
    issuer: str
    subject: str
    session_id: str
    auth_time: int
    acr: str
    authentication_provider: str


def authorize_url(
    settings: Settings,
    *,
    state: str,
    nonce: str,
    verifier: str,
    max_age: int = 0,
) -> str:
    """Construct the only allowed Keycloak authorization request with PKCE S256."""
    _configured(settings)
    metadata = _discovery(settings)
    query = urllib.parse.urlencode(
        {
            "client_id": settings.keycloak_client_id,
            "redirect_uri": settings.keycloak_redirect_uri,
            "response_type": "code",
            "scope": "openid",
            "state": state,
            "nonce": nonce,
            "prompt": "login",
            "max_age": str(max_age),
            "acr_values": settings.keycloak_required_acr,
            "code_challenge": _b64url(hashlib.sha256(verifier.encode("ascii")).digest()),
            "code_challenge_method": "S256",
        }
    )
    return f"{metadata['authorization_endpoint']}?{query}"


def id_token_for(settings: Settings, code: str, verifier: str) -> str:
    """Exchange a one-use authorization code through the confidential PKCE client."""
    _configured(settings)
    metadata = _discovery(settings)
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.keycloak_redirect_uri,
            "code_verifier": verifier,
        }
    ).encode("ascii")
    basic = base64.b64encode(
        f"{settings.keycloak_client_id}:{settings.keycloak_client_secret}".encode()
    ).decode("ascii")
    result = _fetch_json(metadata["token_endpoint"], body, authorization=f"Basic {basic}")
    token = result.get("id_token") if isinstance(result, dict) else None
    if not isinstance(token, str) or len(token) > 32_768:
        raise OidcError("bad_token")
    return token


def verified_identity(settings: Settings, token: str, *, nonce: str, now: int, started_at: int) -> Identity:
    """Verify the callback token and require a fresh local password plus OTP execution."""
    claims = _verified_claims(settings, token, now=now, logout=False)
    given_nonce = claims.get("nonce")
    if (
        not isinstance(given_nonce, str)
        or not given_nonce.isascii()
        or not hmac.compare_digest(given_nonce, nonce)
    ):
        raise OidcError("wrong_nonce")
    subject = _opaque(claims.get("sub"), MAX_SUBJECT)
    session_id = _opaque(claims.get("sid"), MAX_SID)
    auth_time = claims.get("auth_time")
    acr = claims.get("acr")
    if type(auth_time) is not int or not isinstance(acr, str):
        raise OidcError("bad_token")
    if auth_time > now + CLOCK_SKEW_SECONDS or auth_time < started_at - CLOCK_SKEW_SECONDS or auth_time > now:
        raise OidcError("stale_authentication")
    if not acr.isascii() or not hmac.compare_digest(acr, settings.keycloak_required_acr or ""):
        raise OidcError("assurance_required")
    amr = claims.get("amr")
    if (
        not isinstance(amr, list)
        or any(not isinstance(method, str) for method in amr)
        or not {"pwd", "otp"}.issubset(amr)
    ):
        # The first post-enrollment OTP flow may still carry only the password AMR. One fresh
        # company sign-in then executes both authenticators and succeeds without trusting ACR.
        raise OidcError("mfa_verify_again")
    # No brokered or social sign-in is allowed: the realm emits this claim only for one, so any
    # value refuses; absent or empty is the local password-plus-OTP execution.
    provider_claim = claims.get(PROVIDER_CLAIM)
    if provider_claim is not None and provider_claim != "":
        raise OidcError("organization_provider_policy_unconfigured")
    provider = "local"
    return Identity(
        issuer=settings.keycloak_issuer or "",
        subject=subject,
        session_id=session_id,
        auth_time=auth_time,
        acr=acr,
        authentication_provider=provider,
    )


def verified_logout(settings: Settings, token: str, *, now: int) -> tuple[str, str, str, str, int]:
    """Verify a signed backchannel logout token and return exact issuer/sub/sid/jti/expiry.

    Caller must pass the result directly to the narrow database primitive; no unverified claims
    are accepted by that route.
    """
    claims = _verified_claims(settings, token, now=now, logout=True)
    if "nonce" in claims:
        raise OidcError("bad_logout")
    events = claims.get("events")
    event_name = "http://schemas.openid.net/event/backchannel-logout"
    if not isinstance(events, dict) or set(events) != {event_name} or events[event_name] != {}:
        raise OidcError("bad_logout")
    jti = _opaque(claims.get("jti"), 255)
    subject = _opaque(claims.get("sub"), MAX_SUBJECT)
    session_id = _opaque(claims.get("sid"), MAX_SID)
    expiry = claims.get("exp")
    if type(expiry) is not int or expiry <= now:
        raise OidcError("bad_logout")
    return settings.keycloak_issuer or "", subject, session_id, jti, expiry


def seal_verifier(verifier: str, key_text: str, state_hash: bytes) -> str:
    """Encrypt a PKCE verifier at rest with a flow-only key and the state hash as AAD."""
    if len(key_text) < 32 or len(state_hash) != 32:
        raise ValueError("invalid Keycloak flow encryption input")
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"kaagaz keycloak flow verifier v1",
    ).derive(key_text.encode("utf-8"))
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, verifier.encode("ascii"), state_hash)
    return "v1:" + _b64url(nonce + ciphertext)


def open_verifier(sealed: str, key_text: str, state_hash: bytes) -> str:
    """Open the one stored verifier; malformed/corrupt storage is a fixed provider failure."""
    if len(key_text) < 32 or len(state_hash) != 32 or not sealed.startswith("v1:"):
        raise OidcError("invalid_flow")
    try:
        raw = _unb64url(sealed[3:])
        if len(raw) < 12 + 16:
            raise ValueError
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"kaagaz keycloak flow verifier v1",
        ).derive(key_text.encode("utf-8"))
        plain = AESGCM(key).decrypt(raw[:12], raw[12:], state_hash)
        return plain.decode("ascii")
    except (ValueError, UnicodeError, InvalidTag):
        raise OidcError("invalid_flow") from None


def _verified_claims(settings: Settings, token: str, *, now: int, logout: bool) -> dict[str, Any]:
    _configured(settings)
    try:
        header_part, claims_part, signature_part = token.split(".")
        header = json.loads(_unb64url(header_part))
        claims = json.loads(_unb64url(claims_part))
        signature = _unb64url(signature_part)
    except (ValueError, UnicodeError):
        raise OidcError("bad_token") from None
    if not isinstance(header, dict) or not isinstance(claims, dict) or header.get("alg") != "RS256":
        raise OidcError("bad_token")
    if logout and header.get("typ") != "logout+jwt":
        raise OidcError("bad_logout")
    if not logout and header.get("typ", "JWT") != "JWT":
        raise OidcError("bad_token")
    metadata = _discovery(settings)
    key = _signing_key(metadata["jwks_uri"], header.get("kid"))
    try:
        key.verify(
            signature,
            f"{header_part}.{claims_part}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature:
        raise OidcError("bad_token") from None

    client_id = settings.keycloak_client_id or ""
    audience = claims.get("aud")
    audiences = [audience] if isinstance(audience, str) else audience if isinstance(audience, list) else []
    if claims.get("iss") != settings.keycloak_issuer or client_id not in audiences:
        raise OidcError("bad_token")
    azp = claims.get("azp")
    if (not logout and azp != client_id) or (logout and azp is not None and azp != client_id):
        raise OidcError("bad_token")
    expiry, issued = claims.get("exp"), claims.get("iat")
    if type(expiry) is not int or type(issued) is not int:
        raise OidcError("bad_token")
    if expiry <= now - CLOCK_SKEW_SECONDS or issued > now + CLOCK_SKEW_SECONDS:
        raise OidcError("expired")
    if now - issued > MAX_TOKEN_AGE_SECONDS:
        raise OidcError("expired")
    not_before = claims.get("nbf")
    if not_before is not None and (type(not_before) is not int or not_before > now + CLOCK_SKEW_SECONDS):
        raise OidcError("expired")
    if logout:
        if expiry <= now:
            raise OidcError("expired")
    else:
        auth_time = claims.get("auth_time")
        if type(auth_time) is not int or auth_time > now + CLOCK_SKEW_SECONDS:
            raise OidcError("bad_token")
    return claims


def _signing_key(jwks_uri: str, kid: object, *, fresh: bool = False) -> rsa.RSAPublicKey:
    document = _cached_json(jwks_uri, fresh=fresh)
    keys = document.get("keys") if isinstance(document, dict) else None
    for jwk in keys if isinstance(keys, list) and isinstance(kid, str) else []:
        if not isinstance(jwk, dict) or jwk.get("kid") != kid or jwk.get("kty") != "RSA":
            continue
        if jwk.get("use", "sig") != "sig" or jwk.get("alg", "RS256") != "RS256":
            continue
        key_ops = jwk.get("key_ops")
        if key_ops is not None and (not isinstance(key_ops, list) or "verify" not in key_ops):
            continue
        try:
            modulus = int.from_bytes(_unb64url(jwk["n"]), "big")
            exponent = int.from_bytes(_unb64url(jwk["e"]), "big")
            if modulus.bit_length() >= MIN_KEY_BITS:
                return rsa.RSAPublicNumbers(exponent, modulus).public_key()
        except (AttributeError, KeyError, ValueError):
            continue
    if not fresh and isinstance(kid, str):
        return _signing_key(jwks_uri, kid, fresh=True)
    raise OidcError("bad_token")


def _discovery(settings: Settings, *, fresh: bool = False) -> dict[str, str]:
    issuer = settings.keycloak_issuer or ""
    url = f"{issuer}/.well-known/openid-configuration"
    document = _cached_json(url, fresh=fresh)
    expected = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
        "token_endpoint": f"{issuer}/protocol/openid-connect/token",
        "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
        "end_session_endpoint": f"{issuer}/protocol/openid-connect/logout",
    }
    if not isinstance(document, dict) or any(document.get(name) != value for name, value in expected.items()):
        raise OidcError("discovery_refused")
    return expected


def _cached_json(url: str, *, fresh: bool = False) -> Any:
    cached = _cache.get(url)
    if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS and not fresh:
        return cached[1]
    result = _fetch_json(url, None)
    _cache[url] = (time.monotonic(), result)
    return result


def _fetch_json(url: str, data: bytes | None, *, authorization: str | None = None) -> Any:
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if authorization is not None:
        headers["Authorization"] = authorization
    request = urllib.request.Request(url, data, headers)  # noqa: S310 - URL is issuer-pinned above
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_REPLY_BYTES + 1)
    except urllib.error.HTTPError as error:
        error.close()
        raise OidcError("provider_refused", error.code) from None
    except (OSError, ValueError):
        raise OidcError("provider_unavailable") from None
    if len(body) > MAX_REPLY_BYTES:
        raise OidcError("provider_unavailable")
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        raise OidcError("provider_unavailable") from None


def _configured(settings: Settings) -> None:
    try:
        issuer = urllib.parse.urlsplit(settings.keycloak_issuer or "")
        redirect = urllib.parse.urlsplit(settings.keycloak_redirect_uri or "")
    except ValueError:
        raise OidcError("not_configured") from None
    trusted_scheme = issuer.scheme == "https" or (
        issuer.scheme == "http" and issuer.hostname in {"localhost", "127.0.0.1", "::1", "keycloak.localhost"}
    )
    trusted_redirect = redirect.scheme == "https" or (
        redirect.scheme == "http" and redirect.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    valid_ports = _valid_port(issuer) and _valid_port(redirect)
    if not (
        settings.keycloak_enabled
        and settings.keycloak_issuer
        and settings.keycloak_issuer.isascii()
        and trusted_scheme
        and bool(issuer.hostname)
        and issuer.username is None
        and issuer.password is None
        and not issuer.query
        and not issuer.fragment
        and not issuer.path.endswith("/")
        and trusted_redirect
        and bool(redirect.hostname)
        and redirect.username is None
        and redirect.password is None
        and not redirect.query
        and not redirect.fragment
        and redirect.path == "/api/auth/keycloak/callback"
        and valid_ports
        and settings.keycloak_client_id
        and settings.keycloak_client_secret
        and settings.keycloak_redirect_uri
        and settings.keycloak_redirect_uri.isascii()
        and settings.keycloak_required_acr
    ):
        raise OidcError("not_configured")


def _valid_port(parts: urllib.parse.SplitResult) -> bool:
    try:
        return parts.port is None or 1 <= parts.port <= 65535
    except ValueError:
        return False


def _opaque(value: object, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise OidcError("bad_token")
    return value
