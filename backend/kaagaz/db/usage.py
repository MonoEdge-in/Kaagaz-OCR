"""The AI spend ledger (model choice, council 11 Oct 2026, SP1): ``model_usage`` rows,
written by ``kaagaz.ai.usage`` when a usage scope ends.

One row per (scope, model, kind, cached): how many calls, their tokens and their cost in US
dollars. The cost is the sum of OpenRouter's ``usage.cost`` when every call carried one;
otherwise the catalogue's price times the tokens, marked ``cost_estimated``; NULL when the
model is not in the catalogue either. A row holds ids, codes and numbers only, never content.

Time: one INSERT a row, each with two lookups of ``model_catalog`` by its primary key.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from sqlalchemy import Connection, text

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
