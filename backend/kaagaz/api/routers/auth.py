"""Signing in, out and changing the password (PRD 1 A1 to A7, section 6).

* ``POST /auth/login`` {email, password}: an app session (``scope: "app"``).
* ``POST /auth/admin/login`` {email, password, code?}: admins only, an admin
  session (ADMIN_SESSION_MINUTES). Each admin chooses two-step sign-in: while
  it is off the password alone is enough; once on, the right code too, asked for
  by 401 ``code_required`` (counted like a failure). Until the operator allows
  two-step sign-in for the organisation (0029), the password alone: any code is
  ignored, a stored one kept.
* ``POST /auth/admin/2fa/start`` {password} (admin session): with the current
  password, ``{enrol_token, secret, otpauth_uri}`` for turning it on. Asking
  for the password means a stolen admin cookie alone cannot put its own key
  on the account. Nothing is stored yet.
* ``POST /auth/admin/enrol`` {enrol_token, code}: turns the code on and starts
  an admin session. The session version stays, so other browsers stay in.
* ``POST /auth/admin/2fa/off`` {password} (admin session): turns it off. Every
  other browser is signed out, this one is re-signed.
* ``POST /auth/password`` {current_password, new_password}: a new password;
  every other browser is signed out, this one is re-signed.
* ``POST /auth/confirm-password`` {password}: only for a member or reviewer an
  admin allowed to delete forever, or an admin (admin session) without the two-step
  grant; the password, counted like a sign-in, then a
  5-minute confirm cookie (kaagaz/api/auth.py) for Delete forever. 204.
* ``/2fa/start`` and ``/enrol`` without the grant: 403 ``two_step_not_allowed``.
* ``POST /auth/logout``, ``POST /auth/logout-everywhere``, ``GET /auth/me``.
* ``POST /auth/signup`` {organisation_name, name, email, password} (16 Oct 2026): anyone
  makes a new organisation and its first admin, with their own password, and is signed
  in as that admin. ``kaagaz_create_customer`` (the CLI's function) makes both in one
  statement, with an operator-made organisation's defaults (models, permissions and
  features off, two-step off). A taken e-mail is the admin screens' 409. At most
  ``SIGNUPS_PER_HOUR`` a client address (429). Off (``SELF_SIGNUP_ENABLED=false``): 404;
  ``GET /auth/signup`` says ``{enabled}`` for the sign-in pages' link. Audited
  ``org.self_signup`` in the new organisation's log, the new admin as actor. No e-mail
  check: whoever types an address owns that account (PROVISIONAL).

Why the sign-in looks the way it does (council record 29 Sep, WP1.3):

* **One reply for every failure.** A wrong password, an unknown e-mail, a
  locked or disabled account, a non-admin on the admin screen and a wrong or
  reused code all get the same 401 ``bad_credentials`` text (``code_required``
  only ever follows a right password), and every path
  spends exactly one scrypt hash, so neither the reply nor its time says
  which accounts exist or which part was wrong.
* **The attempt is counted before the password is checked**
  (``users.begin_attempt``, one atomic UPDATE, committed on its own), so
  parallel guesses are all counted and a locked account never reaches the
  check. A right password (or password and code) clears the count
  (``users.attempt_succeeded``); anything else leaves it counted, so wrong
  codes count toward the lockout too.
* **The e-mail lookup** is the one SECURITY DEFINER function made for it,
  inside ``db.system()``; everything after it runs as the found customer.
* **A disabled organisation** (0014) is refused like a disabled user: the
  counted attempt says whether the customer is active, so even a right
  password gets the one 401, before the temporary-password 403 could tell it
  was right. ``load_user`` (``users.session_user``) checks it again when the
  session starts, and on every request after.
* **Nothing is stored before the first code is confirmed.** The pending secret
  travels sealed (``SecretBox``, bound to the user id) inside the signed enrol
  token, which also carries the session version: ``users.enrol_totp`` is
  conditional on it, so a token works once and only while nothing changed.
* **Audit rows** carry ids and short reason codes, never a password, a code or
  a secret. A failure has no actor (it may not be the account's owner).
"""

import dataclasses
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import Connection

from kaagaz.api import totp
from kaagaz.api.auth import (
    AdminSession,
    CurrentUser,
    User,
    UserAllowPasswordChange,
    clear_session_cookie,
    hash_password,
    load_user,
    new_enrol,
    new_session,
    read_enrol,
    session_claims,
    set_confirm_cookie,
    set_session_cookie,
    sign_enrol,
    spend_password_time,
    verify_password,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.routers.admin import MAX_PASSWORD, MIN_PASSWORD, valid_email
from kaagaz.api.secret_box import SecretBoxError
from kaagaz.api.twostep import box as _box
from kaagaz.api.twostep import digits as _digits
from kaagaz.api.twostep import refused as _refused
from kaagaz.api.twostep import unix as _unix
from kaagaz.api.views import me_json
from kaagaz.db import audit
from kaagaz.db import users as users_sql
from kaagaz.db.users import AttemptRow

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MAX_CODE = 16  # 6 digits, plus room for the spaces people type
NOT_ALLOWED = "Two-step sign-in is not turned on for your organisation."
# Sign-ups an hour (PROVISIONAL). Behind the Next.js proxy every visitor shares the proxy's
# address (kaagaz/api/middleware.py), so in production this is one cap for the whole site,
# sized for real sign-ups. ponytail: site-wide cap; per-visitor once X-Forwarded-For is trusted.
# No e-mail is proven at sign-up (there is no mail service yet): the super admin can disable
# an organisation, and SELF_SIGNUP_ENABLED=false turns sign-up off.
SIGNUPS_PER_HOUR = 20
signup_limits = RateLimiter(SIGNUPS_PER_HOUR, window_seconds=3600)
TOO_MANY_SIGNUPS = "Too many sign-ups from here. Try again in an hour."
# Trimmed, then the operator's limits (routers/operator_orgs.py NewOrgBody).
OrgName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


class AdminLoginBody(LoginBody):
    code: str | None = Field(default=None, max_length=MAX_CODE)


class EnrolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enrol_token: str = Field(min_length=1, max_length=4096)
    code: str = Field(min_length=1, max_length=MAX_CODE)


class CurrentPasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    new_password: str = Field(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)


class SignupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organisation_name: OrgName
    name: PersonName
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)


# --- the routes ----------------------------------------------------------------------


@router.post("/auth/login")
def login(body: LoginBody, context: Context) -> JSONResponse:
    attempt = _check_password(context, body.email, body.password)
    return _start_session(context, attempt.customer_id, attempt.user_id, scope="app", action="auth.login")


@router.post("/auth/admin/login")
def admin_login(body: AdminLoginBody, context: Context) -> JSONResponse:
    attempt = _check_password(context, body.email, body.password, admins_only=True)
    if attempt.must_change_password:
        # The password was right; the screen sends them to /login to change it first.
        _succeeded(context, attempt)
        raise ApiError(403, "password_change_required")
    if attempt.totp_enabled_at is None or not attempt.two_step_granted:
        # Two-step sign-in is off for this admin, or not allowed for the organisation
        # (any code sent is ignored): the password is enough.
        return _start_session(
            context,
            attempt.customer_id,
            attempt.user_id,
            scope="admin",
            action="auth.admin_login",
            details={"second_factor": False},
        )
    if not _digits(body.code):
        _failed(context, attempt, "code_required")  # counted: a wrong code next can't reset it
        raise ApiError(401, "code_required", "Enter the 6-digit code from your authenticator app.")

    try:  # a missing secret ("") does not open either
        secret = _box(context.settings.totp_encryption_key).open(
            attempt.totp_secret_enc or "", aad=attempt.user_id
        )
    except SecretBoxError:
        _failed(context, attempt, "totp_unreadable")  # another admin or the CLI resets it
        raise _refused(context) from None
    step = totp.matching_step(secret, _digits(body.code), _unix(context.clock()), attempt.totp_last_step)
    if step is None:
        _failed(context, attempt, "wrong_code")
        raise _refused(context)
    with context.db.customer(attempt.customer_id) as conn:
        accepted = users_sql.accept_totp_step(conn, attempt.customer_id, attempt.user_id, step)
    if not accepted:  # another request used this code a moment ago
        _failed(context, attempt, "code_reused")
        raise _refused(context)
    return _start_session(
        context, attempt.customer_id, attempt.user_id, scope="admin", action="auth.admin_login"
    )


@router.post("/auth/admin/enrol")
def admin_enrol(body: EnrolBody, context: Context) -> JSONResponse:
    now = context.clock()
    claims = read_enrol(body.enrol_token, context.settings.session_secret, _unix(now))
    if claims is None:
        raise ApiError(401, "not_signed_in")
    customer_id, user_id = claims.customer_id, claims.user_id
    with context.db.customer(customer_id) as conn:
        still_valid = users_sql.enrol_still_open(conn, customer_id, user_id, claims.session_version)
    if not still_valid:
        # A token that was already used, or whose admin changed since: it can't be
        # a guess at the code, so it is refused without counting a failure, and a
        # replayed token can't lock the admin out.
        raise ApiError(401, "not_signed_in")
    attempt = _begin(context, customer_id, user_id, now)
    if attempt is None:
        raise _refused(context)
    if (
        attempt.role != "admin"
        or attempt.disabled
        or not attempt.customer_active
        or attempt.must_change_password
        or attempt.totp_enabled_at is not None
        or attempt.session_version != claims.session_version
    ):
        _failed(context, attempt, "enrol_refused")
        raise ApiError(401, "not_signed_in")
    if not attempt.two_step_granted:  # stopped since the key was shown
        _failed(context, attempt, "enrol_refused")
        raise ApiError(403, "two_step_not_allowed", NOT_ALLOWED)
    try:
        secret = _box(context.settings.totp_encryption_key).open(claims.sealed_secret, aad=user_id)
    except SecretBoxError:
        _failed(context, attempt, "enrol_refused")
        raise ApiError(401, "not_signed_in") from None
    step = totp.matching_step(secret, _digits(body.code), _unix(now), None)
    if step is None:
        _failed(context, attempt, "wrong_code")
        raise _refused(context)

    with context.db.customer(customer_id) as conn:
        enrolled = users_sql.enrol_totp(
            conn,
            customer_id,
            user_id,
            secret_enc=claims.sealed_secret,
            step=step,
            now=now,
            ver=claims.session_version,
        )
        if enrolled:
            _record(conn, customer_id, "auth.totp_on", actor=user_id, target=user_id)
    if not enrolled:  # a second request with the same token, or something changed
        _failed(context, attempt, "enrol_refused")
        raise ApiError(401, "not_signed_in")
    return _start_session(
        context, attempt.customer_id, attempt.user_id, scope="admin", action="auth.admin_login"
    )


@router.get("/auth/signup")
def signup_open(context: Context) -> dict[str, bool]:
    return {"enabled": context.settings.self_signup_enabled}


@router.post("/auth/signup", status_code=201)
def signup(body: SignupBody, request: Request, context: Context) -> JSONResponse:
    if not context.settings.self_signup_enabled:
        raise ApiError(404, "not_found")
    if not signup_limits.allow(request.client.host if request.client else "-"):
        raise ApiError(429, "rate_limited", TOO_MANY_SIGNUPS, headers={"Retry-After": "3600"})
    email = valid_email(body.email)
    password_hash = hash_password(body.password)  # before the transaction, never during it
    try:
        with context.db.system() as conn:
            customer_id = users_sql.create_customer(
                conn,
                name=body.organisation_name,
                admin_email=email,
                admin_name=body.name,
                password_hash=password_hash,
            )
            found = users_sql.login_lookup(conn, email)
    except users_sql.EmailTaken:
        raise ApiError(409, "conflict", "That e-mail is already in use.") from None
    if found is None:  # never: made in this transaction
        raise ApiError(500, "internal_error")
    response = _start_session(context, customer_id, found.user_id, scope="admin", action="org.self_signup")
    response.status_code = 201
    return response


@router.post("/auth/admin/2fa/start")
def turn_on_two_step(body: CurrentPasswordBody, user: AdminSession, context: Context) -> dict[str, str]:
    if not user.two_step_allowed:
        raise ApiError(403, "two_step_not_allowed", NOT_ALLOWED)  # nothing is counted
    if user.totp_enabled:
        raise ApiError(409, "conflict", "Two-step sign-in is already on.")
    now = context.clock()
    _check_current_password(context, user, body.password, now)
    with context.db.customer(user.customer_id) as conn:
        users_sql.attempt_succeeded(conn, user.customer_id, user.user_id, now=now)
    return _enrol_offer(context, user)


@router.post("/auth/admin/2fa/off")
def turn_off_two_step(
    body: CurrentPasswordBody, user: AdminSession, request: Request, context: Context
) -> JSONResponse:
    if not user.totp_enabled:
        raise ApiError(409, "conflict", "Two-step sign-in is already off.")
    now = context.clock()
    _check_current_password(context, user, body.password, now)
    with context.db.customer(user.customer_id) as conn:
        row = users_sql.reset_totp(conn, user.customer_id, user.user_id)
        if row is None:
            raise ApiError(401, "not_signed_in")
        users_sql.attempt_succeeded(conn, user.customer_id, user.user_id, now=now)
        _record(conn, user.customer_id, "auth.totp_off", actor=user.user_id, target=user.user_id)
    changed = dataclasses.replace(user, session_version=int(row["session_version"]), totp_enabled=False)
    return _keep_this_browser(request, context, changed, now)


@router.post("/auth/password")
def change_password(
    body: PasswordBody, user: UserAllowPasswordChange, request: Request, context: Context
) -> JSONResponse:
    if body.new_password == body.current_password:
        raise ApiError(400, "bad_request", "Choose a new password that is different from the current one.")
    now = context.clock()
    _check_current_password(context, user, body.current_password, now)

    password_hash = hash_password(body.new_password)
    with context.db.customer(user.customer_id) as conn:
        row = users_sql.set_password(
            conn, user.customer_id, user.user_id, password_hash=password_hash, must_change=False
        )
        if row is None:
            raise ApiError(401, "not_signed_in")
        users_sql.attempt_succeeded(conn, user.customer_id, user.user_id, now=now)
        _record(conn, user.customer_id, "auth.password_change", actor=user.user_id, target=user.user_id)
    changed = dataclasses.replace(
        user, session_version=int(row["session_version"]), must_change_password=False
    )
    return _keep_this_browser(request, context, changed, now)


@router.post("/auth/confirm-password", status_code=204)
def confirm_password(body: CurrentPasswordBody, user: User, context: Context) -> Response:
    if user.role == "admin":
        # Only in place of two-step sign-in, which this admin's organisation does not have (0029).
        allowed = user.scope == "admin" and not user.two_step_allowed
    else:
        allowed = user.delete_forever_allowed
    if not allowed:
        raise ApiError(403, "forbidden")  # nothing is counted
    now = context.clock()
    _check_current_password(context, user, body.password, now)
    with context.db.customer(user.customer_id) as conn:
        users_sql.attempt_succeeded(conn, user.customer_id, user.user_id, now=now)
    response = Response(status_code=204)
    set_confirm_cookie(response, user, context.settings, now=_unix(now))
    return response


@router.post("/auth/logout", status_code=204)
def logout(user: UserAllowPasswordChange, context: Context) -> Response:
    with context.db.customer(user.customer_id) as conn:
        _record(conn, user.customer_id, "auth.logout", actor=user.user_id, target=user.user_id)
    response = Response(status_code=204)
    clear_session_cookie(response, context.settings)
    return response


@router.post("/auth/logout-everywhere", status_code=204)
def logout_everywhere(user: User, context: Context) -> Response:
    with context.db.customer(user.customer_id) as conn:
        users_sql.bump_session_version(conn, user.customer_id, user.user_id)
        _record(conn, user.customer_id, "auth.logout_everywhere", actor=user.user_id, target=user.user_id)
    response = Response(status_code=204)
    clear_session_cookie(response, context.settings)
    return response


@router.get("/auth/me")
def me(user: UserAllowPasswordChange) -> dict[str, Any]:
    return me_json(user)


# --- the steps they share ----------------------------------------------------------------


def _check_password(
    context: AppContext, email: str, password: str, *, admins_only: bool = False
) -> AttemptRow:
    """The counted attempt of an enabled user whose password is right, else the one 401.

    Exactly one scrypt hash on every path: the dummy one for an unknown e-mail,
    a locked account or (``admins_only``) a user who isn't an admin, the real
    one otherwise. A non-admin on the admin screen is refused before anything
    is counted: the screen never checks their password, so it tells nothing
    and can't lock them out. An attempt on a locked account writes no audit row
    (``auth.locked`` was written when the lock started), so guessing at a
    locked account can't grow the audit log. At most three short transactions
    (the lookup, the counted attempt, the audit row), and none is held open
    during the hash. O(1).
    """
    with context.db.system() as conn:
        found = users_sql.login_lookup(conn, email)
    if found is None or (admins_only and found.role != "admin"):
        spend_password_time(password)
        raise _refused(context)
    attempt = _begin(context, found.customer_id, found.user_id, context.clock())
    if attempt is None:
        spend_password_time(password)
        raise _refused(context)
    if not verify_password(password, attempt.password_hash):
        _failed(context, attempt, "wrong_password")
        raise _refused(context)
    if attempt.disabled:
        _failed(context, attempt, "disabled")
        raise _refused(context)
    if not attempt.customer_active:  # its organisation is disabled or being deleted (0014)
        _failed(context, attempt, "customer_disabled")
        raise _refused(context)
    return attempt


def _begin(context: AppContext, customer_id: str, user_id: str, now: datetime) -> AttemptRow | None:
    """Count one attempt, committed before any check. None: locked."""
    settings = context.settings
    with context.db.customer(customer_id) as conn:
        return users_sql.begin_attempt(
            conn,
            customer_id,
            user_id,
            now=now,
            max_failures=settings.login_max_failures,
            lock_minutes=settings.login_lock_minutes,
        )


def _check_current_password(context: AppContext, user: CurrentUser, password: str, now: datetime) -> None:
    """The signed-in user's own password, counted like a sign-in (so wrong ones
    lead to the lockout), else a 400 that says what is wrong."""
    attempt = _begin(context, user.customer_id, user.user_id, now)
    if attempt is None:
        # The lock stays on for a signed-in session too: letting a session check
        # passwords during a lock would give a stolen session unlimited guesses.
        # No audit row: the lock itself was audited when it started.
        minutes = context.settings.login_lock_minutes
        raise ApiError(
            400,
            "bad_request",
            f"Too many wrong passwords were tried on this account. Try again in {minutes} minutes,"
            " or ask an admin to unlock it.",
        )
    if not verify_password(password, attempt.password_hash):
        _failed(context, attempt, "wrong_password")
        raise ApiError(400, "bad_request", "Your current password is not right.")


def _keep_this_browser(
    request: Request, context: AppContext, user: CurrentUser, now: datetime
) -> JSONResponse:
    """``Me`` after a change that raised the session version: every other browser
    is now signed out, and this one keeps its scope and end time, re-signed with
    ``user.session_version``."""
    claims = session_claims(request, context.settings, _unix(now))
    if claims is None:
        raise ApiError(401, "not_signed_in")
    renewed = dataclasses.replace(claims, session_version=user.session_version)
    response = JSONResponse(me_json(user))
    set_session_cookie(response, renewed, context.settings, now=_unix(now))
    return response


def _start_session(
    context: AppContext,
    customer_id: str,
    user_id: str,
    *,
    scope: str,
    action: str,
    details: dict[str, audit.DetailValue] | None = None,
) -> JSONResponse:
    """The ``Me`` reply and a new session cookie of this scope; clears the count."""
    now = context.clock()
    user = load_user(context.db, customer_id, user_id, scope=scope)
    if user is None:  # disabled in the meantime
        raise _refused(context)
    with context.db.customer(user.customer_id) as conn:
        users_sql.attempt_succeeded(conn, user.customer_id, user.user_id, now=now)
        _record(conn, user.customer_id, action, actor=user.user_id, target=user.user_id, details=details)
    claims = new_session(
        user_id=user.user_id,
        customer_id=user.customer_id,
        role=user.role,
        scope=scope,
        session_version=user.session_version,
        settings=context.settings,
        now=_unix(now),
    )
    response = JSONResponse(me_json(user))
    set_session_cookie(response, claims, context.settings, now=_unix(now))
    return response


def _enrol_offer(context: AppContext, user: CurrentUser) -> dict[str, str]:
    """A new secret for the admin to put into an authenticator app (typed in, or
    as the otpauth link in a QR code), and the signed enrol token that carries it
    (sealed) for 10 minutes. Nothing is stored. The token, the key and the link
    are all made from this one signed-in user."""
    secret = totp.new_secret()
    sealed = _box(context.settings.totp_encryption_key).seal(secret, aad=user.user_id)
    claims = new_enrol(
        user_id=user.user_id,
        customer_id=user.customer_id,
        session_version=user.session_version,
        sealed_secret=sealed,
        now=_unix(context.clock()),
    )
    return {
        "enrol_token": sign_enrol(claims, context.settings.session_secret),
        "secret": totp.grouped(totp.b32(secret)),
        "otpauth_uri": totp.otpauth_uri(secret, user.email),
    }


def _succeeded(context: AppContext, attempt: AttemptRow) -> None:
    """The password was right but no session starts yet (a password change
    first): the count starts again all the same."""
    with context.db.customer(attempt.customer_id) as conn:
        users_sql.attempt_succeeded(conn, attempt.customer_id, attempt.user_id, now=context.clock())


def _failed(context: AppContext, attempt: AttemptRow, reason: str) -> None:
    """Audit a counted failure, and the lock when this attempt reached the limit."""
    locked = attempt.failed_logins >= context.settings.login_max_failures
    _audit_failure(context, attempt.customer_id, attempt.user_id, reason, locked=locked)


def _audit_failure(
    context: AppContext, customer_id: str, user_id: str, reason: str, *, locked: bool = False
) -> None:
    with context.db.customer(customer_id) as conn:
        _record(
            conn, customer_id, "auth.login_failed", actor=None, target=user_id, details={"reason": reason}
        )
        if locked:
            _record(conn, customer_id, "auth.locked", actor=None, target=user_id)


def _record(
    conn: Connection,
    customer_id: str,
    action: str,
    *,
    actor: str | None,
    target: str,
    details: dict[str, audit.DetailValue] | None = None,
) -> None:
    audit.record(
        conn,
        customer_id,
        actor_user_id=actor,
        action=action,
        target_type="user",
        target_id=target,
        details=details,
    )
