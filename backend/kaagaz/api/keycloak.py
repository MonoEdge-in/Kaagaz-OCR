"""The OpenID Connect steps of the Keycloak sign-in (#435, Tushar 28 Oct 2026), for
``routers/keycloak.py``. The standard library and ``cryptography`` only, no new library.

* **Discovery** (``{KEYCLOAK_ISSUER}/.well-known/openid-configuration``) and the realm's
  keys (its ``jwks_uri``) come through the model client's opener (no proxy from the
  environment, no redirects), within TIMEOUT_SECONDS and MAX_REPLY_BYTES, and are kept
  CACHE_SECONDS. The discovery's ``issuer`` must be KEYCLOAK_ISSUER exactly, and each
  endpoint an https URL (http only on a loopback host, as KEYCLOAK_ISSUER), so no
  ``file:`` or other scheme is ever opened or redirected to.
* **Every sign-in asks again**: ``prompt=login`` and ``max_age=0``, so a Keycloak session
  left over from before never signs anyone in unasked, and ``acr_values=otp`` asks for the
  two-step level (the realm maps ``otp`` to its OTP step, docs/running-locally.md 2d).
* **The code** is swapped at the token endpoint with the PKCE verifier and the client's id
  and secret; the secret goes in that request's body and nowhere else.
* **The ID token** counts only when: RS256, signed by the realm key its ``kid`` names (RSA,
  2048 bits or more; a ``kid`` not in the cached keys fetches them once more, for a new
  key); ``iss`` KEYCLOAK_ISSUER; ``aud`` this client, and ``azp`` this client when it is
  given or ``aud`` names others; ``exp`` not passed and ``iat`` not ahead, each within
  LEEWAY_SECONDS of the clock; ``auth_time`` not before this sign-in was asked for;
  ``nonce`` the one this browser was given; ``acr`` REQUIRED_ACR (``two_step`` otherwise);
  and ``email_verified`` true. It gives the ``email``, trimmed and in lower case, and the
  ``kaagaz_org`` claim (the person's organisation id, a Keycloak user attribute).
* **Every failure** is a ``KeycloakError`` with a short stable code, the only thing logged
  or shown: never a token, a code or an e-mail.
"""

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kaagaz.ai.openrouter import build_opener
from kaagaz.api.auth import _b64url, _is_int, _unb64url
from kaagaz.settings import _LOOPBACK_HOSTS, Settings

TIMEOUT_SECONDS = 10.0
MAX_REPLY_BYTES = 1_000_000
CACHE_SECONDS = 300  # a new realm key is seen within 5 minutes
LEEWAY_SECONDS = 60  # clocks a little apart
MIN_KEY_BITS = 2048
ENDPOINTS = ("authorization_endpoint", "token_endpoint", "jwks_uri", "end_session_endpoint")
REQUIRED_ACR = "otp"  # the realm's level for the password and the two-step code

_OPENER = build_opener()
_cache: dict[str, tuple[float, Any]] = {}  # URL: (when fetched, monotonic seconds; its JSON)


class KeycloakError(Exception):
    """A step failed. ``code`` says which, in a word the sign-in page explains."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def endpoints(settings: Settings) -> dict[str, str]:
    """The realm's ENDPOINTS from its discovery, whose issuer must be KEYCLOAK_ISSUER."""
    found = _cached_json(settings.keycloak_issuer + "/.well-known/openid-configuration")
    if not isinstance(found, dict) or found.get("issuer") != settings.keycloak_issuer:
        raise KeycloakError("unreachable")
    if not all(isinstance(found.get(name), str) and _web(found[name]) for name in ENDPOINTS):
        raise KeycloakError("unreachable")
    return {name: found[name] for name in ENDPOINTS}


def authorize_url(settings: Settings, realm: dict[str, str], *, state: str, nonce: str, verifier: str) -> str:
    """Keycloak's sign-in page for this client, with the state, nonce and PKCE challenge (S256),
    asking for the password and the two-step code every time."""
    query = {
        "response_type": "code",
        "client_id": settings.keycloak_client_id,
        "redirect_uri": settings.keycloak_redirect_uri,
        "scope": "openid email",
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64url(hashlib.sha256(verifier.encode("ascii")).digest()),
        "code_challenge_method": "S256",
        "prompt": "login",
        "max_age": "0",
        "acr_values": REQUIRED_ACR,
    }
    return realm["authorization_endpoint"] + "?" + urllib.parse.urlencode(query)


def id_token_for(settings: Settings, realm: dict[str, str], code: str, verifier: str) -> str:
    """The ID token the token endpoint gives for this code. Keycloak takes a code once."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.keycloak_redirect_uri,
        "code_verifier": verifier,
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
    }
    data = urllib.parse.urlencode(form).encode("ascii")
    reply = _fetch(realm["token_endpoint"], data, refused="token")
    token = reply.get("id_token") if isinstance(reply, dict) else None
    if not isinstance(token, str):
        raise KeycloakError("token")
    return token


def verified_person(
    settings: Settings, realm: dict[str, str], token: str, *, nonce: str, asked_at: int, now: int
) -> tuple[str, str]:
    """(verified e-mail, ``kaagaz_org`` or "") from a genuine ID token for this client, this
    browser's nonce and a sign-in made at or after ``asked_at``."""
    try:
        header_part, claims_part, signature_part = token.split(".")
        header = json.loads(_unb64url(header_part))
        claims = json.loads(_unb64url(claims_part))
        signature = _unb64url(signature_part)
    except ValueError:  # also a wrong part count, bad base64, non-ASCII text or bad JSON
        raise KeycloakError("token") from None
    if not (isinstance(header, dict) and isinstance(claims, dict)) or header.get("alg") != "RS256":
        raise KeycloakError("token")
    key = _signing_key(realm["jwks_uri"], header.get("kid"))
    try:
        signed = f"{header_part}.{claims_part}".encode("ascii")
        key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise KeycloakError("token") from None

    client = settings.keycloak_client_id
    audience = claims.get("aud")
    audiences = [audience] if isinstance(audience, str) else audience if isinstance(audience, list) else []
    azp = claims.get("azp")
    if claims.get("iss") != settings.keycloak_issuer or client not in audiences:
        raise KeycloakError("token")
    if (azp is not None or len(audiences) > 1) and azp != client:
        raise KeycloakError("token")
    expires, issued, signed_in = claims.get("exp"), claims.get("iat"), claims.get("auth_time")
    if not (_is_int(expires) and _is_int(issued) and _is_int(signed_in)):
        raise KeycloakError("token")
    if (
        expires + LEEWAY_SECONDS <= now
        or issued > now + LEEWAY_SECONDS
        or signed_in < asked_at - LEEWAY_SECONDS
    ):
        raise KeycloakError("token")
    given = claims.get("nonce")
    if not isinstance(given, str) or not hmac.compare_digest(given.encode(), nonce.encode()):
        raise KeycloakError("token")
    if claims.get("acr") != REQUIRED_ACR:
        raise KeycloakError("two_step")
    email, organisation = claims.get("email"), claims.get("kaagaz_org")
    if claims.get("email_verified") is not True or not isinstance(email, str) or not email.strip():
        raise KeycloakError("email_unverified")
    return email.strip().lower(), organisation if isinstance(organisation, str) else ""


def logout_url(settings: Settings, realm: dict[str, str]) -> str:
    """Keycloak's sign-out for this client, back to this site's sign-in page after. Keycloak
    asks the person to confirm: no ID token is kept to send as a hint."""
    back = urllib.parse.urljoin(settings.keycloak_redirect_uri, "/login?signed_out=1")
    query = {"client_id": settings.keycloak_client_id, "post_logout_redirect_uri": back}
    return realm["end_session_endpoint"] + "?" + urllib.parse.urlencode(query)


def _signing_key(jwks_uri: str, kid: object, *, fresh: bool = False) -> rsa.RSAPublicKey:
    """The realm's RSA signing key named ``kid``, else ``token``. Not in the cached keys: the
    keys once more (``fresh``), as the realm may have a new one. The token itself came from
    the token endpoint, never from the browser, so no one can make Kaagaz fetch keys at will."""
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
    raise KeycloakError("token")


def _cached_json(url: str, *, fresh: bool = False) -> Any:
    now = time.monotonic()
    kept = _cache.get(url)
    if kept is not None and now - kept[0] < CACHE_SECONDS and not fresh:
        return kept[1]
    value = _fetch(url, None, refused="unreachable")
    _cache[url] = (now, value)
    return value


def _web(url: str) -> bool:
    """An https URL, or http on a loopback host (a realm on this laptop)."""
    parts = urllib.parse.urlsplit(url)
    return (parts.scheme == "https" and bool(parts.hostname)) or (
        parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
    )


def _fetch(url: str, data: bytes | None, *, refused: str) -> Any:
    """The JSON reply from ``url``: a GET, or a form POST of ``data``. An error status is
    ``refused``; no reply, a slow or a too big one, or one that is not JSON, ``unreachable``.
    ``url`` is KEYCLOAK_ISSUER's or an endpoint ``endpoints`` checked (``_web``)."""
    # The scheme written out, so only https or http is ever opened (as the model client does).
    rest = url.split("://", 1)[1]
    headers = {"Accept": "application/json"}
    if data is not None:  # a POST
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if url.startswith("https://"):
        request = urllib.request.Request(f"https://{rest}", data, headers)
    else:
        request = urllib.request.Request(f"http://{rest}", data, headers)
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as reply:
            body = reply.read(MAX_REPLY_BYTES + 1)
    except urllib.error.HTTPError as error:
        error.close()
        raise KeycloakError(refused) from None
    except (OSError, ValueError):  # URLError and timeouts are OSErrors
        raise KeycloakError("unreachable") from None
    if len(body) > MAX_REPLY_BYTES:
        raise KeycloakError("unreachable")
    try:
        return json.loads(body)
    except ValueError:
        raise KeycloakError("unreachable") from None
