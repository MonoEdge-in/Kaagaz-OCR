"""The 10-minute check for locked files (PRD 7 section 6, "Opening a locked file"; decisions 5 and 6;
#605, PR 13).

Why a cookie of its own: one check opens every locked file the person may open, for 10 minutes in
that browser, and never anything else. So it is its own signed token, ``{uid, cid, ver, sid, exp}``
with MAC purpose ``locked_open``: the Delete forever confirm (``kaagaz_confirm``, purpose
``confirm``) can never open a locked file, nor this one confirm a delete (``p2:70``).

* LOCKED_OPEN_SECONDS, absolute: it is set once, at the check, and never extended by use.
* HttpOnly, Secure (as the session), SameSite=Strict, sent to /api/documents only, where the
  page-picture route and the check's own routes live.
* It holds for this user, organisation, session version and browser session (``sid``) only: a new
  password, "sign out everywhere" or a switch-off raise the version; signing out ends the session
  (0076, so the ``sid`` is another one at the next sign-in) and clears the cookie.

Who confirms how (decision 5, changed by Vrushit on 13 Nov 2026): the person's own Kaagaz password
and nothing else, no two-step code; a person whose role signs in with Google only
(``confirms_with_google``, the sign-in's own test, ``routers/auth.py`` ``_google_only``) has no
password to type and confirms with a fresh Google sign-in (``routers/oidc.py``). O(1), no query.
"""

from fastapi import Request, Response

from kaagaz.api.auth import (
    CurrentUser,
    _is_int,
    _is_uuid,
    _is_version,
    _sign,
    _verified,
    session_claims,
)
from kaagaz.settings import Settings

LOCKED_OPEN_COOKIE = "kaagaz_locked_open"
LOCKED_OPEN_SECONDS = 10 * 60  # PRD 7 decision 6
LOCKED_OPEN_PATH = "/api/documents"

_PURPOSE = b"locked_open"
_FIELDS = frozenset({"uid", "cid", "ver", "sid", "exp"})


def confirms_with_google(user: CurrentUser, settings: Settings) -> bool:
    """The person signs in with Google only (Google on, the role uses it only, a passing link), so
    they confirm with Google, never a password: the test a password sign-in refuses them by."""
    return settings.google_signin_enabled and user.google_on and user.google_only and user.google_linked


def sign(user: CurrentUser, session_id: str, expires_at: int, secret: str) -> str:
    payload = {
        "uid": user.user_id,
        "cid": user.customer_id,
        "ver": user.session_version,
        "sid": session_id,
        "exp": expires_at,
    }
    return _sign(payload, _PURPOSE, secret)


def open_until(request: Request, user: CurrentUser, settings: Settings, now: int) -> int | None:
    """When this browser's live check ends, else None: a genuine, unexpired ``locked_open`` token
    with exactly its fields, for this very user, organisation, session version and browser session."""
    data = _verified(request.cookies.get(LOCKED_OPEN_COOKIE) or "", _PURPOSE, settings.session_secret)
    if not isinstance(data, dict) or set(data) != _FIELDS:
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


def set_cookie(
    response: Response, user: CurrentUser, session_id: str, settings: Settings, *, until: int
) -> None:
    """The check, ending at ``until``: LOCKED_OPEN_SECONDS from now."""
    response.set_cookie(
        LOCKED_OPEN_COOKIE,
        sign(user, session_id, until, settings.session_secret),
        max_age=LOCKED_OPEN_SECONDS,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path=LOCKED_OPEN_PATH,
    )


def clear_cookie(response: Response, settings: Settings) -> None:
    """Signing out ends the check (decision 6)."""
    response.delete_cookie(
        LOCKED_OPEN_COOKIE,
        path=LOCKED_OPEN_PATH,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
    )
