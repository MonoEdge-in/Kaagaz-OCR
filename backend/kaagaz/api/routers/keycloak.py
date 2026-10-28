"""Organisation users signing in through Keycloak (#435, Tushar 28 Oct 2026). ``create_app``
adds these routes only while KEYCLOAK_ENABLED, so off, each one is a 404.

* ``GET /auth/keycloak/start?next=/admin``: a new state, nonce and PKCE verifier, kept in a
  signed cookie (``set_keycloak_cookie``: HttpOnly, 10 minutes, sent to the callback only),
  then on to the realm's sign-in page, which asks for the password and the two-step code.
  ``next`` is kept only when it is a plain path on this site, else /dashboard, so the
  sign-in can never send anyone to another site.
* ``GET /auth/keycloak/callback?state&code``: the state must be the cookie's; the code is
  swapped for the ID token, which is checked (kaagaz/api/keycloak.py); its verified e-mail
  must be an enabled user of an active organisation, found by the password sign-in's own
  lookup (``users.login_lookup``: users only, so an operator never matches, and the
  organisation comes from that user's row, never from the request). Then the usual session
  starts (``start_session``: the same cookie, session version, idle timeout and audit
  row; scope "admin" for an admin), marked ``kc``, and the browser goes to ``next``. Any
  failure goes to ``/login?keycloak_error=<code>`` with no session. The cookie is cleared
  either way, so a state, nonce and verifier work once, and Keycloak takes a code once.
* ``GET /auth/keycloak/logout``: the sign-out's second half. The browser comes here after
  ``POST /auth/logout`` ended the Kaagaz session, and goes on to Keycloak's sign-out.

Why GETs without the CSRF header are safe here: the callback does nothing without the state
that only this browser's cookie holds, and start and logout change nothing in Kaagaz.
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
    read_keycloak,
    set_keycloak_cookie,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.keycloak import KeycloakError
from kaagaz.api.routers.auth import start_session
from kaagaz.api.twostep import unix
from kaagaz.db import audit
from kaagaz.db import users as users_sql
from kaagaz.db.users import LoginRow

router = APIRouter()
logger = logging.getLogger(__name__)

Context = Annotated[AppContext, Depends(get_context)]

AFTER_SIGN_IN = "/dashboard"
SIGNED_OUT = "/login?signed_out=1"
# A plain path on this site: no scheme, host, "//", "\", query or fragment can get through.
_NEXT = re.compile(r"/[A-Za-z0-9_-][A-Za-z0-9/_-]{0,199}")


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
        email = keycloak.verified_email(context.settings, realm, id_token, nonce=pending.nonce, now=now)
        response = _sign_in(context, email, pending.next_path)
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


def _sign_in(context: AppContext, email: str, next_path: str) -> RedirectResponse:
    """A session for the enabled user with this e-mail, in their own organisation."""
    with context.db.system() as conn:
        found = users_sql.login_lookup(conn, email)
    if found is None:
        raise KeycloakError("no_account")
    if found.disabled:
        raise _refused(context, found, "disabled")
    try:
        return start_session(
            context,
            found.customer_id,
            found.user_id,
            lambda _: RedirectResponse(next_path, status_code=303),
            scope="admin" if found.role == "admin" else "app",
            action="auth.login",
            details={"via": "keycloak"},
            keycloak=True,
        )
    except ApiError:  # its organisation is disabled or being deleted (0014)
        raise _refused(context, found, "customer_disabled") from None


def _refused(context: AppContext, found: LoginRow, reason: str) -> KeycloakError:
    """Audit the refusal in the user's own organisation (ids and a reason only), as the
    password sign-in does, and the error to raise."""
    with context.db.customer(found.customer_id) as conn:
        audit.record(
            conn,
            found.customer_id,
            actor_user_id=None,
            action="auth.login_failed",
            target_type="user",
            target_id=found.user_id,
            details={"reason": reason, "via": "keycloak"},
        )
    return KeycloakError("no_account")


def _failed(failure: KeycloakError) -> RedirectResponse:
    logger.warning("keycloak sign-in refused", extra={"code": failure.code})
    return RedirectResponse(f"/login?keycloak_error={failure.code}", status_code=303)
