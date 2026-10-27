"""The platform operator's insights (council 11 Oct 2026, F1-F4). Every route needs the
operator cookie (``current_operator`` on the router); only ``OperatorDatabase`` here, and
each read is one read-only ``kaagaz_op_*`` function (``kaagaz.db.operator_insights``,
0024). Reads write no ``platform_audit`` row. Days are in TIMEZONE; ``days`` is 1 to 90
(30 by default) and ends today.

* ``GET /operator/insights?days=``: ``INSIGHTS_KEYS``. ``month`` is {from, today,
  days_in_month}; ``organisations`` are F2 rows (``ORG_HEALTH_KEYS``); ``daily`` are F1
  rows (``DAILY_KEYS``) from ``previous_from`` (twice ``days``), or the month's first day
  when that is earlier, to ``to``: this month's real spend and its forecast are summed
  from them (Tushar, 16 Oct 2026).
* ``GET /operator/quality?days=``: ``QUALITY_REPLY_KEYS``; ``platform`` and each of
  ``organisations`` are F3 rows (``QUALITY_KEYS``; the platform's ``organisation_id`` is null).
* ``GET /operator/model-board?days=``: ``BOARD_REPLY_KEYS``; ``items`` are F4 rows
  (``BOARD_KEYS``).
* ``GET /operator/answer-weeks?weeks=`` (PRD 6 F3, 1 to 12, 4 by default): ``from``, ``to``
  and ``organisations``, rows of ``ANSWER_WEEK_KEYS`` per organisation and ended ISO week
  (0051's ``kaagaz_op_answer_weeks``): counts only, outcomes NULL below K with their complement.
* ``GET /operator/org-numbers?days=`` (#387, 0055): ``from``, ``to``, ``ai_enabled`` and
  ``organisations``, rows of ``ORG_NUMBERS_KEYS``; ``pages_by_route`` and ``failed_documents``
  are {route or reason: count}, every route counted under its own name.

Organisation rows say ``organisation_id`` for the function's ``customer_id``. Decimals are
sent as numbers, NULL (suppressed below K, or not known) as null. Never a user, document,
name or text. No row and a disabled operator: 401, as on ``/operator/activity``.

Each reply sends only its key set's columns, so a column the function gains later stays
unsent until the key set names it. ``ai_enabled`` is the admin usage route's (false in
keyless mode). Two function calls on ``/insights``, one on the others.
"""

import calendar
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, RowMapping

from kaagaz.api.auth import OperatorSession, current_operator
from kaagaz.api.context import AppContext, get_context, get_operator_db
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.admin import ai_enabled
from kaagaz.db import operator_insights as insights_sql
from kaagaz.db import operators as operators_sql
from kaagaz.db.engine import OperatorDatabase

router = APIRouter(prefix="/operator", dependencies=[Depends(current_operator)])

Context = Annotated[AppContext, Depends(get_context)]
OperatorDb = Annotated[OperatorDatabase, Depends(get_operator_db)]
# Checked before the database is touched: 400 outside 1 to 90.
Days = Annotated[int, Query(ge=1, le=90)]

INSIGHTS_KEYS = frozenset({"from", "to", "previous_from", "ai_enabled", "month", "organisations", "daily"})
ORG_HEALTH_KEYS = frozenset(
    {
        "organisation_id",
        "active_users_7d",
        "active_users_30d",
        "active_users_prev_30d",
        "answers_30d",
        "not_found_30d",
        "open_review",
        "oldest_open_review_at",
        "ai_budget_usd",
        "month_cost_usd",
        "month_cost_estimated",
        "month_cost_part_unknown",
        "last_active_day",
    }
)
DAILY_KEYS = frozenset(
    {
        "organisation_id",
        "day",
        "uploads",
        "sign_ins",
        "questions",
        "corrections",
        "runs",
        "runs_failed",
        "pages",
        "scan_pages",
        "cached_pages",
        "cost_usd",
        "cost_scan_usd",
        "cost_documents_usd",
        "cost_questions_usd",
        "cost_estimated",
        "cost_part_unknown",
        # 0034: pages models read and read again (the ledger), and the cost OpenRouter
        # reported, with the calls whose cost is not known counted apart.
        "pages_read",
        "pages_reread",
        "pages_read_costed",
        "cost_real_usd",
        "cost_scan_real_usd",
        "cost_answer_real_usd",
        "cost_unknown_calls",
        "scan_unknown_calls",
        "answer_unknown_calls",
    }
)
QUALITY_REPLY_KEYS = frozenset({"from", "to", "ai_enabled", "platform", "organisations"})
QUALITY_KEYS = frozenset(
    {
        "organisation_id",
        "runs",
        "runs_failed",
        "runs_failed_platform",
        "runs_degraded",
        "documents_run",
        "pages_cells",
        "pages_text_layer",
        "pages_ocr",
        "pages_blank",
        "pages_ai",
        "pages_local",
        "pages_cached",
        "pages_unreadable",
        "ready_documents",
        "ready_p50_seconds",
        "ready_p95_seconds",
        "answers",
        "answered",
        "not_found",
        "unchecked",
        "answers_verified",
        "answers_fixed_query",
        "citations",
        "feedback",
        "feedback_up",
        "values_total",
        "values_needs_review",
        "corrections",
        "corrections_second",
        "corrections_typed",
    }
)
ANSWER_WEEK_KEYS = frozenset(
    {"organisation_id", "week", "replies", "answered", "not_found", "clarify", "rated", "useful"}
)
ORG_NUMBERS_REPLY_KEYS = frozenset({"from", "to", "ai_enabled", "organisations"})
ORG_NUMBERS_KEYS = frozenset(
    {
        "organisation_id",
        "documents",
        "documents_in_trash",
        "pages_read",
        "pages_by_route",
        "failed_documents",
        "active_users",
        "questions",
        "ai_calls",
        "ai_input_tokens",
        "ai_output_tokens",
        "ai_cost_usd",
        "ai_cost_unknown_calls",
    }
)
BOARD_REPLY_KEYS = frozenset({"from", "to", "ai_enabled", "items"})
BOARD_KEYS = frozenset(
    {
        "model",
        "documents",
        "runs",
        "runs_failed",
        "runs_degraded",
        "pages_ai",
        "pages_local",
        "pages_cached",
        "seconds_per_page_p50",
        "seconds_per_page_p95",
        "calls",
        "scan_calls",
        "cached_scan_calls",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "scan_cost_usd",
        "cost_estimated",
        "cost_part_unknown",
        "ocr_pages",
        "pages_disagree",
        "pages_refused",
        "pages_fell_back",
        "pages_unpinned",
    }
)


@router.get("/insights")
def get_insights(
    operator: OperatorSession, db: OperatorDb, context: Context, days: Days = 30
) -> dict[str, Any]:
    """F1 for this period and the one before it (``previous_from`` to ``to``), F2 for today."""
    zone = context.settings.timezone
    start, end = _period(context, days)
    previous_from = start - timedelta(days=days)
    month_from = end.replace(day=1)
    with db.operator() as conn:
        organisations = insights_sql.org_health(conn, operator.operator_id, end, zone)
        daily = insights_sql.daily(conn, operator.operator_id, min(previous_from, month_from), end, zone)
        _check_signed_in(conn, operator.operator_id, [*organisations, *daily])
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "previous_from": previous_from.isoformat(),
        "ai_enabled": ai_enabled(context.settings),
        "month": {
            "from": month_from.isoformat(),
            "today": end.isoformat(),
            "days_in_month": calendar.monthrange(end.year, end.month)[1],
        },
        "organisations": [_row(row, ORG_HEALTH_KEYS) for row in organisations],
        "daily": [_row(row, DAILY_KEYS) for row in daily],
    }


@router.get("/quality")
def get_quality(
    operator: OperatorSession, db: OperatorDb, context: Context, days: Days = 30
) -> dict[str, Any]:
    """F3: the platform row (``customer_id`` NULL) apart from the organisations' rows."""
    start, end = _period(context, days)
    with db.operator() as conn:
        rows = insights_sql.quality(conn, operator.operator_id, start, end, context.settings.timezone)
        _check_signed_in(conn, operator.operator_id, rows)
    platform = next((row for row in rows if row["customer_id"] is None), None)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "ai_enabled": ai_enabled(context.settings),
        "platform": None if platform is None else _row(platform, QUALITY_KEYS),
        "organisations": [_row(row, QUALITY_KEYS) for row in rows if row["customer_id"] is not None],
    }


@router.get("/model-board")
def get_model_board(
    operator: OperatorSession, db: OperatorDb, context: Context, days: Days = 30
) -> dict[str, Any]:
    """F4: one item per model, observational."""
    start, end = _period(context, days)
    with db.operator() as conn:
        rows = insights_sql.model_board(conn, operator.operator_id, start, end, context.settings.timezone)
        _check_signed_in(conn, operator.operator_id, rows)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "ai_enabled": ai_enabled(context.settings),
        "items": [_row(row, BOARD_KEYS) for row in rows],
    }


@router.get("/org-numbers")
def get_org_numbers(
    operator: OperatorSession, db: OperatorDb, context: Context, days: Days = 30
) -> dict[str, Any]:
    """#387: each organisation's numbers for the period, one function call."""
    start, end = _period(context, days)
    with db.operator() as conn:
        rows = insights_sql.org_numbers(conn, operator.operator_id, start, end, context.settings.timezone)
        _check_signed_in(conn, operator.operator_id, rows)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "ai_enabled": ai_enabled(context.settings),
        "organisations": [_row(row, ORG_NUMBERS_KEYS) for row in rows],
    }


@router.get("/answer-weeks")
def get_answer_weeks(
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
    weeks: Annotated[int, Query(ge=1, le=12)] = 4,
) -> dict[str, Any]:
    """PRD 6 F3: the last ``weeks`` whole ISO weeks that have ended, Monday to Sunday in
    TIMEZONE; never this week, so a count is never read twice while it grows."""
    zone = context.settings.timezone
    today = context.clock().astimezone(ZoneInfo(zone)).date()
    monday = today - timedelta(days=today.weekday() + 7 * weeks)
    with db.operator() as conn:
        rows = insights_sql.answer_weeks(conn, operator.operator_id, monday, weeks, zone)
        _check_signed_in(conn, operator.operator_id, rows)
    return {
        "from": monday.isoformat(),
        "to": (monday + timedelta(days=7 * weeks - 1)).isoformat(),
        "organisations": [_row(row, ANSWER_WEEK_KEYS) for row in rows],
    }


def _period(context: AppContext, days: int) -> tuple[date, date]:
    """``days`` days ending today in TIMEZONE, both ends included."""
    end = context.clock().astimezone(ZoneInfo(context.settings.timezone)).date()
    return end - timedelta(days=days - 1), end


def _check_signed_in(conn: Connection, operator_id: str, rows: Sequence[RowMapping]) -> None:
    """No row is also what the functions give an operator disabled since current_operator."""
    if not rows and operators_sql.session_operator(conn, operator_id) is None:
        raise ApiError(401, "not_signed_in")


def _row(row: RowMapping, keys: frozenset[str]) -> dict[str, Any]:
    """Exactly ``keys``; ``organisation_id`` is the function's ``customer_id``."""
    return {key: _value(row["customer_id" if key == "organisation_id" else key]) for key in sorted(keys)}


def _value(value: Any) -> Any:
    """JSON as the TS types say: Decimal a number, a day or time ISO text, None null."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date):  # a datetime too
        return value.isoformat()
    return value
