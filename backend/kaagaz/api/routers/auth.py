"""Signing out, and who is signed in (PRD 1 A1 to A7, section 6).

Keycloak is the only sign-in for company users (21 Nov 2026): ``routers/keycloak.py`` starts it,
receives it and ends it. What stays here:

* ``POST /auth/logout`` ends this browser's session; ``POST /auth/logout-everywhere`` ends all of
  the person's Kaagaz sessions only (it does not sign them out of Keycloak itself).
* ``GET /auth/me`` is the signed-in user (401 when nobody is); ``GET /auth/session`` is the same
  with no session a 200 ``{signed_in: false, keycloak_enabled}``, for the sign-in page.
* The risky actions (locked files, delete forever, access, locks, WhatsApp) are proved only by a
  fresh Keycloak sign-in for that action: ``require_proof``.
* The rate-limit helpers and constants the operator's sign-in and the e-mail routes share.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import Connection

from kaagaz.api.auth import (
    CurrentUser,
    User,
    clear_locked_open_cookie,
    clear_session_cookie,
    clear_whatsapp_proof_cookie,
    current_user,
    session_claims,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.emails import email_on
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import AddressLocks, RateLimiter
from kaagaz.api.twostep import unix as _unix
from kaagaz.api.views import me_json
from kaagaz.db import access as access_sql
from kaagaz.db import audit
from kaagaz.db import users as users_sql

router = APIRouter()
log = logging.getLogger(__name__)

Context = Annotated[AppContext, Depends(get_context)]

MAX_CODE = 16  # an operator's 6-digit code, plus room for the spaces people type
# The operator's sign-in lock (routers/operator.py), per account and per address.
ACCOUNT_MAX_FAILURES = 50
ACCOUNT_LOCK_MINUTES = 60
address_locks = AddressLocks()


@router.post("/auth/logout", status_code=204)
def logout(request: Request, user: User, context: Context) -> Response:
    claims = session_claims(request, context.settings, _unix(context.clock()))
    with context.db.user(user.customer_id, user.user_id) as conn:
        if claims is not None:  # always: ``user`` was read from these claims
            ends = datetime.fromtimestamp(claims.expires_at, UTC)
            if user.auth_authority == "keycloak":
                from kaagaz.db import keycloak as keycloak_sql

                keycloak_sql.revoke_session(conn, claims.session_id, now=context.clock())
            users_sql.end_session(conn, user.customer_id, user.user_id, claims.session_id, expires_at=ends)
        _record(conn, user.customer_id, "auth.logout", actor=user.user_id, target=user.user_id)
    response = Response(status_code=204)
    clear_session_cookie(response, context.settings)
    clear_whatsapp_proof_cookie(response, context.settings)  # #603
    clear_locked_open_cookie(response, context.settings)  # PRD 7 decision 6: it ends at sign-out
    return response


@router.post("/auth/logout-everywhere", status_code=204)
def logout_everywhere(user: User, context: Context) -> Response:
    with context.db.user(user.customer_id, user.user_id) as conn:
        if user.auth_authority == "keycloak":
            from kaagaz.db import keycloak as keycloak_sql

            keycloak_sql.revoke_all_sessions(conn, now=context.clock())
        users_sql.bump_session_version(conn, user.customer_id, user.user_id)
        _record(conn, user.customer_id, "auth.logout_everywhere", actor=user.user_id, target=user.user_id)
    response = Response(status_code=204)
    clear_session_cookie(response, context.settings)
    clear_whatsapp_proof_cookie(response, context.settings)  # #603
    clear_locked_open_cookie(response, context.settings)
    return response


@router.get("/auth/me")
def me(user: User, context: Context) -> dict[str, Any]:
    return _me(context, user)


@router.get("/auth/session")
def session(request: Request) -> dict[str, Any]:
    """``/auth/me``'s checks, with no session a 200 ``{signed_in: false, keycloak_enabled}``, not a 401."""
    try:
        user = current_user(request)
    except ApiError as error:
        if error.status == 401:
            return {"signed_in": False, "keycloak_enabled": get_context(request).settings.keycloak_enabled}
        raise
    return {"signed_in": True, "me": _me(get_context(request), user)}


def _me(context: AppContext, user: CurrentUser) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        access = access_sql.me(conn)
    return me_json(
        user,
        email_on=email_on(context),
        access=access,
        whatsapp=context.settings.whatsapp_enabled,
        keycloak_enabled=context.settings.keycloak_enabled,
    )


def require_proof(user: CurrentUser, action: str) -> None:
    """A risky action's only proof: a fresh Keycloak sign-in for exactly this ``action``
    (``routers/keycloak.py`` ``reauth_start``), else 403 ``keycloak_reauth_required``."""
    if user.auth_authority != "keycloak" or user.reauth_action != action:
        raise ApiError(403, "keycloak_reauth_required")


def _per_user_limit(limiter: RateLimiter, user: CurrentUser, message: str) -> None:
    """At most ``limiter``'s allowance for this user, else 429 with ``message`` and when to try again."""
    key = f"user:{user.user_id}"
    if not limiter.allow(key):
        raise ApiError(429, "rate_limited", message, headers={"Retry-After": str(limiter.retry_after(key))})


def _record(
    conn: Connection,
    customer_id: str,
    action: str,
    *,
    actor: str | None,
    target: str,
    details: dict[str, audit.DetailValue] | None = None,
) -> None:
    audit.record(
        conn,
        customer_id,
        actor_user_id=actor,
        action=action,
        target_type="user",
        target_id=target,
        details=details,
    )
