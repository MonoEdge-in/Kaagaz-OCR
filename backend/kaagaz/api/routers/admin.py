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

Why auto-filing is a setting here (feature 28, council 15 Oct 2026): the platform operator
grants it, then the admin switches it on or off (``autofile_on``) in the same form as the
upload limit. Each field left out of the PATCH is left as it is. Switching it on without
the grant is 409 ``not_granted`` and changes nothing; a real change is audited as
``settings.autofile_on`` or ``settings.autofile_off``.
"""

import csv
import io
import json
import re
import secrets
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
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
from kaagaz.db import admin as admin_sql
from kaagaz.db import audit, identities, listing
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


class SettingsBody(BaseModel):
    """Left out: unchanged (``model_fields_set``). An explicit null limit: the platform's."""

    model_config = ConfigDict(extra="forbid")

    max_upload_mb: int | None = Field(default=None, ge=1)
    autofile_on: bool = False
    auto_approve_connections: bool = False


@router.get("/admin/users")
def list_users(user: AdminSession, context: Context) -> dict[str, Any]:
    on = _providers_on(context)
    with context.db.customer(user.customer_id) as conn:
        rows = users_sql.list_users(conn, user.customer_id)
        connected = identities.providers_by_user(conn, user.customer_id) if on else {}
    now = context.clock()
    return {
        "items": [
            admin_user(
                row,
                now=now,
                signs_in_with=PASSWORD_ONLY + [p for p in connected.get(str(row["id"]), []) if p in on],
            )
            for row in rows
        ]
    }


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
        with context.db.customer(user.customer_id) as conn:
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
    except users_sql.EmailTaken:
        raise ApiError(409, "conflict", "That e-mail is already in use.") from None
    if link is None:
        return JSONResponse(admin_user(row, now=context.clock()), status_code=201)
    invited = emails.send(context, email, "invite", emails.invite(user.customer_name, link))
    if invited:  # the one follow-up write, only when the email went
        with context.db.customer(user.customer_id) as conn:
            _audit_user(conn, user, "auth.invite_sent", target)
    return JSONResponse({**admin_user(row, now=context.clock()), "invited": invited}, status_code=201)


@router.patch("/admin/users/{user_id}")
def update_user(
    user_id: uuid.UUID, body: UserChangesBody, user: AdminSession, context: Context
) -> dict[str, Any]:
    target = str(user_id)
    if target == user.user_id and (body.disabled is True or (body.role is not None and body.role != "admin")):
        raise ApiError(400, "bad_request", "You can't disable yourself or remove your own admin role.")
    password_hash = None if body.password is None else hash_password(body.password)
    with context.db.customer(user.customer_id) as conn:
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
    return admin_user(row, now=context.clock())


@router.post("/admin/users/{user_id}/unlock")
def unlock_user(user_id: uuid.UUID, user: AdminSession, context: Context) -> dict[str, Any]:
    """End a lock early; the count of wrong passwords starts again at 0."""
    target = str(user_id)
    with context.db.customer(user.customer_id) as conn:
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
    with context.db.customer(user.customer_id) as conn:
        row = users_sql.set_password(
            conn, user.customer_id, target, password_hash=password_hash, must_change=True
        )
        if row is None:
            raise ApiError(404, "not_found")
        _audit_user(conn, user, "user.reset_password", target)
        identities.remove_all(conn, user.customer_id, target)  # #457: resetting a user clears them
        links_sql.void_all(conn, user.customer_id, target, now=context.clock())
    reply = admin_user(row, now=context.clock(), signs_in_with=PASSWORD_ONLY)
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
    with context.db.customer(user.customer_id) as conn:
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
    with context.db.customer(user.customer_id) as conn:
        row = users_sql.set_delete_forever(
            conn, user.customer_id, target, allowed=body.allowed, now=context.clock()
        )
        if row is None:
            if users_sql.role_of(conn, user.customer_id, target) is None:
                raise ApiError(404, "not_found")
            raise ApiError(400, "bad_request", "Admins can already delete forever with two-step sign-in.")
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
    with context.db.customer(user.customer_id) as conn:
        return _pending_json(conn, user.customer_id)


@router.post("/admin/identities/{identity_id}/approve")
def approve_identity(
    identity_id: uuid.UUID, user: AdminSession, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    """A waiting connection signs in from now on, unless its email became another user's sign-in
    email meanwhile (409, the one outside account rule). The person stays signed in (approval
    only adds a way in) and gets a notice. The list left."""
    _connections_on(context)
    target = str(identity_id)
    with context.db.customer(user.customer_id) as conn:
        waiting = next(
            (row for row in identities.pending(conn, user.customer_id) if str(row["id"]) == target), None
        )
    if waiting is None:
        raise ApiError(404, "not_found")
    email = waiting["email_at_link"]
    with context.db.system() as conn:
        taken = email is not None and identities.email_belongs_to_other(conn, email, str(waiting["user_id"]))
    if taken:
        raise ApiError(409, "conflict", ALREADY_BELONGS)
    with context.db.customer(user.customer_id) as conn:
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


@router.post("/admin/identities/{identity_id}/decline")
def decline_identity(
    identity_id: uuid.UUID, user: AdminSession, background: BackgroundTasks, context: Context
) -> dict[str, Any]:
    """A waiting connection is removed; the person gets a notice. The list left."""
    _connections_on(context)
    with context.db.customer(user.customer_id) as conn:
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
    with context.db.customer(user.customer_id) as conn:
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
    if count:
        who = (context, user.customer_id, target)
        notice = emails.google_disconnected
        emails.after(background, "identity_unlinked", emails.notify, *who, "identity_unlinked", notice)
    return admin_user(row, now=context.clock(), signs_in_with=PASSWORD_ONLY)


def _audit_identity(conn: Connection, user: CurrentUser, action: str, row: RowMapping) -> None:
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


def _settings_json(row: RowMapping, context: AppContext) -> dict[str, Any]:
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
    }


@router.get("/admin/settings")
def get_settings(user: AdminSession, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        row = admin_sql.get_settings(conn, user.customer_id)
    if row is None:
        raise ApiError(404, "not_found")
    return _settings_json(row, context)


@router.patch("/admin/settings")
def update_settings(body: SettingsBody, user: AdminSession, context: Context) -> dict[str, Any]:
    given = body.model_fields_set
    if "auto_approve_connections" in given and not _providers_on(context):
        raise ApiError(400, "bad_request", "Signing in with Google isn't switched on for Kaagaz yet.")
    platform = context.settings.max_upload_bytes // MIB
    if body.max_upload_mb is not None and body.max_upload_mb > platform:
        raise ApiError(400, "bad_request", f"The limit can be at most {platform} MB.")
    with context.db.customer(user.customer_id) as conn:
        before = admin_sql.get_settings(conn, user.customer_id)
        if before is None:
            raise ApiError(404, "not_found")
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
    if row is None:
        raise ApiError(404, "not_found")
    return _settings_json(row, context)


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
    (every version of its family), actions, and days in TIMEZONE, both ends included."""

    user_id: uuid.UUID | None = None
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
        row = listing.get_document(conn, user.customer_id, str(asked.document_id))
        family, document = (None, str(asked.document_id)) if row is None else (str(row["family_id"]), None)
    return audit.Filters(
        shown_only=False,
        family_id=family,
        folder_ids=folders,
        actor_user_id=None if asked.user_id is None else str(asked.user_id),
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
    but money: the admin's alone."""
    with context.db.customer(user.customer_id) as conn:
        filters = _audit_filters(conn, context, user, asked)
        page = timeline(conn, user.customer_id, filters, limit=limit, cursor=cursor, offset=offset, full=True)
        return {**page, "total": audit.count(conn, user.customer_id, filters)}


@router.get("/admin/audit/export")
def export_audit(
    user: AdminSession, context: Context, asked: Annotated[AuditAsked, Depends()]
) -> StreamingResponse:
    """File management A6: the filtered rows as CSV (AUDIT_CSV_COLUMNS), newest first,
    streamed AUDIT_EXPORT_PAGE rows at a time, each page its own short transaction. The
    export itself is logged first as ``audit.export``, with the filters' ids."""
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        filters = _audit_filters(conn, context, user, asked)
        details: dict[str, audit.DetailValue] = {
            "user_id": None if asked.user_id is None else str(asked.user_id),
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
            with context.db.customer(customer_id) as conn:
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
    with context.db.customer(user.customer_id) as conn:
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
    with context.db.customer(user.customer_id) as conn:
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
    with context.db.customer(user.customer_id) as conn:
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
    is off on this server. One query."""
    customer_id = user.customer_id
    day_from, day_to, start, end = _usage_window(context, first, to)
    with context.db.customer(customer_id) as conn:
        report = usage_sql.usage_report(
            conn,
            customer_id,
            start=start,
            end=end,
            timezone=context.settings.timezone,
            user_id=None if user_id is None else str(user_id),
        )
    return {
        "totals": without_money(report.pop("totals")),
        **{part: [without_money(group) for group in groups] for part, groups in report.items()},
        "from": day_from.isoformat(),
        "to": day_to.isoformat(),
        "ai_enabled": ai_enabled(context.settings),
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
    kind, with USAGE_CSV_COLUMNS. One query."""
    day_from, day_to, start, end = _usage_window(context, first, to)
    with context.db.customer(user.customer_id) as conn:
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
    with context.db.customer(user.customer_id) as conn:
        rows, total = admin_sql.failed_documents(conn, user.customer_id, limit=limit, offset=offset)
    max_pages = context.settings.max_pages
    return {"items": [failed_document(row, max_pages=max_pages) for row in rows], "total": total}
