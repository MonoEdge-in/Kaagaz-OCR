"""The AI spend ledger (model choice, council 11 Oct 2026, SP1): ``model_usage`` rows,
written by ``kaagaz.ai.usage`` when a usage scope ends.

One row per (scope, model, kind, cached): how many calls, their tokens and their cost in US
dollars. The cost is the sum of OpenRouter's ``usage.cost`` when every call carried one;
otherwise the catalogue's price times the tokens, marked ``cost_estimated``; NULL when the
model is not in the catalogue either. A row holds ids, codes and numbers only, never content.
A sum counts the known costs; ``cost_part_unknown`` says some row's cost was not known, so
the screens never show a partial sum as the whole one.

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
    " cost_estimated)"
    " VALUES (:customer_id, :user_id, :document_id, :conversation_id, :kind, :model,"
    " :provider_name, :cached, :calls, :input_tokens, :output_tokens, :reasoning_tokens,"
    " COALESCE(CAST(:cost_usd AS numeric), (SELECT (:input_tokens * c.price_in_usd_per_m"
    " + :output_tokens * c.price_out_usd_per_m) / 1000000 FROM model_catalog c WHERE c.id = :model)),"
    " CAST(:cost_usd AS numeric) IS NULL AND EXISTS (SELECT 1 FROM model_catalog c WHERE c.id = :model))"
)


@dataclass(frozen=True, slots=True)
class UsageRow:
    """One ledger row. ``user_id`` None: work no user asked for (the upgrade sweep, the
    missing-vector pass); ``provider_name`` the last provider OpenRouter named, or None;
    ``cost_usd`` None when a call carried no ``usage.cost`` (the catalogue price is used)."""

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
# when it was document work (scan, upgrade, card, invoice, map, or Dynamic's and Ensemble's
# extra page reads, careful and ensemble_scan, 0026) with no conversation:
# chat, search, stories and the missing-vector pass had none to lose.
# ponytail: an embed row of a deleted document is left out of the deleted bucket, since
# the missing-vector pass writes embed rows with no document too; a kind of its own if needed.
# Names are looked up after the grouping (users and documents by the customer's own key).
_REPORT = text(
    "WITH r AS ("
    " SELECT u.user_id, u.model, u.kind, CAST(timezone(:tz, u.created_at) AS date) AS day, u.document_id,"
    " (u.document_id IS NOT NULL OR (u.conversation_id IS NULL AND u.kind IN"
    " ('scan', 'upgrade', 'card', 'invoice', 'map', 'careful', 'ensemble_scan'))) AS of_document,"
    " u.cached, u.calls, u.input_tokens, u.output_tokens, u.reasoning_tokens, u.cost_usd, u.cost_estimated"
    " FROM model_usage u"
    " WHERE u.customer_id = :c AND u.created_at >= :start_at AND u.created_at < :end_at"
    " AND (CAST(:user AS uuid) IS NULL OR u.user_id = CAST(:user AS uuid))"
    "), g AS ("
    " SELECT CASE WHEN GROUPING(r.user_id) = 0 THEN 'user' WHEN GROUPING(r.model) = 0 THEN 'model'"
    " WHEN GROUPING(r.kind) = 0 THEN 'kind' WHEN GROUPING(r.day) = 0 THEN 'day'"
    " WHEN GROUPING(r.document_id) = 0 THEN 'document' ELSE 'total' END AS dimension,"
    " r.user_id, r.model, r.kind, r.day, r.document_id, r.of_document,"
    " sum(r.calls) AS calls, COALESCE(sum(r.calls) FILTER (WHERE r.cached), 0) AS cached_calls,"
    " sum(r.input_tokens) AS input_tokens, sum(r.output_tokens) AS output_tokens,"
    " sum(r.reasoning_tokens) AS reasoning_tokens, sum(r.cost_usd) AS cost_usd,"
    " bool_or(r.cost_estimated) AS cost_estimated, bool_or(r.cost_usd IS NULL) AS cost_part_unknown"
    " FROM r"
    " GROUP BY GROUPING SETS ((), (r.user_id), (r.model), (r.kind), (r.day), (r.document_id, r.of_document))"
    ")"
    " SELECT g.*, x.display_name AS user_name, m.name AS model_name, d.display_name AS document_name"
    " FROM g"
    " LEFT JOIN users x ON x.customer_id = :c AND x.id = g.user_id"
    " LEFT JOIN model_catalog m ON m.id = g.model"
    " LEFT JOIN documents d ON d.customer_id = :c AND d.id = g.document_id"
    " ORDER BY g.dimension, g.day, g.cost_usd DESC NULLS LAST, g.calls DESC,"
    " g.user_id, g.model, g.kind, g.document_id"
)

# The dollars spent in a period (unknown costs count nothing), and whether any was unknown.
_SPENT = text(
    "SELECT COALESCE(sum(u.cost_usd), 0), COALESCE(bool_or(u.cost_usd IS NULL), false) FROM model_usage u"
    " WHERE u.customer_id = :c AND u.created_at >= :start_at AND u.created_at < :end_at"
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
