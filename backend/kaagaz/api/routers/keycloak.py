"""Keycloak login (the only sign-in), action reauthentication and signed logout."""

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from kaagaz.api import keycloak_oidc
from kaagaz.api.auth import (
    KEYCLOAK_PROOF_ACTIONS,
    LOCKED_OPEN_SECONDS,
    CurrentUser,
    SessionClaims,
    User,
    new_session,
    session_claims,
    set_confirm_cookie,
    set_keycloak_proof_cookie,
    set_locked_open_cookie,
    set_session_cookie,
    set_whatsapp_proof_cookie,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter, client_address
from kaagaz.api.twostep import unix
from kaagaz.db import audit, keycloak, users
from kaagaz.db.engine import Database

router = APIRouter()
Context = Annotated[AppContext, Depends(get_context)]
logger = logging.getLogger("kaagaz.api.keycloak")

STATE_COOKIE = "kaagaz_keycloak_state"
STATE_SECONDS = 10 * 60
LOGIN = "/login"
# What Keycloak's own pages can do for a signed-in person (``kc_action``).
ACCOUNT_ACTIONS = frozenset({"UPDATE_PASSWORD", "CONFIGURE_TOTP"})
CALLBACK_PATH = "/api/auth/keycloak/callback"
BACKCHANNEL_PATH = "/api/auth/keycloak/backchannel-logout"
_start_limits = RateLimiter(20)
_callback_limits = RateLimiter(60)
_reauth_limits = RateLimiter(20)
_backchannel_limits = RateLimiter(120)


class ReauthBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next: str = Field(default="/", max_length=2048)


@router.get("/auth/keycloak/start")
def login_start(request: Request, context: Context) -> RedirectResponse:
    """Start sign-in for already linked users; this creates no identity mapping. An optional
    ``action`` (one of ``ACCOUNT_ACTIONS``) is passed to Keycloak as ``kc_action``, so its own page
    for a new password or an authenticator app opens after the sign-in; any other value is refused."""
    _configured(context)
    _limit(_start_limits, client_address(request), "Too many sign-in attempts. Try again shortly.")
    next_path = _safe_next(request.query_params.get("next", "/"))
    action = request.query_params.get("action")
    if action is not None and action not in ACCOUNT_ACTIONS:
        raise ApiError(400, "bad_request", "That account action is not available.")
    flow, state = _new_flow(context, purpose="login", next_path=next_path)
    url = keycloak_oidc.authorize_url(
        context.settings, state=state, nonce=flow.nonce, verifier=_open_verifier(context, flow, state)
    )
    if action is not None:
        url += ("&" if "?" in url else "?") + urlencode({"kc_action": action})
    response = RedirectResponse(url, status_code=303)
    _set_flow_cookie(response, state, context.settings.cookie_secure)
    _no_store(response)
    return response


@router.post("/auth/keycloak/reauth/{action}/start")
def reauth_start(
    action: str, body: ReauthBody, request: Request, user: User, context: Context
) -> JSONResponse:
    """Start fresh Keycloak proof bound to this user, customer, version, browser and action."""
    _configured(context)
    if user.auth_authority != "keycloak" or action not in KEYCLOAK_PROOF_ACTIONS:
        raise ApiError(403, "forbidden")
    _limit(_reauth_limits, f"user:{user.user_id}", "Too many reauthentication attempts. Try again shortly.")
    claims = session_claims(request, context.settings, unix(context.clock()))
    if (
        claims is None
        or claims.auth_source != "keycloak"
        or claims.assurance != context.settings.keycloak_required_acr
    ):
        raise ApiError(401, "not_signed_in")
    flow, state = _new_flow(
        context,
        purpose="reauth",
        next_path=_safe_next(body.next),
        user=user,
        claims=claims,
        action=action,
    )
    url = keycloak_oidc.authorize_url(
        context.settings, state=state, nonce=flow.nonce, verifier=_open_verifier(context, flow, state)
    )
    response = JSONResponse({"url": url})
    _set_flow_cookie(response, state, context.settings.cookie_secure)
    _no_store(response)
    return response


@router.get("/auth/keycloak/callback")
def callback(request: Request, context: Context) -> RedirectResponse:
    """Consume query-mode state once, validate the ID token, then login, link, or step up."""
    _configured(context)
    _limit(_callback_limits, client_address(request), "Too many callback attempts. Try again shortly.")
    query = _read_query(request, maximum=16_384)
    if set(query) - {"state", "code", "error", "session_state", "iss", "error_description", "error_uri"}:
        raise ApiError(400, "invalid_callback")
    if not query.get("state") or ("code" in query) == ("error" in query):
        raise ApiError(400, "invalid_callback")
    response_issuer = query.get("iss")
    if response_issuer is not None and not hmac.compare_digest(
        response_issuer, context.settings.keycloak_issuer or ""
    ):
        raise ApiError(400, "invalid_callback")
    state = _form_text(query.get("state"), maximum=256)
    cookie_state = request.cookies.get(STATE_COOKIE, "")
    flow: keycloak.FlowRow | None = None
    fallback = RedirectResponse(f"{LOGIN}?keycloak_error=expired", status_code=303)
    if not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
        _clear_flow_cookie(fallback, context.settings.cookie_secure)
        return _no_store(fallback)
    state_hash = hashlib.sha256(state.encode("ascii")).digest()
    now = context.clock()
    with context.db.system() as conn:
        flow = keycloak.consume_flow(conn, state_hash, now=now)
    if flow is None:
        _clear_flow_cookie(fallback, context.settings.cookie_secure)
        return _no_store(fallback)
    if query.get("error") is not None:
        response = _failure(flow, "cancelled")
        _clear_flow_cookie(response, context.settings.cookie_secure)
        return _no_store(response)
    code = _form_text(query.get("code"), maximum=4096)
    if not code:
        response = _failure(flow, "cancelled")
        _clear_flow_cookie(response, context.settings.cookie_secure)
        return _no_store(response)
    try:
        verifier = keycloak_oidc.open_verifier(
            flow.sealed_verifier,
            context.settings.keycloak_flow_encryption_key or "",
            state_hash,
        )
        token = keycloak_oidc.id_token_for(context.settings, code, verifier)
        identity = keycloak_oidc.verified_identity(
            context.settings,
            token,
            nonce=flow.nonce,
            now=unix(now),
            started_at=unix(flow.started_at),
        )
        if flow.purpose == "login":
            response = _login(context, flow, identity)
        else:
            user, claims = _bound_user(request, context, flow)
            if flow.purpose == "reauth":
                response = _reauth(context, flow, identity, user, claims)
            else:
                response = _failure(flow, "expired")
    except keycloak_oidc.OidcError as error:
        logger.info("Keycloak callback refused", extra={"code": error.code})
        response = _failure(flow, _safe_error(error.code))
    except (keycloak.IdentityTaken, ApiError, ValueError):
        response = _failure(flow, "refused")
    except Exception as error:  # noqa: BLE001 - provider/DB details never leave this handler
        logger.error("Keycloak callback failed", extra={"error_type": type(error).__name__})
        response = _failure(flow, "unavailable")
    _clear_flow_cookie(response, context.settings.cookie_secure)
    response.headers["Referrer-Policy"] = "no-referrer"
    return _no_store(response)


@router.post("/auth/keycloak/backchannel-logout")
async def backchannel_logout(request: Request, context: Context) -> Response:
    """Accept only a verified signed logout token; the database stores replay and sid tombstones."""
    _configured(context)
    _limit(_backchannel_limits, client_address(request), "Too many logout notifications. Try again shortly.")
    form = await _read_form(request, maximum=32_768)
    token = _form_text(form.get("logout_token"), maximum=32_768)
    if not token or set(form.keys()) != {"logout_token"}:
        raise ApiError(400, "invalid_logout")
    try:
        issuer, subject, sid, jti, expires = keycloak_oidc.verified_logout(
            context.settings, token, now=unix(context.clock())
        )
    except keycloak_oidc.OidcError:
        raise ApiError(400, "invalid_logout") from None
    now = context.clock()
    with context.db.system() as conn:
        replay = keycloak.apply_signed_logout(
            conn,
            issuer,
            subject,
            sid,
            jti,
            token_expires_at=datetime.fromtimestamp(expires, UTC),
            now=now,
        )
    if replay is None:
        raise ApiError(400, "invalid_logout")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


def _login(context: AppContext, flow: keycloak.FlowRow, identity: keycloak_oidc.Identity) -> RedirectResponse:
    with context.db.system() as conn:
        found = keycloak.lookup(conn, identity.issuer, identity.subject)
    if found is None:
        return _failure(flow, "not_connected")
    now = context.clock()
    with context.db.customer(found.customer_id) as conn:
        attempt = users.peek_attempt(conn, found.customer_id, found.user_id, now=now)
    if (
        attempt is None
        or attempt.disabled
        or not attempt.customer_active
        or attempt.customer_pending
        or attempt.auth_authority != "keycloak"
        or attempt.session_version != found.session_version
        or not _broker_policy_allowed(attempt, provider=identity.authentication_provider)
    ):
        return _failure(flow, "refused")
    scope = "admin" if attempt.role == "admin" else "app"
    user = _load_active(context.db, found.customer_id, found.user_id, scope)
    if user is None or user.role != attempt.role or user.session_version != found.session_version:
        return _failure(flow, "refused")
    claims = new_session(
        user_id=user.user_id,
        customer_id=user.customer_id,
        role=user.role,
        scope=scope,
        session_version=found.session_version,
        settings=context.settings,
        now=unix(now),
        idle_hours=user.idle_hours,
        auth_source="keycloak",
        assurance=identity.acr,
    )
    with context.db.user(user.customer_id, user.user_id) as conn:
        current = users.peek_attempt(conn, user.customer_id, user.user_id, now=now)
        if (
            current is None
            or current.auth_authority != "keycloak"
            or current.disabled
            or not current.customer_active
            or current.role != user.role
            or current.session_version != found.session_version
        ):
            return _failure(flow, "refused")
        users.attempt_succeeded(conn, user.customer_id, user.user_id, now=now)
        if not keycloak.record_session(
            conn,
            identity.issuer,
            identity.subject,
            identity.session_id,
            claims.session_id,
            expected_session_version=found.session_version,
            expires_at=datetime.fromtimestamp(claims.expires_at, UTC),
        ):
            raise ValueError("session changed or revoked")
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="auth.login",
            target_type="user",
            target_id=user.user_id,
            details={"via": "keycloak"},
        )
    response = RedirectResponse(flow.next_path, status_code=303)
    set_session_cookie(response, claims, context.settings, now=unix(now))
    return response


def _reauth(
    context: AppContext,
    flow: keycloak.FlowRow,
    identity: keycloak_oidc.Identity,
    user: CurrentUser,
    claims: SessionClaims,
) -> RedirectResponse:
    if flow.action not in KEYCLOAK_PROOF_ACTIONS or user.auth_authority != "keycloak":
        return _failure(flow, "refused")
    with context.db.system() as conn:
        found = keycloak.lookup(conn, identity.issuer, identity.subject)
    if (
        found is None
        or (found.customer_id, found.user_id, found.session_version)
        != (user.customer_id, user.user_id, flow.session_version)
        or not _broker_policy_allowed(user, provider=identity.authentication_provider)
    ):
        return _failure(flow, "not_yours")
    now = unix(context.clock())
    with context.db.user(user.customer_id, user.user_id) as conn:
        if not keycloak.record_session(
            conn,
            identity.issuer,
            identity.subject,
            identity.session_id,
            claims.session_id,
            expected_session_version=user.session_version,
            expires_at=datetime.fromtimestamp(claims.expires_at, UTC),
        ):
            return _failure(flow, "refused")
    response = RedirectResponse(flow.next_path, status_code=303)
    set_keycloak_proof_cookie(
        response,
        user,
        session_id=claims.session_id,
        action=flow.action,
        auth_time=identity.auth_time,
        assurance=identity.acr,
        settings=context.settings,
        now=now,
    )
    if flow.action == "locked_open":
        set_locked_open_cookie(
            response,
            user,
            claims.session_id,
            context.settings,
            until=now + LOCKED_OPEN_SECONDS,
        )
    elif flow.action == "delete_forever":
        set_confirm_cookie(response, user, context.settings, now=now, session_id=claims.session_id)
    elif flow.action == "whatsapp":
        set_whatsapp_proof_cookie(response, user, context.settings, now=now)
    return response


def _bound_user(
    request: Request, context: AppContext, flow: keycloak.FlowRow
) -> tuple[CurrentUser, SessionClaims]:
    if (
        flow.customer_id is None
        or flow.user_id is None
        or flow.session_version is None
        or flow.local_session_id is None
    ):
        raise ValueError("invalid bound flow")
    claims = session_claims(request, context.settings, unix(context.clock()))
    if (
        claims is None
        or (claims.customer_id, claims.user_id, claims.session_version, claims.session_id)
        != (flow.customer_id, flow.user_id, flow.session_version, flow.local_session_id)
        or (flow.purpose == "reauth" and claims.auth_source != "keycloak")
    ):
        raise ValueError("flow browser session changed")
    user = _load_active(context.db, flow.customer_id, flow.user_id, claims.scope, claims.session_id)
    if user is None or user.role != claims.role or user.session_version != flow.session_version:
        raise ValueError("flow user changed")
    if flow.purpose == "reauth" and user.auth_authority != "keycloak":
        raise ValueError("flow authority changed")
    if flow.purpose == "reauth" and claims.assurance != context.settings.keycloak_required_acr:
        raise ValueError("flow assurance changed")
    return user, claims


def _load_active(
    database: Database,
    customer_id: str,
    user_id: str,
    scope: str,
    session_id: str | None = None,
) -> CurrentUser | None:
    from kaagaz.api.auth import load_user

    return load_user(database, customer_id, user_id, scope=scope, session_id=session_id)


def _new_flow(
    context: AppContext,
    *,
    purpose: str,
    next_path: str,
    user: CurrentUser | None = None,
    claims: SessionClaims | None = None,
    action: str | None = None,
) -> tuple[keycloak.FlowRow, str]:
    state = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(state.encode("ascii")).digest()
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    now = context.clock()
    sealed = keycloak_oidc.seal_verifier(
        verifier,
        context.settings.keycloak_flow_encryption_key or "",
        state_hash,
    )
    values: dict[str, Any] = {
        "customer_id": None,
        "user_id": None,
        "session_version": None,
        "local_session_id": None,
    }
    if purpose != "login":
        if user is None or claims is None:
            raise ValueError("bound flow needs a user session")
        values.update(
            customer_id=user.customer_id,
            user_id=user.user_id,
            session_version=user.session_version,
            local_session_id=claims.session_id,
        )
    flow_expiry = now + timedelta(seconds=STATE_SECONDS)
    if purpose == "login":
        connection = context.db.system()
    else:
        if user is None:
            raise ValueError("bound flow needs a user session")
        connection = context.db.user(user.customer_id, user.user_id)
    with connection as conn:
        keycloak.create_flow(
            conn,
            state_hash,
            nonce=nonce,
            sealed_verifier=sealed,
            purpose=purpose,
            next_path=next_path,
            started_at=now,
            expires_at=flow_expiry,
            action=action,
            **values,
        )
    return (
        keycloak.FlowRow(
            nonce=nonce,
            sealed_verifier=sealed,
            purpose=purpose,
            next_path=next_path,
            customer_id=values["customer_id"],
            user_id=values["user_id"],
            session_version=values["session_version"],
            local_session_id=values["local_session_id"],
            action=action,
            started_at=now,
        ),
        state,
    )


def _open_verifier(context: AppContext, flow: keycloak.FlowRow, state: str) -> str:
    return keycloak_oidc.open_verifier(
        flow.sealed_verifier,
        context.settings.keycloak_flow_encryption_key or "",
        hashlib.sha256(state.encode("ascii")).digest(),
    )


def _broker_policy_allowed(user: Any, *, provider: str) -> bool:
    """Only the configured local password-plus-OTP execution is allowed in this build."""
    if provider == "local":
        return not (user.google_on and user.google_only)
    return False


def _configured(context: AppContext) -> None:
    if not context.settings.keycloak_enabled:
        raise ApiError(404, "not_found")


def _safe_next(value: str) -> str:
    parts = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parts.scheme
        or parts.netloc
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or len(value) > 2048
    ):
        return "/"
    return value


def _set_flow_cookie(response: Response, state: str, _secure: bool) -> None:
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_SECONDS,
        httponly=True,
        # Query mode returns as a top-level GET, which sends the Lax state cookie.
        samesite="lax",
        secure=_secure,
        path="/api/auth/keycloak",
    )


def _clear_flow_cookie(response: Response, _secure: bool) -> None:
    response.delete_cookie(
        STATE_COOKIE,
        path="/api/auth/keycloak",
        httponly=True,
        samesite="lax",
        secure=_secure,
    )


def _failure(flow: keycloak.FlowRow, code: str) -> RedirectResponse:
    code = _safe_error(code)
    if flow.purpose == "reauth":
        separator = "&" if "?" in flow.next_path else "?"
        return RedirectResponse(f"{flow.next_path}{separator}reauth_error={code}", status_code=303)
    return RedirectResponse(f"{LOGIN}?keycloak_error={code}", status_code=303)


def _safe_error(code: str) -> str:
    allowed = {
        "cancelled",
        "not_connected",
        "refused",
        "expired",
        "unavailable",
        "organization_provider_policy_unconfigured",
        "mfa_verify_again",
    }
    return code if code in allowed else "refused"


def _form_text(value: object, *, maximum: int) -> str:
    return (
        value
        if isinstance(value, str)
        and 0 < len(value) <= maximum
        and value == value.strip()
        and value.isascii()
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
        else ""
    )


def _read_query(request: Request, *, maximum: int) -> dict[str, str]:
    """Parse bounded, single-valued authorization response parameters strictly."""
    raw = request.scope.get("query_string", b"")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise ApiError(400, "invalid_callback")
    try:
        encoded = raw.decode("ascii")
        if any(
            encoded[index] == "%"
            and (
                index + 2 >= len(encoded)
                or any(char not in "0123456789abcdefABCDEF" for char in encoded[index + 1 : index + 3])
            )
            for index in range(len(encoded))
        ):
            raise ValueError("bad escape")
        pairs = parse_qsl(
            encoded,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeError, ValueError):
        raise ApiError(400, "invalid_callback") from None
    if len(pairs) != len({name for name, _ in pairs}):
        raise ApiError(400, "invalid_callback")
    limits = {
        "state": 256,
        "code": 4096,
        "error": 256,
        "session_state": 256,
        "iss": 2048,
        "error_description": 1024,
        "error_uri": 2048,
    }
    for name, value in pairs:
        if name not in limits or not _form_text(value, maximum=limits[name]):
            raise ApiError(400, "invalid_callback")
    return dict(pairs)


async def _read_form(request: Request, *, maximum: int) -> dict[str, str]:
    """Read a bounded, single-valued form body without accepting uploads or body logging."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    content_length = request.headers.get("content-length", "")
    try:
        size = int(content_length)
    except ValueError:
        size = maximum + 1
    if content_type != "application/x-www-form-urlencoded" or not 0 < size <= maximum:
        raise ApiError(400, "bad_request")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise ApiError(400, "bad_request")
        body.extend(chunk)
    if len(body) != size:
        raise ApiError(400, "bad_request")
    try:
        pairs = parse_qsl(
            body.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
        )
    except (UnicodeError, ValueError):
        raise ApiError(400, "bad_request") from None
    if len(pairs) != len({name for name, _ in pairs}):
        raise ApiError(400, "bad_request")
    return dict(pairs)


def _limit(limiter: RateLimiter, key: str, message: str) -> None:
    if not limiter.allow(key):
        raise ApiError(429, "rate_limited", message, headers={"Retry-After": str(limiter.retry_after(key))})


def _no_store[ResponseType: Response](response: ResponseType) -> ResponseType:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
