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
  never an e-mail, so an operator is never found), and checked as a password's is, without
  counting (Google proved this step; ``peek_attempt``: locked; a disabled user or organisation
  is ``refused`` and audited). The connection is marked used only once every check passed.
  Without two-step sign-in a member or reviewer gets an app session and goes to ``next``,
  audited ``auth.login`` ``{"via": "google"}``. Anyone with codes on gets the 5-minute second
  step cookie and /login?step=code; an admin with no code yet is sent to sign in with the
  password first (``password_first``). Every failure is 303 /login?oidc_error=<code>:
  ``cancelled``, ``expired``, ``not_connected``, ``refused``, ``locked``, ``unavailable``,
  ``busy`` or ``password_first``. The reply carries ``Referrer-Policy: no-referrer``.
* ``POST /auth/oidc/{provider}/connect`` {password, code?} (signed in, any role): Account, Ways
  to sign in. The Kaagaz password, plus the code for anyone with codes on (always an admin),
  each counted like a sign-in, so a stolen session alone cannot add a lasting way in. Then a
  connect flow cookie that also holds the user, customer and session version, and ``{url}``
  for the browser to open. At most CONNECTS_PER_HOUR a user (429). Its callback connects the
  account only while that very session is signed in here (else ``signed_out``: link-CSRF both
  ways), and refuses ``has_provider`` (one of each provider a user) and ``taken`` (that
  account is connected to anyone, or its email is another user's sign-in email in any
  organisation; the reply names no one). It is approved at once for an admin's own, with the
  organisation's auto-approval on, or when Google's verified email is the user's own (any
  case); else it waits for an admin, who is emailed. Audited ``auth.identity_linked``
  {provider, identity_id, status}; the session version is raised and this browser re-signed;
  a security notice is emailed. Back to /settings/sign-in?connected=google, ?waiting=google
  or ?error=<code>.
* ``GET /auth/identities``: Ways to sign in's ``{providers, identities}``: the providers
  switched on and the user's own connections. ``POST /auth/identities/{id}/remove``
  {password}: disconnect (or cancel a waiting) one of the user's own (someone else's: 404),
  audited ``auth.identity_unlinked``; every other browser is signed out, this one stays, the
  password keeps working, and a security notice is emailed. At most CONNECTS_PER_HOUR a user.
* ``GET /auth/oidc/notice``: "isn't connected" names the provider's email, which never goes in
  a URL (history, logs): the callback leaves it in a signed 60-second cookie, read here once.
* ``POST /auth/oidc/code`` {code}: the second step, ``routers/auth.py``'s ``_finish_second_step``
  for the user the second-step cookie names, while their session version is unchanged.

Why the GETs need no CSRF header: start changes nothing, and the callback does nothing
without the state that only this browser's cookie holds. Start is not limited (it only
redirects); callbacks with the right state are limited for the whole site
(``callback_limits``), and past it the reply is the ``busy`` redirect. Logged: the failure's
code and the provider only (for the token step, also its own failure and the provider's HTTP
status), never a code, token, state, nonce, verifier, subject or email. Anything unexpected in
the callback is logged ``oidc_failed`` {provider, error: its type} and is the ``unavailable``
redirect, never a raw JSON reply.
"""

import dataclasses
import hmac
import json
import logging
import re
import secrets
import urllib.parse
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from kaagaz.api import emails, oidc
from kaagaz.api.auth import (
    NOTICE_COOKIE,
    OIDC_COOKIE,
    OIDC_SECONDS,
    PENDING_COOKIE,
    PENDING_SECONDS,
    CurrentUser,
    FlowClaims,
    PendingClaims,
    User,
    clear_flow_cookie,
    clear_notice_cookie,
    clear_pending_cookie,
    current_user,
    read_flow,
    read_notice,
    read_pending,
    session_claims,
    set_flow_cookie,
    set_notice_cookie,
    set_pending_cookie,
    set_session_cookie,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.oidc import Identity, OidcError, Provider
from kaagaz.api.routers.admin import MAX_PASSWORD
from kaagaz.api.routers.auth import (
    CODE_REQUIRED,
    MAX_CODE,
    WRONG_CODE,
    _audit_failure,
    _begin,
    _check_current_password,
    _code_problem,
    _failed,
    _finish_second_step,
    _open_session,
    _record,
    _scope,
)
from kaagaz.api.twostep import refused as _refused
from kaagaz.api.twostep import unix
from kaagaz.api.views import iso
from kaagaz.db import admin as admin_sql
from kaagaz.db import audit, identities
from kaagaz.db import users as users_sql

router = APIRouter()
logger = logging.getLogger(__name__)

Context = Annotated[AppContext, Depends(get_context)]

# Callbacks an hour for the whole site (PROVISIONAL), counted only once the state is right, so
# stray links cannot use it up; sized for real sign-ins. Behind the Next.js proxy every visitor
# shares the proxy's address (kaagaz/api/middleware.py), as SIGNUPS_PER_HOUR says.
# ponytail: site-wide cap; per-visitor once X-Forwarded-For is trusted.
CALLBACKS_PER_HOUR = 600
callback_limits = RateLimiter(CALLBACKS_PER_HOUR, window_seconds=3600)
# Connect starts an hour for one user (PROVISIONAL): each one checks the password.
CONNECTS_PER_HOUR = 10
connect_limits = RateLimiter(CONNECTS_PER_HOUR, window_seconds=3600)
TOO_MANY_CONNECTS = "Too many tries to connect an account. Try again in an hour."
# Disconnects an hour for one user (PROVISIONAL), the spec's per-user limit: each checks the password.
remove_limits = RateLimiter(CONNECTS_PER_HOUR, window_seconds=3600)
TOO_MANY_REMOVES = "Too many tries to disconnect an account. Try again in an hour."
LOGIN = "/login"
WAYS = "/settings/sign-in"  # Account, Ways to sign in
# A plain path on this site: no scheme, host, "//", "\", "%" or fragment can get through.
_NEXT = re.compile(r"/[A-Za-z0-9_-][A-Za-z0-9/_-]{0,199}(\?[A-Za-z0-9=&_-]{1,200})?")
# The OidcError codes the sign-in page explains as "That sign-in expired. Try again."
_EXPIRED = frozenset({"expired", "wrong_nonce", "bad_token"})


class ConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    code: str | None = Field(default=None, max_length=MAX_CODE)  # with two-step sign-in on


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


class CodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=MAX_CODE)


class _Refused(Exception):
    """A sign-in ends on /login?oidc_error=<code>; ``email`` for the "isn't connected" notice;
    ``cause``, for the log only: the provider step's own failure (``OidcError``)."""

    def __init__(self, code: str, email: str | None = None, cause: OidcError | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.email = email
        self.cause = cause


def _provider_on(provider: str, request: Request) -> Provider:
    """The provider of the path, while it is switched on; else 404."""
    if not oidc.enabled(get_context(request).settings, provider):
        raise ApiError(404, "not_found")
    return oidc.PROVIDERS[provider]


def _any_on(request: Request) -> None:
    """404 while no provider is switched on."""
    settings = get_context(request).settings
    if not oidc.enabled_providers(settings):
        raise ApiError(404, "not_found")


ProviderOn = Annotated[Provider, Depends(_provider_on)]


def _safe_next(path: str) -> str:
    """``path`` when it is a plain path on this site, else ``/``."""
    return path if _NEXT.fullmatch(path) else "/"


def _new_flow(provider: Provider, next_path: str, now: int, user: CurrentUser | None = None) -> FlowClaims:
    """A new state, nonce and PKCE verifier: a sign-in, or with ``user`` their connect."""
    return FlowClaims(
        provider=provider.name,
        mode="signin" if user is None else "connect",
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(48),  # 64 characters, as PKCE asks (43 to 128)
        next_path=next_path,
        user_id=None if user is None else user.user_id,
        customer_id=None if user is None else user.customer_id,
        session_version=None if user is None else user.session_version,
        expires_at=now + OIDC_SECONDS,
    )


@router.get("/auth/oidc/{provider}/start")
def start(provider: ProviderOn, request: Request, context: Context) -> RedirectResponse:
    now = unix(context.clock())
    flow = _new_flow(provider, _safe_next(request.query_params.get("next", "")), now)
    url = oidc.authorize_url(
        provider, context.settings, state=flow.state, nonce=flow.nonce, verifier=flow.verifier
    )
    response = RedirectResponse(url, status_code=303)
    set_flow_cookie(response, flow, context.settings, now=now)
    return response


@router.post("/auth/oidc/{provider}/connect")
def connect(provider: ProviderOn, body: ConnectBody, user: User, context: Context) -> JSONResponse:
    key = f"user:{user.user_id}"
    if not connect_limits.allow(key):
        wait = str(connect_limits.retry_after(key))
        raise ApiError(429, "rate_limited", TOO_MANY_CONNECTS, headers={"Retry-After": wait})
    now = context.clock()
    attempt = _check_current_password(context, user, body.password, now)
    if attempt.totp_enabled_at is not None:  # anyone with codes on (always an admin)
        problem = _code_problem(context, attempt, body.code)
        if problem is not None:
            _failed(context, attempt, problem)
            raise ApiError(400, "bad_request", CODE_REQUIRED if problem == "code_required" else WRONG_CODE)
    with context.db.customer(user.customer_id) as conn:
        users_sql.attempt_succeeded(conn, user.customer_id, user.user_id, now=now)
    flow = _new_flow(provider, WAYS, unix(now), user)
    url = oidc.authorize_url(
        provider, context.settings, state=flow.state, nonce=flow.nonce, verifier=flow.verifier
    )
    response = JSONResponse({"url": url})
    set_flow_cookie(response, flow, context.settings, now=unix(now))
    return response


@router.get("/auth/oidc/{provider}/callback")
def callback(
    provider: ProviderOn, request: Request, background: BackgroundTasks, context: Context
) -> RedirectResponse:
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
        if not callback_limits.allow("site"):
            raise _Refused("busy")
        identity = _identity(request, context, provider, flow, now)
        if flow.mode == "connect":
            response = _connect(request, context, background, provider, flow, identity)
        else:
            response = _sign_in(context, provider, flow, identity)
    except _Refused as refused:
        logged: dict[str, object] = {"event": "oidc_refused", "provider": provider.name, "code": refused.code}
        if refused.cause is not None:  # the token step: why, and the provider's status
            logged |= {"reason": refused.cause.code, "status": refused.cause.status}
        logger.warning(json.dumps(logged, separators=(",", ":")))
        response = _back(flow, refused.code)
        if refused.email is not None:
            set_notice_cookie(response, refused.email, context.settings, now=now)
    except Exception as error:  # noqa: BLE001 - never raw JSON from a redirect page; logged by type only
        failed = {"event": "oidc_failed", "provider": provider.name, "error": type(error).__name__}
        logger.error(json.dumps(failed, separators=(",", ":")))
        response = _back(flow, "unavailable")
    clear_flow_cookie(response, context.settings)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _back(flow: FlowClaims | None, code: str) -> RedirectResponse:
    """Back to the page the flow started from, with why: Ways to sign in for a connect."""
    back = f"{WAYS}?error=" if flow is not None and flow.mode == "connect" else f"{LOGIN}?oidc_error="
    return RedirectResponse(back + code, status_code=303)


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


@router.get("/auth/identities", dependencies=[Depends(_any_on)])
def my_identities(user: User, context: Context) -> dict[str, Any]:
    return _identities_json(context, user)


@router.post("/auth/identities/{identity_id}/remove", dependencies=[Depends(_any_on)])
def remove_identity(
    identity_id: uuid.UUID,
    body: PasswordBody,
    user: User,
    request: Request,
    background: BackgroundTasks,
    context: Context,
) -> JSONResponse:
    """Disconnect (or cancel a waiting) connection of the user's own, with the password. Every
    other browser is signed out, this one stays; the password keeps working."""
    key = f"user:{user.user_id}"
    if not remove_limits.allow(key):
        wait = str(remove_limits.retry_after(key))
        raise ApiError(429, "rate_limited", TOO_MANY_REMOVES, headers={"Retry-After": wait})
    now = context.clock()
    _check_current_password(context, user, body.password, now)
    with context.db.customer(user.customer_id) as conn:
        users_sql.attempt_succeeded(conn, user.customer_id, user.user_id, now=now)
    with context.db.customer(user.customer_id) as conn:
        removed = identities.remove(conn, user.customer_id, str(identity_id), user_id=user.user_id)
        if removed is None:
            raise ApiError(404, "not_found")
        version = users_sql.bump_session_version(conn, user.customer_id, user.user_id)
        details: dict[str, audit.DetailValue] = {
            "provider": str(removed["provider"]),
            "identity_id": str(identity_id),
        }
        _record(
            conn,
            user.customer_id,
            "auth.identity_unlinked",
            actor=user.user_id,
            target=user.user_id,
            details=details,
        )
    response = JSONResponse(_identities_json(context, user))
    _keep_signed_in(request, context, response, version)
    who = (context, user.customer_id, user.user_id)
    notice = emails.google_disconnected
    emails.after(background, "identity_unlinked", emails.notify, *who, "identity_unlinked", notice)
    return response


def _identities_json(context: AppContext, user: CurrentUser) -> dict[str, Any]:
    """Ways to sign in: the providers switched on, and the user's own connections."""
    with context.db.customer(user.customer_id) as conn:
        rows = identities.for_user(conn, user.customer_id, user.user_id)
    return {
        "providers": oidc.enabled_providers(context.settings),
        "identities": [
            {
                "id": str(row["id"]),
                "provider": row["provider"],
                "email": row["email_at_link"],
                "status": row["status"],
                "linked_at": iso(row["linked_at"]),
                "last_used_at": None if row["last_used_at"] is None else iso(row["last_used_at"]),
            }
            for row in rows
        ],
    }


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
        raise _Refused("expired" if failure.code in _EXPIRED else "unavailable", cause=failure) from None


def _sign_in(
    context: AppContext, provider: Provider, flow: FlowClaims, identity: Identity
) -> RedirectResponse:
    """The approved connection's user: a session, the code step, or why not."""
    with context.db.system() as conn:
        found = identities.lookup(conn, provider.name, identity.issuer, identity.subject)
    if found is None or found.status != "approved":
        raise _Refused("not_connected", identity.email)
    now = context.clock()
    # Read, not counted: Google proved this step. The one count of a sign-in is the code step's
    # (anyone with codes on) or cleared at once by the session (#457 review).
    with context.db.customer(found.customer_id) as conn:
        attempt = users_sql.peek_attempt(conn, found.customer_id, found.user_id, now=now)
    if attempt is None:
        raise _Refused("locked")
    if attempt.disabled:
        _audit_failure(context, attempt.customer_id, attempt.user_id, "disabled")
        raise _Refused("refused")
    if not attempt.customer_active:  # disabled, being deleted, or a sign-up still pending
        _audit_failure(context, attempt.customer_id, attempt.user_id, "customer_disabled")
        raise _Refused("refused")
    if attempt.totp_enabled_at is None and attempt.role == "admin":
        raise _Refused("password_first")  # an admin sets up the code at a password sign-in first
    with context.db.customer(found.customer_id) as conn:  # a sign-in that passed every check
        identities.touch_used(conn, found.customer_id, found.id, now)
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


def _connect(
    request: Request,
    context: AppContext,
    background: BackgroundTasks,
    provider: Provider,
    flow: FlowClaims,
    identity: Identity,
) -> RedirectResponse:
    """Connect ``identity`` to the user who started this connect, while that very session is
    still signed in here (link-CSRF both ways); then this browser stays signed in."""
    try:
        user = current_user(request)
    except ApiError:
        raise _Refused("signed_out") from None
    signed = (user.user_id, user.customer_id, user.session_version)
    if signed != (flow.user_id, flow.customer_id, flow.session_version):
        raise _Refused("signed_out")
    customer_id, user_id = user.customer_id, user.user_id
    with context.db.customer(customer_id) as conn:
        mine = identities.for_user(conn, customer_id, user_id)
        organisation = admin_sql.get_settings(conn, customer_id)
    if any(row["provider"] == provider.name for row in mine):
        raise _Refused("has_provider")
    with context.db.system() as conn:
        found = identities.lookup(conn, provider.name, identity.issuer, identity.subject)
        belongs = identity.email is not None and identities.email_belongs_to_other(
            conn, identity.email, user_id
        )
    if found is not None or belongs:  # never says whose
        raise _Refused("taken")
    # Same-person auto-approval (Tushar, 30 Oct): Google's verified email is the user's own.
    same_person = (
        provider.name == "google" and identity.email_verified and identity.email == user.email.lower()
    )
    auto = organisation is not None and bool(organisation["auto_approve_connections"])
    approved = user.role == "admin" or auto or same_person
    try:
        with context.db.customer(customer_id) as conn:
            identity_id = identities.add(
                conn,
                customer_id,
                user_id,
                provider=provider.name,
                issuer=identity.issuer,
                subject=identity.subject,
                email=identity.email,
                approved=approved,
                approved_by=None,
                now=context.clock(),
            )
            version = users_sql.bump_session_version(conn, customer_id, user_id)
            status = "approved" if approved else "pending"
            details: dict[str, audit.DetailValue] = {
                "provider": provider.name,
                "identity_id": identity_id,
                "status": status,
            }
            _record(conn, customer_id, "auth.identity_linked", actor=user_id, target=user_id, details=details)
    except identities.IdentityTaken:  # someone connected it a moment ago
        raise _Refused("taken") from None
    outcome = "connected" if approved else "waiting"
    response = RedirectResponse(f"{WAYS}?{outcome}={provider.name}", status_code=303)
    _keep_signed_in(request, context, response, version)
    purpose, notice = ("identity_linked", emails.google_connected)
    if not approved:
        purpose, notice = ("identity_waiting", emails.google_waiting)
        emails.after(background, "approval_waiting", _tell_admins, context, customer_id, user.customer_name)
    emails.after(background, purpose, emails.notify, context, customer_id, user_id, purpose, notice)
    return response


def _keep_signed_in(request: Request, context: AppContext, response: Response, version: int) -> None:
    """This browser's session, re-signed with the raised session version: every other browser
    of the user is signed out, this one keeps its scope and end time."""
    now = unix(context.clock())
    claims = session_claims(request, context.settings, now)
    if claims is not None:
        renewed = dataclasses.replace(claims, session_version=version)
        set_session_cookie(response, renewed, context.settings, now=now)


def _tell_admins(context: AppContext, customer_id: str, organisation: str) -> None:
    """Waiting for your approval, to each admin, while email is on."""
    if not emails.email_on(context):
        return
    with context.db.customer(customer_id) as conn:
        admins = users_sql.admin_contacts(conn, customer_id)
    link = f"{context.settings.app_url}/admin"
    for email, name in admins:
        emails.send(context, email, "approval_waiting", emails.connection_waiting(name, organisation, link))
