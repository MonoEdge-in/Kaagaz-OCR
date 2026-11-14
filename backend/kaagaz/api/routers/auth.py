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
  (``current_user``); the next sign-in then sets up the code. An admin who turned two-step
  off themselves (``users.totp_off_at``, 0061b) gets the admin session on the password
  alone, after Google too. Google only (#507): a user whose role signs in with Google only
  and who has a passing Google link gets 403 ``password_refused`` after a right password
  (audited, the count cleared; a wrong one is the one 401 as ever), except an admin who still
  has to set up two-step, who enrols first. With no passing link, the password opens the
  limited session (``api/auth.py``, ``google_link_required``), which can only connect Google.
* ``POST /auth/admin/login``: 410 ``gone``, whatever is sent. Not kept as an alias: one
  route to sign in is one set of rules to get right, and an old page gets a clear answer.
* ``POST /auth/2fa/start`` {password} (any session; #457 opened it to every role, it was
  ``/auth/admin/2fa/start``): with the current password, ``{enrol_token, secret,
  otpauth_uri}`` for turning it on. Asking for the password means a stolen cookie alone
  cannot put its own key on the account. Nothing is stored yet. 409 when it is on.
* ``POST /auth/admin/enrol`` {enrol_token, code}: turns the code on (audited
  ``auth.totp_on``, a security notice; clearing "turned off") and starts a session of the
  user's scope (an admin's at sign-in or sign-up, or from Account, Two-step sign-in, like a
  member's or reviewer's). The session version stays, so other browsers stay in.
* ``POST /auth/2fa/off`` {password, code}: anyone turns their own two-step sign-in off, with
  the password and a current code (both counted like a sign-in, else a 400 that says
  which). Every other browser is signed out, this one is re-signed; audited
  ``auth.totp_off``, a security notice. 409 when it is off. An admin's stays off until they
  turn it on again (0061b, Tushar, 30 Oct 2026): they sign in with the password alone,
  deleting a tag still needs it on (``require_admin_with_two_step``), and Delete forever asks
  for the password again (#538, ``require_delete_forever``). While
  the super admin requires two-step for the organisation's admins (0068, #481), an admin's
  turning off is 409 ``two_step_required`` before anything is checked, and one already off
  sets it up at the next sign-in (``users.session_user``, ``_ATTEMPT_COLUMNS``). A
  reset of the code (another admin, the operator, the CLI) is not "turned off": an admin
  sets one up at the next sign-in. A member's or reviewer's lost code: an admin resets it
  (``routers/admin.py``).
* ``POST /auth/password`` {current_password, new_password, code?}: a new password;
  every other browser is signed out, this one is re-signed. Anyone with two-step sign-in on
  also sends the code (#457; the sign-in's own check, ``_code_problem``), and a security
  notice is emailed while email is on. In the limited session (#507) only a temporary
  password may be changed (403 ``google_link_required`` otherwise), so a stolen password
  can't take the account from its owner.
* ``POST /auth/confirm-password`` {password}: only for a member or reviewer an
  admin allowed to delete forever, or an admin in an admin session without two-step sign-in
  on (#538, ``deletes_forever_by_password``); the password, counted like a sign-in, then a
  5-minute confirm cookie (kaagaz/api/auth.py) for Delete forever. 204.
* ``POST /auth/logout``: ends this browser's session on the server (0076, #502, Tushar 6 Nov
  2026): its id goes to ``ended_sessions``, so a copied cookie stops working at once, and the
  user's other browsers stay signed in. ``POST /auth/logout-everywhere`` (the limited session
  too, #507 review) ends every one (``session_version``). ``GET /auth/me``.
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
  your email" link is sent, and the reply is 201 ``{status: "confirm_email", sent}`` (``sent``:
  false when the email failed, so the screen offers to send it again) with no
  set-up offer and no session; sign-in then says 403 ``org_pending`` (after a right
  password only) until an operator approves it (``routers/operator_orgs.py``).
  A taken e-mail is the admin screens' 409; with email on, the same 201 as a new sign-up and
  nothing is sent, so sign-up never shows who has an account. A pending sign-up never
  confirmed within 24 hours no longer holds its e-mail (0063). At most ``SIGNUPS_PER_HOUR`` a client
  address (429). Off (``SELF_SIGNUP_ENABLED=false``, or the super admin's Self sign-up
  switch, 0038): 404; ``GET /auth/signup`` says
  ``{enabled}`` for the sign-in pages' link. Audited ``org.self_signup`` in the new
  organisation's log, the new admin as actor. No e-mail check: whoever types an address
  owns that account (PROVISIONAL). A name too short or
  an obvious test word ("asd") is a 400 (``names.org_name_ok``, UI audit G12).

Why the sign-in looks the way it does (council record 29 Sep, WP1.3):

* **One reply for every failure.** A wrong password, an unknown e-mail, a
  locked or disabled account and a wrong or reused code all get the same 401
  ``bad_credentials`` text (``code_required``, ``mfa_enrol_required`` and ``two_step_unreadable`` only
  ever follow a right password), and every path
  spends exactly one scrypt hash, so neither the reply nor its time says
  which accounts exist or which part was wrong.
* **The attempt is counted before the password is checked**
  (``users.begin_attempt``, one atomic UPDATE, committed on its own), so
  parallel guesses are all counted and a locked account never reaches the
  check. A right password (or password and code) clears the count
  (``users.attempt_succeeded``); anything else leaves it counted, so wrong
  codes count toward the lockout too.
* **A sign-in locks per account and address** (A2, 7 Nov 2026): ``login_max_failures``
  in a row from one address lock the account for that address (``AddressLocks``, in
  memory), and those refused there are not counted, so a flood from one address never
  locks the person out of their own. ``ACCOUNT_MAX_FAILURES`` in a row from anywhere
  lock the account (``users.begin_attempt``). The signed-in checks lock the account at
  ``login_max_failures``, as before.
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
import json
import logging
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import Connection

from kaagaz.api import emails, oidc, totp
from kaagaz.api.auth import (
    GOOGLE_LINK_REQUIRED,
    CurrentUser,
    SessionClaims,
    User,
    UserAllowGoogleLink,
    UserAllowPasswordChange,
    clear_session_cookie,
    current_user_allow_password_change,
    deletes_forever_by_password,
    feature_on,
    hash_password,
    load_user,
    needs_google_link,
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
from kaagaz.api.middleware import AddressLocks, RateLimiter, client_address
from kaagaz.api.names import ORG_NAME_PROBLEM, org_name_ok
from kaagaz.api.routers.admin import MAX_PASSWORD, MIN_PASSWORD, valid_email
from kaagaz.api.secret_box import SecretBoxError
from kaagaz.api.twostep import box as _box
from kaagaz.api.twostep import digits as _digits
from kaagaz.api.twostep import refused as _refused
from kaagaz.api.twostep import unix as _unix
from kaagaz.api.views import iso, me_json
from kaagaz.db import access as access_sql
from kaagaz.db import audit, identities
from kaagaz.db import email_links as links_sql
from kaagaz.db import users as users_sql
from kaagaz.db.users import AttemptRow
from kaagaz.settings import Settings

router = APIRouter()
log = logging.getLogger(__name__)

Context = Annotated[AppContext, Depends(get_context)]

MAX_CODE = 16  # 6 digits, plus room for the spaces people type
ENROL_FIRST = "Set up two-step sign-in to continue. Admins need it to keep your organisation safe."
ADMIN_LOGIN_GONE = "Sign in on the sign-in page. Admins and members use the same one now."
CODE_REQUIRED = "Enter the 6-digit code from your authenticator app."
WRONG_CODE = "That code is not right. Wait for the next code and try again."
ORG_PENDING = "Your organisation is waiting for approval."
UNREADABLE = "Your two-step code can't be checked right now. Ask your admin to reset it."
TWO_STEP_REQUIRED = "Your organisation requires two-step sign-in for admins, so it stays on."
PASSWORD_REFUSED = "Your organisation asks you to sign in with Google. Use Continue with Google."  # noqa: S105
RECENT_DAYS = 14  # how long the app's "Google connected" banner shows (#457 phase 1)
# Sign-ups an hour (PROVISIONAL). Behind the Next.js proxy every visitor shares the proxy's
# address (kaagaz/api/middleware.py), so in production this is one cap for the whole site,
# sized for real sign-ups. ponytail: site-wide cap; per-visitor once X-Forwarded-For is trusted.
# No e-mail is proven at sign-up (there is no mail service yet): the super admin can disable
# an organisation, and SELF_SIGNUP_ENABLED=false turns sign-up off.
SIGNUPS_PER_HOUR = 20
signup_limits = RateLimiter(SIGNUPS_PER_HOUR, window_seconds=3600)
TOO_MANY_SIGNUPS = "Too many sign-ups from here. Try again in an hour."
# A2: a sign-in's cap for the account across every address (each address has
# login_max_failures, address_locks), locked for an hour: at most 50 an hour from anywhere.
# shortcut: in a row, not an hour: one address at 5 a lock reaches it in about 2.5 hours unless
# the person signs in meanwhile, and the one 401's words still say login_lock_minutes. The
# signed-in checks still lock at login_max_failures, counted on the same column, so after a flood
# they can lock sooner.
ACCOUNT_MAX_FAILURES = 50
ACCOUNT_LOCK_MINUTES = 60
address_locks = AddressLocks()
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
    code: str | None = Field(default=None, max_length=MAX_CODE)  # missing: counted, then a 400


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
def login(body: LoginBody, request: Request, context: Context) -> JSONResponse:
    attempt = _check_password(context, body.email, body.password, client_address(request))
    return _finish_second_step(context, attempt, body.code)


@router.post("/auth/admin/login")
def admin_login_gone() -> None:
    """Gone (one sign-in page, 16 Oct 2026): nothing sent is read or checked."""
    raise ApiError(410, "gone", ADMIN_LOGIN_GONE)


@router.post("/auth/admin/enrol")
def admin_enrol(body: EnrolBody, background: BackgroundTasks, context: Context) -> JSONResponse:
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
    who = (context, customer_id, user_id)
    emails.after(background, "two_step_on", emails.notify, *who, "two_step_on", emails.two_step_on)
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
    if not signup_limits.allow(client_address(request)):
        raise ApiError(429, "rate_limited", TOO_MANY_SIGNUPS, headers={"Retry-After": "3600"})
    email = valid_email(body.email)
    if not org_name_ok(body.organisation_name):
        raise ApiError(400, "bad_request", ORG_NAME_PROBLEM)
    if not _signup_on(context):  # the super admin's switch (0038), the first database read
        raise ApiError(404, "not_found")
    password_hash = hash_password(body.password)  # before the transaction, never during it
    pending = email_on(context)  # #457: the e-mail is proven, then an operator approves
    with context.db.system() as conn:  # a stale, unconfirmed sign-up gives the e-mail back (0063)
        users_sql.release_stale_signup(conn, email)
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
        if pending:  # the same reply as a new sign-up, and nothing sent: it never shows who has an account
            return JSONResponse({"status": "confirm_email", "sent": True}, status_code=201)
        raise ApiError(409, "conflict", "That e-mail is already in use.") from None
    if found is None:  # never: made in this transaction
        raise ApiError(500, "internal_error")
    if pending:
        with context.db.customer(customer_id) as conn:
            _record(conn, customer_id, "org.self_signup", actor=found.user_id, target=found.user_id)
        link = new_link(context, customer_id, found.user_id, "confirm")
        sent = send(context, email, "confirm", emails.confirm_email(link))
        return JSONResponse({"status": "confirm_email", "sent": sent}, status_code=201)
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
    _succeeded(context, _check_current_password(context, user, body.password, now), signed_in=True)
    return _enrol_offer(context, user)


@router.post("/auth/2fa/off")
def turn_off_two_step(
    body: TurnOffBody, user: User, request: Request, background: BackgroundTasks, context: Context
) -> JSONResponse:
    """Anyone, with the password and a code. An admin's stays off until they turn it on
    again (0061b, Tushar, 30 Oct 2026); never while the organisation requires it (0068, #481:
    should it be required a moment after this check, the admin sets it up at the next request)."""
    if not user.totp_enabled:
        raise ApiError(409, "conflict", "Two-step sign-in is already off.")
    if user.role == "admin" and user.two_step_required:
        raise ApiError(409, "two_step_required", TWO_STEP_REQUIRED)
    now = context.clock()
    _prove(context, user, body.password, body.code)
    with context.db.user(user.customer_id, user.user_id) as conn:
        version = users_sql.turn_off_totp(conn, user.customer_id, user.user_id, now=now)
        if version is not None:
            _record(conn, user.customer_id, "auth.totp_off", actor=user.user_id, target=user.user_id)
    if version is None:  # another request turned it off a moment ago
        raise ApiError(409, "conflict", "Two-step sign-in is already off.")
    changed = dataclasses.replace(
        user, session_version=version, totp_enabled=False, two_step_turned_off=user.role == "admin"
    )
    response = _keep_this_browser(request, context, changed, now)
    notice = emails.two_step_off(user.display_name)
    emails.after(background, "two_step_off", send, context, user.email, "two_step_off", notice)
    return response


@router.post("/auth/password")
def change_password(
    body: PasswordBody,
    user: UserAllowPasswordChange,
    request: Request,
    background: BackgroundTasks,
    context: Context,
) -> JSONResponse:
    if needs_google_link(user, context.settings.google_signin_enabled) and not user.must_change_password:
        raise ApiError(403, "google_link_required", GOOGLE_LINK_REQUIRED)
    if body.new_password == body.current_password:
        raise ApiError(400, "bad_request", "Choose a new password that is different from the current one.")
    now = context.clock()
    _prove(context, user, body.current_password, body.code)
    password_hash = hash_password(body.new_password)
    with context.db.user(user.customer_id, user.user_id) as conn:
        row = users_sql.set_password(
            conn, user.customer_id, user.user_id, password_hash=password_hash, must_change=False
        )
        if row is None:
            raise ApiError(401, "not_signed_in")
        links_sql.void_all(conn, user.customer_id, user.user_id, now=now)  # a reset link sent before
        _record(conn, user.customer_id, "auth.password_change", actor=user.user_id, target=user.user_id)
    changed = dataclasses.replace(
        user, session_version=int(row["session_version"]), must_change_password=False
    )
    response = _keep_this_browser(request, context, changed, now)
    notice = emails.password_changed(user.display_name)
    emails.after(background, "password_changed", send, context, user.email, "password_changed", notice)
    return response


@router.post("/auth/confirm-password", status_code=204)
def confirm_password(body: CurrentPasswordBody, user: User, request: Request, context: Context) -> Response:
    if not deletes_forever_by_password(user):
        raise ApiError(403, "forbidden")  # nothing is counted
    now = context.clock()
    claims = session_claims(request, context.settings, _unix(now))
    if claims is None:  # never: ``user`` was read from these claims
        raise ApiError(401, "not_signed_in")
    _succeeded(context, _check_current_password(context, user, body.password, now), signed_in=True)
    response = Response(status_code=204)
    set_confirm_cookie(response, user, context.settings, now=_unix(now), session_id=claims.session_id)
    return response


@router.post("/auth/logout", status_code=204)
def logout(request: Request, user: UserAllowPasswordChange, context: Context) -> Response:
    claims = session_claims(request, context.settings, _unix(context.clock()))
    with context.db.user(user.customer_id, user.user_id) as conn:
        if claims is not None:  # always: ``user`` was read from these claims
            ends = datetime.fromtimestamp(claims.expires_at, UTC)
            users_sql.end_session(conn, user.customer_id, user.user_id, claims.session_id, expires_at=ends)
        _record(conn, user.customer_id, "auth.logout", actor=user.user_id, target=user.user_id)
    response = Response(status_code=204)
    clear_session_cookie(response, context.settings)
    return response


@router.post("/auth/logout-everywhere", status_code=204)
def logout_everywhere(user: UserAllowGoogleLink, context: Context) -> Response:  # also the limited session
    with context.db.user(user.customer_id, user.user_id) as conn:
        users_sql.bump_session_version(conn, user.customer_id, user.user_id)
        _record(conn, user.customer_id, "auth.logout_everywhere", actor=user.user_id, target=user.user_id)
    response = Response(status_code=204)
    clear_session_cookie(response, context.settings)
    return response


@router.get("/auth/me")
def me(user: UserAllowPasswordChange, context: Context) -> dict[str, Any]:
    return me_json(
        user,
        email_on=email_on(context),
        providers=oidc.enabled_providers(context.settings),
        recent_connections=_recent_connections(context, user),
        access=_access(context, user),
    )


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
                "providers": oidc.enabled_providers(settings),
            }
        raise
    context = get_context(request)
    recent = _recent_connections(context, user)
    providers = oidc.enabled_providers(context.settings)
    me = me_json(
        user,
        email_on=email_on(context),
        providers=providers,
        recent_connections=recent,
        access=_access(context, user),
    )
    return {"signed_in": True, "me": me}


def _access(context: AppContext, user: CurrentUser) -> tuple[bool, list[str]]:
    """PRD 7 (#601): the person's Management and the departments they head (``kaagaz_access()``)."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        return access_sql.me(conn)


# --- the steps they share ----------------------------------------------------------------


def _recent_connections(context: AppContext, user: CurrentUser) -> list[dict[str, str]]:
    """The user's approved connections made in the last RECENT_DAYS, for the app's "Google
    connected on 30 Oct." banner (#457 phase 1); none, and nothing read, while no provider is on."""
    if not oidc.enabled_providers(context.settings):
        return []
    since = context.clock() - timedelta(days=RECENT_DAYS)
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows = identities.for_user(conn, user.customer_id, user.user_id)
    return [
        {"provider": str(row["provider"]), "linked_at": iso(row["linked_at"])}
        for row in rows
        if row["status"] == "approved" and row["linked_at"] >= since
    ]


def _check_password(context: AppContext, email: str, password: str, address: str) -> AttemptRow:
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
    attempt = _begin(context, found.customer_id, found.user_id, context.clock(), address=address)
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
        _succeeded(context, attempt)  # not a failure: waiting must never lock its admin out
        raise ApiError(403, "org_pending", ORG_PENDING)
    if not attempt.customer_active:  # its organisation is disabled or being deleted (0014)
        _failed(context, attempt, "customer_disabled")
        raise _refused(context)
    if _google_only(context, attempt):  # told only after a right password, so no account is shown
        _succeeded(context, attempt)
        _audit_failure(context, attempt.customer_id, attempt.user_id, "password_refused")
        raise ApiError(403, "password_refused", PASSWORD_REFUSED)
    return attempt


def _google_only(context: AppContext, attempt: AttemptRow) -> bool:
    """Google only for this user (#507): Google on, the role uses it only, and a passing link.
    Not for an admin who still has to set up two-step: Google says ``password_first`` to them."""
    enrolling = attempt.role == "admin" and attempt.totp_enabled_at is None and attempt.totp_off_at is None
    return (
        context.settings.google_signin_enabled
        and attempt.google_on
        and attempt.google_only
        and attempt.google_linked
        and not enrolling
    )


def _account(
    context: AppContext, customer_id: str, user_id: str, *, signed_in: bool
) -> AbstractContextManager[Connection]:
    """The attempt helpers' transaction (#583): as the person when the signed-in user proves
    themselves again (``_check_current_password``, ``_prove``); on the sign-in path, where the
    account is not proven yet, the organisation only."""
    return context.db.user(customer_id, user_id) if signed_in else context.db.customer(customer_id)


def _begin(
    context: AppContext,
    customer_id: str,
    user_id: str,
    now: datetime,
    *,
    address: str | None = None,
    signed_in: bool = False,
) -> AttemptRow | None:
    """Count one attempt, committed before any check. None: locked. A sign-in gives the visitor's
    ``address``: locked per account and address, then per account at ACCOUNT_MAX_FAILURES (A2).
    Its ``failed_logins`` is then the count that locks, for ``_failed``'s ``auth.locked``."""
    settings = context.settings
    key, count = f"{user_id}|{address}", 0
    if address is not None:
        taken = _take(key, now, settings)
        if taken is None:
            seen = address_locks.seen(key)  # before the read (AddressLocks.seen)
            with _account(context, customer_id, user_id, signed_in=signed_in) as conn:
                account = users_sql.peek_attempt(conn, customer_id, user_id, now=now)
            if account is None or account.failed_logins >= seen:
                return None
            address_locks.clear(key)  # the account's count started again since: a right password, an unlock
            taken = _take(key, now, settings)
        count = taken or 1
    with _account(context, customer_id, user_id, signed_in=signed_in) as conn:
        attempt = users_sql.begin_attempt(
            conn,
            customer_id,
            user_id,
            now=now,
            max_failures=settings.login_max_failures if address is None else ACCOUNT_MAX_FAILURES,
            lock_minutes=settings.login_lock_minutes if address is None else ACCOUNT_LOCK_MINUTES,
        )
    if attempt is None or address is None or attempt.failed_logins >= ACCOUNT_MAX_FAILURES:
        return attempt
    address_locks.saw(key, attempt.failed_logins)
    return dataclasses.replace(attempt, failed_logins=count)


def _take(key: str, now: datetime, settings: Settings) -> int | None:
    """``address_locks.take`` with the sign-in's limit and lock."""
    return address_locks.take(
        key, now.timestamp(), settings.login_max_failures, settings.login_lock_minutes * 60
    )


def _check_current_password(
    context: AppContext, user: CurrentUser, password: str, now: datetime
) -> AttemptRow:
    """The signed-in user's own password, counted like a sign-in (so wrong ones
    lead to the lockout), else a 400 that says what is wrong. The counted attempt."""
    attempt = _begin(context, user.customer_id, user.user_id, now, signed_in=True)
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
        _failed(context, attempt, "wrong_password", signed_in=True)
        raise ApiError(400, "bad_request", "Your current password is not right.")
    return attempt


def prove_password(context: AppContext, user: CurrentUser, password: str) -> None:
    """An Admin giving themselves access (PRD 7, #601): their own password, counted like a sign-in
    (``_check_current_password``), with its own codes: 400 ``password_locked`` while the sign-in
    lock is on, 400 ``wrong_password``; then the count starts again."""
    attempt = _begin(context, user.customer_id, user.user_id, context.clock(), signed_in=True)
    if attempt is None:
        minutes = context.settings.login_lock_minutes
        raise ApiError(
            400,
            "password_locked",
            f"Too many wrong passwords were tried on this account. Try again in {minutes} minutes,"
            " or ask another admin to unlock it.",
        )
    if not verify_password(password, attempt.password_hash):
        _failed(context, attempt, "wrong_password", signed_in=True)
        raise ApiError(400, "wrong_password", "Your password is not right.")
    _succeeded(context, attempt, signed_in=True)


def _prove(context: AppContext, user: CurrentUser, password: str, code: str | None) -> None:
    """The signed-in user's proof before a change to how they sign in: the password, then the
    code for anyone with two-step sign-in on (always an admin), each counted like a sign-in,
    else a 400 that says what is wrong; then the count starts again. Turning codes off, Change
    password and connecting an account share it."""
    attempt = _check_current_password(context, user, password, context.clock())
    if attempt.totp_enabled_at is not None:
        problem = _code_problem(context, attempt, code, signed_in=True)
        if problem is not None:
            _failed(context, attempt, problem, signed_in=True)
            raise ApiError(400, "bad_request", CODE_REQUIRED if problem == "code_required" else WRONG_CODE)
    _succeeded(context, attempt, signed_in=True)


def _per_user_limit(limiter: RateLimiter, user: CurrentUser, message: str) -> None:
    """At most ``limiter``'s allowance for this user, else 429 with ``message`` and when to try again."""
    key = f"user:{user.user_id}"
    if not limiter.allow(key):
        raise ApiError(429, "rate_limited", message, headers={"Retry-After": str(limiter.retry_after(key))})


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

    * No code set up: a member or reviewer gets their app session, and an admin who turned it
      off themselves (``totp_off_at``, 0061b) their admin session. Any other admin must set
      one up first: 403 ``mfa_enrol_required`` with the set-up offer and no session, except
      with a temporary password (the set-new-password screen first; the next sign-in sets it up).
    * A code set up: 401 ``code_required`` until it is sent, else the next unused code
      (``_code_problem``: the replay check). A missing or wrong code is counted like a wrong
      password, so it leads to the lock too, and audited with its reason.
    """
    scope = _scope(attempt)
    if attempt.totp_enabled_at is None:
        if attempt.role != "admin" or attempt.must_change_password or attempt.totp_off_at is not None:
            # An admin's set-new-password screen first (current_user keeps every other route
            # shut); the next sign-in then sets up the code. Or turned off by the admin.
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


def _code_problem(
    context: AppContext, attempt: AttemptRow, code: str | None, *, signed_in: bool = False
) -> str | None:
    """None when ``code`` is this user's next unused two-step code, which is then used up;
    else why not (``code_required``, ``wrong_code``, ``code_reused``), for the caller to
    count, audit and answer. Sign-in and Change password share it.

    A secret that does not open (a changed key, a damaged row) is not the person's fault: it is
    logged ``totp_unreadable`` at error level, the attempt is not counted (the password was
    right), and the reply is 403 ``two_step_unreadable``. Another admin or the CLI resets it."""
    if not _digits(code):
        return "code_required"
    try:  # a missing secret ("") does not open either
        secret = _box(context.settings.totp_encryption_key).open(
            attempt.totp_secret_enc or "", aad=attempt.user_id
        )
    except SecretBoxError:
        log.error(json.dumps({"event": "totp_unreadable"}, separators=(",", ":")))
        with _account(context, attempt.customer_id, attempt.user_id, signed_in=signed_in) as conn:
            users_sql.unlock(conn, attempt.customer_id, attempt.user_id)  # not counted toward the lock
        raise ApiError(403, "two_step_unreadable", UNREADABLE) from None
    step = totp.matching_step(secret, _digits(code), _unix(context.clock()), attempt.totp_last_step)
    if step is None:
        return "wrong_code"
    with _account(context, attempt.customer_id, attempt.user_id, signed_in=signed_in) as conn:
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
    providers = oidc.enabled_providers(context.settings)
    response = JSONResponse(
        me_json(user, email_on=email_on(context), providers=providers, access=_access(context, user))
    )
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
    providers = oidc.enabled_providers(context.settings)
    response = JSONResponse(
        me_json(user, email_on=email_on(context), providers=providers, access=_access(context, user))
    )
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
    with context.db.user(user.customer_id, user.user_id) as conn:
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


def _succeeded(context: AppContext, attempt: AttemptRow, *, signed_in: bool = False) -> None:
    """The password was right but no session starts yet (a password change
    first): the count starts again all the same."""
    with _account(context, attempt.customer_id, attempt.user_id, signed_in=signed_in) as conn:
        users_sql.attempt_succeeded(conn, attempt.customer_id, attempt.user_id, now=context.clock())


def _failed(context: AppContext, attempt: AttemptRow, reason: str, *, signed_in: bool = False) -> None:
    """Audit a counted failure, and the lock when this attempt reached the limit."""
    locked = attempt.failed_logins >= context.settings.login_max_failures
    _audit_failure(context, attempt.customer_id, attempt.user_id, reason, locked=locked, signed_in=signed_in)


def _audit_failure(
    context: AppContext,
    customer_id: str,
    user_id: str,
    reason: str,
    *,
    locked: bool = False,
    details: dict[str, audit.DetailValue] | None = None,
    signed_in: bool = False,
) -> None:
    """``auth.login_failed`` {reason, and ``details``: a Google sign-in's ``via`` and ``domain``}."""
    with _account(context, customer_id, user_id, signed_in=signed_in) as conn:
        written: dict[str, audit.DetailValue] = {"reason": reason, **(details or {})}
        _record(conn, customer_id, "auth.login_failed", actor=None, target=user_id, details=written)
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
