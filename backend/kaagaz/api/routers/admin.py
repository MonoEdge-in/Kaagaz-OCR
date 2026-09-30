"""The admin routes: users, the customer's settings, the audit log and the numbers.

Why the admin session: every route here needs a session started on the admin
screen (/admin/login, with the two-step code when it is on). The check sits on the router
itself (``require_admin_session``), so a route added later cannot forget it;
an admin's own app session from /login gets 403 ``admin_session_required``.

Why the self-protection rules: an admin cannot disable themselves, take away
their own admin role or reset their own two-step code, so a customer can never
lock out its last way in by accident (the CLI ``reset-admin-2fa`` is the way
back for the last admin). Every change writes one audit row with ids and
statuses only (never a password, hashed or not).

Why the session version: disabling a user, setting their password, resetting
their password or their two-step code all raise ``session_version`` (in
``kaagaz.db.users``), so every session of theirs ends at its next request. A
password an admin sets is temporary: the user must choose a new one first.
A role change raises it too: every request re-checks the role, but a user
demoted and later promoted again must not get their old sessions back.
"""

import re
import secrets
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, RowMapping

from kaagaz.api.auth import AdminSession, CurrentUser, hash_password, require_admin_session
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.views import admin_user, audit_entry
from kaagaz.db import admin as admin_sql
from kaagaz.db import audit
from kaagaz.db import runs as runs_sql
from kaagaz.db import users as users_sql

router = APIRouter(dependencies=[Depends(require_admin_session)])

Context = Annotated[AppContext, Depends(get_context)]

MIB = 1024 * 1024
MIN_PASSWORD = 12
MAX_PASSWORD = 1024
TEMPORARY_PASSWORD_BYTES = 12  # secrets.token_urlsafe gives 16 characters
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

RoleName = Literal["member", "reviewer", "admin"]


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


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_upload_mb: int | None = Field(ge=1)


@router.get("/admin/users")
def list_users(user: AdminSession, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        rows = users_sql.list_users(conn, user.customer_id)
    now = context.clock()
    return {"items": [admin_user(row, now=now) for row in rows]}


@router.post("/admin/users", status_code=201)
def create_user(body: NewUserBody, user: AdminSession, context: Context) -> JSONResponse:
    email = valid_email(body.email)
    name = body.display_name.strip()
    if not name:
        raise ApiError(400, "bad_request", "Enter a name.")
    password_hash = hash_password(body.password)
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
    except users_sql.EmailTaken:
        raise ApiError(409, "conflict", "That e-mail is already in use.") from None
    return JSONResponse(admin_user(row, now=context.clock()), status_code=201)


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
    return {"user": admin_user(row, now=context.clock()), "temporary_password": temporary}


@router.post("/admin/users/{user_id}/reset-2fa")
def reset_two_step(user_id: uuid.UUID, user: AdminSession, context: Context) -> dict[str, Any]:
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
        # PROVISIONAL: "user.reset_totp", not the plan's "user.reset_2fa", because audit
        # actions must match ^[a-z_.]{1,64}$ (PRD 1 A7, the audit_log CHECK): no digits.
        _audit_user(conn, user, "user.reset_totp", target)
    return admin_user(row, now=context.clock())


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
    platform = context.settings.max_upload_bytes // MIB
    if body.max_upload_mb is not None and body.max_upload_mb > platform:
        raise ApiError(400, "bad_request", f"The limit can be at most {platform} MB.")
    with context.db.customer(user.customer_id) as conn:
        row = admin_sql.update_settings(conn, user.customer_id, max_upload_mb=body.max_upload_mb)
        if row is None:
            raise ApiError(404, "not_found")
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="settings.update",
            target_type="customer",
            target_id=user.customer_id,
            details={"max_upload_mb": body.max_upload_mb},
        )
    return _settings_json(row, context)


@router.get("/admin/audit")
def list_audit(
    user: AdminSession,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        rows, total = audit.list_entries(conn, user.customer_id, limit=limit, offset=offset)
    return {"items": [audit_entry(row) for row in rows], "total": total}


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
    """The admin numbers, with the pages read this month by route (PRD 2 R6)."""
    start, end = month_window(context.clock(), context.settings.timezone)
    with context.db.customer(user.customer_id) as conn:
        numbers = admin_sql.stats(conn, user.customer_id)
        numbers["pages_this_month"] = runs_sql.pages_by_route(conn, user.customer_id, start=start, end=end)
    return numbers
