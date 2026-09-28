"""The admin routes: users, the customer's settings, the audit log and the numbers.

Why the self-protection rule: an admin cannot disable themselves or take away
their own admin role, so a customer can never lock out its last way in by
accident. Every change writes one audit row with ids and statuses only (never
a password, hashed or not).

A role or disabled change takes effect on the user's next request, because
every request re-checks the user in the database (kaagaz.api.auth).
"""

import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import RowMapping

from kaagaz.api.auth import Admin, hash_password
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.views import admin_user, audit_entry
from kaagaz.db import admin as admin_sql
from kaagaz.db import audit
from kaagaz.db import users as users_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MIB = 1024 * 1024
MIN_PASSWORD = 12
MAX_PASSWORD = 1024
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
def list_users(user: Admin, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        rows = users_sql.list_users(conn, user.customer_id)
    return {"items": [admin_user(row) for row in rows]}


@router.post("/admin/users", status_code=201)
def create_user(body: NewUserBody, user: Admin, context: Context) -> JSONResponse:
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
    return JSONResponse(admin_user(row), status_code=201)


@router.patch("/admin/users/{user_id}")
def update_user(user_id: uuid.UUID, body: UserChangesBody, user: Admin, context: Context) -> dict[str, Any]:
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
    return admin_user(row)


def _settings_json(row: RowMapping, context: AppContext) -> dict[str, Any]:
    return {
        "customer_name": row["name"],
        "max_upload_mb": row["max_upload_mb"],
        "platform_max_upload_mb": context.settings.max_upload_bytes // MIB,
    }


@router.get("/admin/settings")
def get_settings(user: Admin, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        row = admin_sql.get_settings(conn, user.customer_id)
    if row is None:
        raise ApiError(404, "not_found")
    return _settings_json(row, context)


@router.patch("/admin/settings")
def update_settings(body: SettingsBody, user: Admin, context: Context) -> dict[str, Any]:
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
    user: Admin,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        rows, total = audit.list_entries(conn, user.customer_id, limit=limit, offset=offset)
    return {"items": [audit_entry(row) for row in rows], "total": total}


@router.get("/admin/stats")
def get_stats(user: Admin, context: Context) -> dict[str, Any]:
    with context.db.customer(user.customer_id) as conn:
        return admin_sql.stats(conn, user.customer_id)
