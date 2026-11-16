"""The AI spend ledger (model choice, council 11 Oct 2026, SP1): ``model_usage`` rows,
written by ``kaagaz.ai.usage`` when a usage scope ends.

One row per (scope, model, kind, cached, thinking, resolution): how many calls, their tokens
and their cost in US dollars; ``thinking`` and ``resolution`` what a page read was sent at
(#463), NULL for every other kind. The cost is the sum of OpenRouter's ``usage.cost`` when
every call carried one; otherwise the catalogue's price times the tokens, marked
``cost_estimated``; NULL when the model is not in the catalogue either. A row holds ids,
codes and numbers only, never content. A sum counts the known costs; ``cost_part_unknown``
says some row's cost was not known, so the screens never show a partial sum as the whole one.

The admin's Usage screen (SP2) reads one organisation's rows back over a period
[``start``, ``end``) (the route turns its days into one: ``api.routers.admin``):
``usage_report`` (totals, by user, model, kind and day in TIMEZONE, and the top
documents) and ``month_spend`` (the dollars spent, for the operator's budget, SP4, warn
only); its CSV is ``db.listing.usage_rows``. Every statement names the customer and runs on a
``Database.customer()`` connection, so row level security filters it too. The ledger
outlives what it was about: a removed user's work counts as no user's, a document deleted
for good shows as DELETED_DOCUMENT, and a document in Trash or an older version keeps its
name (the money was spent on it).

Time: one INSERT a row, each with two lookups of ``model_catalog`` by its primary key. A
report reads the period's rows once (``model_usage_period``), grouped six ways at once
(GROUPING SETS): O(r log r) for its r rows.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, RowMapping, text

TABLE = "model_usage"  # migration 0023; _INSERT spells it out (no SQL is built from strings)

_INSERT = text(
    "INSERT INTO model_usage (customer_id, user_id, document_id, conversation_id, kind, model,"
    " provider_name, cached, calls, input_tokens, output_tokens, reasoning_tokens, cost_usd,"
    " cost_estimated, thinking, resolution, limit_hits, pages)"
    " VALUES (:customer_id, :user_id, :document_id, :conversation_id, :kind, :model,"
    " :provider_name, :cached, :calls, :input_tokens, :output_tokens, :reasoning_tokens,"
    " COALESCE(CAST(:cost_usd AS numeric), (SELECT (:input_tokens * c.price_in_usd_per_m"
    " + :output_tokens * c.price_out_usd_per_m) / 1000000 FROM model_catalog c WHERE c.id = :model)),"
    " CAST(:cost_usd AS numeric) IS NULL AND EXISTS (SELECT 1 FROM model_catalog c WHERE c.id = :model),"
    " CAST(:thinking AS text), CAST(:resolution AS text), :limit_hits, :pages)"
)


@dataclass(frozen=True, slots=True)
class UsageRow:
    """One ledger row. ``user_id`` None: work no user asked for (the upgrade sweep, the
    missing-vector pass); ``provider_name`` the last provider OpenRouter named, or None;
    ``cost_usd`` None when a call carried no ``usage.cost`` (the catalogue price is used);
    ``thinking`` and ``resolution`` a page read's (#463), else None."""

    customer_id: str
    user_id: str | None
    document_id: str | None
    conversation_id: str | None
    kind: str
    model: str
    provider_name: str | None
    cached: bool
    calls: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float | None
    thinking: str | None = None
    resolution: str | None = None
    limit_hits: int = 0  # #463: calls that hit the page read's output limit
    pages: int = 0  # #463 review: page reads among the calls, each counted once


def insert_usage(conn: Connection, rows: Sequence[UsageRow]) -> None:
    """Insert ``rows``, on a ``Database.customer()`` connection of their customer (row level
    security refuses any other customer's row). Nothing to do for no rows."""
    if rows:
        conn.execute(_INSERT, [asdict(row) for row in rows])


# --- reading it back: the admin's Usage screen (SP2) and the budget (SP4) ----------------

DELETED_DOCUMENT = "Deleted document"  # the name of spend on a document deleted for good
TOP_DOCUMENTS = 10
_COUNTS = ("calls", "cached_calls", "input_tokens", "output_tokens", "reasoning_tokens")  # whole numbers

# The period's rows grouped six ways in one read: the total, and by user, model, kind, day
# (in TIMEZONE) and document. A row with no document counts as a deleted document only
# when it was document work (scan, upgrade, card, invoice, land, map, or Dynamic's and Ensemble's
# extra page reads, careful and ensemble_scan, 0026; #488's ulc_scan) with no conversation:
# chat, search, stories and the missing-vector pass had none to lose.
# ponytail: an embed row of a deleted document is left out of the deleted bucket, since
# the missing-vector pass writes embed rows with no document too; a kind of its own if needed.
# Names are looked up after the grouping (users and documents by the customer's own key).
# #463 review: an operator's accuracy checks are the operator's, never on the admin's screen.
# PRD 7 (#606): an Admin's numbers, whole (decision 20): read by the owner-rights kaagaz_admin_usage
# (0091), which checks the caller and holds this statement; ``:c`` keeps another customer's id at none.
_REPORT = text(
    "SELECT * FROM kaagaz_admin_usage(:start_at, :end_at, :tz, CAST(:user AS uuid))"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)

# The dollars spent in a period (unknown costs count nothing), and whether any was unknown.
# #463: an operator's accuracy checks are never the organisation's budget. PRD 7 (#606): the whole
# organisation's (kaagaz_spent, 0091), so the budget can't fail open; another customer's id, none.
_SPENT = text(
    "SELECT coalesce(sum(s.spent), 0), coalesce(bool_or(s.unknown), false)"
    " FROM kaagaz_spent(:start_at, :end_at) s"
    " WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)


def _money(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _totals(row: RowMapping | None) -> dict[str, Any]:
    """The UsageTotals of one group of rows; no group is 0 calls at no cost."""
    if row is None or not row["calls"]:
        return {
            "calls": 0,
            "cached_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cost_usd": 0.0,
            "cost_estimated": False,
            "cost_part_unknown": False,
        }
    return {
        **{column: int(row[column]) for column in _COUNTS},
        "cost_usd": _money(row["cost_usd"]),
        "cost_estimated": bool(row["cost_estimated"]),
        "cost_part_unknown": bool(row["cost_part_unknown"]),
    }


def _params(
    customer_id: str, start: datetime, end: datetime, timezone: str, user_id: str | None
) -> dict[str, Any]:
    return {"c": customer_id, "start_at": start, "end_at": end, "tz": timezone, "user": user_id}


def usage_report(
    conn: Connection,
    customer_id: str,
    *,
    start: datetime,
    end: datetime,
    timezone: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """``customer_id``'s AI use in [``start``, ``end``), only ``user_id``'s when given, its
    days counted in ``timezone`` (TIMEZONE): ``totals``, ``by_user``, ``by_model``,
    ``by_kind``, ``by_day`` and ``top_documents`` of frontend/lib/types.ts's UsageReport
    (the route adds the period and the budget). Groups are the costliest first (``by_day``
    the oldest first); ``top_documents`` the TOP_DOCUMENTS costliest, one deleted for good
    named DELETED_DOCUMENT. On a ``Database.customer()`` connection of ``customer_id``.
    One statement."""
    rows = conn.execute(_REPORT, _params(customer_id, start, end, timezone, user_id)).mappings().all()
    by: dict[str, list[RowMapping]] = {}
    for row in rows:
        by.setdefault(row["dimension"], []).append(row)
    documents = [row for row in by.get("document", []) if row["of_document"]][:TOP_DOCUMENTS]
    return {
        "totals": _totals(next(iter(by.get("total", [])), None)),
        "by_user": [
            {**_totals(row), "user_id": _id(row["user_id"]), "user_name": row["user_name"]}
            for row in by.get("user", [])
        ],
        "by_model": [
            {**_totals(row), "model": row["model"], "model_name": row["model_name"] or row["model"]}
            for row in by.get("model", [])
        ],
        "by_kind": [{**_totals(row), "kind": row["kind"]} for row in by.get("kind", [])],
        "by_day": [{**_totals(row), "day": row["day"].isoformat()} for row in by.get("day", [])],
        "top_documents": [
            {
                **_totals(row),
                "document_id": _id(row["document_id"]),
                "name": DELETED_DOCUMENT if row["document_id"] is None else row["document_name"],
            }
            for row in documents
        ],
    }


def _id(value: object) -> str | None:
    return None if value is None else str(value)


def month_spend(conn: Connection, customer_id: str, *, start: datetime, end: datetime) -> float:
    """The US dollars ``customer_id`` spent in [``start``, ``end``) (for the budget, SP4:
    the route gives this month in TIMEZONE); 0.0 with no rows, and a cost not known counts
    nothing. On a ``Database.customer()`` connection of ``customer_id``."""
    return spend(conn, customer_id, start=start, end=end)[0]


def spend(conn: Connection, customer_id: str, *, start: datetime, end: datetime) -> tuple[float, bool]:
    """(``month_spend``, whether some cost in the period was not known). One statement."""
    spent, part_unknown = conn.execute(_SPENT, {"c": customer_id, "start_at": start, "end_at": end}).one()
    return float(spent), bool(part_unknown)
