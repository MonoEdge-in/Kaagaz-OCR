"""What a platform operator sees of how the platform is used, how well it reads and
answers, and what each model costs (operator insights, council 11 Oct 2026, F1-F4; 0024),
on ``OperatorDatabase.operator()`` connections only: each call is one read-only
``kaagaz_op_*`` function, which checks the operator (a disabled one gets no rows) and
suppresses outcome values below K in SQL.

* ``daily`` (F1): per organisation and day, uploads, sign-ins, questions, corrections,
  runs, pages and spend. Only days with something.
* ``org_health`` (F2): per organisation, active users, answers, open review, this
  month's spend against its budget and the last day it was active.
* ``quality`` (F3): reading, answers and review per organisation, plus one platform row
  (``customer_id`` NULL) whose outcome values and their totals count only the
  organisations at K or more (no leak by subtraction).
* ``model_board`` (F4): per model, pages, speed, page signals and spend. No customer.

Days are in ``zone`` (TIMEZONE), both ends included; the function refuses a backwards or
over-long range and an unknown zone (check_violation). Never a user, document, name or
text: the functions have no column for one.

Time: each is one statement under a 5 s local timeout (as ``listing.page_readings``),
O(rows of the window).
"""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import Connection, RowMapping, text

INSIGHTS_TIMEOUT_MS = 5000
_SET_TIMEOUT = text("SELECT set_config('statement_timeout', :ms, true)")


def daily(conn: Connection, operator_id: str, start: date, end: date, zone: str) -> Sequence[RowMapping]:
    """F1: one row per organisation and day from ``start`` to ``end``, days with something only."""
    return _rows(
        conn,
        "SELECT * FROM kaagaz_op_daily(:o, :start, :end, :zone)",
        {"o": operator_id, "start": start, "end": end, "zone": zone},
    )


def org_health(conn: Connection, operator_id: str, today: date, zone: str) -> Sequence[RowMapping]:
    """F2: one row per organisation, windows ending on ``today``; the month is ``today``'s."""
    return _rows(
        conn,
        "SELECT * FROM kaagaz_op_org_health(:o, :today, :zone)",
        {"o": operator_id, "today": today, "zone": zone},
    )


def quality(conn: Connection, operator_id: str, start: date, end: date, zone: str) -> Sequence[RowMapping]:
    """F3: one row per organisation and the platform row (``customer_id`` None)."""
    return _rows(
        conn,
        "SELECT * FROM kaagaz_op_quality(:o, :start, :end, :zone)",
        {"o": operator_id, "start": start, "end": end, "zone": zone},
    )


def model_board(
    conn: Connection, operator_id: str, start: date, end: date, zone: str
) -> Sequence[RowMapping]:
    """F4: one row per model, observational (not an A/B test)."""
    return _rows(
        conn,
        "SELECT * FROM kaagaz_op_model_board(:o, :start, :end, :zone)",
        {"o": operator_id, "start": start, "end": end, "zone": zone},
    )


def _rows(conn: Connection, sql: str, params: Mapping[str, Any]) -> Sequence[RowMapping]:
    """The timeout lasts until this transaction ends (set_config(..., true))."""
    conn.execute(_SET_TIMEOUT, {"ms": str(INSIGHTS_TIMEOUT_MS)})
    return conn.execute(text(sql), params).mappings().all()
