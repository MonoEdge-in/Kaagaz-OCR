"""Processing runs: one row per delivery of a document's job (PRD 2 R6, X2).

A run records what one delivery did: its routes per page, its models, its
counts and its outcome. PRD 5 adds who read its pages (the reading model, the
backup reader, the page cache, a read again on request, and how many were
unreadable) and the tokens its reading-model calls were billed (READER_COUNTERS).

``start_run`` marks the document's other runs that are still ``running`` (a
delivery that died, or one still going that this delivery replaces) as
``superseded``. ``finish_run`` writes the outcome of a run still
``running``. A superseded run gets only the counts of its own delivery's finish,
once (its ``seconds_total`` is still empty until then), and keeps its status,
code and finish time, so the pages it read still count; a run finished already
stays as it is.

The run fence (Lead's resolution 2): only a run that is still ``running`` may
save results or mark the document ready. ``is_run_open`` is read under the
family lock by the save and by ``mark_ready``, which raise
``kaagaz.jobs.worker.Superseded`` otherwise. It asks which run is open, not which
started last, so a worker whose clock runs ahead can't win it with a stale run.
A run superseded just after its save passed the fence still commits that save;
the newer run replaces it, as every save replaces the one before.

Lock order: ``start_run`` first takes the document's run lock (a transaction
advisory lock on the document id), so two starts of one document run one after
the other and the second supersedes the first; a partial unique index
(``processing_runs_one_running``) is the backstop. Then it locks the parent
documents row FOR KEY SHARE (AR8), which does not wait for the family lock
(FOR NO KEY UPDATE), so a new delivery can start while an older one holds the
family in its save.

A document's runs go with it: deleting the document deletes them (0005, ON
DELETE CASCADE), so nothing of a deleted document is kept (Vrushit's decision:
"if a document is deleted, instantly forget everything about it"; AR12 kept them).

Times are passed in from the injected clock (``now``), never read with SQL
``now()``. A run never ends before it started: another worker's clock may be
ahead, so the finish time is the later of ``now`` and the start (AR11).

``close_running`` ends a document's runs still ``running`` with a status of its
own: the worker's last delivery marks them failed with ``retries_exhausted``
(AR11). ``pages_by_route`` counts the pages of the runs started in a window, for
the admin numbers: the runs of documents still kept, never a deleted one's.
``recent_timings`` gives the time-left estimate (kaagaz.eta) this customer's own
last ready runs, and never another customer's.

Module functions take a connection from ``Database.customer(customer_id)``, in
the ``db/trash.py`` style; ``PgRuns`` opens its own transactions.
"""

import json
from dataclasses import fields
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection, text

from kaagaz.db.engine import Database, unavailable
from kaagaz.eta import HISTORY_RUNS, RunTiming, as_seconds
from kaagaz.jobs.worker import JobFailed

if TYPE_CHECKING:
    from kaagaz.pipeline import FinishedRun

_RUN_LOCK = (
    "SELECT pg_advisory_xact_lock(hashtextextended('kaagaz.processing_runs:' || CAST(:d AS uuid)::text, 0))"
)
_LOCK_DOCUMENT = "SELECT id FROM documents WHERE customer_id = :c AND id = :d FOR KEY SHARE"

_SUPERSEDE_RUNNING = (
    "UPDATE processing_runs SET status = 'superseded',"
    " finished_at = GREATEST(CAST(:now AS timestamptz), started_at)"
    " WHERE customer_id = :c AND document_id = :d AND status = 'running'"
)

_INSERT = (
    "INSERT INTO processing_runs (customer_id, document_id, started_at, status)"
    " VALUES (:c, :d, :now, 'running') RETURNING id"
)

# A superseded run keeps its status, code and finish time; the CASEs read the row as it was.
_FINISH = (
    "UPDATE processing_runs SET status = CASE WHEN status = 'running' THEN :status ELSE status END,"
    " error_code = CASE WHEN status = 'running' THEN :error_code ELSE error_code END,"
    " ocr_engine = :ocr_engine,"
    " model_chat = :model_chat, model_vision = :model_vision, model_embed = :model_embed,"
    " pages_cells = :pages_cells, pages_text_layer = :pages_text_layer, pages_ocr = :pages_ocr,"
    " pages_blank = :pages_blank, vision_crops = :vision_crops, model_calls = :model_calls,"
    " dropped_quotes = :dropped_quotes, quotes_returned = :quotes_returned,"
    " values_verified = :values_verified, values_needs_review = :values_needs_review,"
    " seconds_total = :seconds_total, seconds_ocr = :seconds_ocr, config = CAST(:config AS jsonb),"
    " pages_gemini = :pages_gemini, pages_local = :pages_local, pages_cached = :pages_cached,"
    " pages_read_again = :pages_read_again, pages_unreadable = :pages_unreadable,"
    " input_tokens = :input_tokens, output_tokens = :output_tokens,"
    " finished_at = CASE WHEN status = 'running'"
    " THEN GREATEST(CAST(:now AS timestamptz), started_at) ELSE finished_at END"
    " WHERE customer_id = :c AND id = :id"
    " AND (status = 'running' OR (status = 'superseded' AND seconds_total IS NULL))"
)

# PRD 5 (0021): who read the pages and the tokens billed (G7, G9, F2, F5, Q7), by their
# RunTotals field names (kaagaz.pipeline). A totals record without one writes 0.
READER_COUNTERS = (
    "pages_gemini",
    "pages_local",
    "pages_cached",
    "pages_read_again",
    "pages_unreadable",
    "input_tokens",
    "output_tokens",
)

# The column limits of the run's text values (0004). finish_run cuts each to its
# limit, so a long engine version can never make a finish fail.
_TEXT_LIMITS = {
    "error_code": 64,
    "ocr_engine": 300,
    "model_chat": 300,
    "model_vision": 300,
    "model_embed": 300,
}

_CLOSE_RUNNING = (
    "UPDATE processing_runs SET status = :status, error_code = :error_code,"
    " finished_at = GREATEST(CAST(:now AS timestamptz), started_at)"
    " WHERE customer_id = :c AND document_id = :d AND status = 'running'"
)

# The pages read by route and, PRD 5 U9, by who read them, and the tokens billed, of the runs started
# in a window: an Admin's numbers, whole (PRD 7 decision 20, #606), so read by the owner-rights
# kaagaz_admin_reader_pages (0091), which checks the caller; ``:c`` keeps another customer's id at none.
_READER_PAGES = (
    "SELECT * FROM kaagaz_admin_reader_pages(:start, :end) WHERE CAST(:c AS uuid) = kaagaz_current_customer()"
)
_ROUTES = ("cells", "text_layer", "ocr", "blank")
# reader_numbers' names, each its column of kaagaz_admin_reader_pages.
_READERS = {name: f"pages_{name}" for name in ("gemini", "local", "cached", "read_again")} | {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
}

_OPEN = (
    "SELECT EXISTS (SELECT 1 FROM processing_runs WHERE customer_id = :c AND document_id = :d"
    " AND id = :r AND status = 'running')"
)

# The time left's history (kaagaz.eta): the newest ready runs, on processing_runs_started.
_RECENT = (
    "SELECT ocr_engine, pages_ocr, pages_text_layer + pages_blank AS pages_fast, pages_cells, seconds_ocr,"
    " config -> 'seconds_reading' AS seconds_reading, config -> 'seconds_after_reading' AS seconds_after,"
    " config -> 'seconds_preparing' AS seconds_preparing"
    " FROM processing_runs WHERE customer_id = :c AND status = 'ready'"
    " ORDER BY started_at DESC LIMIT :k"
)


def start_run(conn: Connection, customer_id: str, document_id: str, *, now: datetime) -> str:
    """Mark this document's runs still ``running`` as superseded, then insert a
    ``running`` run started at ``now`` and return its id. Raises
    JobFailed("document_missing") when this customer has no such document.
    4 statements on the index processing_runs_document: O(log R + k) for k open runs."""
    params = {"c": customer_id, "d": document_id, "now": now}
    conn.execute(text(_RUN_LOCK), params)
    if conn.execute(text(_LOCK_DOCUMENT), params).first() is None:
        raise JobFailed("document_missing", retryable=False)
    conn.execute(text(_SUPERSEDE_RUNNING), params)
    return str(conn.execute(text(_INSERT), params).scalar_one())


def finish_run(conn: Connection, customer_id: str, run: "FinishedRun") -> None:
    """Write the run's outcome and totals, and its finish time, while it is still
    running; a superseded run gets its totals only, once (the module docstring). Text
    values are cut to their column limits; a READER_COUNTERS count the totals do not
    carry is written as 0. A finished run, a run of another customer,
    or no run, changes nothing. One statement by primary key: O(log R)."""
    totals: dict[str, Any] = {item.name: getattr(run.totals, item.name) for item in fields(run.totals)}
    for name in READER_COUNTERS:
        totals.setdefault(name, 0)
    for name, limit in _TEXT_LIMITS.items():
        if totals[name] is not None:
            totals[name] = totals[name][:limit]
    totals["config"] = json.dumps(dict(run.totals.config))
    conn.execute(text(_FINISH), {**totals, "c": customer_id, "id": run.run_id, "now": run.now})


def is_run_open(conn: Connection, customer_id: str, document_id: str, run_id: str) -> bool:
    """True while ``run_id`` is this document's run and still ``running`` (the run
    fence). The caller holds the family lock (``trash.lock_family``). One query by
    primary key: O(log R)."""
    params = {"c": customer_id, "d": document_id, "r": run_id}
    return bool(conn.execute(text(_OPEN), params).scalar())


def close_running(
    conn: Connection, customer_id: str, document_id: str, *, status: str, error_code: str, now: datetime
) -> None:
    """End this document's runs still ``running`` with ``status`` and ``error_code``,
    at ``now`` (never before each started). One statement on the index
    processing_runs_document: O(log R + k) for k open runs."""
    params = {"c": customer_id, "d": document_id, "status": status, "error_code": error_code, "now": now}
    conn.execute(text(_CLOSE_RUNNING), params)


def pages_by_route(conn: Connection, customer_id: str, *, start: datetime, end: datetime) -> dict[str, int]:
    """The pages read by each route (cells, text_layer, ocr, blank) in this
    customer's runs started in [start, end), 0 for a route with none. Every run
    counts, a superseded or failed one too: its pages were read. A deleted document's
    runs were deleted with it (0005), so its pages no longer count. One call on the
    index processing_runs_started: O(log R + runs in the window)."""
    row = conn.execute(text(_READER_PAGES), {"c": customer_id, "start": start, "end": end}).mappings().first()
    return {route: 0 if row is None else int(row[f"pages_{route}"]) for route in _ROUTES}


def reader_numbers(conn: Connection, customer_id: str, *, start: datetime, end: datetime) -> dict[str, int]:
    """PRD 5 U9: the pages this customer's runs started in [start, end) had read by the
    reading model (gemini), the backup reader (local) and the page cache (cached), the
    pages read again (read_again), and the input and output tokens billed; 0 when none.
    Every run counts, as in ``pages_by_route``. One query on the index
    processing_runs_started: O(log R + runs in the window)."""
    row = conn.execute(text(_READER_PAGES), {"c": customer_id, "start": start, "end": end}).mappings().first()
    return {name: 0 if row is None else int(row[column]) for name, column in _READERS.items()}


def recent_timings(conn: Connection, customer_id: str) -> list[RunTiming]:
    """This customer's last HISTORY_RUNS ready runs, newest first; a config time that is
    missing or not a number is None. On processing_runs_started: O(log R + runs passed)."""
    rows = conn.execute(text(_RECENT), {"c": customer_id, "k": HISTORY_RUNS}).all()
    return [
        RunTiming(
            engine, ocr, fast, cells, seconds_ocr, as_seconds(reading), as_seconds(after), as_seconds(prep)
        )
        for engine, ocr, fast, cells, seconds_ocr, reading, after, prep in rows
    ]


class PgRuns:
    """The runs, each call in its own short transaction as the customer."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def start(self, customer_id: str, document_id: str, *, now: datetime) -> str:
        with unavailable(), self._db.customer(customer_id) as conn:
            return start_run(conn, customer_id, document_id, now=now)

    def finish(self, customer_id: str, run: "FinishedRun") -> None:
        with unavailable(), self._db.customer(customer_id) as conn:
            finish_run(conn, customer_id, run)

    def recent_timings(self, customer_id: str) -> list[RunTiming]:
        with unavailable(), self._db.customer(customer_id) as conn:
            return recent_timings(conn, customer_id)
