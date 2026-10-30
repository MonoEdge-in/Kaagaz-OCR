"""Signing in, out and changing the password (PRD 1 A1 to A7, section 6).

* ``POST /auth/login`` {email, password, code?}: the one sign-in for everyone (Tushar,
  16 Oct 2026). A member or reviewer gets an app session (``scope: "app"``). An admin must
  have two-step sign-in (MFA required for admins) and gets an admin session
  (``scope: "admin"``, the shorter ADMIN_SESSION_MINUTES): after a right password, an admin
  without a code set up gets 403 ``mfa_enrol_required`` with ``details`` {enrol_token,
  secret, otpauth_uri} and no session. Anyone with a code set up (always an admin, and a
  member or reviewer who turned it on, #457) gets 401 ``code_required`` (counted like a
  failure) until it is sent: ``_finish_second_step``, for every role. An admin with a
  temporary password and no code yet gets a session that can only set the new password
  (``current_user``); the next sign-in then sets up the code.
* ``POST /auth/admin/login``: 410 ``gone``, whatever is sent. Not kept as an alias: one
  route to sign in is one set of rules to get right, and an old page gets a clear answer.
* ``POST /auth/2fa/start`` {password} (any session; #457 opened it to every role, it was
  ``/auth/admin/2fa/start``): with the current password, ``{enrol_token, secret,
  otpauth_uri}`` for turning it on. Asking for the password means a stolen cookie alone
  cannot put its own key on the account. Nothing is stored yet. 409 when it is on.
* ``POST /auth/admin/enrol`` {enrol_token, code}: turns the code on (audited
  ``auth.totp_on``, a security notice) and starts a session of the user's scope (an admin's
  at sign-in or sign-up; a member's or reviewer's from Account, Two-step sign-in). The
  session version stays, so other browsers stay in.
* ``POST /auth/2fa/off`` {password, code}: a member or reviewer turns it off, with the
  password and a current code (both counted like a sign-in). Every other browser is signed
  out, this one is re-signed; audited ``auth.totp_off``, a security notice. 409 when it is
  off. An admin: 403 ``two_step_required``, nothing read or counted. Admins keep two-step
  sign-in on; another admin or the operator resets a lost one, and the admin sets it up
  again at sign-in. A member's or reviewer's lost code: an admin resets it
  (``routers/admin.py``).
* ``POST /auth/password`` {current_password, new_password, code?}: a new password;
  every other browser is signed out, this one is re-signed. Anyone with two-step sign-in on
  also sends the code (#457; the sign-in's own check, ``_code_problem``), and a security
  notice is emailed while email is on.
* ``POST /auth/confirm-password`` {password}: only for a member or reviewer an
  admin allowed to delete forever; the password, counted like a sign-in, then a
  5-minute confirm cookie (kaagaz/api/auth.py) for Delete forever. 204.
* ``POST /auth/logout``, ``POST /auth/logout-everywhere``, ``GET /auth/me``.
* ``GET /auth/session`` (issue 378): the app's first look, 200 either way: ``{signed_in:
  false, providers}`` with no session (one ``/auth/me`` would refuse with 401; ``providers``:
  those switched on, #457), else ``{signed_in: true, me}``, the same checks and reply as
  ``/auth/me``. A signed-out visit then logs no expected 401 in the browser before it goes to
  sign in.
* ``POST /auth/signup`` {organisation_name, name, email, password} (16 Oct 2026): anyone
  makes a new organisation and its first admin, with their own password. The reply (201) is
  the two-step set-up offer {enrol_token, secret, otpauth_uri}, no session: the first code
  (``/auth/admin/enrol``) signs them in as that admin (MFA required, 0035).
  ``kaagaz_create_customer`` (the CLI's function) makes both in one statement, with an
  operator-made organisation's defaults (models, permissions and features off).
  With email on (#457 phase 0) the organisation is made ``pending`` instead, a "Confirm
  your email" link is sent, and the reply is 201 ``{status: "confirm_email"}`` with no
  set-up offer and no session; sign-in then says 403 ``org_pending`` (after a right
  password only) until an operator approves it (``routers/operator_orgs.py``).
  A taken e-mail is the admin screens' 409. At most ``SIGNUPS_PER_HOUR`` a client
  address (429). Off (``SELF_SIGNUP_ENABLED=false``, or the super admin's Self sign-up
  switch, 0038): 404; ``GET /auth/signup`` says
  ``{enabled}`` for the sign-in pages' link. Audited ``org.self_signup`` in the new
  organisation's log, the new admin as actor. No e-mail check: whoever types an address
  owns that account (PROVISIONAL). A name too short or
  an obvious test word ("asd") is a 400 (``names.org_name_ok``, UI audit G12).

Why the sign-in looks the way it does (council record 29 Sep, WP1.3):

* **One reply for every failure.** A wrong password, an unknown e-mail, a
  locked or disabled account and a wrong or reused code all get the same 401
  ``bad_credentials`` text (``code_required`` and ``mfa_enrol_required`` only
  ever follow a right password), and every path
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

from kaagaz.api import emails, oidc, totp
from kaagaz.api.auth import (
    CurrentUser,
    SessionClaims,
    User,
    UserAllowPasswordChange,
    clear_session_cookie,
    current_user_allow_password_change,
    feature_on,
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
from kaagaz.api.emails import email_on, new_link, send
from kaagaz.api.errors import ApiError
from kaagaz.api.middleware import RateLimiter
from kaagaz.api.names import ORG_NAME_PROBLEM, org_name_ok
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
ENROL_FIRST = "Set up two-step sign-in to continue. Admins need it to keep your organisation safe."
KEEP_TWO_STEP = "Admins keep two-step sign-in on to keep your organisation safe."
ADMIN_LOGIN_GONE = "Sign in on the sign-in page. Admins and members use the same one now."
CODE_REQUIRED = "Enter the 6-digit code from your authenticator app."
WRONG_CODE = "That code is not right. Wait for the next code and try again."
ORG_PENDING = "Your organisation is waiting for approval."
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
    code: str | None = Field(default=None, max_length=MAX_CODE)  # with two-step sign-in on


class EnrolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enrol_token: str = Field(min_length=1, max_length=4096)
    code: str = Field(min_length=1, max_length=MAX_CODE)


class CurrentPasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


class TurnOffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    code: str | None = Field(default=None, max_length=MAX_CODE)


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    new_password: str = Field(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)
    code: str | None = Field(default=None, max_length=MAX_CODE)  # with two-step sign-in on


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
    return _finish_second_step(context, attempt, body.code)


@router.post("/auth/admin/login")
def admin_login_gone() -> None:
    """Gone (one sign-in page, 16 Oct 2026): nothing sent is read or checked."""
    raise ApiError(410, "gone", ADMIN_LOGIN_GONE)


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
        attempt.disabled
        or not attempt.customer_active
        or attempt.must_change_password
        or attempt.totp_enabled_at is not None
        or attempt.session_version != claims.session_version
    ):
        _failed(context, attempt, "enrol_refused")
        raise ApiError(401, "not_signed_in")
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
    emails.notify(context, customer_id, user_id, "two_step_on", emails.two_step_on)
    return _start_session(
        context, attempt.customer_id, attempt.user_id, scope=_scope(attempt), action="auth.login"
    )


@router.get("/auth/signup")
def signup_open(context: Context) -> dict[str, bool]:
    return {"enabled": _signup_on(context)}


def _signup_on(context: AppContext) -> bool:
    """SELF_SIGNUP_ENABLED and the super admin's Self sign-up switch (0038)."""
    return context.settings.self_signup_enabled and feature_on(context, "self_signup")


@router.post("/auth/signup", status_code=201)
def signup(body: SignupBody, request: Request, context: Context) -> JSONResponse:
    if not context.settings.self_signup_enabled:
        raise ApiError(404, "not_found")
    if not signup_limits.allow(request.client.host if request.client else "-"):
        raise ApiError(429, "rate_limited", TOO_MANY_SIGNUPS, headers={"Retry-After": "3600"})
    email = valid_email(body.email)
    if not org_name_ok(body.organisation_name):
        raise ApiError(400, "bad_request", ORG_NAME_PROBLEM)
    if not _signup_on(context):  # the super admin's switch (0038), the first database read
        raise ApiError(404, "not_found")
    password_hash = hash_password(body.password)  # before the transaction, never during it
    pending = email_on(context)  # #457: the e-mail is proven, then an operator approves
    try:
        with context.db.system() as conn:
            customer_id = users_sql.create_customer(
                conn,
                name=body.organisation_name,
                admin_email=email,
                admin_name=body.name,
                password_hash=password_hash,
                pending=pending,
            )
            found = users_sql.login_lookup(conn, email)
    except users_sql.EmailTaken:
        raise ApiError(409, "conflict", "That e-mail is already in use.") from None
    if found is None:  # never: made in this transaction
        raise ApiError(500, "internal_error")
    if pending:
        with context.db.customer(customer_id) as conn:
            _record(conn, customer_id, "org.self_signup", actor=found.user_id, target=found.user_id)
        link = new_link(context, customer_id, found.user_id, "confirm")
        send(context, email, "confirm", emails.confirm_email(body.name, link))
        return JSONResponse({"status": "confirm_email"}, status_code=201)
    user = load_user(context.db, customer_id, found.user_id, scope="admin")
    if user is None:  # never: made in this transaction
        raise ApiError(500, "internal_error")
    with context.db.customer(customer_id) as conn:
        _record(conn, customer_id, "org.self_signup", actor=user.user_id, target=user.user_id)
    # Every admin must have two-step sign-in (0035): the set-up offer, and no session until
    # the first code (POST /auth/admin/enrol), so the new admin never types the password twice.
    return JSONResponse(_enrol_offer(context, user), status_code=201)


@router.post("/auth/2fa/start")
def turn_on_two_step(body: CurrentPasswordBody, user: User, context: Context) -> dict[str, str]:
    if user.totp_enabled:
        raise ApiError(409, "conflict", "Two-step sign-in is already on.")
    now = context.clock()
    _check_current_password(context, user, body.password, now)
    with context.db.customer(user.customer_id) as conn:
        users_sql.attempt_succeeded(conn, user.customer_id, user.user_id, now=now)
    return _enrol_offer(context, user)


@router.post("/auth/2fa/off")
def turn_off_two_step(body: TurnOffBody, user: User, request: Request, context: Context) -> JSONResponse:
    """A member or reviewer, with the password and a code. Refused for an admin: every admin
    keeps two-step sign-in on (MFA required, 16 Oct 2026)."""
    if user.role == "admin":
        raise ApiError(403, "two_step_required", KEEP_TWO_STEP)
    if not user.totp_enabled:
        raise ApiError(409, "conflict", "Two-step sign-in is already off.")
    now = context.clock()
    attempt = _check_current_password(context, user, body.password, now)
    problem = _code_problem(context, attempt, body.code)
    if problem is not None:
        _failed(context, attempt, problem)
        raise ApiError(400, "bad_request", CODE_REQUIRED if problem == "code_required" else WRONG_CODE)

    with context.db.customer(user.customer_id) as conn:
        row = users_sql.reset_totp(conn, user.customer_id, user.user_id)
        if row is None:
            raise ApiError(401, "not_signed_in")
        users_sql.attempt_succeeded(conn, user.customer_id, user.user_id, now=now)
        _record(conn, user.customer_id, "auth.totp_off", actor=user.user_id, target=user.user_id)
    changed = dataclasses.replace(user, session_version=int(row["session_version"]), totp_enabled=False)
    response = _keep_this_browser(request, context, changed, now)
    send(context, user.email, "two_step_off", emails.two_step_off(user.display_name))
    return response


@router.post("/auth/password")
def change_password(
    body: PasswordBody, user: UserAllowPasswordChange, request: Request, context: Context
) -> JSONResponse:
    if body.new_password == body.current_password:
        raise ApiError(400, "bad_request", "Choose a new password that is different from the current one.")
    now = context.clock()
    attempt = _check_current_password(context, user, body.current_password, now)
    if attempt.totp_enabled_at is not None:
        problem = _code_problem(context, attempt, body.code)
        if problem is not None:
            _failed(context, attempt, problem)
            raise ApiError(400, "bad_request", CODE_REQUIRED if problem == "code_required" else WRONG_CODE)

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
    response = _keep_this_browser(request, context, changed, now)
    send(context, user.email, "password_changed", emails.password_changed(user.display_name))
    return response


@router.post("/auth/confirm-password", status_code=204)
def confirm_password(body: CurrentPasswordBody, user: User, context: Context) -> Response:
    if user.role == "admin" or not user.delete_forever_allowed:
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
def me(user: UserAllowPasswordChange, context: Context) -> dict[str, Any]:
    return me_json(user, email_on=email_on(context))


@router.get("/auth/session")
def session(request: Request) -> dict[str, Any]:
    """``/auth/me``'s checks, with no session a 200 ``{signed_in: false, providers}``, not a
    401. ``providers``: the sign-in page's "Continue with" buttons, those switched on (#457)."""
    try:
        user = current_user_allow_password_change(request)
    except ApiError as error:
        if error.status == 401:
            settings = get_context(request).settings
            return {
                "signed_in": False,
                "providers": [name for name in oidc.PROVIDERS if oidc.enabled(settings, name)],
            }
        raise
    return {"signed_in": True, "me": me_json(user, email_on=email_on(get_context(request)))}


# --- the steps they share ----------------------------------------------------------------


def _check_password(context: AppContext, email: str, password: str) -> AttemptRow:
    """The counted attempt of an enabled user whose password is right, else the one 401.

    Exactly one scrypt hash on every path: the dummy one for an unknown e-mail,
    a locked account, the real one otherwise. An attempt on a locked account writes no audit row
    (``auth.locked`` was written when the lock started), so guessing at a
    locked account can't grow the audit log. At most three short transactions
    (the lookup, the counted attempt, the audit row), and none is held open
    during the hash. O(1).
    """
    with context.db.system() as conn:
        found = users_sql.login_lookup(conn, email)
    if found is None:
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
    if attempt.customer_pending:  # a sign-up waiting for an operator (0061), told after a right password
        _failed(context, attempt, "customer_pending")
        raise ApiError(403, "org_pending", ORG_PENDING)
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


def _check_current_password(
    context: AppContext, user: CurrentUser, password: str, now: datetime
) -> AttemptRow:
    """The signed-in user's own password, counted like a sign-in (so wrong ones
    lead to the lockout), else a 400 that says what is wrong. The counted attempt."""
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
    return attempt


def _scope(attempt: AttemptRow) -> str:
    """An admin's session is an admin session; everyone else's an app session."""
    return "admin" if attempt.role == "admin" else "app"


def _finish_second_step(
    context: AppContext,
    attempt: AttemptRow,
    code: str | None,
    details: dict[str, audit.DetailValue] | None = None,
) -> JSONResponse:
    """After a right first step (the password, or a connected Google account), the second, for
    every role (#457): the session, once the code is right for anyone with two-step sign-in on.
    ``attempt`` is the counted attempt of an enabled user; ``details`` go on its ``auth.login``
    audit row (a provider sign-in's ``{"via": "google"}``).

    * No code set up: a member or reviewer gets their app session. An admin must set one up
      first: 403 ``mfa_enrol_required`` with the set-up offer and no session, except with a
      temporary password (the set-new-password screen first; the next sign-in sets it up).
    * A code set up: 401 ``code_required`` until it is sent, else the next unused code
      (``_code_problem``: the replay check). A missing or wrong code is counted like a wrong
      password, so it leads to the lock too, and audited with its reason.
    """
    scope = _scope(attempt)
    if attempt.totp_enabled_at is None:
        if attempt.role != "admin" or attempt.must_change_password:
            # An admin's set-new-password screen first (current_user keeps every other route
            # shut); the next sign-in then sets up the code.
            return _start_session(
                context, attempt.customer_id, attempt.user_id, scope=scope, action="auth.login"
            )
        _succeeded(context, attempt)  # the password was right; no session until a code is set up
        user = load_user(context.db, attempt.customer_id, attempt.user_id, scope=scope)
        if user is None:  # disabled in the meantime
            raise _refused(context)
        raise ApiError(403, "mfa_enrol_required", ENROL_FIRST, details=_enrol_offer(context, user))
    problem = _code_problem(context, attempt, code)
    if problem is not None:
        _failed(context, attempt, problem)  # counted: a wrong code next can't reset it
        if problem == "code_required":
            raise ApiError(401, "code_required", CODE_REQUIRED)
        raise _refused(context)
    return _start_session(
        context, attempt.customer_id, attempt.user_id, scope=scope, action="auth.login", details=details
    )


def _code_problem(context: AppContext, attempt: AttemptRow, code: str | None) -> str | None:
    """None when ``code`` is this user's next unused two-step code, which is then used up;
    else why not (``code_required``, ``totp_unreadable``, ``wrong_code``, ``code_reused``),
    for the caller to count, audit and answer. Sign-in and Change password share it."""
    if not _digits(code):
        return "code_required"
    try:  # a missing secret ("") does not open either
        secret = _box(context.settings.totp_encryption_key).open(
            attempt.totp_secret_enc or "", aad=attempt.user_id
        )
    except SecretBoxError:
        return "totp_unreadable"  # another admin or the CLI resets it
    step = totp.matching_step(secret, _digits(code), _unix(context.clock()), attempt.totp_last_step)
    if step is None:
        return "wrong_code"
    with context.db.customer(attempt.customer_id) as conn:
        accepted = users_sql.accept_totp_step(conn, attempt.customer_id, attempt.user_id, step)
    return None if accepted else "code_reused"  # another request used this code a moment ago


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
    response = JSONResponse(me_json(user, email_on=email_on(context)))
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
    user, claims = _open_session(context, customer_id, user_id, scope=scope, action=action, details=details)
    response = JSONResponse(me_json(user, email_on=email_on(context)))
    set_session_cookie(response, claims, context.settings, now=_unix(context.clock()))
    return response


def _open_session(
    context: AppContext,
    customer_id: str,
    user_id: str,
    *,
    scope: str,
    action: str,
    details: dict[str, audit.DetailValue] | None = None,
) -> tuple[CurrentUser, SessionClaims]:
    """A new session of this scope for an enabled user: clears the count, audits ``action``,
    and gives the claims for the caller's reply (``_start_session``'s, or a provider sign-in's
    redirect). The one 401 when the user was disabled in the meantime."""
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
        idle_hours=user.idle_hours,  # set: 24 hours, the idle timeout ends it (0040)
    )
    return user, claims


def _enrol_offer(context: AppContext, user: CurrentUser) -> dict[str, str]:
    """A new secret for the user to put into an authenticator app (typed in, or
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
