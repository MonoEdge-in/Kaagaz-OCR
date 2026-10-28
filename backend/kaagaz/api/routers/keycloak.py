"""Organisation users signing in through Keycloak (#435, Tushar 28 Oct 2026). ``create_app``
adds these routes only while KEYCLOAK_ENABLED, so off, each one is a 404.

* ``GET /auth/keycloak/start?next=/admin``: a new state, nonce and PKCE verifier, kept in a
  signed cookie (``set_keycloak_cookie``: HttpOnly, 10 minutes, sent to the callback only),
  then on to the realm's sign-in page. ``next`` is kept only when it is a plain path on this
  site (an optional query of letters, digits, ``=``, ``&``, ``-`` and ``_``), else /dashboard,
  so the sign-in can never send anyone to another site. ``confirm=1``: a signed-in member
  or reviewer an admin allowed to delete forever confirms it is them (in place of retyping a
  Kaagaz password).
* ``GET /auth/keycloak/callback?state&code``: the state must be the cookie's; the code is
  swapped for the ID token, which is checked (kaagaz/api/keycloak.py). Its verified e-mail
  is looked up as the password sign-in does (``users.login_lookup``: users only, so an
  operator never matches), and its ``kaagaz_org`` must be that user's organisation, so an
  admin who adds someone else's e-mail to their own organisation gets nothing. A sign-in
  then starts the usual session (``start_session``, marked ``kc``) and goes to ``next`` with
  ``signed_in=1``, for the page to tell the other tabs; a confirm, for the user of this very
  session only, sets the 5-minute confirm cookie (auth.py) and goes back to ``next``. Any
  failure goes to ``/login?keycloak_error=<code>`` with no session or cookie. The keycloak
  cookie is cleared either way, so a state, nonce and verifier work once, and Keycloak
  takes a code once.
* ``GET /auth/keycloak/logout``: the sign-out's second half. The browser comes here after
  ``POST /auth/logout`` ended the Kaagaz session, and goes on to Keycloak's sign-out.

Why GETs without the CSRF header are safe here: the callback does nothing without the state
that only this browser's cookie holds, and start and logout change nothing in Kaagaz.
Known limit: one sign-in at a time per browser; two tabs starting one each share the cookie,
so the first back gets ``state``.
"""

import hmac
import logging
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from kaagaz.api import keycloak
from kaagaz.api.auth import (
    KEYCLOAK_COOKIE,
    KEYCLOAK_SECONDS,
    KeycloakClaims,
    clear_keycloak_cookie,
    current_user,
    read_keycloak,
    set_confirm_cookie,
    set_keycloak_cookie,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.keycloak import KeycloakError
from kaagaz.api.routers.auth import _audit_failure, start_session
from kaagaz.api.twostep import unix
from kaagaz.db import users as users_sql
from kaagaz.db.users import LoginRow

router = APIRouter()
logger = logging.getLogger(__name__)

Context = Annotated[AppContext, Depends(get_context)]

AFTER_SIGN_IN = "/dashboard"
SIGNED_OUT = "/login?signed_out=1"
# A plain path on this site: no scheme, host, "//", "\", "%" or fragment can get through.
_NEXT = re.compile(r"/[A-Za-z0-9_-][A-Za-z0-9/_-]{0,199}(\?[A-Za-z0-9=&_-]{1,200})?")


@router.get("/auth/keycloak/start")
def start(request: Request, context: Context) -> RedirectResponse:
    try:
        realm = keycloak.endpoints(context.settings)
    except KeycloakError as failure:
        return _failed(failure)
    now = unix(context.clock())
    wanted = request.query_params.get("next", "")
    pending = KeycloakClaims(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(48),  # 64 characters, as PKCE asks (43 to 128)
        next_path=wanted if _NEXT.fullmatch(wanted) else AFTER_SIGN_IN,
        confirm=request.query_params.get("confirm") == "1",
        expires_at=now + KEYCLOAK_SECONDS,
    )
    url = keycloak.authorize_url(
        context.settings, realm, state=pending.state, nonce=pending.nonce, verifier=pending.verifier
    )
    response = RedirectResponse(url, status_code=303)
    set_keycloak_cookie(response, pending, context.settings, now=now)
    return response


@router.get("/auth/keycloak/callback")
def callback(request: Request, context: Context) -> RedirectResponse:
    now = unix(context.clock())
    pending = read_keycloak(request.cookies.get(KEYCLOAK_COOKIE) or "", context.settings.session_secret, now)
    state, code = request.query_params.get("state", ""), request.query_params.get("code", "")
    try:
        if pending is None or not hmac.compare_digest(state.encode(), pending.state.encode()):
            raise KeycloakError("state")
        if "error" in request.query_params or not code:  # cancelled or refused at Keycloak
            raise KeycloakError("cancelled")
        realm = keycloak.endpoints(context.settings)
        id_token = keycloak.id_token_for(context.settings, realm, code, pending.verifier)
        asked_at = pending.expires_at - KEYCLOAK_SECONDS
        email, organisation = keycloak.verified_person(
            context.settings, realm, id_token, nonce=pending.nonce, asked_at=asked_at, now=now
        )
        found = _user_of(context, email, organisation)
        if pending.confirm:
            response = _confirmed(request, context, found, pending.next_path)
        else:
            response = _sign_in(context, found, pending.next_path)
    except KeycloakError as failure:
        response = _failed(failure)
    clear_keycloak_cookie(response, context.settings)
    return response


@router.get("/auth/keycloak/logout")
def logout(context: Context) -> RedirectResponse:
    try:
        url = keycloak.logout_url(context.settings, keycloak.endpoints(context.settings))
    except KeycloakError:
        url = SIGNED_OUT  # Kaagaz's session is already over; Keycloak's ends with its own timeout
    return RedirectResponse(url, status_code=303)


def _user_of(context: AppContext, email: str, organisation: str) -> LoginRow:
    """The enabled user with this e-mail, in the organisation the token names."""
    with context.db.system() as conn:
        found = users_sql.login_lookup(conn, email)
    if found is None:
        raise KeycloakError("no_account")
    if organisation != found.customer_id:  # an e-mail added to another organisation
        _audit_failure(context, found.customer_id, found.user_id, "org_mismatch", via="keycloak")
        raise KeycloakError("wrong_org")
    if found.disabled:
        _audit_failure(context, found.customer_id, found.user_id, "disabled", via="keycloak")
        raise KeycloakError("no_account")
    return found


def _sign_in(context: AppContext, found: LoginRow, next_path: str) -> RedirectResponse:
    """The usual session for this user, in their own organisation."""
    reply = RedirectResponse(next_path + ("&" if "?" in next_path else "?") + "signed_in=1", status_code=303)
    try:
        start_session(
            context,
            found.customer_id,
            found.user_id,
            scope="admin" if found.role == "admin" else "app",
            action="auth.login",
            details={"via": "keycloak"},
            reply=reply,
            keycloak=True,
        )
    except ApiError:  # no enabled user of an active organisation any more: say which
        with context.db.customer(found.customer_id) as conn:
            row = users_sql.session_user(conn, found.customer_id, found.user_id)
        reason = "customer_disabled" if row is None else "disabled"
        _audit_failure(context, found.customer_id, found.user_id, reason, via="keycloak")
        raise KeycloakError("no_account") from None
    return reply


def _confirmed(request: Request, context: AppContext, found: LoginRow, next_path: str) -> RedirectResponse:
    """The confirm cookie for Delete forever, for the signed-in member or reviewer an admin
    allowed it, when Keycloak just signed in that same user."""
    try:
        user = current_user(request)
    except ApiError:
        raise KeycloakError("confirm") from None
    if found.user_id != user.user_id or user.role == "admin" or not user.delete_forever_allowed:
        raise KeycloakError("confirm")
    reply = RedirectResponse(next_path, status_code=303)
    set_confirm_cookie(reply, user, context.settings, now=unix(context.clock()))
    return reply


def _failed(failure: KeycloakError) -> RedirectResponse:
    logger.warning("keycloak sign-in refused", extra={"code": failure.code})
    return RedirectResponse(f"/login?keycloak_error={failure.code}", status_code=303)
