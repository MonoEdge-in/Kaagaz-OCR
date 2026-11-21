"""The platform operator's organisations (super admin PR 3) and their admins (PR 3b),
council 6 Oct 2026, PROVISIONAL. Every route needs the operator cookie
(``current_operator`` on the router); only ``OperatorDatabase`` here, and each call is
one ``kaagaz_op_*`` function, which writes its own ``platform_audit`` row.

* ``GET /operator/orgs``: every organisation with counts only (never a document's or a
  user's name). ``POST /operator/orgs`` {name, admin_email, admin_name} (#737): the
  organisation and its first admin, then the admin's company sign-in account (Keycloak) and the
  setup email. There is no temporary password. An email that already has a Keycloak account is
  refused (409) before anything is made. A name too short or an obvious test word ("asd") is a
  400 (``names.org_name_ok``).
* ``GET /operator/orgs/{id}/sign-in`` {to_move, connected} and ``POST .../move-everyone``
  (#737, named confirm "Move n people"): each active person not yet on company sign-in gets an
  account, a link and the setup email; an email already in Keycloak is NOT linked and is listed
  as needing attention. ``POST .../people/{uid}/resend-setup`` sends one person the setup email
  again. Disable, enable, unlock and reset-2fa below also act on the person's linked Keycloak
  account; so do disabling and enabling an organisation (its linked people).
* ``POST /operator/orgs/{id}/disable`` {code} (step-up) and ``/enable``. Enable refuses a
  pending organisation (409): only Approve makes it active.
* ``POST /operator/orgs/{id}/approve`` (#457 phase 0): a password sign-up waiting as
  ``pending`` becomes active, only once one of its admins confirmed their e-mail (409
  otherwise), audited ``org.approved`` in both logs. The list says ``admin_email_confirmed``.
  No step-up, as enable. Not behind EMAIL_ENABLED: an organisation left pending when email
  was switched off must still be approvable.
* ``PUT /operator/orgs/{id}/autofile`` {granted} (feature 28): allow or stop auto-filing;
  its admin then switches it on in Settings. Stopping it switches it off too. No step-up,
  as an organisation's models. The list says ``autofile_granted`` and ``autofile_on``.
* ``POST /operator/orgs/{id}/schedule-delete`` {name, code} (PR 4, step-up): a DISABLED
  organisation whose name is typed exactly is removed for good after a grace period
  (7 days, PROVISIONAL), by the worker; ``/cancel-delete`` {code} (step-up) during it
  makes it disabled again. There is no "delete now" here (the CLI's break-glass only).
* ``GET /operator/orgs/{id}/admins`` (admins only), ``POST`` one more {email, name,
  code} (a company sign-in account, as above; step-up), and ``POST .../admins/{uid}/disable``,
  ``/enable``, ``/unlock`` and ``/reset-2fa`` {code} (step-up; removes the authenticator in
  Keycloak: they set one up again at their next sign-in).

An unknown organisation, and an id that is not an admin of THIS organisation (another
organisation's admin, a member), get the same 404, and nothing changes.

Step-up: disabling an organisation, creating an admin and resetting an admin's two-step
code need a fresh code from the operator's own authenticator in the body. As at
sign-in, but on the step-up's OWN count and lock (``kaagaz_op_begin_step_up``, the
sign-in's thresholds; a sign-in lock never blocks a step-up, nor the reverse): the
attempt is counted first, the code is checked against the operator's sealed secret, and
the database function uses its step up in the same transaction as the change, so a
code works once. A refused code gets 403 ``step_up_failed`` and one ``platform_audit``
row. A taken e-mail gets 409 and one ``*.create_refused`` row (no e-mail stored), so
probing for e-mails leaves a trace.
"""

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import SQLAlchemyError

from kaagaz.api import emails, totp
from kaagaz.api import keycloak_accounts as accounts
from kaagaz.api import keycloak_admin as kc_admin
from kaagaz.api.auth import CurrentOperator, OperatorSession, current_operator, hash_password
from kaagaz.api.context import AppContext, get_context, get_operator_db
from kaagaz.api.errors import ApiError
from kaagaz.api.names import ORG_NAME_PROBLEM, org_name_ok
from kaagaz.api.routers.admin import TEMPORARY_PASSWORD_BYTES, valid_email
from kaagaz.api.routers.auth import MAX_CODE
from kaagaz.api.secret_box import SecretBoxError
from kaagaz.api.twostep import box, digits, unix
from kaagaz.api.views import iso
from kaagaz.db import keycloak as keycloak_sql
from kaagaz.db import operator_orgs as orgs_sql
from kaagaz.db import users as users_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.operator_orgs import Change, Outcome

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/operator/orgs", dependencies=[Depends(current_operator)])

Context = Annotated[AppContext, Depends(get_context)]
OperatorDb = Annotated[OperatorDatabase, Depends(get_operator_db)]

EXISTS_REASON = "This email already has a company sign-in account."
MOVE_AT_A_TIME = 50  # people moved per run: each takes a few calls to Keycloak
STEP_UP_MESSAGE = "That two-step code was not accepted. Wait for the next code and try again."
# A write's outcome that changed nothing, and its reply.
_REFUSALS = {
    "signed_out": (401, "not_signed_in", None),
    "missing": (404, "not_found", None),
    "deleting": (409, "conflict", "This organisation is being deleted."),
    "taken": (409, "conflict", "That e-mail is already in use."),  # audited in the function
    "not_disabled": (409, "conflict", "Disable the organisation before deleting it."),
    "name_mismatch": (409, "name_mismatch", "Type the organisation's name exactly."),  # audited
    "not_deleting": (409, "conflict", "This organisation is not being deleted."),
    "purging": (409, "conflict", "The grace period is over: the organisation is being removed."),
    "pending": (409, "conflict", "This organisation is waiting for approval. Use Approve."),
    "not_pending": (409, "conflict", "This organisation is not waiting for approval."),
    "unconfirmed": (409, "conflict", "The organisation's admin has not confirmed their email yet."),
}


@dataclass(frozen=True, slots=True)
class StepUp:
    step: int  # of the fresh code, for the database function to use up
    locks: bool  # this attempt reached the limit: a refusal is audited operator.step_up_locked


class NewOrgBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    admin_email: str = Field(min_length=3, max_length=254)
    admin_name: str = Field(min_length=1, max_length=100)


class ScheduleDeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)  # compared exactly, never trimmed
    code: str = Field(min_length=1, max_length=MAX_CODE)


class NewAdminBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=MAX_CODE)  # step-up: the password signs in as an admin


class AutofileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    granted: bool


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
    if not org_name_ok(name):
        raise ApiError(400, "bad_request", ORG_NAME_PROBLEM)
    _email_free(context, email)  # before anything is made
    with context.db.system() as conn:  # a stale, unconfirmed sign-up gives the e-mail back (0063)
        users_sql.release_stale_signup(conn, email)
    with db.operator() as conn:
        outcome = orgs_sql.create_customer(
            conn,
            operator.operator_id,
            name=name,
            admin_email=email,
            admin_name=admin_name,
            password_hash=_unusable_password(),
        )
    _check(db, operator, outcome)
    customer_id = str(outcome.result_id)
    with db.operator() as conn:
        admin_id = str(orgs_sql.list_admins(conn, operator.operator_id, customer_id)[0]["user_id"])
    sent, message = _first_account(context, customer_id, admin_id, email, admin_name, "Created")
    reply = {"id": customer_id, "name": name, "admin": {"email": email, "name": admin_name}}
    return JSONResponse(
        {"organisation": reply, "setup_email_sent": sent, "message": message}, status_code=201
    )


@router.post("/{customer_id}/disable")
def disable_org(
    customer_id: uuid.UUID, body: StepUpBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Its users can't sign in, their sessions end; the worker refuses its jobs (PR 1)."""
    step_up = _step_up(db, context, operator, body.code)
    return _set_status(db, context, operator, str(customer_id), "disabled", step_up)


@router.post("/{customer_id}/enable")
def enable_org(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    return _set_status(db, context, operator, str(customer_id), "active", None)


@router.post("/{customer_id}/approve")
def approve_org(customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb) -> dict[str, Any]:
    with db.operator() as conn:
        outcome = orgs_sql.approve_customer(conn, operator.operator_id, str(customer_id))
    _check(db, operator, outcome)
    return {"organisation": {"id": str(customer_id), "status": "active", "changed": True}}


@router.put("/{customer_id}/autofile")
def set_org_autofile(
    customer_id: uuid.UUID, body: AutofileBody, operator: OperatorSession, db: OperatorDb
) -> dict[str, Any]:
    with db.operator() as conn:
        outcome = orgs_sql.set_org_autofile(
            conn, operator.operator_id, str(customer_id), granted=body.granted
        )
    _check(db, operator, outcome)
    changed = outcome.code == "done"
    return {"organisation": {"id": str(customer_id), "autofile_granted": body.granted, "changed": changed}}


@router.post("/{customer_id}/schedule-delete")
def schedule_delete(
    customer_id: uuid.UUID,
    body: ScheduleDeleteBody,
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
) -> dict[str, Any]:
    """Removed for good, with every user, row and file, once the grace period ends."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = orgs_sql.schedule_delete(
            conn, operator.operator_id, str(customer_id), name=body.name, step=step_up.step
        )
    _check(db, operator, outcome, step_up)
    return {"organisation": {"id": str(customer_id), "status": "deleting", "changed": True}}


@router.post("/{customer_id}/cancel-delete")
def cancel_delete(
    customer_id: uuid.UUID, body: StepUpBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = orgs_sql.cancel_delete(conn, operator.operator_id, str(customer_id), step=step_up.step)
    _check(db, operator, outcome, step_up)
    return {"organisation": {"id": str(customer_id), "status": "disabled", "changed": True}}


@router.get("/{customer_id}/admins")
def list_admins(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    with db.operator() as conn:
        rows = orgs_sql.list_admins(conn, operator.operator_id, str(customer_id))
    if not rows:  # every organisation keeps an admin: none means no such organisation
        raise ApiError(404, "not_found")
    now = context.clock()
    return {"items": [_admin(row, now) for row in rows]}


@router.post("/{customer_id}/admins", status_code=201)
def create_admin(
    customer_id: uuid.UUID, body: NewAdminBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> JSONResponse:
    email, name = valid_email(body.email), _name(body.name)
    _email_free(context, email)  # before the code is used or anything is made
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = orgs_sql.create_admin(
            conn,
            operator.operator_id,
            str(customer_id),
            email=email,
            name=name,
            password_hash=_unusable_password(),
            step=step_up.step,
        )
    _check(db, operator, outcome, step_up)
    sent, message = _first_account(context, str(customer_id), str(outcome.result_id), email, name, "Added")
    admin = {"id": outcome.result_id, "email": email, "name": name}
    return JSONResponse({"admin": admin, "setup_email_sent": sent, "message": message}, status_code=201)


@router.get("/{customer_id}/sign-in")
def sign_in_summary(customer_id: uuid.UUID, operator: OperatorSession, context: Context) -> dict[str, Any]:
    """How many active people are on company sign-in and how many are still to move."""
    status, people = _people(context, str(customer_id))
    active = [p for p in people if not p.disabled]
    return {
        "status": status,
        "available": accounts.enabled(context),
        "connected": sum(p.linked for p in active),
        "to_move": sum(not p.linked for p in active),
    }


@router.post("/{customer_id}/move-everyone")
def move_everyone(
    customer_id: uuid.UUID,
    body: StepUpBody,
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
) -> dict[str, Any]:
    """Give each active person not yet on company sign-in an account, a link and the setup email,
    after a fresh code. Everyone moved is signed out (their link raises the session version) and
    can't use the old password. An email Keycloak already has is never linked: it is listed for
    attention. At most ``MOVE_AT_A_TIME`` people are moved per run (an email already taken costs
    nothing against it); press Move again for the rest. Safe to run again: people already linked
    are skipped, and a failure leaves no half-made person. One audit row, counts only."""
    accounts.require(context)
    step_up = _step_up(db, context, operator, body.code)
    status, people = _people(context, str(customer_id))
    if status != "active":
        raise ApiError(409, "conflict", "Enable the organisation first.")
    active = [p for p in people if not p.disabled]
    moved, connected = 0, sum(p.linked for p in active)
    attention: list[dict[str, str]] = []
    unsent: list[dict[str, str]] = []
    todo = [p for p in active if not p.linked]
    done = 0  # how many of ``todo`` were tried
    for person in todo:
        if moved >= MOVE_AT_A_TIME:
            break
        done += 1
        try:
            result = accounts.link_new(
                context, str(customer_id), person.user_id, email=person.email, name=person.name
            )
        except accounts.AccountExists:
            attention.append({"email": person.email, "reason": EXISTS_REASON, "kind": "exists"})
        except ApiError as error:
            attention.append({"email": person.email, "reason": error.message or "Could not be moved."})
            if error.status == 502:  # Keycloak is down: do not try everyone
                attention += [{"email": rest.email, "reason": "Not tried."} for rest in todo[done:]]
                done = len(todo)
                break
        except (keycloak_sql.IdentityTaken, SQLAlchemyError):
            attention.append({"email": person.email, "reason": "Could not be linked."})
        else:
            moved += 1
            if result == "email_failed":
                unsent.append({"user_id": person.user_id, "email": person.email})
    remaining = len(todo) - done
    with db.operator() as conn:
        orgs_sql.record_move_everyone(
            conn,
            operator.operator_id,
            str(customer_id),
            moved=moved,
            attention=len(attention),
            step=step_up.step,
        )
    message = f"{moved} moved, {connected} already connected, {len(attention)} need attention."
    if unsent:
        message += f" {len(unsent)} setup email(s) were not sent: use Resend setup email."
    if remaining:
        message += f" {remaining} still to move. Press Move again to continue."
    return {
        "moved": moved,
        "connected": connected,
        "attention": attention,
        "email_not_sent": unsent,
        "still_to_move": remaining,
        "message": message,
    }


@router.post("/{customer_id}/people/{user_id}/resend-setup")
def resend_setup(
    customer_id: uuid.UUID, user_id: uuid.UUID, operator: OperatorSession, context: Context
) -> dict[str, Any]:
    """Email one linked person a fresh link to set a password and an authenticator, while their
    setup is unfinished (a finished account is left alone)."""
    accounts.resend_setup(context, str(customer_id), str(user_id))
    return {"message": "Done. We emailed a new setup link. It works for 12 hours."}


@router.post("/{customer_id}/admins/{user_id}/disable")
def disable_admin(
    customer_id: uuid.UUID, user_id: uuid.UUID, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Every session of theirs ends."""
    return _change(db, context, operator, customer_id, user_id, "disable", None)


@router.post("/{customer_id}/admins/{user_id}/enable")
def enable_admin(
    customer_id: uuid.UUID, user_id: uuid.UUID, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    return _change(db, context, operator, customer_id, user_id, "enable", None)


@router.post("/{customer_id}/admins/{user_id}/unlock")
def unlock_admin(
    customer_id: uuid.UUID, user_id: uuid.UUID, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    return _change(db, context, operator, customer_id, user_id, "unlock", None)


@router.post("/{customer_id}/admins/{user_id}/reset-2fa")
def reset_admin_two_step(
    customer_id: uuid.UUID,
    user_id: uuid.UUID,
    body: StepUpBody,
    operator: OperatorSession,
    db: OperatorDb,
    background: BackgroundTasks,
    context: Context,
) -> dict[str, Any]:
    """Their authenticator is removed in company sign-in (they set one up again at the next
    sign-in), every session ends; a security notice is emailed to the admin while email is on (#457).
    """
    with db.operator() as conn:  # only an admin of THIS organisation: anyone else is the same 404
        admins = orgs_sql.list_admins(conn, operator.operator_id, str(customer_id))
    if not any(str(row["user_id"]) == str(user_id) for row in admins):
        raise ApiError(404, "not_found")
    accounts.linked_subject(context, str(customer_id), str(user_id))  # before anything is changed
    step_up = _step_up(db, context, operator, body.code)
    reply = _change(db, context, operator, customer_id, user_id, "reset_totp", step_up)
    admin = reply["admin"]
    notice = emails.two_step_reset(admin["name"])
    emails.after(background, "two_step_reset", emails.send, context, admin["email"], "two_step_reset", notice)
    reply["message"] = f"Done. {admin['name']} will set up their authenticator again at their next sign-in."
    return reply


def _set_status(
    db: OperatorDatabase,
    context: AppContext,
    operator: CurrentOperator,
    customer_id: str,
    status: str,
    step_up: StepUp | None,
) -> dict[str, Any]:
    step = None if step_up is None else step_up.step
    with db.operator() as conn:
        outcome = orgs_sql.set_customer_status(
            conn, operator.operator_id, customer_id, status, now=context.clock(), step=step
        )
    _check(db, operator, outcome, step_up)
    accounts.mirror_enabled(context, customer_id, enabled_now=status == "active")  # #737: linked people
    return {"organisation": {"id": customer_id, "status": status, "changed": outcome.code == "done"}}


def _change(
    db: OperatorDatabase,
    context: AppContext,
    operator: CurrentOperator,
    customer_id: uuid.UUID,
    user_id: uuid.UUID,
    change: Change,
    step_up: StepUp | None,
) -> dict[str, Any]:
    """One admin of this organisation, changed and audited; the admin as it is now."""
    with db.operator() as conn:
        outcome = orgs_sql.change_admin(
            conn,
            operator.operator_id,
            str(customer_id),
            str(user_id),
            change,
            step=None if step_up is None else step_up.step,
        )
        rows = orgs_sql.list_admins(conn, operator.operator_id, str(customer_id))
    _check(db, operator, outcome, step_up)  # before the admin is looked for: 404 when it is not one
    uid, cid = str(user_id), str(customer_id)
    if change in ("disable", "enable"):  # #737: the linked company sign-in account follows
        accounts.mirror_enabled(context, cid, enabled_now=change == "enable", only=[uid])
    elif change == "unlock":
        accounts.act(context, cid, uid, kc_admin.clear_lock, needs_link=False)
    elif change == "reset_totp":
        accounts.act(context, cid, uid, kc_admin.remove_otp)
    now = context.clock()
    return {"admin": next(_admin(row, now) for row in rows if str(row["user_id"]) == str(user_id))}


def _step_up(db: OperatorDatabase, context: AppContext, operator: CurrentOperator, code: str) -> StepUp:
    """The step of a fresh code from this operator's own secret, else 403. The attempt is
    counted first (committed) on the step-up's own count and lock, never the sign-in's;
    the change uses the step up and clears the count."""
    settings = context.settings
    now = context.clock()
    lock_end = now + timedelta(minutes=settings.login_lock_minutes)
    with db.operator() as conn:
        attempt = orgs_sql.begin_step_up(
            conn, operator.operator_id, now=now, max_failures=settings.login_max_failures, lock_end=lock_end
        )
    if attempt is None:  # locked: refused, and no row (the lock was audited when it began)
        raise ApiError(403, "step_up_failed", STEP_UP_MESSAGE)
    sealed, last_step, failures = attempt
    try:
        secret = box(settings.totp_encryption_key).open(sealed or "", aad=operator.operator_id)
    except SecretBoxError:
        secret = None
    step = None if secret is None else totp.matching_step(secret, digits(code), unix(now), last_step)
    locks = failures >= settings.login_max_failures  # this attempt began the lock
    if step is None:
        raise _step_up_failed(db, operator, locked=locks)
    return StepUp(step, locks)


def _step_up_failed(db: OperatorDatabase, operator: CurrentOperator, *, locked: bool) -> ApiError:
    with db.operator() as conn:
        orgs_sql.step_up_failed(conn, operator.operator_id, locked=locked)
    return ApiError(403, "step_up_failed", STEP_UP_MESSAGE)


def _check(
    db: OperatorDatabase, operator: CurrentOperator, outcome: Outcome, step_up: StepUp | None = None
) -> None:
    """Nothing to do for ``done`` and ``unchanged``; the reply for every other outcome."""
    if outcome.code == "step_up":  # the step was used meanwhile, by a request at the same time
        raise _step_up_failed(db, operator, locked=step_up is not None and step_up.locks)
    if outcome.code in _REFUSALS:
        status, code, message = _REFUSALS[outcome.code]
        raise ApiError(status, code, message)


def _unusable_password() -> str:
    """The Kaagaz user's password hash: a random password nobody sees. Sign-in is company sign-in."""
    return hash_password(secrets.token_urlsafe(TEMPORARY_PASSWORD_BYTES * 2))


def _email_free(context: AppContext, email: str) -> None:
    try:
        accounts.ensure_free(context, email)
    except accounts.AccountExists:
        raise ApiError(
            409, "conflict", "This email already has a company sign-in account. Use a different email."
        ) from None


def _first_account(
    context: AppContext, customer_id: str, user_id: str, email: str, name: str, verb: str
) -> tuple[bool, str]:
    """The new admin's company sign-in account, link and setup email: (email sent, result line).
    A failure leaves the admin made but not linked; Move everyone finishes it."""
    try:
        result = accounts.link_new(context, customer_id, user_id, email=email, name=name)
    except (accounts.AccountExists, ApiError, keycloak_sql.IdentityTaken, SQLAlchemyError):
        return False, f"{verb}, but the sign-in account could not be made. Use Move everyone to try again."
    if result == "email_failed":
        return False, f"{verb}, but the setup email could not be sent. Use Resend setup email."
    return True, accounts.sent_line(email, verb)


def _people(context: AppContext, customer_id: str) -> tuple[str, list[keycloak_sql.Person]]:
    """The organisation's status and each person with whether they are on company sign-in; 404 for
    an unknown organisation."""
    with context.db.customer(customer_id) as conn:
        status = conn.execute(text("SELECT status FROM customers WHERE id = :c"), {"c": customer_id}).scalar()
        people = keycloak_sql.people(conn, customer_id, context.settings.keycloak_issuer or "")
    if status is None:
        raise ApiError(404, "not_found")
    return str(status), people


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
        "autofile_granted": bool(row["autofile_granted"]),  # feature 28
        "autofile_on": bool(row["autofile_on"]),
        "admin_email_confirmed": bool(row["admin_email_confirmed"]),  # Approve needs it (#457)
    }


def _admin(row: RowMapping, now: datetime) -> dict[str, Any]:
    locked_until: datetime | None = row["locked_until"]
    return {
        "id": str(row["user_id"]),
        "name": row["display_name"],
        "email": row["email"],
        "two_step": bool(row["totp_enabled"]),
        "locked": locked_until is not None and locked_until > now,
        "disabled": bool(row["disabled"]),
        "must_change_password": bool(row["must_change_password"]),
        "last_sign_in_at": _when(row["last_login_at"]),
        "created_at": iso(row["created_at"]),
    }
