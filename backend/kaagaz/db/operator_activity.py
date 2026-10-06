"""What a platform operator sees of the whole platform (0020, super admin PR 5),
PROVISIONAL: the activity feed and the health counts, on ``OperatorDatabase.operator()``
connections only, each one ``kaagaz_op_*`` function. Read-only: they write no row.

The feed holds the action, time, organisation, target type and who acted as a kind
only; the health, counts only. Neither holds ``audit_log.details``, a ``target_id``,
a person or a document's name: the functions never select them.

Time: ``activity`` reads at most three pages of ``limit`` rows by index, O(limit log n)
(the platform log is read whole: it holds operator actions only). ``health`` is
O(all documents); ``purges`` O(platform log).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, RowMapping, text

SOURCES = ("organisation", "platform")  # audit_log, platform_audit


@dataclass(frozen=True, slots=True)
class Before:
    """The keyset cursor: the last row of the previous page (the two logs' ids overlap)."""

    created_at: datetime
    source: str  # one of SOURCES
    entry_id: int


def activity(
    conn: Connection,
    operator_id: str,
    *,
    before: Before | None,
    customer_id: str | None,
    action_prefix: str | None,
    limit: int,
) -> Sequence[RowMapping]:
    """Rows of both logs newest first, strictly after ``before``, at most ``limit`` (the
    function also caps it at 100). Empty for an operator who is not enabled."""
    params = {
        "o": operator_id,
        "t": None if before is None else before.created_at,
        "s": None if before is None else before.source,
        "i": None if before is None else before.entry_id,
        "c": customer_id,
        "a": action_prefix,
        "n": limit,
    }
    call = "SELECT * FROM kaagaz_op_activity(:o, :t, :s, :i, :c, :a, :n)"
    return conn.execute(text(call), params).mappings().all()


def health(conn: Connection, operator_id: str) -> Sequence[RowMapping]:
    """Every organisation with its document counts, oldest organisation first."""
    return conn.execute(text("SELECT * FROM kaagaz_op_health(:o)"), {"o": operator_id}).mappings().all()


def purges(conn: Connection, operator_id: str) -> RowMapping | None:
    """Organisations purged and swept again: how many, when last. None: not enabled."""
    return conn.execute(text("SELECT * FROM kaagaz_op_purges(:o)"), {"o": operator_id}).mappings().first()
