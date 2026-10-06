"""The platform operator's organisations (super admin PR 3, council 6 Oct 2026,
PROVISIONAL). Every route needs the operator cookie (``current_operator`` on the
router); only ``OperatorDatabase`` here, and each call is one ``kaagaz_op_*`` function,
which writes its own ``platform_audit`` row. Their admins: PR 3b.

* ``GET /operator/orgs``: every organisation with counts only (never a document's or a
  user's name). ``POST /operator/orgs`` {name, admin_email, admin_name}: the
  organisation and its first admin; the temporary password is in this reply ONCE
  (stored only as its hash, never logged) and must be changed at the first sign-in.
* ``POST /operator/orgs/{id}/disable`` {code} (step-up) and ``/enable``. An unknown
  organisation gets 404 and nothing changes.

Step-up: disabling an organisation needs a fresh code from the operator's own
authenticator in the body. As at sign-in: the attempt is counted first
(``kaagaz_op_begin_login``, the same lockout), the code is checked against the
operator's sealed secret, and the database function uses its step up in the same
transaction as the change, so a code works once. A refused code gets 403
``step_up_failed`` and one ``platform_audit`` row.
"""

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import RowMapping

from kaagaz.api import totp
from kaagaz.api.auth import CurrentOperator, OperatorSession, current_operator, hash_password
from kaagaz.api.context import AppContext, get_context, get_operator_db
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.admin import TEMPORARY_PASSWORD_BYTES, valid_email
from kaagaz.api.routers.auth import MAX_CODE
from kaagaz.api.secret_box import SecretBoxError
from kaagaz.api.twostep import box, digits, unix
from kaagaz.api.views import iso
from kaagaz.db import operator_orgs as orgs_sql
from kaagaz.db import operators as operators_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.operator_orgs import Outcome
from kaagaz.db.users import EmailTaken

router = APIRouter(prefix="/operator/orgs", dependencies=[Depends(current_operator)])

Context = Annotated[AppContext, Depends(get_context)]
OperatorDb = Annotated[OperatorDatabase, Depends(get_operator_db)]

STEP_UP_MESSAGE = "That two-step code was not accepted. Wait for the next code and try again."
# A write's outcome that changed nothing, and its reply.
_REFUSALS = {
    "signed_out": (401, "not_signed_in", None),
    "missing": (404, "not_found", None),
    "deleting": (409, "conflict", "This organisation is being deleted."),
}


class NewOrgBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    admin_email: str = Field(min_length=3, max_length=254)
    admin_name: str = Field(min_length=1, max_length=100)


class StepUpBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=MAX_CODE)


@router.get("")
def list_orgs(operator: OperatorSession, db: OperatorDb) -> dict[str, Any]:
    with db.operator() as conn:
        rows = orgs_sql.list_customers(conn, operator.operator_id)
    return {"items": [_org(row) for row in rows]}


@router.post("", status_code=201)
def create_org(body: NewOrgBody, operator: OperatorSession, db: OperatorDb, context: Context) -> JSONResponse:
    name, email, admin_name = _name(body.name), valid_email(body.admin_email), _name(body.admin_name)
    temporary = secrets.token_urlsafe(TEMPORARY_PASSWORD_BYTES)
    try:
        with db.operator() as conn:
            outcome = orgs_sql.create_customer(
                conn,
                operator.operator_id,
                name=name,
                admin_email=email,
                admin_name=admin_name,
                password_hash=hash_password(temporary),
            )
    except EmailTaken:
        raise ApiError(409, "conflict", "That e-mail is already in use.") from None
    _check(db, operator, outcome)
    reply = {"id": outcome.result_id, "name": name, "admin": {"email": email, "name": admin_name}}
    return JSONResponse({"organisation": reply, "temporary_password": temporary}, status_code=201)


@router.post("/{customer_id}/disable")
def disable_org(
    customer_id: uuid.UUID, body: StepUpBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Its users can't sign in, their sessions end; the worker refuses its jobs (PR 1)."""
    step = _step_up(db, context, operator, body.code)
    return _set_status(db, context, operator, str(customer_id), "disabled", step)


@router.post("/{customer_id}/enable")
def enable_org(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    return _set_status(db, context, operator, str(customer_id), "active", None)


def _set_status(
    db: OperatorDatabase,
    context: AppContext,
    operator: CurrentOperator,
    customer_id: str,
    status: str,
    step: int | None,
) -> dict[str, Any]:
    with db.operator() as conn:
        outcome = orgs_sql.set_customer_status(
            conn, operator.operator_id, customer_id, status, now=context.clock(), step=step
        )
    _check(db, operator, outcome)
    return {"organisation": {"id": customer_id, "status": status, "changed": outcome.code == "done"}}


def _step_up(db: OperatorDatabase, context: AppContext, operator: CurrentOperator, code: str) -> int:
    """The step of a fresh code from this operator's own secret, else 403. The attempt
    is counted first (committed), as a sign-in is; the change uses the step up."""
    settings = context.settings
    now = context.clock()
    lock_end = now + timedelta(minutes=settings.login_lock_minutes)
    with db.operator() as conn:
        attempt = operators_sql.begin_login(conn, operator.email, now, settings.login_max_failures, lock_end)
    if attempt is None:  # locked: refused, and no row (the lock was audited when it began)
        raise ApiError(403, "step_up_failed", STEP_UP_MESSAGE)
    try:
        secret = box(settings.totp_encryption_key).open(
            attempt.totp_secret_enc or "", aad=attempt.operator_id
        )
    except SecretBoxError:
        secret = None
    step = (
        None
        if secret is None
        else totp.matching_step(secret, digits(code), unix(now), attempt.totp_last_step)
    )
    if step is None:
        locked = attempt.failed_logins >= settings.login_max_failures
        raise _step_up_failed(db, operator, locked=locked)
    return step


def _step_up_failed(db: OperatorDatabase, operator: CurrentOperator, *, locked: bool) -> ApiError:
    with db.operator() as conn:
        orgs_sql.step_up_failed(conn, operator.operator_id, locked=locked)
    return ApiError(403, "step_up_failed", STEP_UP_MESSAGE)


def _check(db: OperatorDatabase, operator: CurrentOperator, outcome: Outcome) -> None:
    """Nothing to do for ``done`` and ``unchanged``; the reply for every other outcome."""
    if outcome.code == "step_up":  # the step was used meanwhile, by a request at the same time
        raise _step_up_failed(db, operator, locked=False)
    if outcome.code in _REFUSALS:
        status, code, message = _REFUSALS[outcome.code]
        raise ApiError(status, code, message)


def _name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ApiError(400, "bad_request", "Enter a name.")
    return name


def _when(value: datetime | None) -> str | None:
    return None if value is None else iso(value)


def _org(row: RowMapping) -> dict[str, Any]:
    """Counts only: no document or user is named."""
    return {
        "id": str(row["customer_id"]),
        "name": row["name"],
        "status": row["status"],
        "disabled_at": _when(row["disabled_at"]),
        "delete_after": _when(row["delete_after"]),
        "created_at": iso(row["created_at"]),
        "users": {"admins": row["admins"], "reviewers": row["reviewers"], "members": row["members"]},
        "documents": row["documents"],
        "pages": row["pages"],
        "storage_bytes": row["size_bytes"],
        "last_activity_at": _when(row["last_activity_at"]),
    }
