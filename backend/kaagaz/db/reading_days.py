"""The reading pause's counter and holds (#478, Tushar, 3 Nov 2026; migration 0066).

``reading_days`` holds one row per (organisation, India day): the pages AI read and the
dollars reported, bumped as each page is read (``count``, from ``kaagaz.ai.usage``), so a
daily page limit holds while a long document is still being read; ``model_usage`` is written
only when a document's reading ends. The day is the India date (IST: no daylight saving).

* ``state``: the organisation's limit in effect (its own, else the platform's), its pages
  today, its monthly budget and this month's spend: the larger of the ledger's (the figure the
  console shows against the budget) and the counter's (live, but only since 0066), and whether
  one document is held.
* ``claim``: one page held for a document about to be read, in one statement on the day's row
  (its row lock), only while the pages are under the limit, so two workers can never both
  pass it; ``give_back`` returns it when the document ends (its pages were counted as read).
* ``hold`` and ``release``: a document waiting for the pause to end, in the database, not the
  queue (the server switches itself off while the queue is empty); ``held_customers`` (on a
  ``Database.system()`` connection, owner rights, ids only) the organisations with one.
* ``alert_once``: true for the first caller of the day only, so the super admin is emailed once.

Every statement names the customer and runs on a ``Database.customer()`` connection, so row
level security filters it too. Time: O(1) statements each; ``state`` sums at most one month of
the organisation's ledger rows (``model_usage_period``) when a budget is set.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

IST = ZoneInfo("Asia/Kolkata")


def india_day(now: datetime) -> date:
    """The India date at ``now`` (an aware time)."""
    return now.astimezone(IST).date()


def _month(day: date) -> tuple[date, date]:
    """The first day of ``day``'s month and of the next."""
    first = day.replace(day=1)
    return first, (first + timedelta(days=32)).replace(day=1)


def _start(day: date) -> datetime:
    return datetime.combine(day, time(), IST)


@dataclass(frozen=True, slots=True)
class State:
    """One organisation now: its name (for the super admin's email), ``page_limit`` in effect
    (None: no limit), ``pages_today``, ``budget`` in dollars (None: none) and ``spent`` this
    month (None without a budget), and the asked document's hold (None)."""

    name: str
    page_limit: int | None
    pages_today: int
    budget: Decimal | None
    spent: Decimal | None
    held: str | None


_STATE = text(
    "SELECT c.name, COALESCE(s.daily_page_limit, p.daily_page_limit) AS page_limit,"
    " COALESCE((SELECT r.pages FROM reading_days r WHERE r.customer_id = c.id AND r.day = :day), 0)"
    " AS pages_today, c.ai_budget_usd AS budget,"
    " CASE WHEN c.ai_budget_usd IS NOT NULL THEN GREATEST("
    "(SELECT COALESCE(sum(u.cost_usd), 0) FROM model_usage u WHERE u.customer_id = c.id"
    " AND u.kind <> 'accuracy_check' AND u.created_at >= :month_at AND u.created_at < :next_month_at),"
    " (SELECT COALESCE(sum(r.cost_usd), 0) FROM reading_days r WHERE r.customer_id = c.id"
    " AND r.day >= :month AND r.day < :next_month)) END AS spent,"
    " (SELECT d.reading_held FROM documents d WHERE d.customer_id = c.id AND d.id = CAST(:d AS uuid)) AS held"
    " FROM customers c"
    " LEFT JOIN customer_ai_settings s ON s.customer_id = c.id"
    " LEFT JOIN platform_ai_settings p ON true"
    " WHERE c.id = :c"
)
# One page held for a document being started, only under the limit (the row's lock decides).
_CLAIM = text(
    "INSERT INTO reading_days AS r (customer_id, day, pages) VALUES (:c, :day, 1)"
    " ON CONFLICT (customer_id, day) DO UPDATE SET pages = r.pages + 1 WHERE r.pages < :limit"
    " RETURNING r.pages"
)
_GIVE_BACK = text(
    "UPDATE reading_days SET pages = pages - 1 WHERE customer_id = :c AND day = :day AND pages > 0"
)
_COUNT = text(
    "INSERT INTO reading_days AS r (customer_id, day, pages, cost_usd)"
    " VALUES (:c, :day, :pages, CAST(:cost AS numeric))"
    " ON CONFLICT (customer_id, day) DO UPDATE"
    " SET pages = r.pages + EXCLUDED.pages, cost_usd = r.cost_usd + EXCLUDED.cost_usd"
)
_ALERT = text(
    "INSERT INTO reading_days AS r (customer_id, day, alerted_at) VALUES (:c, :day, :now)"
    " ON CONFLICT (customer_id, day) DO UPDATE SET alerted_at = EXCLUDED.alerted_at"
    " WHERE r.alerted_at IS NULL RETURNING 1"
)
_HOLD = text("UPDATE documents SET reading_held = :reason WHERE customer_id = :c AND id = :d")
_UNHOLD = text(
    "UPDATE documents SET reading_held = NULL WHERE customer_id = :c AND id = :d AND reading_held IS NOT NULL"
)
_HELD_CUSTOMERS = text("SELECT t.id FROM kaagaz_customer_ids_held() AS t (id) ORDER BY t.id")
# Every hold ends; only a document still waiting to be read is queued again.
_RELEASE = text(
    "UPDATE documents SET reading_held = NULL WHERE customer_id = :c AND reading_held IS NOT NULL"
    " RETURNING id, status IN ('received', 'clean')"
)


def state(conn: Connection, customer_id: str, day: date, document_id: str | None = None) -> State:
    """The organisation's ``State`` on ``day`` (``document_id``'s hold too). One statement."""
    month, next_month = _month(day)
    row = (
        conn.execute(
            _STATE,
            {
                "c": customer_id,
                "d": document_id,
                "day": day,
                "month": month,
                "next_month": next_month,
                "month_at": _start(month),
                "next_month_at": _start(next_month),
            },
        )
        .mappings()
        .one()
    )
    return State(
        name=str(row["name"]),
        page_limit=row["page_limit"],
        pages_today=int(row["pages_today"]),
        budget=row["budget"],
        spent=row["spent"],
        held=row["held"],
    )


def claim(conn: Connection, customer_id: str, day: date, page_limit: int) -> bool:
    """Hold one page of ``day`` for a document about to be read: False, and nothing held, when
    the organisation already has ``page_limit`` pages. One statement, safe with any number of
    callers at once: they wait on the day's row in turn."""
    return conn.execute(_CLAIM, {"c": customer_id, "day": day, "limit": page_limit}).first() is not None


def give_back(conn: Connection, customer_id: str, day: date) -> None:
    """Return the page ``claim`` held on ``day``. One statement."""
    conn.execute(_GIVE_BACK, {"c": customer_id, "day": day})


def count(conn: Connection, customer_id: str, day: date, *, pages: int, cost_usd: float) -> None:
    """Add ``pages`` pages read and ``cost_usd`` dollars to ``day``'s counter. One statement."""
    conn.execute(_COUNT, {"c": customer_id, "day": day, "pages": pages, "cost": cost_usd})


def alert_once(conn: Connection, customer_id: str, day: date, *, now: datetime) -> bool:
    """True for the first caller on ``day`` only (it emails the super admin). One statement."""
    return conn.execute(_ALERT, {"c": customer_id, "day": day, "now": now}).first() is not None


def hold(conn: Connection, customer_id: str, document_id: str, reason: str) -> None:
    """The document waits in the database for ``reason`` (daily_limit or budget)."""
    conn.execute(_HOLD, {"c": customer_id, "d": document_id, "reason": reason})


def unhold(conn: Connection, customer_id: str, document_id: str) -> None:
    """The document is no longer held (it is being read). One statement."""
    conn.execute(_UNHOLD, {"c": customer_id, "d": document_id})


def held_customers(conn: Connection) -> list[str]:
    """The organisations with a held document (0066's owner-rights function: ids only), on a
    ``Database.system()`` connection. One statement on ``documents_reading_held``."""
    return [str(row[0]) for row in conn.execute(_HELD_CUSTOMERS)]


def release(conn: Connection, customer_id: str) -> list[str]:
    """End every hold of the organisation; the ids of the documents still waiting to be read
    (the caller queues them). One statement on ``documents_reading_held``."""
    return [str(row[0]) for row in conn.execute(_RELEASE, {"c": customer_id}) if row[1]]
