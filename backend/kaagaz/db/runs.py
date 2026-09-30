"""Processing runs: one row per delivery of a document's job (PRD 2 R6, X2).

A run records what one delivery did: its routes per page, its models, its
counts and its outcome. ``start_run`` closes the document's earlier runs that
are still ``running`` (a delivery that died, or one a newer delivery replaced)
as failed with ``interrupted``. ``finish_run`` writes the outcome; a later
finish of the same run overwrites an earlier close, so runs stay truthful.

The run fence (Lead's resolution 2): only the document's latest run, by
``started_at`` then ``id``, may save results or mark the document ready.
``is_latest_run`` is read under the family lock by the save and by
``mark_ready``, which raise ``kaagaz.pipeline.Superseded`` otherwise.

Lock order (AR8): ``start_run`` locks the parent documents row FOR KEY SHARE
first, which does not wait for the family lock (FOR NO KEY UPDATE), so a new
delivery can start while an older one holds the family in its save. A run row
keeps its runs when its document is deleted, with ``document_id`` set NULL (AR12).

Times are passed in from the injected clock (``now``), never read with SQL
``now()``. A run never ends before it started: another worker's clock may be
ahead, so the finish time is the later of ``now`` and the start (AR11).

Module functions take a connection from ``Database.customer(customer_id)``, in
the ``db/trash.py`` style; ``PgRuns`` opens its own transactions.
"""

import json
from dataclasses import fields
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection, text

from kaagaz.db.engine import Database, unavailable
from kaagaz.jobs.worker import JobFailed

if TYPE_CHECKING:
    from kaagaz.pipeline import FinishedRun

_LOCK_DOCUMENT = "SELECT id FROM documents WHERE customer_id = :c AND id = :d FOR KEY SHARE"

_CLOSE_RUNNING = (
    "UPDATE processing_runs SET status = 'failed', error_code = 'interrupted',"
    " finished_at = GREATEST(CAST(:now AS timestamptz), started_at)"
    " WHERE customer_id = :c AND document_id = :d AND status = 'running'"
)

_INSERT = (
    "INSERT INTO processing_runs (customer_id, document_id, started_at, status)"
    " VALUES (:c, :d, :now, 'running') RETURNING id"
)

_FINISH = (
    "UPDATE processing_runs SET status = :status, error_code = :error_code, ocr_engine = :ocr_engine,"
    " model_chat = :model_chat, model_vision = :model_vision, model_embed = :model_embed,"
    " pages_cells = :pages_cells, pages_text_layer = :pages_text_layer, pages_ocr = :pages_ocr,"
    " pages_blank = :pages_blank, vision_crops = :vision_crops, model_calls = :model_calls,"
    " dropped_quotes = :dropped_quotes, quotes_returned = :quotes_returned,"
    " values_verified = :values_verified, values_needs_review = :values_needs_review,"
    " seconds_total = :seconds_total, seconds_ocr = :seconds_ocr, config = CAST(:config AS jsonb),"
    " finished_at = GREATEST(CAST(:now AS timestamptz), started_at)"
    " WHERE customer_id = :c AND id = :id"
)

_LATEST = (
    "SELECT id FROM processing_runs WHERE customer_id = :c AND document_id = :d"
    " ORDER BY started_at DESC, id DESC LIMIT 1"
)


def start_run(conn: Connection, customer_id: str, document_id: str, *, now: datetime) -> str:
    """Close this document's runs still ``running`` as interrupted, then insert a
    ``running`` run started at ``now`` and return its id. Raises
    JobFailed("document_missing") when this customer has no such document.
    3 statements on the index processing_runs_document: O(log R + k) for k open runs."""
    params = {"c": customer_id, "d": document_id, "now": now}
    if conn.execute(text(_LOCK_DOCUMENT), params).first() is None:
        raise JobFailed("document_missing", retryable=False)
    conn.execute(text(_CLOSE_RUNNING), params)
    return str(conn.execute(text(_INSERT), params).scalar_one())


def finish_run(conn: Connection, customer_id: str, run: "FinishedRun") -> None:
    """Write the run's outcome and totals, and its finish time. A run of another
    customer, or no run, changes nothing. One statement by primary key: O(log R)."""
    totals: dict[str, Any] = {item.name: getattr(run.totals, item.name) for item in fields(run.totals)}
    totals["config"] = json.dumps(dict(run.totals.config))
    conn.execute(text(_FINISH), {**totals, "c": customer_id, "id": run.run_id, "now": run.now})


def is_latest_run(conn: Connection, customer_id: str, document_id: str, run_id: str) -> bool:
    """True when ``run_id`` is the document's latest run (the run fence). The caller
    holds the family lock (``trash.lock_family``). One indexed query: O(log R)."""
    latest = conn.execute(text(_LATEST), {"c": customer_id, "d": document_id}).scalar()
    return latest is not None and str(latest) == run_id


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
