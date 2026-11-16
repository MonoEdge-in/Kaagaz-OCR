"""What each worker last saw of the reading model (PRD 5 U9, 0022): the worker writes
its gate's state every 30 seconds (``report``), and the operator's Health screen reads
the reports of the last 10 minutes (``read``, the ``kaagaz_op_reader`` function).

Status only: a worker name, a state, two times. Never an error body, a key or a
customer: ``reader_status`` has no column for any of them.

Time: each is one statement on the primary key or the whole small table (one row a
worker copy), O(workers).
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, RowMapping, text

from kaagaz.db.engine import Database, unavailable

STATES = ("working", "failing", "down", "key_problem")  # 0022's CHECK; the gate's status()

_REPORT = text(
    "INSERT INTO reader_status (worker, state, since, seen_at) VALUES (:w, :s, :since, now())"
    " ON CONFLICT (worker) DO UPDATE SET state = EXCLUDED.state, since = EXCLUDED.since,"
    " seen_at = EXCLUDED.seen_at, role = EXCLUDED.role"  # PRD 7 (#606): the role it runs as (0091)
)


def report(db: Database, worker: str, state: str, since: datetime | None) -> None:
    """Upsert this worker's report, seen now (the database's clock, so every copy is
    timed alike). ``worker`` is its name (letters, digits, ``.``, ``-`` and ``_``, at
    most 64), ``state`` one of STATES and ``since`` when it began, as a wall-clock
    time (None: not known). A bad name or state is refused by 0022's CHECKs; a
    database that does not answer is BackendUnavailable("database")."""
    with unavailable(), db.system() as conn:
        conn.execute(_REPORT, {"w": worker, "s": state, "since": since})


def read(conn: Connection, operator_id: str) -> Sequence[RowMapping]:
    """The reports of the last 10 minutes, by worker name: (worker, state, since,
    seen_at). Empty for an operator who is not enabled. On an
    ``OperatorDatabase.operator()`` connection."""
    return conn.execute(text("SELECT * FROM kaagaz_op_reader(:o)"), {"o": operator_id}).mappings().all()
