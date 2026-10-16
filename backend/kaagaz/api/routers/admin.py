"""The admin routes: users, the customer's settings, the audit log, the numbers and
the failed jobs (PRD 3 Z1; Retry is the documents router's reprocess route), and the
organisation's AI models and use in tokens (model choice, council 11 Oct 2026).

Why the admin session: every route here needs an admin's session (scope "admin": an
admin's sign-in at /login, always with the two-step code since 0035). The check sits on
the router itself (``require_admin_session``), so a route added later cannot forget it;
anyone else gets 403 ``admin_session_required``.

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

Why auto-filing is a setting here (feature 28, council 15 Oct 2026): the platform operator
grants it, then the admin switches it on or off (``autofile_on``) in the same form as the
upload limit. Each field left out of the PATCH is left as it is. Switching it on without
the grant is 409 ``not_granted`` and changes nothing; a real change is audited as
``settings.autofile_on`` or ``settings.autofile_off``.
"""

import csv
import io
import re
import secrets
import uuid
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, RowMapping

from kaagaz.api.auth import AdminSession, CurrentUser, hash_password, require_admin_session
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.api.readers import usable
from kaagaz.api.routers.documents import day_start
from kaagaz.api.views import admin_user, audit_entry, failed_document, without_money
from kaagaz.db import admin as admin_sql
from kaagaz.db import audit, listing
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

USAGE_MAX_DAYS = 366
USAGE_CSV_COLUMNS = ("day", "user", "model", "kind", "calls", "tokens")
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
UsageFrom = Annotated[str | None, Query(alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$")]
UsageTo = Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")]


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


@router.put("/admin/users/{user_id}/delete-forever")
def set_delete_forever(
    user_id: uuid.UUID, body: DeleteForeverBody, user: AdminSession, context: Context
) -> dict[str, Any]:
    """Allow or stop a member's or reviewer's Delete forever. Audited with ids only."""
    if body.allowed and not user.totp_enabled:
        raise ApiError(403, "two_step_required", "Turn on two-step sign-in in Admin, Settings to allow this.")
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
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(USAGE_CSV_COLUMNS)
    writer.writerows([csv_cell(value) for value in line] for line in lines)
    return out.getvalue()


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
