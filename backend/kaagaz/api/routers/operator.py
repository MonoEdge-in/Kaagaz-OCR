"""The platform operator's sign-in (super admin PR 2, council 6 Oct 2026, PROVISIONAL).

* ``POST /operator/login`` {email, password, code?}: two-step sign-in is
  MANDATORY. While the operator has not set it up, the right password gets
  ``{"state": "enrol_required", enrol_token, secret, otpauth_uri}`` and no
  session; after that, the right password and code get
  ``{"state": "signed_in", operator}`` and the ``kaagaz_operator`` cookie
  (30 minutes, absolute; ``api/auth.py``).
* ``POST /operator/enrol`` {enrol_token, code}: the first code sets two-step
  sign-in up and starts the session. The token (10 minutes) carries the sealed
  secret and the session version, so it works once.
* ``POST /operator/logout``: ends every session of this operator. 204.
* ``GET /operator/me``.

As the customers' sign-in (``routers/auth.py``): one 401 ``bad_credentials``
for every failure, one scrypt hash on every path, the attempt counted before
the password is checked, the lockout, a code used once. Each outcome writes one
``platform_audit`` row inside its own database function (never a password, a
code or a secret). Every route here uses ``OperatorDatabase`` (the
``kaagaz_operator`` role) and never ``Database``; ``signed_in``'s routes all
need ``current_operator``, which reads only the operator cookie.
"""

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from kaagaz.api import totp
from kaagaz.api.auth import (
    ENROL_SECONDS,
    OPERATOR_SESSION_SECONDS,
    OperatorClaims,
    OperatorSession,
    clear_operator_cookie,
    current_operator,
    read_operator,
    set_operator_cookie,
    sign_operator,
    spend_password_time,
    verify_password,
)
from kaagaz.api.context import AppContext, get_context, get_operator_db
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.admin import MAX_PASSWORD
from kaagaz.api.routers.auth import MAX_CODE, EnrolBody, _box, _digits, _refused, _unix
from kaagaz.api.secret_box import SecretBoxError
from kaagaz.db import operators as operators_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.operators import OperatorAttempt

router = APIRouter(prefix="/operator")  # no session yet
signed_in = APIRouter(prefix="/operator", dependencies=[Depends(current_operator)])

Context = Annotated[AppContext, Depends(get_context)]
OperatorDb = Annotated[OperatorDatabase, Depends(get_operator_db)]


class OperatorLoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    code: str | None = Field(default=None, max_length=MAX_CODE)


@router.post("/login")
def login(body: OperatorLoginBody, db: OperatorDb, context: Context) -> JSONResponse:
    now = context.clock()
    attempt = _begin(db, context, now, email=body.email)
    if attempt is None:  # unknown or locked: no audit row (the lock was audited when it started)
        spend_password_time(body.password)
        raise _refused(context)
    if not verify_password(body.password, attempt.password_hash) or attempt.disabled:
        raise _failed(db, context, attempt)
    if not attempt.totp_enabled:
        return JSONResponse({"state": "enrol_required", **_enrol_offer(context, attempt, now)})
    try:  # a missing secret ("") does not open either
        secret = _box(context.settings.totp_encryption_key).open(
            attempt.totp_secret_enc or "", aad=attempt.operator_id
        )
    except SecretBoxError:
        raise _failed(db, context, attempt) from None  # reset-operator-2fa fixes it
    step = totp.matching_step(secret, _digits(body.code), _unix(now), attempt.totp_last_step)
    with db.operator() as conn:
        version = (
            None if step is None else operators_sql.login_succeeded(conn, attempt.operator_id, step, now)
        )
    if version is None:  # a wrong code, or one used a moment ago
        raise _failed(db, context, attempt)
    return _start_session(context, attempt, version, now)


@router.post("/enrol")
def enrol(body: EnrolBody, db: OperatorDb, context: Context) -> JSONResponse:
    now = context.clock()
    claims = read_operator(body.enrol_token, context.settings.session_secret, _unix(now), enrol=True)
    if claims is None:
        raise ApiError(401, "not_signed_in")
    attempt = _begin(db, context, now, operator_id=claims.operator_id)
    if attempt is None:
        raise _refused(context)
    try:
        secret = _box(context.settings.totp_encryption_key).open(claims.sealed_secret, aad=claims.operator_id)
    except SecretBoxError:
        raise _failed(db, context, attempt) from None
    step = totp.matching_step(secret, _digits(body.code), _unix(now), None)
    with db.operator() as conn:
        version = (
            None
            if step is None
            else operators_sql.enrol(
                conn,
                claims.operator_id,
                secret_enc=claims.sealed_secret,
                step=step,
                now=now,
                version=claims.session_version,
            )
        )
    if version is None:  # a wrong code, a used token, or the operator changed since
        raise _failed(db, context, attempt)
    return _start_session(context, attempt, version, now)


@signed_in.post("/logout", status_code=204)
def logout(operator: OperatorSession, db: OperatorDb, context: Context) -> Response:
    with db.operator() as conn:
        operators_sql.logout(conn, operator.operator_id)
    response = Response(status_code=204)
    clear_operator_cookie(response, context.settings)
    return response


@signed_in.get("/me")
def me(operator: OperatorSession) -> dict[str, Any]:
    return {
        "operator": {
            "id": operator.operator_id,
            "email": operator.email,
            "display_name": operator.display_name,
        },
        "session": {"expires_at": operator.expires_at},
    }


def _begin(
    db: OperatorDatabase,
    context: AppContext,
    now: datetime,
    *,
    email: str | None = None,
    operator_id: str | None = None,
) -> OperatorAttempt | None:
    """Count one attempt (by e-mail or by id), committed before any check."""
    settings = context.settings
    with db.operator() as conn:
        return operators_sql.begin_login(
            conn,
            email=email,
            operator_id=operator_id,
            now=now,
            max_failures=settings.login_max_failures,
            lock_end=now + timedelta(minutes=settings.login_lock_minutes),
        )


def _failed(db: OperatorDatabase, context: AppContext, attempt: OperatorAttempt) -> ApiError:
    """Audit a counted failure (``operator.locked`` when it started the lock); the one 401."""
    with db.operator() as conn:
        locked = attempt.failed_logins >= context.settings.login_max_failures
        operators_sql.login_failed(conn, attempt.operator_id, locked=locked)
    return _refused(context)


def _enrol_offer(context: AppContext, attempt: OperatorAttempt, now: datetime) -> dict[str, str]:
    """A new secret, sealed into a 10-minute enrol token. Nothing is stored yet."""
    secret = totp.new_secret()
    claims = OperatorClaims(
        operator_id=attempt.operator_id,
        session_version=attempt.session_version,
        expires_at=_unix(now) + ENROL_SECONDS,
        sealed_secret=_box(context.settings.totp_encryption_key).seal(secret, aad=attempt.operator_id),
    )
    return {
        "enrol_token": sign_operator(claims, context.settings.session_secret),
        "secret": totp.grouped(totp.b32(secret)),
        "otpauth_uri": totp.otpauth_uri(secret, attempt.email),
    }


def _start_session(
    context: AppContext, attempt: OperatorAttempt, version: int, now: datetime
) -> JSONResponse:
    claims = OperatorClaims(
        operator_id=attempt.operator_id,
        session_version=version,
        expires_at=_unix(now) + OPERATOR_SESSION_SECONDS,
    )
    operator = {"id": attempt.operator_id, "email": attempt.email, "display_name": attempt.display_name}
    response = JSONResponse({"state": "signed_in", "operator": operator})
    set_operator_cookie(response, claims, context.settings, now=_unix(now))
    return response
