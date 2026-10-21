"""The platform operator's sign-in (super admin PR 2, council 6 Oct 2026, PROVISIONAL).

* ``POST /operator/login`` {email, password, code}: two-step is MANDATORY and its
  secret is issued ONLY on the server (``cli create-operator``, ``reset-operator-2fa``).
  The password and a fresh code from that secret get the cookie; the first code ever
  also confirms the secret. Anything else, the password alone included, gets the one 401.
* ``POST /operator/logout`` (ends every session of this operator), ``GET /operator/me``.
* Moving two-step to a new device (Tushar, 16 Oct 2026: a handover to another person's
  phone; 0031). ``POST /operator/2fa/move/start`` {password, code}: the password and the
  CURRENT code, counted like a sign-in (the lockout too; a refusal is 403
  ``move_refused``, never the 401 that ends the console), give ``{move_token, secret,
  otpauth_uri}`` for the NEW device. Nothing is stored: the new secret travels sealed in
  the signed token (10 minutes, bound to this session's version), and the old device
  keeps working. ``POST /operator/2fa/move/confirm`` {move_token, code}: a code from the
  NEW secret replaces the secret in one statement, every other browser is signed out and
  this one re-signed. Any wrong, used or expired token or code: the one 403, nothing
  changed. Not counted: whoever holds the token was shown the new secret already.
  No route turns two-step off.

As ``routers/auth.py``: one scrypt hash on every path, the attempt counted first, the
lockout, a code used once. Each outcome writes one ``platform_audit`` row in its
database function. Only ``OperatorDatabase`` here.
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
    CurrentOperator,
    MoveClaims,
    OperatorClaims,
    OperatorSession,
    clear_operator_cookie,
    current_operator,
    read_move,
    set_operator_cookie,
    sign_move,
    spend_password_time,
    verify_password,
)
from kaagaz.api.context import AppContext, get_context, get_operator_db
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.admin import MAX_PASSWORD
from kaagaz.api.routers.auth import MAX_CODE
from kaagaz.api.secret_box import SecretBoxError
from kaagaz.api.twostep import box, digits, refused, unix
from kaagaz.db import operators as operators_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.operators import OperatorAttempt

router = APIRouter(prefix="/operator")  # no session yet
signed_in = APIRouter(prefix="/operator", dependencies=[Depends(current_operator)])

Context = Annotated[AppContext, Depends(get_context)]
OperatorDb = Annotated[OperatorDatabase, Depends(get_operator_db)]

MOVE_NOT_ACCEPTED = (
    "That password or code was not accepted. Check both, wait for a new code, and try again."
    " After many wrong tries, this stops for a while."
)
MOVE_NOT_CONFIRMED = (
    "That code from the new device was not accepted, so nothing changed."
    " Check the new device and try again, or start again."
)


class OperatorLoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    code: str | None = Field(default=None, max_length=MAX_CODE)  # none: the one 401, counted


class MoveStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    code: str = Field(min_length=1, max_length=MAX_CODE)  # from the CURRENT device


class MoveConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    move_token: str = Field(min_length=1, max_length=4096)
    code: str = Field(min_length=1, max_length=MAX_CODE)  # from the NEW device


@router.post("/login")
def login(body: OperatorLoginBody, db: OperatorDb, context: Context) -> JSONResponse:
    now = context.clock()
    attempt = _begin(db, context, now, body.email)
    if attempt is None:  # unknown or locked: no audit row (the lock was audited when it began)
        spend_password_time(body.password)
        raise refused(context)
    if not verify_password(body.password, attempt.password_hash) or attempt.disabled:
        raise _failed(db, context, attempt)
    try:  # no secret issued yet ("") does not open either: no session on the password alone
        secret = box(context.settings.totp_encryption_key).open(
            attempt.totp_secret_enc or "", aad=attempt.operator_id
        )
    except SecretBoxError:
        raise _failed(db, context, attempt) from None
    step = totp.matching_step(secret, digits(body.code), unix(now), attempt.totp_last_step)
    version = None
    if step is not None:  # the function refuses a step already used, even by a request at the same time
        with db.operator() as conn:
            version = operators_sql.login_succeeded(conn, attempt.operator_id, step, now)
    if version is None:
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
        "operator": {"id": operator.operator_id, "email": operator.email, "name": operator.display_name},
        "session": {"expires_at": operator.expires_at},
    }


@signed_in.post("/2fa/move/start")
def move_start(
    body: MoveStartBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, str]:
    now = context.clock()
    attempt = _begin(db, context, now, operator.email)
    if attempt is None:  # locked: no audit row (the lock was audited when it began)
        spend_password_time(body.password)
        raise ApiError(403, "move_refused", MOVE_NOT_ACCEPTED)
    if not verify_password(body.password, attempt.password_hash) or attempt.disabled:
        raise _move_refused(db, context, attempt)
    try:
        secret = box(context.settings.totp_encryption_key).open(
            attempt.totp_secret_enc or "", aad=attempt.operator_id
        )
    except SecretBoxError:
        raise _move_refused(db, context, attempt) from None
    step = totp.matching_step(secret, digits(body.code), unix(now), attempt.totp_last_step)
    used = False
    if step is not None:  # the function refuses a step already used, even by a request at the same time
        with db.operator() as conn:
            used = operators_sql.move_check(conn, attempt.operator_id, step)
    if not used:
        raise _move_refused(db, context, attempt)
    new_secret = totp.new_secret()
    claims = MoveClaims(
        operator_id=operator.operator_id,
        session_version=operator.session_version,
        sealed_secret=box(context.settings.totp_encryption_key).seal(new_secret, aad=operator.operator_id),
        expires_at=unix(now) + ENROL_SECONDS,
    )
    return {
        "move_token": sign_move(claims, context.settings.session_secret),
        "secret": totp.grouped(totp.b32(new_secret)),
        "otpauth_uri": totp.otpauth_uri(new_secret, operator.email),
    }


@signed_in.post("/2fa/move/confirm")
def move_confirm(
    body: MoveConfirmBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> JSONResponse:
    now = context.clock()
    found = _new_code(context, operator, body, now)
    version = None
    if found is not None:  # only while the session version is still the token's: it works once
        claims, step = found
        with db.operator() as conn:
            version = operators_sql.move_totp(
                conn,
                operator.operator_id,
                version=claims.session_version,
                sealed=claims.sealed_secret,
                step=step,
                now=now,
            )
    if version is None:
        raise ApiError(403, "move_refused", MOVE_NOT_CONFIRMED)
    return _start_session(context, operator, version, now)


def _new_code(
    context: AppContext, operator: CurrentOperator, body: MoveConfirmBody, now: datetime
) -> tuple[MoveClaims, int] | None:
    """This operator's genuine move token from this session and the step of a code from
    its NEW secret, else None."""
    claims = read_move(body.move_token, context.settings.session_secret, unix(now))
    if claims is None or claims.operator_id != operator.operator_id:
        return None
    if claims.session_version != operator.session_version:  # signed out or moved since
        return None
    try:
        secret = box(context.settings.totp_encryption_key).open(
            claims.sealed_secret, aad=operator.operator_id
        )
    except SecretBoxError:
        return None
    step = totp.matching_step(secret, digits(body.code), unix(now), None)
    return None if step is None else (claims, step)


def _move_refused(db: OperatorDatabase, context: AppContext, attempt: OperatorAttempt) -> ApiError:
    """Counted and audited as a failed sign-in; a 403, so the console stays open."""
    _failed(db, context, attempt)
    return ApiError(403, "move_refused", MOVE_NOT_ACCEPTED)


def _begin(db: OperatorDatabase, context: AppContext, now: datetime, email: str) -> OperatorAttempt | None:
    """Count one attempt, committed before any check. None: unknown or locked."""
    settings = context.settings
    lock_end = now + timedelta(minutes=settings.login_lock_minutes)
    with db.operator() as conn:
        return operators_sql.begin_login(conn, email, now, settings.login_max_failures, lock_end)


def _failed(db: OperatorDatabase, context: AppContext, attempt: OperatorAttempt) -> ApiError:
    """Audit a counted failure (``operator.locked`` when it began the lock); the one 401."""
    with db.operator() as conn:
        locked = attempt.failed_logins >= context.settings.login_max_failures
        operators_sql.login_failed(conn, attempt.operator_id, locked=locked)
    return refused(context)


def _start_session(
    context: AppContext, who: OperatorAttempt | CurrentOperator, version: int, now: datetime
) -> JSONResponse:
    expires = unix(now) + OPERATOR_SESSION_SECONDS
    claims = OperatorClaims(operator_id=who.operator_id, session_version=version, expires_at=expires)
    operator = {"id": who.operator_id, "email": who.email, "name": who.display_name}
    response = JSONResponse({"operator": operator, "session": {"expires_at": expires}})
    set_operator_cookie(response, claims, context.settings, now=unix(now))
    return response
