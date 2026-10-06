"""Sign-in: password hashes, the session and enrol tokens, who the caller is, and CSRF.

Why each piece (FULL-BUILD-SPEC section 3, PRD 1 A1 to A7):

* **Passwords** are hashed with scrypt (standard library), 16 random salt
  bytes, n=2**14, r=8, p=1, 32 bytes out, stored as
  ``scrypt$16384$8$1$<salt b64>$<hash b64>``, and compared in constant time.
* **The session** is a cookie ``<payload b64url>.<HMAC-SHA256 b64url>``. The
  payload is ``{"uid","cid","role","scope","ver","exp"}`` and the MAC is over
  ``b"session\\x00" + body``. ``scope`` is "app" (from /login) or "admin" (from
  /admin/login, admins only); ``ver`` is the user's ``session_version``. Nothing
  is stored server side, so every request re-checks, with one query inside
  ``db.customer(cid)``, that the user still exists, is not disabled, still has
  that role and still has that session version. Raising the version (a new
  password, "sign out everywhere", disabling, a 2FA reset) ends every other
  session at its next request.
* **The enrol token** carries a pending TOTP secret between an admin pressing
  "Turn on" (/auth/admin/2fa/start) and their first code: ``{"uid","cid","ver","sec","exp"}``, MAC over
  ``b"enrol\\x00" + body``, 10 minutes. The purposes have different MAC
  inputs and different fields, so neither token ever reads as the other.
* **The confirm token** (council 4 Oct 2026, PROVISIONAL): a granted member or
  reviewer's re-typed password for Delete forever. ``{"uid","cid","ver","exp"}``,
  MAC over ``b"confirm\\x00" + body``, 5 minutes, an HttpOnly SameSite=Strict
  cookie for /api/documents only.
* **Strict claims**: a token with a missing, extra or wrongly typed field is
  refused, so a Sprint 1 cookie (no scope, no ver) is refused and its user
  signs in once more.
* **The customer always comes from the session**, never from the request: the
  signed ``cid`` is the only customer id any route uses.
* **The password-change gate is inside ``current_user``** (deny by default):
  while ``must_change_password`` is set, every route refuses with 403
  ``password_change_required``, except the few that use
  ``current_user_allow_password_change`` (/auth/password, /auth/logout, /auth/me).
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
from dataclasses import dataclass
from functools import cache
from typing import Annotated, Any

from fastapi import Depends, Request, Response

from kaagaz.api.context import get_context
from kaagaz.api.errors import ApiError
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


_SESSION_PURPOSE = b"session"
_ENROL_PURPOSE = b"enrol"
_CONFIRM_PURPOSE = b"confirm"
_SESSION_FIELDS = frozenset({"uid", "cid", "role", "scope", "ver", "exp"})
_ENROL_FIELDS = frozenset({"uid", "cid", "ver", "sec", "exp"})
_CONFIRM_FIELDS = frozenset({"uid", "cid", "ver", "exp"})


def session_seconds(scope: str, settings: Settings) -> int:
    """How long a session of this scope lasts: ADMIN_SESSION_MINUTES or SESSION_HOURS."""
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
) -> SessionClaims:
    """Claims for a new session that ends one scope lifetime after ``now``."""
    return SessionClaims(
        user_id=user_id,
        customer_id=customer_id,
        role=role,
        scope=scope,
        session_version=session_version,
        expires_at=now + session_seconds(scope, settings),
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
        },
        _CONFIRM_PURPOSE,
        secret,
    )


def read_confirm(token: str, secret: str, now: int) -> ConfirmClaims | None:
    """The claims of a genuine, unexpired confirm token, else None. Strict: exactly
    the four fields, each of its type. Constant-time check."""
    data = _verified(token, _CONFIRM_PURPOSE, secret)
    if not isinstance(data, dict) or set(data) != _CONFIRM_FIELDS:
        return None
    uid, cid, ver, exp = data["uid"], data["cid"], data["ver"], data["exp"]
    if not (isinstance(uid, str) and isinstance(cid, str) and _is_uuid(uid) and _is_uuid(cid)):
        return None
    if not (_is_version(ver) and _is_int(exp)) or exp <= now:
        return None
    return ConfirmClaims(user_id=uid, customer_id=cid, session_version=ver, expires_at=exp)


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
    """Strict: exactly the six fields, each of its type; scope "admin" needs role "admin"."""
    if not isinstance(data, dict) or set(data) != _SESSION_FIELDS:
        return None
    uid, cid, role, scope = data["uid"], data["cid"], data["role"], data["scope"]
    ver, exp = data["ver"], data["exp"]
    if not (isinstance(uid, str) and isinstance(cid, str) and isinstance(role, str)):
        return None
    if not isinstance(scope, str) or scope not in SCOPES or role not in ROLES:
        return None
    if scope == "admin" and role != "admin":
        return None
    if not (_is_version(ver) and _is_int(exp)) or exp <= now:
        return None
    if not (_is_uuid(uid) and _is_uuid(cid)):
        return None
    return SessionClaims(
        user_id=uid, customer_id=cid, role=role, scope=scope, session_version=ver, expires_at=exp
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
    totp_enabled: bool  # two-step sign-in is on (each admin chooses)
    delete_forever_allowed: bool  # an admin allowed Delete forever (ignored for an admin)


def set_confirm_cookie(response: Response, user: CurrentUser, settings: Settings, *, now: int) -> None:
    """A confirm token for this user and session version, CONFIRM_SECONDS long."""
    claims = ConfirmClaims(
        user_id=user.user_id,
        customer_id=user.customer_id,
        session_version=user.session_version,
        expires_at=now + CONFIRM_SECONDS,
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
    ``password_change_required`` while the user must set a new password."""
    user = _signed_in(request)
    if user.must_change_password:
        raise ApiError(403, "password_change_required")
    return user


def current_user_allow_password_change(request: Request) -> CurrentUser:
    """``current_user`` without the password-change gate. Only for /auth/password,
    /auth/logout and /auth/me."""
    return _signed_in(request)


def _signed_in(request: Request) -> CurrentUser:
    context = get_context(request)
    now = int(context.clock().timestamp())
    claims = session_claims(request, context.settings, now)
    if claims is None:
        raise ApiError(401, "not_signed_in")
    user = load_user(context.db, claims.customer_id, claims.user_id, scope=claims.scope)
    if user is None or user.role != claims.role or user.session_version != claims.session_version:
        raise ApiError(401, "not_signed_in")
    return user


def load_user(db: Database, customer_id: str, user_id: str, *, scope: str) -> CurrentUser | None:
    """The enabled user with this id in this customer, or None (also while the
    customer is not active, 0014). ``scope`` is the session's (at sign-in, the
    one being started). One indexed query."""
    with db.customer(customer_id) as conn:
        row = users_sql.session_user(conn, customer_id, user_id)
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
    )


User = Annotated[CurrentUser, Depends(current_user)]
UserAllowPasswordChange = Annotated[CurrentUser, Depends(current_user_allow_password_change)]


def require_admin_session(user: User) -> CurrentUser:
    """For the /admin router: an admin signed in at /admin/login, else 403
    ``admin_session_required`` (an admin's own app session included)."""
    if user.scope != "admin" or user.role != "admin":
        raise ApiError(403, "admin_session_required")
    return user


AdminSession = Annotated[CurrentUser, Depends(require_admin_session)]


def require_admin_with_two_step(user: AdminSession) -> CurrentUser:
    """For what can't be undone (deleting a document forever, deleting a tag): an
    admin session whose admin has two-step sign-in on, else 403
    ``two_step_required``. Two-step sign-in is each admin's choice, so an admin
    session alone may rest on a password; these actions must not."""
    if not user.totp_enabled:
        raise ApiError(403, "two_step_required", "Turn on two-step sign-in in Admin, Settings to do this.")
    return user


AdminWithTwoStep = Annotated[CurrentUser, Depends(require_admin_with_two_step)]


def require_delete_forever(request: Request, user: User) -> CurrentUser:
    """For deleting a document forever (council 4 Oct 2026). An admin needs what
    they always did: an admin session with two-step sign-in on; a grant never
    counts for an admin, so a password-only admin can't route round two-step.
    Anyone else needs an admin's grant (403 ``forbidden``) and a confirm token
    from typing their password in the last CONFIRM_SECONDS, for this user and
    session version (403 ``password_confirm_required``). The grant is read
    again on every request, so stopping it works at once."""
    if user.role == "admin":
        return require_admin_with_two_step(require_admin_session(user))
    if not user.delete_forever_allowed:
        raise ApiError(403, "forbidden")
    context = get_context(request)
    token = request.cookies.get(CONFIRM_COOKIE) or ""
    claims = read_confirm(token, context.settings.session_secret, int(context.clock().timestamp()))
    signed = None if claims is None else (claims.user_id, claims.customer_id, claims.session_version)
    if signed != (user.user_id, user.customer_id, user.session_version):
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


def csrf_guard(request: Request) -> None:
    """Applied to every route: a state-changing request needs the CSRF header."""
    if request.method in _SAFE_METHODS:
        return
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise ApiError(403, "csrf")
