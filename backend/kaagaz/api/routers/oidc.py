"""Ways to sign in (#457 phase 1): signing in with a connected Google account. The core is
``kaagaz/api/oidc.py``; the routes' shape is ported from the Keycloak branch's router (#438).

The router is always mounted, and every route is 404 while no provider is switched on (the
provider's own routes: while that one is off), before anything else is read.

* ``GET /auth/oidc/{provider}/start?next=/path``: a new state, nonce and PKCE verifier in the
  signed flow cookie (``set_flow_cookie``: HttpOnly, 10 minutes, sent to /api/auth/oidc only),
  then 303 to the provider's account chooser. ``next`` is kept only when it is a plain path on
  this site (``_safe_next``), else ``/``.
* ``GET /auth/oidc/{provider}/callback?state&code|error``: the flow cookie is read and always
  cleared, so a callback works once (a Back or a refresh says ``expired``); ``state`` must be
  the cookie's, compared in constant time. The mode comes from the cookie, never the query.
  The code is swapped for the ID token and the token checked. Then, for a sign-in, the
  connection is looked up by its key (provider, issuer, subject; only an approved one counts,
  never an e-mail, so an operator is never found), and the attempt counted as a password's is
  (``_begin``: locked; a disabled user or organisation is ``refused``, counted and audited).
  Without two-step sign-in a member or reviewer gets an app session and goes to ``next``,
  audited ``auth.login`` ``{"via": "google"}``. Anyone with codes on gets the 5-minute second
  step cookie and /login?step=code; an admin with no code yet is sent to sign in with the
  password first (``password_first``). Every failure is 303 /login?oidc_error=<code>:
  ``cancelled``, ``expired``, ``not_connected``, ``refused``, ``locked``, ``unavailable`` or
  ``password_first``. The reply carries ``Referrer-Policy: no-referrer``.
* ``GET /auth/oidc/notice``: "isn't connected" names the provider's email, which never goes in
  a URL (history, logs): the callback leaves it in a signed 60-second cookie, read here once.
* ``POST /auth/oidc/code`` {code}: the second step, ``routers/auth.py``'s ``_finish_second_step``
  for the user the second-step cookie names, while their session version is unchanged.

Why the GETs need no CSRF header: start changes nothing, and the callback does nothing
without the state that only this browser's cookie holds. Start and callback are limited per
client address (``start_limits``). Logged: the failure's code and the provider only, never a
code, token, state, nonce, verifier, subject or email.
"""

import hmac
import json
import logging
import re
import secrets
import urllib.parse
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from kaagaz.api import oidc
from kaagaz.api.auth import (
    NOTICE_COOKIE,
    OIDC_COOKIE,
    OIDC_SECONDS,
    PENDING_COOKIE,
    PENDING_SECONDS,
    FlowClaims,
    PendingClaims,
    clear_flow_cookie,
    clear_notice_cookie,
    clear_pending_cookie,
    read_flow,
    read_notice,
    read_pending,
    set_flow_cookie,
    set_notice_cookie,
    set_pending_cookie,
    set_session_cookie,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.oidc import Identity, OidcError, Provider
from kaagaz.api.routers.auth import (
    MAX_CODE,
    _begin,
    _failed,
    _finish_second_step,
    _open_session,
    _scope,
)
from kaagaz.api.twostep import refused as _refused
from kaagaz.api.twostep import unix
from kaagaz.db import identities

router = APIRouter()
logger = logging.getLogger(__name__)

Context = Annotated[AppContext, Depends(get_context)]

# Starts and callbacks an hour from one client address (PROVISIONAL; behind the Next.js proxy
# every visitor shares the proxy's address, as SIGNUPS_PER_HOUR says).
STARTS_PER_HOUR = 30
start_limits = RateLimiter(STARTS_PER_HOUR, window_seconds=3600)
TOO_MANY = "Too many sign-ins from here. Try again in an hour."
LOGIN = "/login"
# A plain path on this site: no scheme, host, "//", "\", "%" or fragment can get through.
_NEXT = re.compile(r"/[A-Za-z0-9_-][A-Za-z0-9/_-]{0,199}(\?[A-Za-z0-9=&_-]{1,200})?")
# The OidcError codes the sign-in page explains as "That sign-in expired. Try again."
_EXPIRED = frozenset({"expired", "wrong_nonce", "bad_token"})


class CodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=MAX_CODE)


class _Refused(Exception):
    """A sign-in ends on /login?oidc_error=<code>; ``email`` for the "isn't connected" notice."""

    def __init__(self, code: str, email: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.email = email


def _provider_on(provider: str, request: Request) -> Provider:
    """The provider of the path, while it is switched on; else 404."""
    if not oidc.enabled(get_context(request).settings, provider):
        raise ApiError(404, "not_found")
    return oidc.PROVIDERS[provider]


def _any_on(request: Request) -> None:
    """404 while no provider is switched on."""
    settings = get_context(request).settings
    if not any(oidc.enabled(settings, name) for name in oidc.PROVIDERS):
        raise ApiError(404, "not_found")


ProviderOn = Annotated[Provider, Depends(_provider_on)]


def _safe_next(path: str) -> str:
    """``path`` when it is a plain path on this site, else ``/``."""
    return path if _NEXT.fullmatch(path) else "/"


def _limited(request: Request) -> None:
    if not start_limits.allow(f"ip:{request.client.host if request.client else '-'}"):
        raise ApiError(429, "rate_limited", TOO_MANY, headers={"Retry-After": "3600"})


@router.get("/auth/oidc/{provider}/start")
def start(provider: ProviderOn, request: Request, context: Context) -> RedirectResponse:
    _limited(request)
    now = unix(context.clock())
    flow = FlowClaims(
        provider=provider.name,
        mode="signin",
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(48),  # 64 characters, as PKCE asks (43 to 128)
        next_path=_safe_next(request.query_params.get("next", "")),
        user_id=None,
        customer_id=None,
        session_version=None,
        expires_at=now + OIDC_SECONDS,
    )
    url = oidc.authorize_url(
        provider, context.settings, state=flow.state, nonce=flow.nonce, verifier=flow.verifier
    )
    response = RedirectResponse(url, status_code=303)
    set_flow_cookie(response, flow, context.settings, now=now)
    return response


@router.get("/auth/oidc/{provider}/callback")
def callback(provider: ProviderOn, request: Request, context: Context) -> RedirectResponse:
    _limited(request)
    now = unix(context.clock())
    flow = read_flow(request.cookies.get(OIDC_COOKIE) or "", context.settings.session_secret, now)
    state = request.query_params.get("state", "")
    try:
        if (
            flow is None
            or flow.provider != provider.name
            or not hmac.compare_digest(state.encode(), flow.state.encode())
        ):
            raise _Refused("expired")
        response = _sign_in(context, provider, flow, _identity(request, context, provider, flow, now))
    except _Refused as refused:
        logger.warning(
            json.dumps(
                {"event": "oidc_refused", "provider": provider.name, "code": refused.code},
                separators=(",", ":"),
            )
        )
        response = RedirectResponse(f"{LOGIN}?oidc_error={refused.code}", status_code=303)
        if refused.email is not None:
            set_notice_cookie(response, refused.email, context.settings, now=now)
    clear_flow_cookie(response, context.settings)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/auth/oidc/notice", dependencies=[Depends(_any_on)])
def notice(request: Request, context: Context) -> JSONResponse:
    email = read_notice(
        request.cookies.get(NOTICE_COOKIE) or "", context.settings.session_secret, unix(context.clock())
    )
    response = JSONResponse({"email": email})
    clear_notice_cookie(response, context.settings)
    return response


@router.post("/auth/oidc/code", dependencies=[Depends(_any_on)])
def second_step(body: CodeBody, request: Request, context: Context) -> JSONResponse:
    now = context.clock()
    pending = read_pending(
        request.cookies.get(PENDING_COOKIE) or "", context.settings.session_secret, unix(now)
    )
    if pending is None or not oidc.enabled(context.settings, pending.provider):
        raise ApiError(401, "not_signed_in")
    attempt = _begin(context, pending.customer_id, pending.user_id, now)
    if attempt is None:
        raise _refused(context)
    if (
        attempt.disabled
        or not attempt.customer_active
        or attempt.totp_enabled_at is None
        or attempt.session_version != pending.session_version
    ):
        _failed(context, attempt, "second_step_refused")
        raise _refused(context)
    response = _finish_second_step(context, attempt, body.code, details={"via": pending.provider})
    clear_pending_cookie(response, context.settings)
    return response


# --- the steps -------------------------------------------------------------------------------


def _identity(
    request: Request, context: AppContext, provider: Provider, flow: FlowClaims, now: int
) -> Identity:
    """The checked identity the provider gives for this callback's code."""
    code = request.query_params.get("code", "")
    if "error" in request.query_params or not code:  # cancelled or refused at the provider
        raise _Refused("cancelled")
    try:
        token = oidc.id_token_for(provider, context.settings, code, flow.verifier)
        return oidc.verified_identity(provider, context.settings, token, nonce=flow.nonce, now=now)
    except OidcError as failure:
        raise _Refused("expired" if failure.code in _EXPIRED else "unavailable") from None


def _sign_in(
    context: AppContext, provider: Provider, flow: FlowClaims, identity: Identity
) -> RedirectResponse:
    """The approved connection's user: a session, the code step, or why not."""
    with context.db.system() as conn:
        found = identities.lookup(conn, provider.name, identity.issuer, identity.subject)
    if found is None or found.status != "approved":
        raise _Refused("not_connected", identity.email)
    now = context.clock()
    attempt = _begin(context, found.customer_id, found.user_id, now)
    with context.db.customer(found.customer_id) as conn:
        identities.touch_used(conn, found.customer_id, found.id, now)
    if attempt is None:
        raise _Refused("locked")
    if attempt.disabled:
        _failed(context, attempt, "disabled")
        raise _Refused("refused")
    if not attempt.customer_active:  # disabled, being deleted, or a sign-up still pending
        _failed(context, attempt, "customer_disabled")
        raise _Refused("refused")
    if attempt.totp_enabled_at is not None:
        pending = PendingClaims(
            provider=provider.name,
            user_id=attempt.user_id,
            customer_id=attempt.customer_id,
            session_version=attempt.session_version,
            expires_at=unix(now) + PENDING_SECONDS,
        )
        where = "" if flow.next_path == "/" else "&next=" + urllib.parse.quote(flow.next_path, safe="")
        response = RedirectResponse(f"{LOGIN}?step=code{where}", status_code=303)
        set_pending_cookie(response, pending, context.settings, now=unix(now))
        return response
    if attempt.role == "admin":  # an admin sets up the code at a password sign-in first
        raise _Refused("password_first")
    try:
        _, claims = _open_session(
            context,
            attempt.customer_id,
            attempt.user_id,
            scope=_scope(attempt),
            action="auth.login",
            details={"via": provider.name},
        )
    except ApiError:  # disabled in the meantime
        raise _Refused("refused") from None
    response = RedirectResponse(flow.next_path, status_code=303)
    set_session_cookie(response, claims, context.settings, now=unix(now))
    return response
