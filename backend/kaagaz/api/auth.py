"""Sign-in: the session token, who the caller is, and CSRF. Keycloak is the only sign-in for company
users (21 Nov 2026): ``routers/keycloak.py`` starts and ends it; the operator has its own.

Why each piece (FULL-BUILD-SPEC section 3, PRD 1 A1 to A7):

* **The session** is a cookie ``<payload b64url>.<HMAC-SHA256 b64url>``. The
  payload is ``{"uid","cid","role","scope","ver","exp","sid","src","acr"}`` and the MAC is over
  ``b"session\x00" + body``. ``scope`` is "app" (a member or reviewer) or "admin" (every admin
  session is one); ``ver`` is the user's ``session_version``. Nothing is stored server side, so
  every request re-checks, with one query inside ``db.customer(cid)``, that
  the user still exists, is not disabled, still has that role and still has
  that session version. Raising the version ("sign out everywhere", disabling) ends every other
  session at its next request.
* **The idle timeout** (0040): the same query reads the hours the super admin set (the
  organisation's, else the platform's; none saved: no idle limit) and the user's last use
  or sign-in. Unused for longer ends the session (401 ``not_signed_in``); a use writes
  ``last_seen_at`` at most once a minute. ``exp`` stays the outer cap: 24 hours for a sign-in
  while hours are set (member and admin alike), else SESSION_HOURS or ADMIN_SESSION_MINUTES.
* **The confirm token** (council 4 Oct 2026, PROVISIONAL): a granted member or
  reviewer's fresh Keycloak proof for Delete forever. ``{"uid","cid","ver","exp","sid"}``,
  MAC over ``b"confirm\x00" + body``, 5 minutes, an HttpOnly SameSite=Strict
  cookie for /api/documents only.
* **Strict claims**: a token with a missing, extra or wrongly typed field is
  refused, so a Sprint 1 cookie (no scope, no ver) is refused and its user
  signs in once more.
* **The customer always comes from the session**, never from the request: the
  signed ``cid`` is the only customer id any route uses.
* **CSRF**: every request that is not GET, HEAD or OPTIONS must carry
  ``X-Requested-With: kaagaz``. A cross-site form cannot add a header, and a
  cross-site script cannot either without CORS, which the API never allows.

PROVISIONAL (security configuration is Vrushit's): logout only clears this
browser's cookie; "sign out everywhere" is what ends a copied token early.
"""

import base64
import binascii
import dataclasses
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
KEYCLOAK_PROOF_COOKIE = "kaagaz_keycloak_proof"
KEYCLOAK_PROOF_SECONDS = 5 * 60
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "kaagaz"
ROLES = ("member", "reviewer", "admin")
ENROL_SECONDS = 10 * 60  # the operator's two-step move token (routers/operator.py)
SCOPES = ("app", "admin")
CONFIRM_COOKIE = "kaagaz_confirm"
CONFIRM_SECONDS = 5 * 60  # PROVISIONAL (council 4 Oct 2026)
CONFIRM_PATH = "/api/documents"  # the browser calls the API on its own origin, under /api
OPERATOR_COOKIE = "kaagaz_operator"
OPERATOR_PATH = "/api/operator"
OPERATOR_SESSION_SECONDS = 30 * 60  # absolute, PROVISIONAL (change plan question 4)
# Signing in with a provider (#457 phase 1): the flow from start to callback, the code step
# after it for anyone with two-step sign-in on, and the "isn't connected" notice for the page.
# A session's end while an idle timeout is set (0040): the idle timeout, 1 to 24 hours, ends it.
# Cookies issued before keep the end they were given.
CAPPED_SECONDS = 24 * 3600
WHATSAPP_WEBHOOK_PATH = "/api/whatsapp/webhook"  # #603, routers/whatsapp.py
KEYCLOAK_BACKCHANNEL_PATH = "/api/auth/keycloak/backchannel-logout"
# WhatsApp's Connect (#603 W3): the person proved it is them (the password and code, or Google), so for
# this long they may ask for connect codes; the cookie goes to /api/whatsapp only.
WHATSAPP_PATH = "/api/whatsapp"
WHATSAPP_PROOF_COOKIE = "kaagaz_whatsapp_proof"
WHATSAPP_PROOF_SECONDS = 10 * 60

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
    auth_source: str = "legacy_password"
    assurance: str | None = None
    capped: bool = False  # given the 24-hour cap while idle hours were set (0040): "cap": true
    # This browser's session (0076, #502): logout writes it to ended_sessions, which every
    # request checks. Random, so a cookie can't name another browser's session.
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmClaims:
    user_id: str
    customer_id: str
    session_version: int
    expires_at: int  # seconds since the epoch
    session_id: str  # the browser session that typed the password (A4, 7 Nov 2026)


_SESSION_PURPOSE = b"session"
_CONFIRM_PURPOSE = b"confirm"
_SESSION_FIELDS = frozenset({"uid", "cid", "role", "scope", "ver", "exp", "sid", "src", "acr"})
_CONFIRM_FIELDS = frozenset({"uid", "cid", "ver", "exp", "sid"})
_OPERATOR_PURPOSE = b"operator"
_OPERATOR_FIELDS = frozenset({"oid", "ver", "exp"})
_MOVE_PURPOSE = b"operator_move"
_MOVE_FIELDS = frozenset({"oid", "ver", "sec", "exp"})
_WHATSAPP_PROOF_PURPOSE = b"whatsapp_proof"
_WHATSAPP_PROOF_FIELDS = frozenset({"uid", "cid", "ver", "exp"})
_KEYCLOAK_PROOF_PURPOSE = b"keycloak_action_proof"
_KEYCLOAK_PROOF_FIELDS = frozenset({"uid", "cid", "ver", "sid", "action", "at", "acr", "exp"})
KEYCLOAK_PROOF_ACTIONS = frozenset({"locked_open", "delete_forever", "whatsapp", "access", "locks"})


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
    auth_source: str = "legacy_password",
    assurance: str | None = None,
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
        auth_source=auth_source,
        assurance=assurance,
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
            "src": claims.auth_source,
            "acr": claims.assurance,
            **({"cap": True} if claims.capped else {}),  # left out otherwise: older cookies stay valid
        },
        _SESSION_PURPOSE,
        secret,
    )


def read_session(token: str, secret: str, now: int) -> SessionClaims | None:
    """The claims of a genuine, unexpired session token, else None. Constant-time check."""
    return _claims(_verified(token, _SESSION_PURPOSE, secret), now)


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


def sign_whatsapp_proof(user: "CurrentUser", expires_at: int, secret: str) -> str:
    """#603: exactly ``{uid, cid, ver, exp}``, purpose ``whatsapp_proof``."""
    payload = {"uid": user.user_id, "cid": user.customer_id, "ver": user.session_version, "exp": expires_at}
    return _sign(payload, _WHATSAPP_PROOF_PURPOSE, secret)


def whatsapp_proved(token: str, user: "CurrentUser", secret: str, now: int) -> bool:
    """Whether ``token`` is a genuine, unexpired proof of this very user at this session version."""
    data = _verified(token, _WHATSAPP_PROOF_PURPOSE, secret)
    if not isinstance(data, dict) or set(data) != _WHATSAPP_PROOF_FIELDS:
        return False
    exp = data["exp"]
    if not _is_int(exp) or exp <= now:
        return False
    return (data["uid"], data["cid"], data["ver"]) == (user.user_id, user.customer_id, user.session_version)


def set_whatsapp_proof_cookie(
    response: Response, user: "CurrentUser", settings: Settings, *, now: int
) -> None:
    """HttpOnly, SameSite=Lax (it may be set on Google's redirect back), sent to /api/whatsapp only."""
    response.set_cookie(
        WHATSAPP_PROOF_COOKIE,
        sign_whatsapp_proof(user, now + WHATSAPP_PROOF_SECONDS, settings.session_secret),
        max_age=WHATSAPP_PROOF_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=WHATSAPP_PATH,
    )


def clear_whatsapp_proof_cookie(response: Response, settings: Settings) -> None:
    """Signing out ends the WhatsApp proof too (#603)."""
    response.delete_cookie(
        WHATSAPP_PROOF_COOKIE,
        path=WHATSAPP_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


# --- the 10-minute check for locked files (PRD 7 section 6, #605, PR 13) ---------------------
# The 10-minute check for locked files (PRD 7 section 6, "Opening a locked file"; decisions 5 and 6;
# #605, PR 13).
#
# Why a cookie of its own: one check opens every locked file the person may open, for 10 minutes in
# that browser, and never anything else. So it is its own signed token, ``{uid, cid, ver, sid, exp}``
# with MAC purpose ``locked_open``: the Delete forever confirm (``kaagaz_confirm``, purpose
# ``confirm``) can never open a locked file, nor this one confirm a delete (``p2:70``).
#
# * LOCKED_OPEN_SECONDS, absolute: it is set once, at the check, and never extended by use.
# * HttpOnly, Secure (as the session), SameSite=Strict, sent to /api/documents only, where the
#   page-picture route and the check's own routes live.
# * It holds for this user, organisation, session version and browser session (``sid``) only: a new
#   password, "sign out everywhere" or a switch-off raise the version; signing out ends the session
#   (0076, so the ``sid`` is another one at the next sign-in) and clears the cookie.
#
# Who confirms how (21 Nov 2026): a fresh Keycloak sign-in for ``locked_open``, nothing else
# (``routers/keycloak.py`` ``reauth_start``). O(1), no query.

LOCKED_OPEN_COOKIE = "kaagaz_locked_open"
LOCKED_OPEN_SECONDS = 10 * 60  # PRD 7 decision 6
LOCKED_OPEN_PATH = "/api/documents"
LOCKED_OPEN_FIRST = "Enter your password to open locked files."

_LOCKED_OPEN_PURPOSE = b"locked_open"
_LOCKED_OPEN_FIELDS = frozenset({"uid", "cid", "ver", "sid", "exp"})


def sign_locked_open(user: "CurrentUser", session_id: str, expires_at: int, secret: str) -> str:
    payload = {
        "uid": user.user_id,
        "cid": user.customer_id,
        "ver": user.session_version,
        "sid": session_id,
        "exp": expires_at,
    }
    return _sign(payload, _LOCKED_OPEN_PURPOSE, secret)


def locked_open_until(request: Request, user: "CurrentUser", settings: Settings, now: int) -> int | None:
    """When this browser's live check ends, else None: a genuine, unexpired ``locked_open`` token
    with exactly its fields, for this very user, organisation, session version and browser session."""
    data = _verified(
        request.cookies.get(LOCKED_OPEN_COOKIE) or "", _LOCKED_OPEN_PURPOSE, settings.session_secret
    )
    if not isinstance(data, dict) or set(data) != _LOCKED_OPEN_FIELDS:
        return None
    exp, sid = data["exp"], data["sid"]
    if (
        not (_is_int(exp) and _is_version(data["ver"]) and isinstance(sid, str) and _is_uuid(sid))
        or exp <= now
    ):
        return None
    session = session_claims(request, settings, now)
    if session is None:
        return None
    mine = (user.user_id, user.customer_id, user.session_version, session.session_id)
    if (data["uid"], data["cid"], data["ver"], sid) != mine:
        return None
    return int(exp)


def require_locked_open(request: Request, user: "CurrentUser", settings: Settings, now: int) -> None:
    """403 ``locked_open_required`` unless this browser's check is live (``locked_open_until``)."""
    if locked_open_until(request, user, settings, now) is None:
        raise ApiError(403, "locked_open_required", LOCKED_OPEN_FIRST)


def set_locked_open_cookie(
    response: Response, user: "CurrentUser", session_id: str, settings: Settings, *, until: int
) -> None:
    """The check, ending at ``until``: LOCKED_OPEN_SECONDS from now."""
    response.set_cookie(
        LOCKED_OPEN_COOKIE,
        sign_locked_open(user, session_id, until, settings.session_secret),
        max_age=LOCKED_OPEN_SECONDS,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path=LOCKED_OPEN_PATH,
    )


def clear_locked_open_cookie(response: Response, settings: Settings) -> None:
    """Signing out ends the check (decision 6)."""
    response.delete_cookie(
        LOCKED_OPEN_COOKIE,
        path=LOCKED_OPEN_PATH,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
    )


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
    source, assurance = data["src"], data["acr"]
    if not (isinstance(uid, str) and isinstance(cid, str) and isinstance(role, str) and isinstance(sid, str)):
        return None
    if not isinstance(scope, str) or scope not in SCOPES or role not in ROLES:
        return None
    if scope == "admin" and role != "admin":
        return None
    if not isinstance(source, str) or source not in {
        "legacy_password",
        "legacy_mfa",
        "legacy_google",
        "legacy_google_mfa",
        "keycloak",
    }:  # old cookies still parse, to be refused by name in ``current_user``
        return None
    if (source == "keycloak") != (isinstance(assurance, str) and bool(assurance)):
        return None
    if source != "keycloak" and assurance is not None:
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
        auth_source=source,
        assurance=assurance,
    )


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


def set_keycloak_proof_cookie(
    response: Response,
    user: "CurrentUser",
    *,
    session_id: str,
    action: str,
    auth_time: int,
    assurance: str,
    settings: Settings,
    now: int,
) -> None:
    """Set a short action-bound proof after a fresh, same-user Keycloak callback."""
    if action not in KEYCLOAK_PROOF_ACTIONS or not assurance:
        raise ValueError("invalid Keycloak reauthentication proof")
    response.set_cookie(
        KEYCLOAK_PROOF_COOKIE,
        _sign(
            {
                "uid": user.user_id,
                "cid": user.customer_id,
                "ver": user.session_version,
                "sid": session_id,
                "action": action,
                "at": auth_time,
                "acr": assurance,
                "exp": now + KEYCLOAK_PROOF_SECONDS,
            },
            _KEYCLOAK_PROOF_PURPOSE,
            settings.session_secret,
        ),
        max_age=KEYCLOAK_PROOF_SECONDS,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path="/api",
    )


def keycloak_proof_action(
    request: Request,
    user: "CurrentUser",
    *,
    session_id: str,
    settings: Settings,
    now: int,
) -> str | None:
    """Return a valid, fresh proof action bound to this Keycloak user and browser session."""
    data = _verified(
        request.cookies.get(KEYCLOAK_PROOF_COOKIE) or "",
        _KEYCLOAK_PROOF_PURPOSE,
        settings.session_secret,
    )
    if not isinstance(data, dict) or set(data) != _KEYCLOAK_PROOF_FIELDS:
        return None
    uid, cid, sid, action, assurance = (
        data.get("uid"),
        data.get("cid"),
        data.get("sid"),
        data.get("action"),
        data.get("acr"),
    )
    version, auth_time, expiry = data.get("ver"), data.get("at"), data.get("exp")
    if (
        not isinstance(action, str)
        or not isinstance(auth_time, int)
        or isinstance(auth_time, bool)
        or not isinstance(expiry, int)
        or isinstance(expiry, bool)
    ):
        return None
    if (
        uid != user.user_id
        or cid != user.customer_id
        or sid != session_id
        or version != user.session_version
        or action not in KEYCLOAK_PROOF_ACTIONS
        or not isinstance(assurance, str)
        or not hmac.compare_digest(assurance, settings.keycloak_required_acr or "")
        or not _is_version(version)
        or expiry <= now
        or auth_time > now
        or now - auth_time > KEYCLOAK_PROOF_SECONDS
    ):
        return None
    return action


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
    auth_authority: str = "legacy"
    auth_source: str = "legacy_password"
    assurance: str | None = None
    reauth_action: str | None = None


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
    """The signed-in user, re-checked in the database on every request: 401 ``not_signed_in``
    unless the session is genuine and still matches the user (enabled, same role, same session
    version, same sign-in authority). Keycloak is the only sign-in (21 Nov 2026): a session of any
    other source is refused, whatever the settings, so an old password cookie ends at the deploy."""
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
    if (user.auth_authority == "keycloak") != (claims.auth_source == "keycloak"):
        raise ApiError(401, "not_signed_in")
    if claims.auth_source == "keycloak" and claims.assurance != context.settings.keycloak_required_acr:
        raise ApiError(401, "not_signed_in")
    if claims.auth_source != "keycloak":
        raise ApiError(401, "not_signed_in")  # an old password session ends at the deploy
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
    return dataclasses.replace(
        user,
        auth_source=claims.auth_source,
        assurance=claims.assurance,
        reauth_action=keycloak_proof_action(
            request,
            user,
            session_id=claims.session_id,
            settings=context.settings,
            now=int(now.timestamp()),
        ),
    )


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
        auth_authority=str(row["auth_authority"]),
    )


User = Annotated[CurrentUser, Depends(current_user)]


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
    if user.auth_authority != "keycloak" and not user.totp_enabled:  # Keycloak asks for it at sign-in
        raise ApiError(403, "two_step_required", "Turn on two-step sign-in in Account to do this.")
    return user


AdminWithTwoStep = Annotated[CurrentUser, Depends(require_admin_with_two_step)]


def require_delete_forever(request: Request, user: User) -> CurrentUser:
    """For deleting a document forever (council 4 Oct 2026; 21 Nov 2026). An admin needs an admin
    session (403 ``admin_session_required``); anyone else needs an admin's grant (403
    ``forbidden``), read again on every request, so stopping it works at once. Then everyone needs
    the confirm token, which only a fresh Keycloak sign-in for ``delete_forever`` sets
    (``routers/keycloak.py`` ``_reauth``), in the last CONFIRM_SECONDS, for this user, session version
    and browser session (its ``sid``, A4: never another browser's, nor one from before signing out
    and in again; 403 ``keycloak_reauth_required``)."""
    if user.role == "admin":
        require_admin_session(user)
    elif not user.delete_forever_allowed:
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
        raise ApiError(403, "keycloak_reauth_required")
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
    """Applied to every route: a state-changing request needs the CSRF header. But Meta's webhook
    (#603), which no browser sends and which reads no cookie: its signature is checked instead."""
    if request.method in _SAFE_METHODS or request.url.path in {
        WHATSAPP_WEBHOOK_PATH,
        KEYCLOAK_BACKCHANNEL_PATH,
    }:
        return
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise ApiError(403, "csrf")
