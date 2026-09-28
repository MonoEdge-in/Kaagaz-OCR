"""Sign-in: password hashes, the session cookie, who the caller is, and CSRF.

Why each piece (FULL-BUILD-SPEC section 3):

* **Passwords** are hashed with scrypt (standard library), 16 random salt
  bytes, n=2**14, r=8, p=1, 32 bytes out, stored as
  ``scrypt$16384$8$1$<salt b64>$<hash b64>``, and compared in constant time.
* **The session** is a cookie ``<payload b64url>.<HMAC-SHA256 b64url>``. The
  payload is ``{"uid","cid","role","exp"}``. Nothing is stored server side,
  so every request re-checks, with one query inside ``db.customer(cid)``, that
  the user still exists, is not disabled and still has that role.
* **The customer always comes from the session**, never from the request: the
  signed ``cid`` is the only customer id any route uses.
* **CSRF**: every request that is not GET, HEAD or OPTIONS must carry
  ``X-Requested-With: kaagaz``. A cross-site form cannot add a header, and a
  cross-site script cannot either without CORS, which the API never allows.

PROVISIONAL (security configuration is Vrushit's): logout only clears the
cookie, so a copied token stays valid until it expires; a password change
does not end other sessions. Ending them needs a session version column.
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


# --- session tokens ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionClaims:
    user_id: str
    customer_id: str
    role: str
    expires_at: int  # seconds since the epoch


def sign_session(claims: SessionClaims, secret: str) -> str:
    payload = json.dumps(
        {"uid": claims.user_id, "cid": claims.customer_id, "role": claims.role, "exp": claims.expires_at},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    body = _b64url(payload)
    return body + "." + _b64url(_mac(body, secret))


def read_session(token: str, secret: str, now: int) -> SessionClaims | None:
    """The claims of a genuine, unexpired token, else None. Constant-time check."""
    body, dot, signature = token.partition(".")
    if not dot or not body or not signature:
        return None
    try:
        given = _unb64url(signature)
        if not hmac.compare_digest(given, _mac(body, secret)):
            return None
        data = json.loads(_unb64url(body))
    except (binascii.Error, ValueError):
        return None
    return _claims(data, now)


def _claims(data: Any, now: int) -> SessionClaims | None:
    if not isinstance(data, dict):
        return None
    uid, cid, role, exp = data.get("uid"), data.get("cid"), data.get("role"), data.get("exp")
    if not (isinstance(uid, str) and isinstance(cid, str) and isinstance(role, str)):
        return None
    if not isinstance(exp, int) or isinstance(exp, bool) or exp <= now or role not in ROLES:
        return None
    if not (_is_uuid(uid) and _is_uuid(cid)):
        return None
    return SessionClaims(user_id=uid, customer_id=cid, role=role, expires_at=exp)


def _mac(body: str, secret: str) -> bytes:
    return hmac.new(secret.encode(), body.encode("ascii"), hashlib.sha256).digest()


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


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
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


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_id: str
    customer_id: str
    email: str
    display_name: str
    role: str
    customer_name: str


def current_user(request: Request) -> CurrentUser:
    """The signed-in user, re-checked in the database on every request. 401 otherwise."""
    context = get_context(request)
    now = int(context.clock().timestamp())
    claims = session_claims(request, context.settings, now)
    if claims is None:
        raise ApiError(401, "not_signed_in")
    user = load_user(context.db, claims.customer_id, claims.user_id)
    if user is None or user.role != claims.role:
        raise ApiError(401, "not_signed_in")
    return user


def load_user(db: Database, customer_id: str, user_id: str) -> CurrentUser | None:
    """The enabled user with this id in this customer, or None."""
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
    )


User = Annotated[CurrentUser, Depends(current_user)]


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


def me_json(user: CurrentUser) -> dict[str, Any]:
    """The ``Me`` type of frontend/lib/types.ts."""
    return {
        "user": {
            "id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
        },
        "customer": {"id": user.customer_id, "name": user.customer_name},
    }
