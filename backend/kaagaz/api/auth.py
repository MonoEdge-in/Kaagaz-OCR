"""Sign-in: password hashes, the session and enrol tokens, who the caller is, and CSRF.

Why each piece (FULL-BUILD-SPEC section 3, PRD 1 A1 to A7):

* **Passwords** are hashed with scrypt (standard library), 16 random salt
  bytes, n=2**14, r=8, p=1, 32 bytes out, stored as
  ``scrypt$16384$8$1$<salt b64>$<hash b64>``, and compared in constant time.
* **The session** is a cookie ``<payload b64url>.<HMAC-SHA256 b64url>``. The
  payload is ``{"uid","cid","role","scope","ver","exp"}`` and the MAC is over
  ``b"session\\x00" + body``. ``scope`` is "app" (a member or reviewer from /login)
  or "admin" (an admin from /login or from enrolling; every admin session is one);
  ``ver`` is the user's ``session_version``. Nothing is stored server side, so
  every request re-checks, with one query inside ``db.customer(cid)``, that
  the user still exists, is not disabled, still has that role and still has
  that session version. Raising the version (a new password, "sign out
  everywhere", disabling, a 2FA reset) ends every other session at its next
  request.
* **The idle timeout** (0040): the same query reads the hours the super admin set (the
  organisation's, else the platform's; none saved: no idle limit) and the user's last use
  or sign-in. Unused for longer ends the session (401 ``not_signed_in``); a use writes
  ``last_seen_at`` at most once a minute. ``exp`` stays the outer cap: 24 hours for a sign-in
  while hours are set (member and admin alike), else SESSION_HOURS or ADMIN_SESSION_MINUTES.
* **The enrol token** carries a pending TOTP secret between an admin's right
  password at /auth/login (403 ``mfa_enrol_required``) and their first code:
  ``{"uid","cid","ver","sec","exp"}``, MAC over
  ``b"enrol\\x00" + body``, 10 minutes. The purposes have different MAC
  inputs and different fields, so neither token ever reads as the other.
* **The confirm token** (council 4 Oct 2026, PROVISIONAL): a granted member or
  reviewer's re-typed password for Delete forever. ``{"uid","cid","ver","exp"}``,
  MAC over ``b"confirm\\x00" + body``, 5 minutes, an HttpOnly SameSite=Strict
  cookie for /api/documents only.
* **The provider sign-in's tokens** (#457 phase 1), each its own MAC purpose, HttpOnly,
  SameSite=Lax, on /api/auth/oidc only: the flow (``oidc_flow``, 10 minutes, single use: the
  state, nonce and PKCE verifier from start to callback, and for a connect the signed-in user
  and session version); the second step (``second_step``, 5 minutes: a right provider sign-in
  for someone with two-step sign-in on, never a session); and the "isn't connected" notice
  (``oidc_notice``, 60 seconds), so the provider's email never goes in a URL.
* **Strict claims**: a token with a missing, extra or wrongly typed field is
  refused, so a Sprint 1 cookie (no scope, no ver) is refused and its user
  signs in once more.
* **The customer always comes from the session**, never from the request: the
  signed ``cid`` is the only customer id any route uses.
* **The password-change and two-step gates are inside ``current_user``** (deny
  by default): while ``must_change_password`` is set, every route refuses with 403
  ``password_change_required``; then an admin without two-step sign-in set up gets
  403 ``mfa_enrol_required`` (MFA required for admins, 16 Oct 2026). Only the few
  routes that use ``current_user_allow_password_change`` (/auth/password,
  /auth/logout, /auth/me, /auth/session) skip both; /auth/admin/enrol needs no session.
* **The limited session** (#507): last, a "Google only" user with no passing Google link
  (``needs_google_link``) gets 403 ``google_link_required`` everywhere except the routes above
  and those using ``current_user_allow_google_link`` (connect, its callback, Ways to sign in's
  list and remove, and sign out everywhere). Their password signs them in only to connect Google.
* **CSRF**: every request that is not GET, HEAD or OPTIONS must carry
  ``X-Requested-With: kaagaz``. A cross-site form cannot add a header, and a
  cross-site script cannot either without CORS, which the API never allows.

PROVISIONAL (security configuration is Vrushit's): logout only clears this
browser's cookie; "sign out everywhere" is what ends a copied token early.
"""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cache
from typing import Annotated, Any

from fastapi import Depends, Request, Response

from kaagaz.api.context import AppContext, get_context, get_operator_db
from kaagaz.api.errors import ApiError
from kaagaz.db import ai_settings
from kaagaz.db import operators as operators_sql
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from kaagaz.settings import Settings

SESSION_COOKIE = "kaagaz_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "kaagaz"
ROLES = ("member", "reviewer", "admin")
SCOPES = ("app", "admin")
ENROL_SECONDS = 10 * 60
CONFIRM_COOKIE = "kaagaz_confirm"
CONFIRM_SECONDS = 5 * 60  # PROVISIONAL (council 4 Oct 2026)
CONFIRM_PATH = "/api/documents"  # the browser calls the API on its own origin, under /api
OPERATOR_COOKIE = "kaagaz_operator"
OPERATOR_PATH = "/api/operator"
OPERATOR_SESSION_SECONDS = 30 * 60  # absolute, PROVISIONAL (change plan question 4)
# Signing in with a provider (#457 phase 1): the flow from start to callback, the code step
# after it for anyone with two-step sign-in on, and the "isn't connected" notice for the page.
OIDC_PATH = "/api/auth/oidc"
OIDC_COOKIE = "kaagaz_oidc"
OIDC_SECONDS = 10 * 60
PENDING_COOKIE = "kaagaz_second_step"
PENDING_SECONDS = 5 * 60
NOTICE_COOKIE = "kaagaz_oidc_notice"
NOTICE_SECONDS = 60
# A session's end while an idle timeout is set (0040): the idle timeout, 1 to 24 hours, ends it.
# Cookies issued before keep the end they were given.
CAPPED_SECONDS = 24 * 3600

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_LEN = 2**14, 8, 1, 32


# --- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    """O(1) but deliberately slow (about 16 MiB and tens of milliseconds)."""
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt)
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        )
    )


def verify_password(password: str, stored: str) -> bool:
    """True only for a well-formed hash with our parameters that matches."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[:4] != ["scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P)]:
        return False
    try:
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
    except (binascii.Error, ValueError):
        return False
    return hmac.compare_digest(_scrypt(password, salt), expected)


def spend_password_time(password: str) -> None:
    """Hash against a dummy, so an unknown e-mail takes as long as a known one."""
    verify_password(password, _dummy_hash())


@cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_LEN
    )


# --- session and enrol tokens ----------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionClaims:
    user_id: str
    customer_id: str
    role: str
    scope: str  # "app" or "admin"
    session_version: int
    expires_at: int  # seconds since the epoch
    capped: bool = False  # given the 24-hour cap while idle hours were set (0040): "cap": true
    # This browser's session (0076, #502): logout writes it to ended_sessions, which every
    # request checks. Random, so a cookie can't name another browser's session.
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True, slots=True, kw_only=True)
class EnrolClaims:
    user_id: str
    customer_id: str
    session_version: int
    sealed_secret: str  # SecretBox.seal(pending secret, aad=user_id)
    expires_at: int  # seconds since the epoch


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmClaims:
    user_id: str
    customer_id: str
    session_version: int
    expires_at: int  # seconds since the epoch
    session_id: str  # the browser session that typed the password (A4, 7 Nov 2026)


_SESSION_PURPOSE = b"session"
_ENROL_PURPOSE = b"enrol"
_CONFIRM_PURPOSE = b"confirm"
_SESSION_FIELDS = frozenset({"uid", "cid", "role", "scope", "ver", "exp", "sid"})
_ENROL_FIELDS = frozenset({"uid", "cid", "ver", "sec", "exp"})
_CONFIRM_FIELDS = frozenset({"uid", "cid", "ver", "exp", "sid"})
_OPERATOR_PURPOSE = b"operator"
_OPERATOR_FIELDS = frozenset({"oid", "ver", "exp"})
_MOVE_PURPOSE = b"operator_move"
_MOVE_FIELDS = frozenset({"oid", "ver", "sec", "exp"})
_FLOW_PURPOSE = b"oidc_flow"
_FLOW_FIELDS = frozenset({"p", "m", "st", "no", "ve", "nx", "uid", "cid", "ver", "exp"})
_PENDING_PURPOSE = b"second_step"
_PENDING_FIELDS = frozenset({"p", "uid", "cid", "ver", "exp"})
_NOTICE_PURPOSE = b"oidc_notice"
_NOTICE_FIELDS = frozenset({"em", "exp"})


def session_seconds(scope: str, settings: Settings, idle_hours: int | None = None) -> int:
    """How long a session of this scope lasts: ADMIN_SESSION_MINUTES or SESSION_HOURS; with an
    idle timeout set (0040, Tushar 18 Oct 2026) CAPPED_SECONDS for both, as the idle timeout ends it."""
    if scope not in SCOPES:
        raise ValueError("unknown session scope")
    if idle_hours is not None:
        return CAPPED_SECONDS
    if scope == "admin":
        return settings.admin_session_minutes * 60
    if scope == "app":
        return settings.session_hours * 3600
    raise ValueError("unknown session scope")


def new_session(
    *,
    user_id: str,
    customer_id: str,
    role: str,
    scope: str,
    session_version: int,
    settings: Settings,
    now: int,
    idle_hours: int | None = None,
) -> SessionClaims:
    """Claims for a new session that ends one scope lifetime after ``now``."""
    return SessionClaims(
        user_id=user_id,
        customer_id=customer_id,
        role=role,
        scope=scope,
        session_version=session_version,
        expires_at=now + session_seconds(scope, settings, idle_hours),
        capped=idle_hours is not None,
    )


def sign_session(claims: SessionClaims, secret: str) -> str:
    return _sign(
        {
            "uid": claims.user_id,
            "cid": claims.customer_id,
            "role": claims.role,
            "scope": claims.scope,
            "ver": claims.session_version,
            "exp": claims.expires_at,
            "sid": claims.session_id,
            **({"cap": True} if claims.capped else {}),  # left out otherwise: older cookies stay valid
        },
        _SESSION_PURPOSE,
        secret,
    )


def read_session(token: str, secret: str, now: int) -> SessionClaims | None:
    """The claims of a genuine, unexpired session token, else None. Constant-time check."""
    return _claims(_verified(token, _SESSION_PURPOSE, secret), now)


def new_enrol(
    *, user_id: str, customer_id: str, session_version: int, sealed_secret: str, now: int
) -> EnrolClaims:
    """Claims for an enrol token that ends ENROL_SECONDS (10 minutes) after ``now``."""
    return EnrolClaims(
        user_id=user_id,
        customer_id=customer_id,
        session_version=session_version,
        sealed_secret=sealed_secret,
        expires_at=now + ENROL_SECONDS,
    )


def sign_enrol(claims: EnrolClaims, secret: str) -> str:
    return _sign(
        {
            "uid": claims.user_id,
            "cid": claims.customer_id,
            "ver": claims.session_version,
            "sec": claims.sealed_secret,
            "exp": claims.expires_at,
        },
        _ENROL_PURPOSE,
        secret,
    )


def read_enrol(token: str, secret: str, now: int) -> EnrolClaims | None:
    """The claims of a genuine, unexpired enrol token, else None. Constant-time check."""
    return _enrol_claims(_verified(token, _ENROL_PURPOSE, secret), now)


def sign_confirm(claims: ConfirmClaims, secret: str) -> str:
    return _sign(
        {
            "uid": claims.user_id,
            "cid": claims.customer_id,
            "ver": claims.session_version,
            "exp": claims.expires_at,
            "sid": claims.session_id,
        },
        _CONFIRM_PURPOSE,
        secret,
    )


def read_confirm(token: str, secret: str, now: int) -> ConfirmClaims | None:
    """The claims of a genuine, unexpired confirm token, else None. Strict: exactly
    the five fields, each of its type. Constant-time check."""
    data = _verified(token, _CONFIRM_PURPOSE, secret)
    if not isinstance(data, dict) or set(data) != _CONFIRM_FIELDS:
        return None
    uid, cid, ver, exp, sid = data["uid"], data["cid"], data["ver"], data["exp"], data["sid"]
    if not all(isinstance(value, str) and _is_uuid(value) for value in (uid, cid, sid)):
        return None
    if not (_is_version(ver) and _is_int(exp)) or exp <= now:
        return None
    return ConfirmClaims(user_id=uid, customer_id=cid, session_version=ver, expires_at=exp, session_id=sid)


# --- the platform operator's tokens (super admin PR 2, PROVISIONAL): other purposes and
# fields, so customer and operator tokens never read as each other. One SESSION_SECRET.


@dataclass(frozen=True, slots=True, kw_only=True)
class OperatorClaims:
    operator_id: str
    session_version: int
    expires_at: int  # seconds since the epoch


def sign_operator(claims: OperatorClaims, secret: str) -> str:
    """An operator session token: exactly ``{oid, ver, exp}``, MAC purpose ``operator``."""
    payload = {"oid": claims.operator_id, "ver": claims.session_version, "exp": claims.expires_at}
    return _sign(payload, _OPERATOR_PURPOSE, secret)


def read_operator(token: str, secret: str, now: int) -> OperatorClaims | None:
    """The claims of a genuine, unexpired operator session token, else None. Strict:
    exactly its three fields, each of its type. Constant-time check."""
    data = _verified(token, _OPERATOR_PURPOSE, secret)
    if not isinstance(data, dict) or set(data) != _OPERATOR_FIELDS:
        return None
    oid, ver, exp = data["oid"], data["ver"], data["exp"]
    if not (isinstance(oid, str) and _is_uuid(oid) and _is_version(ver) and _is_int(exp)) or exp <= now:
        return None
    return OperatorClaims(operator_id=oid, session_version=ver, expires_at=exp)


@dataclass(frozen=True, slots=True, kw_only=True)
class MoveClaims:
    """Moving the operator's two-step to a new device (0031): the new secret, sealed."""

    operator_id: str
    session_version: int
    sealed_secret: str  # SecretBox.seal(new secret, aad=operator_id)
    expires_at: int  # seconds since the epoch


def sign_move(claims: MoveClaims, secret: str) -> str:
    """A move token: exactly ``{oid, ver, sec, exp}``, MAC purpose ``operator_move``."""
    payload = {
        "oid": claims.operator_id,
        "ver": claims.session_version,
        "sec": claims.sealed_secret,
        "exp": claims.expires_at,
    }
    return _sign(payload, _MOVE_PURPOSE, secret)


def read_move(token: str, secret: str, now: int) -> MoveClaims | None:
    """The claims of a genuine, unexpired move token, else None. Strict, as ``read_operator``."""
    data = _verified(token, _MOVE_PURPOSE, secret)
    if not isinstance(data, dict) or set(data) != _MOVE_FIELDS:
        return None
    oid, ver, sec, exp = data["oid"], data["ver"], data["sec"], data["exp"]
    if not (isinstance(oid, str) and _is_uuid(oid) and isinstance(sec, str) and sec):
        return None
    if not (_is_version(ver) and _is_int(exp)) or exp <= now:
        return None
    return MoveClaims(operator_id=oid, session_version=ver, sealed_secret=sec, expires_at=exp)


# --- the provider sign-in's tokens (#457 phase 1): their own purposes and fields, so none
# reads as a session or as each other. All three live on OIDC_PATH only.


@dataclass(frozen=True, slots=True, kw_only=True)
class FlowClaims:
    """One sign-in or connect at a provider, from its start to its callback (single use)."""

    provider: str
    mode: str  # "signin", "connect", or "locked_open" (#605: Google proves it is them, for locked files)
    state: str
    nonce: str
    verifier: str  # the PKCE verifier
    next_path: str
    user_id: str | None  # a connect's: the signed-in user, customer and session version
    customer_id: str | None
    session_version: int | None
    expires_at: int  # seconds since the epoch


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingClaims:
    """A right provider sign-in for someone with two-step sign-in on: the code step is next."""

    provider: str
    user_id: str
    customer_id: str
    session_version: int
    expires_at: int  # seconds since the epoch


def sign_flow(claims: FlowClaims, secret: str) -> str:
    """A flow token: exactly ``{p, m, st, no, ve, nx, uid, cid, ver, exp}``, purpose ``oidc_flow``."""
    payload = {
        "p": claims.provider,
        "m": claims.mode,
        "st": claims.state,
        "no": claims.nonce,
        "ve": claims.verifier,
        "nx": claims.next_path,
        "uid": claims.user_id,
        "cid": claims.customer_id,
        "ver": claims.session_version,
        "exp": claims.expires_at,
    }
    return _sign(payload, _FLOW_PURPOSE, secret)


def read_flow(token: str, secret: str, now: int) -> FlowClaims | None:
    """The claims of a genuine, unexpired flow token, else None. Strict: exactly its fields,
    each of its type; a connect (or a locked files' confirm, #605) carries its user, customer and
    session version, a sign-in none."""
    data = _verified(token, _FLOW_PURPOSE, secret)
    if not isinstance(data, dict) or set(data) != _FLOW_FIELDS:
        return None
    texts = [data[name] for name in ("p", "st", "no", "ve", "nx")]
    if not all(isinstance(value, str) and value for value in texts):
        return None
    uid, cid, ver, exp = data["uid"], data["cid"], data["ver"], data["exp"]
    if not _is_int(exp) or exp <= now:
        return None
    if data["m"] in ("connect", "locked_open"):
        if not (isinstance(uid, str) and isinstance(cid, str) and _is_uuid(uid) and _is_uuid(cid)):
            return None
        if not _is_version(ver):
            return None
    elif data["m"] != "signin" or (uid, cid, ver) != (None, None, None):
        return None
    provider, state, nonce, verifier, next_path = texts
    return FlowClaims(
        provider=provider,
        mode=data["m"],
        state=state,
        nonce=nonce,
        verifier=verifier,
        next_path=next_path,
        user_id=uid,
        customer_id=cid,
        session_version=ver,
        expires_at=exp,
    )


def sign_pending(claims: PendingClaims, secret: str) -> str:
    """A second-step token: exactly ``{p, uid, cid, ver, exp}``, purpose ``second_step``."""
    payload = {
        "p": claims.provider,
        "uid": claims.user_id,
        "cid": claims.customer_id,
        "ver": claims.session_version,
        "exp": claims.expires_at,
    }
    return _sign(payload, _PENDING_PURPOSE, secret)


def read_pending(token: str, secret: str, now: int) -> PendingClaims | None:
    """The claims of a genuine, unexpired second-step token, else None. Strict."""
    data = _verified(token, _PENDING_PURPOSE, secret)
    if not isinstance(data, dict) or set(data) != _PENDING_FIELDS:
        return None
    provider, uid, cid, ver, exp = data["p"], data["uid"], data["cid"], data["ver"], data["exp"]
    if not (isinstance(provider, str) and provider and isinstance(uid, str) and isinstance(cid, str)):
        return None
    if not (_is_uuid(uid) and _is_uuid(cid) and _is_version(ver) and _is_int(exp)) or exp <= now:
        return None
    return PendingClaims(provider=provider, user_id=uid, customer_id=cid, session_version=ver, expires_at=exp)


def sign_notice(email: str, expires_at: int, secret: str) -> str:
    """The "isn't connected" notice: exactly ``{em, exp}``, purpose ``oidc_notice``. It carries
    the provider's email to the sign-in page without putting it in a URL."""
    return _sign({"em": email, "exp": expires_at}, _NOTICE_PURPOSE, secret)


def read_notice(token: str, secret: str, now: int) -> str | None:
    """The email of a genuine, unexpired notice token, else None. Strict."""
    data = _verified(token, _NOTICE_PURPOSE, secret)
    if not isinstance(data, dict) or set(data) != _NOTICE_FIELDS:
        return None
    email, exp = data["em"], data["exp"]
    if not (isinstance(email, str) and email and _is_int(exp)) or exp <= now:
        return None
    return email


def _set_oidc_cookie(response: Response, name: str, token: str, max_age: int, settings: Settings) -> None:
    """HttpOnly, SameSite=Lax (the provider's redirect back is a top-level GET from its site),
    sent only to OIDC_PATH."""
    response.set_cookie(
        name,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=OIDC_PATH,
    )


def _clear_oidc_cookie(response: Response, name: str, settings: Settings) -> None:
    response.delete_cookie(name, path=OIDC_PATH, httponly=True, samesite="lax", secure=settings.cookie_secure)


def set_flow_cookie(response: Response, claims: FlowClaims, settings: Settings, *, now: int) -> None:
    _set_oidc_cookie(
        response, OIDC_COOKIE, sign_flow(claims, settings.session_secret), claims.expires_at - now, settings
    )


def clear_flow_cookie(response: Response, settings: Settings) -> None:
    _clear_oidc_cookie(response, OIDC_COOKIE, settings)


def set_pending_cookie(response: Response, claims: PendingClaims, settings: Settings, *, now: int) -> None:
    _set_oidc_cookie(
        response,
        PENDING_COOKIE,
        sign_pending(claims, settings.session_secret),
        claims.expires_at - now,
        settings,
    )


def clear_pending_cookie(response: Response, settings: Settings) -> None:
    _clear_oidc_cookie(response, PENDING_COOKIE, settings)


def set_notice_cookie(response: Response, email: str, settings: Settings, *, now: int) -> None:
    token = sign_notice(email, now + NOTICE_SECONDS, settings.session_secret)
    _set_oidc_cookie(response, NOTICE_COOKIE, token, NOTICE_SECONDS, settings)


def clear_notice_cookie(response: Response, settings: Settings) -> None:
    _clear_oidc_cookie(response, NOTICE_COOKIE, settings)


def set_operator_cookie(response: Response, claims: OperatorClaims, settings: Settings, *, now: int) -> None:
    """HttpOnly, SameSite=Strict, sent only to /api/operator, for the token's own lifetime."""
    response.set_cookie(
        OPERATOR_COOKIE,
        sign_operator(claims, settings.session_secret),
        max_age=claims.expires_at - now,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path=OPERATOR_PATH,
    )


def clear_operator_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        OPERATOR_COOKIE, path=OPERATOR_PATH, httponly=True, samesite="strict", secure=settings.cookie_secure
    )


def _sign(payload: dict[str, Any], purpose: bytes, secret: str) -> str:
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return body + "." + _b64url(_mac(purpose, body, secret))


def _verified(token: str, purpose: bytes, secret: str) -> Any:
    """The decoded JSON body of a token whose MAC is right for this purpose, else None."""
    body, dot, signature = token.partition(".")
    if not dot or not body or not signature or not body.isascii():
        return None
    try:
        given = _unb64url(signature)
        if not hmac.compare_digest(given, _mac(purpose, body, secret)):
            return None
        return json.loads(_unb64url(body))
    except (binascii.Error, ValueError):
        return None


def _claims(data: Any, now: int) -> SessionClaims | None:
    """Strict: exactly the seven fields, each of its type, and "cap" (only ever true, 0040);
    scope "admin" needs role "admin". A cookie from before 0076 has no "sid": refused, so its
    browser signs in once more."""
    if (
        not isinstance(data, dict)
        or set(data) - {"cap"} != _SESSION_FIELDS
        or data.get("cap", True) is not True
    ):
        return None
    uid, cid, role, scope = data["uid"], data["cid"], data["role"], data["scope"]
    ver, exp, sid = data["ver"], data["exp"], data["sid"]
    if not (isinstance(uid, str) and isinstance(cid, str) and isinstance(role, str) and isinstance(sid, str)):
        return None
    if not isinstance(scope, str) or scope not in SCOPES or role not in ROLES:
        return None
    if scope == "admin" and role != "admin":
        return None
    if not (_is_version(ver) and _is_int(exp)) or exp <= now:
        return None
    if not (_is_uuid(uid) and _is_uuid(cid) and _is_uuid(sid)):
        return None
    return SessionClaims(
        user_id=uid,
        customer_id=cid,
        role=role,
        scope=scope,
        session_version=ver,
        expires_at=exp,
        capped="cap" in data,
        session_id=sid,
    )


def _enrol_claims(data: Any, now: int) -> EnrolClaims | None:
    """Strict: exactly the five fields, each of its type."""
    if not isinstance(data, dict) or set(data) != _ENROL_FIELDS:
        return None
    uid, cid, ver, sec, exp = data["uid"], data["cid"], data["ver"], data["sec"], data["exp"]
    if not (isinstance(uid, str) and isinstance(cid, str) and isinstance(sec, str) and sec):
        return None
    if not (_is_version(ver) and _is_int(exp)) or exp <= now:
        return None
    if not (_is_uuid(uid) and _is_uuid(cid)):
        return None
    return EnrolClaims(user_id=uid, customer_id=cid, session_version=ver, sealed_secret=sec, expires_at=exp)


def _is_int(value: object) -> bool:
    """A JSON whole number: an int and not a bool (``True`` is an int in Python)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_version(value: object) -> bool:
    """A session version: a whole number of at least 1, like the column's CHECK."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _mac(purpose: bytes, body: str, secret: str) -> bytes:
    return hmac.new(secret.encode(), purpose + b"\x00" + body.encode("ascii"), hashlib.sha256).digest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    if not text.isascii():
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def session_claims(request: Request, settings: Settings, now: int) -> SessionClaims | None:
    """The request's signed claims, without touching the database."""
    token = request.cookies.get(SESSION_COOKIE)
    return None if not token else read_session(token, settings.session_secret, now)


def set_session_cookie(response: Response, claims: SessionClaims, settings: Settings, *, now: int) -> None:
    """Sign ``claims`` and set them as the cookie. Its max-age is the token's own
    remaining lifetime (``expires_at - now``), so the two never disagree."""
    max_age = claims.expires_at - now
    if max_age <= 0:
        raise ValueError("the session has already expired")
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(claims, settings.session_secret),
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=settings.cookie_secure
    )


# --- who is calling ------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CurrentUser:
    user_id: str
    customer_id: str
    email: str
    display_name: str
    role: str
    customer_name: str
    scope: str  # the session's: "app" or "admin"
    session_version: int
    must_change_password: bool
    totp_enabled: bool  # two-step sign-in is on (required for an admin, unless turned off)
    delete_forever_allowed: bool  # an admin allowed Delete forever (ignored for an admin)
    two_step_turned_off: bool = False  # the admin turned it off themselves (0061b)
    two_step_required: bool = False  # the organisation requires it for admins (0068, #481)
    idle_hours: int | None = None  # the idle timeout in effect (0040); None: no idle limit
    seen_at: datetime | None = None  # the session's last use or sign-in, the idle clock
    email_confirmed: bool = False  # the user proved their e-mail with a link (0061)
    # Google sign-in (#507, ``users._GOOGLE_COLUMNS``): the organisation's switch, else the
    # platform's; the role may use Google; uses it only; a passing link; the domains; an approved
    # link whose domain is not checked yet (``users.google_unchecked``).
    google_on: bool = True
    google_allowed: bool = True
    google_only: bool = False
    google_linked: bool = False
    google_domains: tuple[str, ...] = ()
    google_unchecked: bool = False


GOOGLE_LINK_REQUIRED = (
    "Your organisation asks you to sign in with Google. Connect your Google account to continue."
)


def needs_google_link(user: CurrentUser, google_enabled: bool) -> bool:
    """The limited session (#507): Google is on (``google_enabled``, GOOGLE_SIGNIN_ENABLED, and
    the organisation's or platform's switch) and the role uses Google only, with no passing link."""
    return google_enabled and user.google_on and user.google_only and not user.google_linked


# The idle timeout (0040): the last use is written at most this often, so a request is not a write.
TOUCH_EVERY = timedelta(minutes=1)


def idle_over(seen_at: datetime | None, idle_hours: int | None, now: datetime) -> bool:
    """Unused for longer than the idle timeout. No timeout: never. Every sign-in stamps
    ``last_login_at``, so a live session always has a ``seen_at``; with none, never too."""
    return idle_hours is not None and seen_at is not None and now - seen_at > timedelta(hours=idle_hours)


def cap_outlived(claims: SessionClaims, idle_hours: int | None, settings: Settings, now: int) -> bool:
    """A cookie given the 24-hour cap while hours were set ("cap"), once the hours are removed:
    it may not outlive the normal length. It began CAPPED_SECONDS before its end, and ends one
    normal length after that."""
    normal = session_seconds(claims.scope, settings)
    return idle_hours is None and claims.capped and now >= claims.expires_at - CAPPED_SECONDS + normal


def touch_due(seen_at: datetime | None, now: datetime) -> bool:
    """The last use or sign-in on record is a minute old or more."""
    return seen_at is not None and now - seen_at >= TOUCH_EVERY


def set_confirm_cookie(
    response: Response, user: CurrentUser, settings: Settings, *, now: int, session_id: str
) -> None:
    """A confirm token for this user, session version and browser session, CONFIRM_SECONDS long."""
    claims = ConfirmClaims(
        user_id=user.user_id,
        customer_id=user.customer_id,
        session_version=user.session_version,
        expires_at=now + CONFIRM_SECONDS,
        session_id=session_id,
    )
    response.set_cookie(
        CONFIRM_COOKIE,
        sign_confirm(claims, settings.session_secret),
        max_age=CONFIRM_SECONDS,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path=CONFIRM_PATH,
    )


def current_user(request: Request) -> CurrentUser:
    """The signed-in user, re-checked in the database on every request.

    401 ``not_signed_in`` unless the session is genuine and still matches the
    user (enabled, same role, same session version). Then 403
    ``password_change_required`` while the user must set a new password, and 403
    ``mfa_enrol_required`` for an admin without two-step sign-in set up (one who turned it
    off themselves, 0061b, signs in with the password and is let through). Then 403
    ``google_link_required`` in the limited session (#507, ``needs_google_link``)."""
    user = current_user_allow_google_link(request)
    if needs_google_link(user, get_context(request).settings.google_signin_enabled):
        raise ApiError(403, "google_link_required", GOOGLE_LINK_REQUIRED)
    return user


def current_user_allow_google_link(request: Request) -> CurrentUser:
    """``current_user`` without the limited session's gate (#507). Only for connecting Google
    (its start and callback), Ways to sign in's list and remove, and sign out everywhere."""
    user = _signed_in(request)
    if user.must_change_password:
        raise ApiError(403, "password_change_required")
    if user.role == "admin" and not user.totp_enabled and not user.two_step_turned_off:
        raise ApiError(403, "mfa_enrol_required")
    return user


def current_user_allow_password_change(request: Request) -> CurrentUser:
    """``current_user`` without the password-change gate. Only for /auth/password,
    /auth/logout, /auth/me and /auth/session."""
    return _signed_in(request)


def _signed_in(request: Request) -> CurrentUser:
    context = get_context(request)
    now = context.clock()
    claims = session_claims(request, context.settings, int(now.timestamp()))
    if claims is None:
        raise ApiError(401, "not_signed_in")
    user = load_user(
        context.db, claims.customer_id, claims.user_id, scope=claims.scope, session_id=claims.session_id
    )
    if user is None or user.role != claims.role or user.session_version != claims.session_version:
        raise ApiError(401, "not_signed_in")
    if user.role == "admin" and claims.scope != "admin":
        # An admin's app session from before 16 Oct 2026 rested on the password alone.
        raise ApiError(401, "not_signed_in")
    if cap_outlived(claims, user.idle_hours, context.settings, int(now.timestamp())):
        raise ApiError(401, "not_signed_in")
    if idle_over(user.seen_at, user.idle_hours, now):  # the super admin's idle timeout (0040)
        raise ApiError(401, "not_signed_in")
    if touch_due(user.seen_at, now):
        # ponytail: one last use per user, not per browser; a use or a sign-in anywhere keeps
        # every session of that user alive. Per session needs a server-side session row.
        with context.db.user(user.customer_id, user.user_id) as conn:
            users_sql.touch_seen(conn, user.customer_id, user.user_id, now=now)
    return user


def load_user(
    db: Database, customer_id: str, user_id: str, *, scope: str, session_id: str | None = None
) -> CurrentUser | None:
    """The enabled user with this id in this customer, or None (also while the
    customer is not active, 0014, and while ``session_id``, a request's session, was ended
    by logout, 0076). ``scope`` is the session's (at sign-in, the one being started). One
    indexed query."""
    with db.customer(customer_id) as conn:
        row = users_sql.session_user(conn, customer_id, user_id, session_id=session_id)
    if row is None or row["disabled"]:
        return None
    return CurrentUser(
        user_id=str(row["id"]),
        customer_id=str(row["customer_id"]),
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        role=str(row["role"]),
        customer_name=str(row["customer_name"]),
        scope=scope,
        session_version=int(row["session_version"]),
        must_change_password=bool(row["must_change_password"]),
        totp_enabled=bool(row["totp_enabled"]),
        delete_forever_allowed=bool(row["delete_forever_allowed"]),
        two_step_turned_off=bool(row["two_step_turned_off"]),
        two_step_required=bool(row["two_step_required"]),
        idle_hours=row["idle_hours"],
        seen_at=row["seen_at"],
        email_confirmed=bool(row["email_confirmed"]),
        google_on=bool(row["google_on"]),
        google_allowed=bool(row["google_allowed"]),
        google_only=bool(row["google_only"]),
        google_linked=users_sql.google_linked(row),
        google_domains=tuple(row["google_domains"]),
        google_unchecked=users_sql.google_unchecked(row),
    )


User = Annotated[CurrentUser, Depends(current_user)]
UserAllowGoogleLink = Annotated[CurrentUser, Depends(current_user_allow_google_link)]
UserAllowPasswordChange = Annotated[CurrentUser, Depends(current_user_allow_password_change)]


def require_admin_session(user: User) -> CurrentUser:
    """For the /admin router: an admin's session (scope "admin", from /login), else 403
    ``admin_session_required`` (an admin's own app session included)."""
    if user.scope != "admin" or user.role != "admin":
        raise ApiError(403, "admin_session_required")
    return user


AdminSession = Annotated[CurrentUser, Depends(require_admin_session)]


def require_admin_with_two_step(user: AdminSession) -> CurrentUser:
    """For what can't be undone (deleting a tag; ``require_delete_forever`` lets an admin
    without two-step type their password instead, #538): an admin session whose admin has
    two-step sign-in on, else 403
    ``two_step_required``. ``current_user`` lets through only an admin with two-step
    sign-in on or one who turned it off themselves (0061b), whom this refuses."""
    if not user.totp_enabled:
        raise ApiError(403, "two_step_required", "Turn on two-step sign-in in Account to do this.")
    return user


AdminWithTwoStep = Annotated[CurrentUser, Depends(require_admin_with_two_step)]


def deletes_forever_by_password(user: CurrentUser) -> bool:
    """Who types their password again to delete forever (``require_delete_forever``): a member
    or reviewer an admin allowed, or an admin in an admin session without two-step sign-in on
    (never while the organisation requires it)."""
    if user.role == "admin":
        return user.scope == "admin" and not user.totp_enabled and not user.two_step_required
    return user.delete_forever_allowed


def require_delete_forever(request: Request, user: User) -> CurrentUser:
    """For deleting a document forever (council 4 Oct 2026; #538, Tushar, 7 Nov 2026). An admin
    needs an admin session (403 ``admin_session_required``): with two-step sign-in on, nothing
    more; without it, a confirm token, as below. A grant never counts for an admin. While the
    organisation requires two-step for its admins (0068, #481) one without it is refused as
    before (403 ``two_step_required``; ``current_user`` already refuses that state). Anyone else
    needs an admin's grant (403 ``forbidden``). The confirm token comes from typing the password
    in the last CONFIRM_SECONDS, for this user, session version and browser session (its ``sid``,
    A4: never another browser's, nor one from before signing out and in again; 403
    ``password_confirm_required``). The grant is read again on every request, so stopping it
    works at once."""
    if user.role == "admin" and not deletes_forever_by_password(require_admin_session(user)):
        return require_admin_with_two_step(user)
    if not deletes_forever_by_password(user):
        raise ApiError(403, "forbidden")
    context = get_context(request)
    now = int(context.clock().timestamp())
    token = request.cookies.get(CONFIRM_COOKIE) or ""
    claims = read_confirm(token, context.settings.session_secret, now)
    session = session_claims(request, context.settings, now)
    signed = (
        None
        if claims is None
        else (claims.user_id, claims.customer_id, claims.session_version, claims.session_id)
    )
    if session is None or signed != (
        user.user_id,
        user.customer_id,
        user.session_version,
        session.session_id,
    ):
        raise ApiError(403, "password_confirm_required")
    return user


CanDeleteForever = Annotated[CurrentUser, Depends(require_delete_forever)]


def require_role(*roles: str) -> Callable[[CurrentUser], CurrentUser]:
    """A dependency that lets only these roles through (403 ``forbidden``)."""
    allowed = frozenset(roles)

    def dependency(user: User) -> CurrentUser:
        if user.role not in allowed:
            raise ApiError(403, "forbidden")
        return user

    return dependency


Reviewer = Annotated[CurrentUser, Depends(require_role("reviewer", "admin"))]
Admin = Annotated[CurrentUser, Depends(require_role("admin"))]

FEATURE_OFF = "This is turned off for now."


def feature_on(context: AppContext, feature: str, user: CurrentUser | None = None) -> bool:
    """The super admin's switch for ``feature`` (0038) and ``user``'s organisation's own (0039;
    None: the platform's alone); no row: on. One statement."""
    with context.db.system() if user is None else context.db.user(user.customer_id, user.user_id) as conn:
        return ai_settings.switch_on(conn, feature, None if user is None else user.customer_id)


def require_feature(feature: str) -> Callable[[Request, CurrentUser], None]:
    """A dependency: 403 ``feature_off`` while the super admin switched ``feature`` off for
    every organisation (0038) or for the user's own (0039)."""

    def dependency(request: Request, user: User) -> None:
        if not feature_on(get_context(request), feature, user):
            raise ApiError(403, "feature_off", FEATURE_OFF)

    return dependency


@dataclass(frozen=True, slots=True)
class CurrentOperator:
    operator_id: str
    email: str
    display_name: str
    expires_at: int  # the session's, seconds since the epoch
    session_version: int  # the session's (a two-step move binds its token to it)


def current_operator(request: Request) -> CurrentOperator:
    """For the signed-in /api/operator routes: the operator cookie ONLY (never the
    customer session), re-checked in the database on every request: enabled, set up
    for two-step, same session version. Else 401 ``not_signed_in``."""
    context = get_context(request)
    token = request.cookies.get(OPERATOR_COOKIE) or ""
    claims = read_operator(token, context.settings.session_secret, int(context.clock().timestamp()))
    if claims is None:
        raise ApiError(401, "not_signed_in")
    with get_operator_db(request).operator() as conn:
        found = operators_sql.session_operator(conn, claims.operator_id)
    if found is None or found[2] != claims.session_version:
        raise ApiError(401, "not_signed_in")
    return CurrentOperator(claims.operator_id, found[0], found[1], claims.expires_at, claims.session_version)


OperatorSession = Annotated[CurrentOperator, Depends(current_operator)]


def csrf_guard(request: Request) -> None:
    """Applied to every route: a state-changing request needs the CSRF header."""
    if request.method in _SAFE_METHODS:
        return
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise ApiError(403, "csrf")
