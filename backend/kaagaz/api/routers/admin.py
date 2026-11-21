"""The admin routes: users, the customer's settings, the audit log, the numbers and
the failed jobs (PRD 3 Z1; Retry is the documents router's reprocess route), the
organisation's AI models and use in tokens (model choice, council 11 Oct 2026), and each
member's numbers (#386, ``/admin/members``: this organisation's users only, never money).

Why the admin session: every route here needs an admin's session (scope "admin": an
admin's sign-in at /login, always with the two-step code since 0035). The check sits on
the router itself (``require_admin_session``), so a route added later cannot forget it;
anyone else gets 403 ``admin_session_required``.

Why the self-protection rules: an admin cannot disable themselves, take away
their own admin role or reset their own two-step code, so a customer can never
lock out its last way in by accident (the CLI ``reset-admin-2fa`` is the way
back for the last admin). Every change writes one audit row with ids and
statuses only (never a password, hashed or not).

Why an invite (#457 phase 0): with email on, Add person also emails "You're invited" (a 7-day
link to choose their own password, which proves the e-mail), made in the same transaction as
the user and sent after it; the reply says whether it went (``invited``). The password the
admin typed is always kept as a temporary one (the user must change it), the fallback when
the email fails or never arrives. With email off it is exactly as before, with no ``invited``.

Why the session version: disabling a user, setting their password, resetting
their password or their two-step code all raise ``session_version`` (in
``kaagaz.db.users``), so every session of theirs ends at its next request. A
password an admin sets is temporary: the user must choose a new one first.
A role change raises it too: every request re-checks the role, but a user
demoted and later promoted again must not get their old sessions back.

Why Delete forever is granted this way (council 4 Oct 2026, PROVISIONAL): an
admin may allow a member or reviewer to delete documents in Trash forever, and
stop it again. Allowing needs this admin's two-step sign-in on (403
``two_step_required``), else a password-only admin could route round two-step by
allowing a made-up account; an admin is never a target (400), a role change
clears the grant, and stopping it works at the user's next request.

Why AI models and use look the way they do (model choice, council 11 Oct 2026):
the operator sets the organisation's models and budget, so ``/admin/models`` only
shows their names. Org admins never see money, prices, budgets or costs in any
response here (Tushar, 15 Oct 2026): AI use is in tokens. ``/admin/usage`` and its
CSV are the admin's alone (SP2): a member or an app session gets 403, like every
route here. Days are the customer's TIMEZONE days, both ends included, at most
USAGE_MAX_DAYS (400 otherwise); this month to today when left out. A ``user_id``
names a user of this customer only: another customer's user matches no row, so the
report is empty. A CSV cell that starts with ``= + - @`` (or a tab or a carriage
return) gets a leading ``'``, so a spreadsheet never runs it as a formula.

Why connected accounts are here (#457 phase 1): the admin approves or declines a member's or
reviewer's waiting Google account (``/admin/identities``), removes a person's connections
(signed out everywhere, the password still works), and switches "Approve new sign-in
connections automatically" (``auto_approve_connections``, audited ``settings.auto_approve_on``
or ``_off``). Each is 404 while no provider is switched on (the switch: left out of the
settings, a 400 to set); another organisation's ids are 404. Approving or declining leaves
the person's sessions as they are. Resetting a person's password clears their connections
too. ``/admin/users`` says how each person signs in (``signs_in_with``).

Why Google sign-in has rules here (#507, 0074): where the super admin leaves Google on for the
organisation (``kaagaz.db.google_rules``), its admin sets, per role, whether Google is allowed and
whether it is the only way in, and the company domains (``/admin/settings`` ``google``, all three
replaced at once, in one transaction, audited ``settings.google_rules``). A save signs out the
people with an approved connection of each role whose rules changed (every role when the domains
did); the saver stays signed in. Making admins "Google only" needs the saver's own connection to
pass the new domains, else 409 ``google_lockout`` (audited ``settings.google_rules_refused`` in its
own transaction) with its reason: ``no_link``, ``link_pending`` (it waits for another admin),
``domain_unchecked`` (from before 0074: sign in with Google once) or ``outside_domains``.
Approving a connection refuses one's own (403 ``own_connection``), an organisation that is off
(409 ``google_off``), a role that may not use Google (``role_refused``), an account whose domain
is not checked yet (``domain_unchecked``) and one outside the domains (``domain_refused``).
Promotion to admin puts the person's approved connections back to waiting (audited
``auth.identity_reapproval``; the admins are emailed), so another admin approves them again (a new
connection within 30 days waits too, ``routers/oidc.py``). The people list says ``google_needed``
(their role uses Google only, and no connection of theirs passes) and ``google_unchecked``, and so
does every reply that may change them (a role change, Remove connected accounts, Reset password);
the invite says to connect Google while the new person's role uses it.

Why auto-filing is a setting here (feature 28, council 15 Oct 2026): the platform operator
grants it, then the admin switches it on or off (``autofile_on``) in the same form as the
upload limit. Each field left out of the PATCH is left as it is. Switching it on without
the grant is 409 ``not_granted`` and changes nothing; a real change is audited as
``settings.autofile_on`` or ``settings.autofile_off``.

Why the storage line (#516): ``/admin/storage`` gives the bytes the organisation's documents hold
(every version and the Trash) and its storage limit, which the super admin sets, so the admin
sees how close it is before an upload is refused ``storage_full``. Numbers only.
"""

import csv
import io
import json
import re
import secrets
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, RowMapping

from kaagaz.api import emails, oidc
from kaagaz.api.auth import AdminSession, CurrentUser, hash_password, require_admin_session
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.emails import email_on
from kaagaz.api.errors import ApiError
from kaagaz.api.readers import usable
from kaagaz.api.routers.documents import day_start
from kaagaz.api.views import CURSOR, admin_user, failed_document, iso, timeline, without_money
from kaagaz.db import access as access_sql
from kaagaz.db import admin as admin_sql
from kaagaz.db import audit, google_rules, identities, listing, reading_days, storage_limits
from kaagaz.db import email_links as links_sql
from kaagaz.db import folders as folders_sql
from kaagaz.db import runs as runs_sql
from kaagaz.db import usage as usage_sql
from kaagaz.db import users as users_sql
from kaagaz.reading.ocr import GEMINI
from kaagaz.settings import Settings

router = APIRouter(dependencies=[Depends(require_admin_session)])

Context = Annotated[AppContext, Depends(get_context)]

MIB = 1024 * 1024
MIN_PASSWORD = 12
MAX_PASSWORD = 1024
TEMPORARY_PASSWORD_BYTES = 12  # secrets.token_urlsafe gives 16 characters
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

RoleName = Literal["member", "reviewer", "admin"]
PASSWORD_ONLY = ["password"]  # every account keeps its Kaagaz password (#457)
ALREADY_BELONGS = "This Google account already belongs to a Kaagaz user."
# #507: the org admin's Google rules, refused in plain words (GOOGLE_OFF and ROLE_REFUSED: also
# routers/oidc.py's).
GOOGLE_OFF = "Google sign-in is off for your organisation."
ROLE_REFUSED = "Your organisation doesn't use Google sign-in for this role."
DOMAIN_UNCHECKED = "Sign in with Google once so Kaagaz can check its domain."
ONLY_NOT_ALLOWED = "A role that uses Google only must also be allowed to use Google."
BAD_DOMAIN = "Enter a domain like sharmatraders.com."
PERSONAL_DOMAIN = "Gmail and other personal email domains can't be used. Enter your company's own domain."
TOO_MANY_DOMAINS = f"Add at most {google_rules.MAX_DOMAINS} domains."
LOCKOUT = {
    "no_link": "Connect your own Google account first, in Account, Ways to sign in. Then you can make"
    " Google the only way in for admins.",
    "link_pending": "Your Google account is waiting for another admin to approve it. Then you can make"
    " Google the only way in for admins.",
    "domain_unchecked": DOMAIN_UNCHECKED,
    "outside_domains": "Your own Google account isn't from these domains, so you'd be locked out. Add its"
    " domain, or connect one that is.",
}
_DOMAIN = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")

USAGE_MAX_DAYS = 366
USAGE_CSV_COLUMNS = ("day", "user", "model", "kind", "calls", "tokens")
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
UsageFrom = Annotated[str | None, Query(alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$")]
UsageTo = Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")]
# File management A5, A6: the admin log's filters and its CSV export.
MAX_AUDIT_ACTIONS = 20
_AUDIT_ACTION = re.compile(r"^[a-z_.]{1,64}$")  # the audit_log CHECK
AUDIT_EXPORT_PAGE = 1000
AUDIT_CSV_COLUMNS = ("When", "Who", "Action", "Document", "Folder", "Details")


def valid_email(email: str) -> str:
    """Lower case, trimmed and plausibly an e-mail, or 400."""
    cleaned = email.strip().lower()
    if not (3 <= len(cleaned) <= 254) or not _EMAIL.match(cleaned):
        raise ApiError(400, "bad_request", "Enter a valid e-mail address.")
    return cleaned


class NewUserBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=100)
    role: RoleName
    password: str = Field(min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)


class UserChangesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: RoleName | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=MIN_PASSWORD, max_length=MAX_PASSWORD)


class DeleteForeverBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool


class GoogleRulesBody(BaseModel):
    """#507: all three rules, replacing the stored ones."""

    model_config = ConfigDict(extra="forbid")

    allowed_roles: list[RoleName] = Field(max_length=10)
    only_roles: list[RoleName] = Field(max_length=10)
    domains: list[Annotated[str, Field(max_length=253)]] = Field(max_length=50)


class SettingsBody(BaseModel):
    """Left out: unchanged (``model_fields_set``). An explicit null limit: the platform's."""

    model_config = ConfigDict(extra="forbid")

    max_upload_mb: int | None = Field(default=None, ge=1)
    autofile_on: bool = False
    auto_approve_connections: bool = False
    google: GoogleRulesBody | None = None


def _google_on(conn: Connection, context: AppContext, customer_id: str) -> google_rules.Rules | None:
    """The organisation's Google rules while Google sign-in is on for it (the server's switch and
    the super admin's); None while it is off."""
    if not oidc.enabled(context.settings, "google"):
        return None
    rules = google_rules.rules(conn, customer_id)
    return rules if rules is not None and rules.on else None


def _people(
    conn: Connection, context: AppContext, customer_id: str, rows: Iterable[RowMapping]
) -> list[dict[str, Any]]:
    """``admin_user`` with how each signs in, ``google_needed`` and ``google_unchecked`` (#507):
    Google counts only while it is on, for an allowed role, with a connection that passes the
    domains; a "Google only" role with one no longer signs in with the password. One read of the
    rules and the connections."""
    rules = _google_on(conn, context, customer_id)
    links: dict[str, list[google_rules.Link]] = {}
    for link in google_rules.links(conn, customer_id) if rules is not None else []:
        links.setdefault(link.user_id, []).append(link)
    now = context.clock()
    people = []
    for row in rows:
        role = str(row["role"])
        mine = links.get(str(row["id"]), [])
        passes = (
            rules is not None
            and role in rules.allowed_roles
            and any(link.passes(rules.domains) for link in mine)
        )
        only = rules is not None and role in rules.only_roles
        ways = ([] if only and passes else PASSWORD_ONLY) + (["google"] if passes else [])
        needed = only and not passes
        unchecked = (
            needed
            and rules is not None
            and any(link.status == "approved" and link.unchecked(rules.domains) for link in mine)
        )
        people.append(
            admin_user(row, now=now, signs_in_with=ways, google_needed=needed, google_unchecked=unchecked)
        )
    return people


@router.get("/admin/users")
def list_users(user: AdminSession, context: Context) -> dict[str, Any]:
    """Each person with their Management and departments (PRD 7, #601: ``[{id, name, head}]``)."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows = users_sql.list_users(conn, user.customer_id)
        people = _people(conn, context, user.customer_id, rows)
        member_of: dict[str, list[dict[str, Any]]] = {}
        for row in access_sql.departments(conn, user.customer_id):
            member_of.setdefault(str(row["user_id"]), []).append(
                {"id": str(row["department_id"]), "name": row["name"], "head": bool(row["is_head"])}
            )
    return {"items": [{**person, "departments": member_of.get(person["id"], [])} for person in people]}


@router.post("/admin/users", status_code=201)
def create_user(body: NewUserBody, user: AdminSession, context: Context) -> JSONResponse:
    email = valid_email(body.email)
    name = body.display_name.strip()
    if not name:
        raise ApiError(400, "bad_request", "Enter a name.")
    inviting = email_on(context)
    # The typed password is the temporary one, with an invite too: the fallback if it never arrives.
    password_hash = hash_password(body.password)
    with context.db.system() as conn:  # a stale, unconfirmed sign-up gives the e-mail back (0063)
        users_sql.release_stale_signup(conn, email)
    try:
        with context.db.user(user.customer_id, user.user_id) as conn:
            row = users_sql.create_user(
                conn,
                user.customer_id,
                email=email,
                display_name=name,
                role=body.role,
                password_hash=password_hash,
                must_change_password=True,
            )
            audit.record(
                conn,
                user.customer_id,
                actor_user_id=user.user_id,
                action="user.create",
                target_type="user",
                target_id=str(row["id"]),
                details={"role": body.role},
            )
            target = str(row["id"])
            link = emails.add_link(conn, context, user.customer_id, target, "invite") if inviting else None
            rules = _google_on(conn, context, user.customer_id) if inviting else None
    except users_sql.EmailTaken:
        raise ApiError(409, "conflict", "That e-mail is already in use.") from None
    if link is None:
        return JSONResponse(admin_user(row, now=context.clock()), status_code=201)
    google = rules is not None and body.role in rules.allowed_roles  # #507: "connect your Google account"
    invited = emails.send(context, email, "invite", emails.invite(user.customer_name, link, google))
    if invited:  # the one follow-up write, only when the email went
        with context.db.user(user.customer_id, user.user_id) as conn:
            _audit_user(conn, user, "auth.invite_sent", target)
    return JSONResponse({**admin_user(row, now=context.clock()), "invited": invited}, status_code=201)


@router.patch("/admin/users/{user_id}")
def update_user(
    user_id: uuid.UUID,
    body: UserChangesBody,
    user: AdminSession,
    background: BackgroundTasks,
    context: Context,
) -> dict[str, Any]:
    target = str(user_id)
    if target == user.user_id and (body.disabled is True or (body.role is not None and body.role != "admin")):
        raise ApiError(400, "bad_request", "You can't disable yourself or remove your own admin role.")
    password_hash = None if body.password is None else hash_password(body.password)
    with context.db.user(user.customer_id, user.user_id) as conn:
        if (
            password_hash is not None
            and users_sql.auth_authority(conn, user.customer_id, target) == "keycloak"
        ):
            raise ApiError(403, "legacy_auth_disabled")
        if body.disabled is not None:  # PRD 7 (#601): users_switched raises the access version
            folders_sql._lock_tree(conn, user.customer_id)
        promoted = body.role == "admin" and users_sql.role_of(conn, user.customer_id, target) not in (
            None,
            "admin",
        )
        row = users_sql.update_user(
            conn,
            user.customer_id,
            target,
            role=body.role,
            disabled=body.disabled,
            password_hash=password_hash,
        )
        if row is None:
            raise ApiError(404, "not_found")
        if body.disabled is True or password_hash is not None:  # their emailed links stop working
            links_sql.void_all(conn, user.customer_id, target, now=context.clock())
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="user.update",
            target_type="user",
            target_id=target,
            details={
                "role": row["role"],
                "disabled": bool(row["disabled"]),
                "password_changed": password_hash is not None,
            },
        )
        # #507: a new admin's connections wait for another admin again (update_user signed them out).
        reapproved = google_rules.reapprove(conn, user.customer_id, target) if promoted else []
        for identity in reapproved:
            _audit_identity(conn, user, "auth.identity_reapproval", {**identity, "user_id": target})
        [reply] = _people(conn, context, user.customer_id, [row])  # the role may change the tag
    if reapproved:
        emails.after(
            background,
            "approval_waiting",
            tell_admins,
            context,
            user.customer_id,
            user.customer_name,
            row["email"],
        )
    return reply


def tell_admins(context: AppContext, customer_id: str, organisation: str, but: str | None = None) -> None:
    """Waiting for your approval, to each admin but ``but`` (the e-mail of one promoted), while
    email is on. Also ``routers/oidc.py``'s, for a connection that waits."""
    if not email_on(context):
        return
    with context.db.customer(customer_id) as conn:
        admins = users_sql.admin_contacts(conn, customer_id)
    link = f"{context.settings.app_url}/admin"
    for address, name in admins:
        if address != but:
            emails.send(
                context, address, "approval_waiting", emails.connection_waiting(name, organisation, link)
            )


@router.post("/admin/users/{user_id}/unlock")
def unlock_user(user_id: uuid.UUID, user: AdminSession, context: Context) -> dict[str, Any]:
    """End a lock early; the count of wrong passwords starts again at 0."""
    target = str(user_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        row = users_sql.unlock(conn, user.customer_id, target)
        if row is None:
            raise ApiError(404, "not_found")
        _audit_user(conn, user, "user.unlock", target)
    return admin_user(row, now=context.clock())


@router.post("/admin/users/{user_id}/reset-password")
def reset_password(user_id: uuid.UUID, user: AdminSession, context: Context) -> dict[str, Any]:
    """A new temporary password, returned this once and stored only as its hash.
    The user must set their own at the next sign-in; their sessions end."""
    target = str(user_id)
    if target == user.user_id:
        # It would end this admin session and force a password change; the
        # account menu's Change password is the way to change your own.
        raise ApiError(400, "bad_request", "You can't reset your own password here. Use Change password.")
    temporary = secrets.token_urlsafe(TEMPORARY_PASSWORD_BYTES)
    password_hash = hash_password(temporary)
    with context.db.user(user.customer_id, user.user_id) as conn:
        row = users_sql.set_password(
            conn, user.customer_id, target, password_hash=password_hash, must_change=True
        )
        if row is None:
            raise ApiError(404, "not_found")
        _audit_user(conn, user, "user.reset_password", target)
        identities.remove_all(conn, user.customer_id, target)  # #457: resetting a user clears them
        links_sql.void_all(conn, user.customer_id, target, now=context.clock())
        [reply] = _people(conn, context, user.customer_id, [row])  # no connection now: the tag may change
    return {"user": reply, "temporary_password": temporary}


@router.post("/admin/users/{user_id}/reset-2fa")
def reset_two_step(
    user_id: uuid.UUID, user: AdminSession, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    """Turn a user's two-step code off, so their password alone signs in again."""
    target = str(user_id)
    if target == user.user_id:
        raise ApiError(
            400,
            "bad_request",
            "You can't reset your own two-step code. Ask another admin, or use the command line.",
        )
    with context.db.user(user.customer_id, user.user_id) as conn:
        row = users_sql.reset_totp(conn, user.customer_id, target)
        if row is None:
            raise ApiError(404, "not_found")
        # The spec's auth.totp_reset (#457; it was user.reset_totp, never the plan's
        # "user.reset_2fa": audit actions must match ^[a-z_.]{1,64}$, the audit_log CHECK).
        _audit_user(conn, user, "auth.totp_reset", target)
    notice = emails.two_step_reset(row["display_name"])
    emails.after(background, "two_step_reset", emails.send, context, row["email"], "two_step_reset", notice)
    return admin_user(row, now=context.clock())


@router.put("/admin/users/{user_id}/delete-forever")
def set_delete_forever(
    user_id: uuid.UUID, body: DeleteForeverBody, user: AdminSession, context: Context
) -> dict[str, Any]:
    """Allow or stop a member's or reviewer's Delete forever. Audited with ids only."""
    if body.allowed and not user.totp_enabled:
        raise ApiError(403, "two_step_required", "Turn on two-step sign-in in Account to allow this.")
    target = str(user_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        row = users_sql.set_delete_forever(
            conn, user.customer_id, target, allowed=body.allowed, now=context.clock()
        )
        if row is None:
            if users_sql.role_of(conn, user.customer_id, target) is None:
                raise ApiError(404, "not_found")
            raise ApiError(400, "bad_request", "Admins can already delete forever.")
        action = "user.delete_forever_on" if body.allowed else "user.delete_forever_off"
        _audit_user(conn, user, action, target)
    return admin_user(row, now=context.clock())


# --- connected accounts (#457 phase 1) -----------------------------------------------------------


def _providers_on(context: AppContext) -> list[str]:
    return oidc.enabled_providers(context.settings)


def _connections_on(context: AppContext) -> None:
    """404 while no provider is switched on (after the admin session check, as every route here)."""
    if not _providers_on(context):
        raise ApiError(404, "not_found")


def _pending_json(conn: Connection, customer_id: str) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": str(row["id"]),
                "user_id": str(row["user_id"]),
                "display_name": row["display_name"],
                "user_email": row["user_email"],
                "provider": row["provider"],
                "email": row["email_at_link"],
                "linked_at": iso(row["linked_at"]),
            }
            for row in identities.pending(conn, customer_id)
        ]
    }


@router.get("/admin/identities")
def pending_identities(
    user: AdminSession, context: Context, status: Literal["pending"] = "pending"
) -> dict[str, Any]:
    """Connections to approve: who, the provider, the connected email and when, oldest first."""
    _connections_on(context)
    with context.db.user(user.customer_id, user.user_id) as conn:
        return _pending_json(conn, user.customer_id)


@router.post("/admin/identities/{identity_id}/approve")
def approve_identity(
    identity_id: uuid.UUID, user: AdminSession, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    """A waiting connection signs in from now on, unless its email became another user's sign-in
    email meanwhile (409, the one outside account rule). The person stays signed in (approval
    only adds a way in) and gets a notice. The list left. #507: never one's own (another admin
    approves it), and only what the organisation's Google rules let in now."""
    _connections_on(context)
    target = str(identity_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        waiting = next(
            (row for row in identities.pending(conn, user.customer_id) if str(row["id"]) == target), None
        )
    if waiting is None:
        raise ApiError(404, "not_found")
    if str(waiting["user_id"]) == user.user_id:
        raise ApiError(403, "own_connection", "Another admin needs to approve your own Google account.")
    email = waiting["email_at_link"]
    with context.db.system() as conn:
        taken = email is not None and identities.email_belongs_to_other(conn, email, str(waiting["user_id"]))
    if taken:
        raise ApiError(409, "conflict", ALREADY_BELONGS)
    with context.db.user(user.customer_id, user.user_id) as conn:
        _approvable(conn, context, user.customer_id, target)
        row = identities.approve(conn, user.customer_id, target, user.user_id, context.clock())
        if row is None:  # approved or declined a moment ago
            raise ApiError(404, "not_found")
        _audit_identity(conn, user, "auth.identity_approved", row)
        reply = _pending_json(conn, user.customer_id)
    who = (context, user.customer_id, str(row["user_id"]))
    emails.after(
        background, "identity_approved", emails.notify, *who, "identity_approved", emails.google_approved
    )
    return reply


def _approvable(conn: Connection, context: AppContext, customer_id: str, identity_id: str) -> None:
    """409 while Google is off for the organisation, for a role that may not use it, or for an
    account outside its domains (#507). Read in the approval's own transaction."""
    link = google_rules.link(conn, customer_id, identity_id)
    if link is None:  # not a Google connection: no Google rule applies
        return
    rules = _google_on(conn, context, customer_id)
    if rules is None:
        raise ApiError(409, "google_off", GOOGLE_OFF)
    if link.role not in rules.allowed_roles:
        raise ApiError(409, "role_refused", ROLE_REFUSED)
    if link.unchecked(rules.domains):  # from before 0074: their Google sign-in checks it (#507 review)
        raise ApiError(409, "domain_unchecked", DOMAIN_UNCHECKED)
    if not google_rules.domain_ok(rules.domains, link.hosted_domain, link.email_at_link):
        raise ApiError(409, "domain_refused", "This Google account isn't from your organisation's domains.")


@router.post("/admin/identities/{identity_id}/decline")
def decline_identity(
    identity_id: uuid.UUID, user: AdminSession, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    """A waiting connection is removed; the person gets a notice. The list left."""
    _connections_on(context)
    with context.db.user(user.customer_id, user.user_id) as conn:
        row = identities.remove(conn, user.customer_id, str(identity_id), status="pending")
        if row is None:
            raise ApiError(404, "not_found")
        _audit_identity(conn, user, "auth.identity_declined", row)
        reply = _pending_json(conn, user.customer_id)
    who = (context, user.customer_id, str(row["user_id"]))
    emails.after(
        background, "identity_declined", emails.notify, *who, "identity_declined", emails.google_declined
    )
    return reply


@router.post("/admin/users/{user_id}/remove-connections")
def remove_connections(
    user_id: uuid.UUID, user: AdminSession, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    """ "Remove connected accounts": every connection of this person goes, and they are signed
    out everywhere. Their password keeps working. With none, nothing changes and nothing is
    audited."""
    _connections_on(context)
    target = str(user_id)
    with context.db.user(user.customer_id, user.user_id) as conn:
        if users_sql.role_of(conn, user.customer_id, target) is None:
            raise ApiError(404, "not_found")
        count = identities.remove_all(conn, user.customer_id, target)
        if count:
            users_sql.bump_session_version(conn, user.customer_id, target)
            audit.record(
                conn,
                user.customer_id,
                actor_user_id=user.user_id,
                action="auth.identity_unlinked",
                target_type="user",
                target_id=target,
                details={"count": count},
            )
        row = next(row for row in users_sql.list_users(conn, user.customer_id) if str(row["id"]) == target)
        [reply] = _people(conn, context, user.customer_id, [row])  # no connection now: the tag may change
    if count:
        who = (context, user.customer_id, target)
        notice = emails.google_disconnected
        emails.after(background, "identity_unlinked", emails.notify, *who, "identity_unlinked", notice)
    return reply


def _audit_identity(
    conn: Connection, user: CurrentUser, action: str, row: Mapping[str, Any] | RowMapping
) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="user",
        target_id=str(row["user_id"]),
        details={"provider": str(row["provider"]), "identity_id": str(row["id"])},
    )


def _audit_user(conn: Connection, user: CurrentUser, action: str, target: str) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="user",
        target_id=target,
    )


def _settings_json(row: RowMapping, context: AppContext, google: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "customer_name": row["name"],
        "max_upload_mb": row["max_upload_mb"],
        "platform_max_upload_mb": context.settings.max_upload_bytes // MIB,
        "autofile": {"granted": bool(row["autofile_granted"]), "on": bool(row["autofile_on"])},
        # Only while a provider is on: there is nothing to approve otherwise.
        **(
            {"auto_approve_connections": bool(row["auto_approve_connections"])}
            if _providers_on(context)
            else {}
        ),
        **({} if google is None else {"google": google}),
    }


def _google_json(conn: Connection, context: AppContext, user: CurrentUser) -> dict[str, Any] | None:
    """#507's ``google``: whether Google sign-in is on for the organisation (``available``) and its
    rules. None (left out) while the server's Google sign-in is off, as
    ``auto_approve_connections``."""
    if not oidc.enabled(context.settings, "google"):
        return None
    rules = google_rules.rules(conn, user.customer_id)
    if rules is None:
        return None
    return {
        "available": rules.on,
        "allowed_roles": list(rules.allowed_roles),
        "only_roles": list(rules.only_roles),
        "domains": list(rules.domains),
    }


@router.get("/admin/settings")
def get_settings(user: AdminSession, context: Context) -> dict[str, Any]:
    with context.db.user(user.customer_id, user.user_id) as conn:
        row = admin_sql.get_settings(conn, user.customer_id)
        google = _google_json(conn, context, user)
    if row is None:
        raise ApiError(404, "not_found")
    return _settings_json(row, context, google)


def _wanted_rules(body: GoogleRulesBody) -> tuple[list[str], list[str], list[str]]:
    """The roles in order and the domains trimmed, lower case and once each; 400 in plain words."""
    allowed, only = google_rules.ordered(body.allowed_roles), google_rules.ordered(body.only_roles)
    if not set(only) <= set(allowed):
        raise ApiError(400, "bad_request", ONLY_NOT_ALLOWED)
    domains = list(dict.fromkeys(domain.strip().lower() for domain in body.domains))
    if len(domains) > google_rules.MAX_DOMAINS:
        raise ApiError(400, "bad_request", TOO_MANY_DOMAINS)
    for domain in domains:
        if not _DOMAIN.fullmatch(domain):
            raise ApiError(400, "bad_request", BAD_DOMAIN)
        if google_rules.personal(domain):
            raise ApiError(400, "bad_request", PERSONAL_DOMAIN)
    return allowed, only, domains


class _Lockout(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _save_google(
    conn: Connection, context: AppContext, user: CurrentUser, wanted: tuple[list[str], list[str], list[str]]
) -> None:
    """Replace the rules (in the caller's transaction): 409 while Google is off, ``_Lockout`` when
    admins would be "Google only" and the saver has no approved connection that passes the new
    domains (why: none, one waiting, one not checked yet, or one outside).
    Signs out the people with an approved connection of each role whose rules changed (every role
    when the domains did), the saver kept. Nothing changed: nothing written, nothing audited."""
    allowed, only, domains = wanted
    rules = _google_on(conn, context, user.customer_id)
    if rules is None:
        raise ApiError(409, "google_off", GOOGLE_OFF)
    if "admin" in only:
        mine = google_rules.links(conn, user.customer_id, user_id=user.user_id)
        approved = [link for link in mine if link.status == "approved"]
        if not approved:
            raise _Lockout("link_pending" if mine else "no_link")
        if not any(link.passes(domains) for link in approved):
            unchecked = any(link.unchecked(domains) for link in approved)
            raise _Lockout("domain_unchecked" if unchecked else "outside_domains")
    if (tuple(allowed), tuple(only), tuple(domains)) == (
        rules.allowed_roles,
        rules.only_roles,
        rules.domains,
    ):
        return
    google_rules.save_rules(conn, user.customer_id, allowed_roles=allowed, only_roles=only, domains=domains)
    details: dict[str, audit.DetailValue] = {
        "allowed_roles": ",".join(allowed),
        "only_roles": ",".join(only),
        "domains": ",".join(domains),
    }
    _audit_settings(conn, user, "settings.google_rules", details)
    changed = [
        role
        for role in google_rules.ROLES
        if (role in allowed) != (role in rules.allowed_roles) or (role in only) != (role in rules.only_roles)
    ]
    if set(domains) != set(rules.domains):
        changed = list(google_rules.ROLES)
    if changed:
        google_rules.sign_out_linked(conn, user.customer_id, changed, keep=user.user_id)


@router.patch("/admin/settings")
def update_settings(body: SettingsBody, user: AdminSession, context: Context) -> dict[str, Any]:
    given = body.model_fields_set
    if "auto_approve_connections" in given and not _providers_on(context):
        raise ApiError(400, "bad_request", "Signing in with Google isn't switched on for Kaagaz yet.")
    platform = context.settings.max_upload_bytes // MIB
    if body.max_upload_mb is not None and body.max_upload_mb > platform:
        raise ApiError(400, "bad_request", f"The limit can be at most {platform} MB.")
    wanted = None if body.google is None else _wanted_rules(body.google)
    try:
        return _update_settings(body, user, context, wanted)
    except _Lockout as refused:  # the whole PATCH was rolled back; the refusal is kept on its own
        with context.db.user(user.customer_id, user.user_id) as conn:
            _audit_settings(conn, user, "settings.google_rules_refused", {"reason": refused.reason})
        raise ApiError(409, "google_lockout", LOCKOUT[refused.reason]) from None


def _update_settings(
    body: SettingsBody,
    user: CurrentUser,
    context: AppContext,
    wanted: tuple[list[str], list[str], list[str]] | None,
) -> dict[str, Any]:
    """Every field given, in one transaction."""
    given = body.model_fields_set
    with context.db.user(user.customer_id, user.user_id) as conn:
        before = admin_sql.get_settings(conn, user.customer_id)
        if before is None:
            raise ApiError(404, "not_found")
        if wanted is not None:
            _save_google(conn, context, user, wanted)
        if "max_upload_mb" in given:
            admin_sql.update_settings(conn, user.customer_id, max_upload_mb=body.max_upload_mb)
            _audit_settings(conn, user, "settings.update", {"max_upload_mb": body.max_upload_mb})
        if "autofile_on" in given and body.autofile_on != before["autofile_on"]:
            if not admin_sql.set_autofile(conn, user.customer_id, on=body.autofile_on):
                # Raised inside the transaction: a new upload limit in the same PATCH is not kept.
                raise ApiError(409, "not_granted", "Auto-filing isn't available for your organisation yet.")
            action = "settings.autofile_on" if body.autofile_on else "settings.autofile_off"
            _audit_settings(conn, user, action)
        on = body.auto_approve_connections
        if "auto_approve_connections" in given and on != before["auto_approve_connections"]:
            admin_sql.set_auto_approve(conn, user.customer_id, on=on)
            _audit_settings(conn, user, "settings.auto_approve_on" if on else "settings.auto_approve_off")
        row = admin_sql.get_settings(conn, user.customer_id)
        google = _google_json(conn, context, user)
    if row is None:
        raise ApiError(404, "not_found")
    return _settings_json(row, context, google)


def _audit_settings(
    conn: Connection, user: CurrentUser, action: str, details: dict[str, audit.DetailValue] | None = None
) -> None:
    audit.record(
        conn,
        user.customer_id,
        actor_user_id=user.user_id,
        action=action,
        target_type="customer",
        target_id=user.customer_id,
        details=details,
    )


@dataclass(frozen=True, slots=True)
class AuditAsked:
    """File management A5's filter chips: a person, a folder (with its subfolders), a file
    (every version of its family), actions, and days in TIMEZONE, both ends included; PRD 7
    (#601): the person an entry is about (``subject_id``)."""

    user_id: uuid.UUID | None = None
    subject_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    action: Annotated[list[str] | None, Query(max_length=MAX_AUDIT_ACTIONS)] = None
    first: UsageFrom = None
    to: UsageTo = None


def _audit_filters(
    conn: Connection, context: AppContext, user: CurrentUser, asked: AuditAsked
) -> audit.Filters:
    """The whole log (never ``shown_only``) narrowed by ``asked``. Another customer's folder
    or file matches nothing (row level security): 0 rows, never 404. Bad actions or days: 400."""
    actions = tuple(dict.fromkeys(asked.action or ()))
    if not all(_AUDIT_ACTION.match(action) for action in actions):
        raise ApiError(400, "bad_request", "Choose actions from the list.")
    zone = context.settings.timezone
    try:
        start = None if asked.first is None else day_start(asked.first, zone)
        end = None if asked.to is None else day_start(asked.to, zone) + timedelta(days=1)
    except ValueError:
        raise ApiError(400, "bad_request", "Use real dates, written YYYY-MM-DD.") from None
    folders = None
    if asked.folder_id is not None:
        folders = tuple(folders_sql.descendant_ids(conn, user.customer_id, str(asked.folder_id)))
    family = document = None
    if asked.document_id is not None:
        # PRD 7 (#601): a file the Admin can't open filters by its whole family too (decision 20).
        row = access_sql.admin_document(conn, str(asked.document_id))
        family, document = (None, str(asked.document_id)) if row is None else (row.family_id, None)
    return audit.Filters(
        shown_only=False,
        family_id=family,
        folder_ids=folders,
        actor_user_id=None if asked.user_id is None else str(asked.user_id),
        subject_user_id=None if asked.subject_id is None else str(asked.subject_id),
        document_id=document,
        actions=actions,
        start=start,
        end=end,
    )


@router.get("/admin/audit")
def list_audit(
    user: AdminSession,
    context: Context,
    asked: Annotated[AuditAsked, Depends()],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    cursor: Annotated[str | None, Query(pattern=CURSOR)] = None,
) -> dict[str, Any]:
    """``{items: [AuditEntry], next_cursor, total}``, newest first: the next page is
    ``cursor=next_cursor`` (``offset`` still pages as before). Every action, every detail
    but money: the admin's alone. PRD 7 (#601): the first page also has ``self_given``, the
    newest ``limit`` entries the filters keep in which an Admin gave themselves access, shown
    first ("Admin gave themselves access")."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        filters = _audit_filters(conn, context, user, asked)
        page = timeline(conn, user.customer_id, filters, limit=limit, cursor=cursor, offset=offset, full=True)
        reply = {**page, "total": audit.count(conn, user.customer_id, filters)}
        if cursor is None and offset == 0:
            mine = replace(filters, self_only=True)
            reply["self_given"] = timeline(conn, user.customer_id, mine, limit=limit, full=True)["items"]
        return reply


@router.get("/admin/audit/export")
def export_audit(
    user: AdminSession, context: Context, asked: Annotated[AuditAsked, Depends()]
) -> StreamingResponse:
    """File management A6: the filtered rows as CSV (AUDIT_CSV_COLUMNS), newest first,
    streamed AUDIT_EXPORT_PAGE rows at a time, each page its own short transaction. The
    export itself is logged first as ``audit.export``, with the filters' ids."""
    customer_id = user.customer_id
    with context.db.user(customer_id, user.user_id) as conn:
        filters = _audit_filters(conn, context, user, asked)
        details: dict[str, audit.DetailValue] = {
            "user_id": None if asked.user_id is None else str(asked.user_id),
            "subject_id": None if asked.subject_id is None else str(asked.subject_id),
            "folder_id": None if asked.folder_id is None else str(asked.folder_id),
            "document_id": None if asked.document_id is None else str(asked.document_id),
            "actions": len(filters.actions),
            "from": asked.first,
            "to": asked.to,
        }
        audit.record(
            conn,
            customer_id,
            actor_user_id=user.user_id,
            action="audit.export",
            target_type="customer",
            target_id=customer_id,
            details=details,
        )

    def pages() -> Iterator[str]:
        yield _csv_text([AUDIT_CSV_COLUMNS])
        cursor = None
        while True:
            with context.db.user(customer_id, user.user_id) as conn:
                page = timeline(conn, customer_id, filters, limit=AUDIT_EXPORT_PAGE, cursor=cursor, full=True)
            yield _csv_text(_audit_line(entry) for entry in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                return

    return StreamingResponse(
        pages(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="activity.csv"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


def _audit_line(entry: dict[str, Any]) -> list[object]:
    document, folder, details = entry["document"], entry["folder"], entry["details"]
    return [
        entry["created_at"],
        entry["actor_name"] or "",
        entry["action"],
        "" if document is None else document["name"],
        "" if folder is None else folder["path"] or "",
        json.dumps(details, ensure_ascii=False, sort_keys=True) if details else "",
    ]


def _csv_text(lines: Iterable[Iterable[object]]) -> str:
    """``lines`` as CSV, each cell made safe (``csv_cell``). O(cells)."""
    out = io.StringIO()
    csv.writer(out).writerows([csv_cell(value) for value in line] for line in lines)
    return out.getvalue()


def month_window(now: datetime, timezone: str) -> tuple[datetime, datetime]:
    """The first instant of this month and of the next, in ``timezone`` (the
    customer's TIMEZONE, never the server's), for ``now``. O(1)."""
    local = now.astimezone(ZoneInfo(timezone))
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, following


@router.get("/admin/stats")
def get_stats(user: AdminSession, context: Context) -> dict[str, Any]:
    """The admin numbers, with the pages read this month by route (PRD 2 R6), by who read
    them and the scan tokens (PRD 5 U9). No money: org admins never see it (Tushar,
    15 Oct 2026). ``reader_pages.gemini`` keeps its name but, with model choice, counts the
    pages read by any AI model (the run's model), so the screen may call it "Read by AI
    model"."""
    start, end = month_window(context.clock(), context.settings.timezone)
    with context.db.user(user.customer_id, user.user_id) as conn:
        numbers = admin_sql.stats(conn, user.customer_id)
        numbers["pages_this_month"] = runs_sql.pages_by_route(conn, user.customer_id, start=start, end=end)
        reader = runs_sql.reader_numbers(conn, user.customer_id, start=start, end=end)
    numbers["scan_tokens"] = {
        "input_tokens": reader.pop("input_tokens"),
        "output_tokens": reader.pop("output_tokens"),
    }
    numbers["reader_pages"] = reader
    return numbers


MemberPeriod = Literal["month", "30d", "all"]
MEMBER_DAYS = 30


@router.get("/admin/storage")
def get_storage(user: AdminSession, context: Context) -> dict[str, Any]:
    """#516: ``{used_bytes, limit_bytes, limit_gb}`` of the admin's own organisation."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        room = storage_limits.storage_room(conn, user.customer_id)
    return {
        "used_bytes": room.used_bytes,
        "limit_bytes": room.limit_bytes,
        "limit_gb": room.limit_bytes // storage_limits.GIB,
    }


@router.get("/admin/members")
def get_member_numbers(
    user: AdminSession, context: Context, period: MemberPeriod = "month"
) -> dict[str, Any]:
    """#386: each member of the admin's own organisation with what they did in the period (this
    month, the last 30 days or all time, in TIMEZONE): files uploaded, pages of them read,
    questions asked, values verified, and when they were last active. No money and no tokens.
    ``from`` is the first day counted (null for all time), ``to`` today. One query."""
    zone = context.settings.timezone
    now = context.clock()
    today = now.astimezone(ZoneInfo(zone)).date()
    start: datetime | None = None
    end: datetime | None = None
    if period == "month":
        start, end = month_window(now, zone)
    elif period == "30d":
        start = day_start((today - timedelta(days=MEMBER_DAYS - 1)).isoformat(), zone)
        end = day_start((today + timedelta(days=1)).isoformat(), zone)
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows = admin_sql.member_numbers(conn, user.customer_id, start=start, end=end)
    return {
        "period": period,
        "from": None if start is None else start.date().isoformat(),
        "to": today.isoformat(),
        "members": [member_numbers(row) for row in rows],
    }


def member_numbers(row: RowMapping) -> dict[str, Any]:
    """``MemberNumbers``: one member's row, counts as whole numbers."""
    last: datetime | None = row["last_active_at"]
    return {
        "user_id": str(row["id"]),
        "display_name": row["display_name"],
        "email": row["email"],
        "role": row["role"],
        "disabled": bool(row["disabled"]),
        **{
            key: int(row[key])
            for key in ("documents_uploaded", "pages_read", "questions_asked", "values_verified")
        },
        "last_active_at": None if last is None else last.isoformat(),
    }


# --- AI models and use (model choice, council 11 Oct 2026) ---------------------------


@router.get("/admin/models")
def get_models(user: AdminSession, context: Context) -> dict[str, Any]:
    """``OrgModels``, read-only (MS1, MS2): the names of the models that read this
    organisation's scans and answer its questions, the default marked. Names only, no
    prices or budget (Tushar, 15 Oct 2026). One query."""
    settings = context.settings
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows = usable(conn, user.customer_id, settings)
    default = default_scan(rows, settings)
    scan = [{"name": row["name"], "default": str(row["id"]) == default} for row in rows]
    if not scan and default is not None:  # no models set: the platform's model reads (MS1)
        scan = [{"name": default, "default": True}]
    chat = [{"name": settings.model_chat, "default": True}] if settings.model_enabled else []
    return {"scan": scan, "chat": chat}


def default_scan(rows: Sequence[RowMapping], settings: Settings) -> str | None:
    """The model that reads this organisation's scans by default: its default while usable,
    else the platform's, OCR_MODEL (what ``db.models.resolve`` falls back to); None in keyless
    mode, where no model reads scans (R14)."""
    if settings.ocr_engine != GEMINI:
        return None
    return next((str(row["id"]) for row in rows if row["is_default_scan"]), settings.ocr_model)


def usage_days(first: str | None, last: str | None, today: date) -> tuple[date, date]:
    """The report's days, both included: this month to ``today`` when left out. 400 for a
    date that isn't real, a start after the end, or more than USAGE_MAX_DAYS days. O(1)."""
    try:
        start = today.replace(day=1) if first is None else date.fromisoformat(first)
        end = today if last is None else date.fromisoformat(last)
    except ValueError:
        raise ApiError(400, "bad_request", "Use real dates, written YYYY-MM-DD.") from None
    if start > end:
        raise ApiError(400, "bad_request", "The start date must not be after the end date.")
    if (end - start).days + 1 > USAGE_MAX_DAYS:
        raise ApiError(400, "bad_request", f"Choose at most {USAGE_MAX_DAYS} days.")
    return start, end


def _usage_window(
    context: AppContext, first: str | None, last: str | None
) -> tuple[date, date, datetime, datetime]:
    """The days and the instants ``[start, end)`` they cover, in TIMEZONE."""
    zone = context.settings.timezone
    start, end = usage_days(first, last, context.clock().astimezone(ZoneInfo(zone)).date())
    return (
        start,
        end,
        day_start(start.isoformat(), zone),
        day_start((end + timedelta(days=1)).isoformat(), zone),
    )


@router.get("/admin/usage")
def get_usage(
    user: AdminSession,
    context: Context,
    first: UsageFrom = None,
    to: UsageTo = None,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """``UsageReport`` (SP2): the organisation's AI use in the period, for one user when
    given, in tokens: the ledger's costs are left out (Tushar, 15 Oct 2026). ``ai_enabled``
    is false in keyless mode (no AI scan reader, or MODEL_ENABLED off): the screen says AI
    is off on this server. ``reading_today`` (#478): the pages AI read today (the India day) and
    the daily page limit in effect (null: none), never money, in one cheap query (no month's
    sums). Two queries."""
    customer_id = user.customer_id
    day_from, day_to, start, end = _usage_window(context, first, to)
    with context.db.user(customer_id, user.user_id) as conn:
        report = usage_sql.usage_report(
            conn,
            customer_id,
            start=start,
            end=end,
            timezone=context.settings.timezone,
            user_id=None if user_id is None else str(user_id),
        )
        pages, limit = reading_days.today(conn, customer_id, reading_days.india_day(context.clock()))
    return {
        "totals": without_money(report.pop("totals")),
        **{part: [without_money(group) for group in groups] for part, groups in report.items()},
        "from": day_from.isoformat(),
        "to": day_to.isoformat(),
        "ai_enabled": ai_enabled(context.settings),
        "reading_today": {"pages": pages, "limit": limit},
    }


def ai_enabled(settings: Settings) -> bool:
    """Whether this server calls AI models at all: scans read by a model, or MODEL_ENABLED
    (chat, cards, search). False only in keyless mode."""
    return settings.ocr_engine == GEMINI or settings.model_enabled


@router.get("/admin/usage.csv")
def get_usage_csv(
    user: AdminSession,
    context: Context,
    first: UsageFrom = None,
    to: UsageTo = None,
    user_id: uuid.UUID | None = None,
) -> Response:
    """The same period as a CSV file to download (SP2): one line per day, user, model and
    kind, with USAGE_CSV_COLUMNS. One query, and one ``usage.export`` audit row with the
    period and the person filtered on (#482)."""
    day_from, day_to, start, end = _usage_window(context, first, to)
    with context.db.user(user.customer_id, user.user_id) as conn:
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="usage.export",
            target_type="customer",
            target_id=user.customer_id,
            details={
                "from": day_from.isoformat(),
                "to": day_to.isoformat(),
                "user_id": None if user_id is None else str(user_id),
            },
        )
        rows = listing.usage_rows(
            conn,
            user.customer_id,
            start=start,
            end=end,
            timezone=context.settings.timezone,
            user_id=None if user_id is None else str(user_id),
        )
    lines = (
        (
            row["day"].isoformat(),
            row["user_name"] or "",
            row["model"],
            row["kind"],
            row["calls"],
            row["tokens"],
        )
        for row in rows
    )
    return Response(
        content=usage_csv(lines),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="ai-usage-{day_from}-to-{day_to}.csv"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


def usage_csv(lines: Iterable[Iterable[object]]) -> str:
    """USAGE_CSV_COLUMNS then ``lines``, each cell made safe (``csv_cell``). O(cells)."""
    return _csv_text([USAGE_CSV_COLUMNS, *lines])


def csv_cell(value: object) -> str:
    """The cell's text, with a leading ``'`` when it starts like a formula (CSV injection)."""
    cell = str(value)
    return "'" + cell if cell.startswith(_FORMULA_STARTS) else cell


@router.get("/admin/failed")
def list_failed(
    user: AdminSession,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Documents that failed or can't be read, with their message and last run (PRD 3 Z1)."""
    with context.db.user(user.customer_id, user.user_id) as conn:
        rows, total = admin_sql.failed_documents(conn, user.customer_id, limit=limit, offset=offset)
    max_pages = context.settings.max_pages
    return {"items": [failed_document(row, max_pages=max_pages) for row in rows], "total": total}
