"""The platform operator's sign-in (super admin PR 2, council 6 Oct 2026, PROVISIONAL).

* ``POST /operator/login`` {email, password, code}: two-step is MANDATORY and its
  secret is issued ONLY on the server (``cli create-operator``, ``reset-operator-2fa``).
  The password and a fresh code from that secret get the cookie; the first code ever
  also confirms the secret. Anything else, the password alone included, gets the one 401.
* ``POST /operator/logout`` (ends every session of this operator), ``GET /operator/me``.

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
    OPERATOR_SESSION_SECONDS,
    OperatorClaims,
    OperatorSession,
    clear_operator_cookie,
    current_operator,
    set_operator_cookie,
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


class OperatorLoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    code: str | None = Field(default=None, max_length=MAX_CODE)  # none: the one 401, counted


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
    context: AppContext, attempt: OperatorAttempt, version: int, now: datetime
) -> JSONResponse:
    expires = unix(now) + OPERATOR_SESSION_SECONDS
    claims = OperatorClaims(operator_id=attempt.operator_id, session_version=version, expires_at=expires)
    operator = {"id": attempt.operator_id, "email": attempt.email, "name": attempt.display_name}
    response = JSONResponse({"operator": operator, "session": {"expires_at": expires}})
    set_operator_cookie(response, claims, context.settings, now=unix(now))
    return response
