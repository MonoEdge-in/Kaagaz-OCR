"""The platform operator's insights (council 11 Oct 2026, F1-F4). Every route needs the
operator cookie (``current_operator`` on the router); only ``OperatorDatabase`` here, and
each read is one read-only ``kaagaz_op_*`` function (``kaagaz.db.operator_insights``,
0024). Reads write no ``platform_audit`` row. Days are in TIMEZONE; ``days`` is 1 to 90
(30 by default) and ends today.

* ``GET /operator/insights?days=``: ``INSIGHTS_KEYS``. ``month`` is {from, today,
  days_in_month}; ``organisations`` are F2 rows (``ORG_HEALTH_KEYS``); ``daily`` are F1
  rows (``DAILY_KEYS``) from ``previous_from`` to ``to`` (twice ``days``).
* ``GET /operator/quality?days=``: ``QUALITY_REPLY_KEYS``; ``platform`` and each of
  ``organisations`` are F3 rows (``QUALITY_KEYS``; the platform's ``organisation_id`` is null).
* ``GET /operator/model-board?days=``: ``BOARD_REPLY_KEYS``; ``items`` are F4 rows
  (``BOARD_KEYS``).

Organisation rows say ``organisation_id`` for the function's ``customer_id``. Decimals are
sent as numbers, NULL (suppressed below K, or not known) as null. Never a user, document,
name or text. No row and a disabled operator: 401, as on ``/operator/activity``.
"""

from fastapi import APIRouter, Depends

from kaagaz.api.auth import current_operator

router = APIRouter(prefix="/operator", dependencies=[Depends(current_operator)])

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
