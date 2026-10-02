"""Runs, progress, read again and the month's pages across customers (PRD 2 R6, X1, X3).

Customer B, using customer A's ids, can start no run on A's document, write no
progress on it, send it to be read again, fail it, or count A's pages.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments, mark_for_reprocess
from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns, close_running, pages_by_route
from kaagaz.jobs.worker import JobFailed, Superseded
from kaagaz.pipeline import FinishedRun, Progress
from tests.db_support import insert_document, new_customer
from tests.test_runs_db import totals

pytestmark = pytest.mark.db

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)


def state(db: Database, customer: str, doc: str) -> tuple[object, ...]:
    with db.customer(customer) as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT status, failure_code, attempts, progress,"
                    " (SELECT count(*) FROM processing_runs r WHERE r.document_id = d.id)"
                    " FROM documents d WHERE customer_id = :c AND id = :d"
                ),
                {"c": customer, "d": doc},
            ).one()
        )


def test_b_can_start_no_run_on_a_document_of_a(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    with db.customer(a) as conn:
        doc = insert_document(conn, a, status="clean")
    with pytest.raises(JobFailed) as failed:
        PgRuns(db).start(b, doc, now=T0)
    assert failed.value.code == "document_missing"
    assert state(db, a, doc) == ("clean", None, 0, None, 0)


def test_b_changes_nothing_on_a_document_or_run_of_a(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    with db.customer(a) as conn:
        doc = insert_document(conn, a, status="clean")
    run = PgRuns(db).start(a, doc, now=T0)
    before = state(db, a, doc)

    PgDocuments(db).set_progress(b, doc, Progress("reading", 1, 2))
    PgRuns(db).finish(b, FinishedRun(run, totals("failed", error_code="corrupt_file"), T0))
    with pytest.raises(Superseded):  # B has no such run: nothing is failed
        PgDocuments(db).set_failed(b, doc, "corrupt_file", run_id=run)
    with db.customer(b) as conn:
        close_running(conn, b, doc, status="failed", error_code="retries_exhausted", now=T0)
        assert mark_for_reprocess(conn, b, doc, per_hour=20) is None

    assert state(db, a, doc) == before
    with db.customer(a) as conn:
        assert (
            conn.execute(text("SELECT status FROM processing_runs WHERE id = :r"), {"r": run}).scalar()
            == "running"
        )


def test_b_never_counts_the_pages_of_a(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    with db.customer(a) as conn:
        doc = insert_document(conn, a)
    run = PgRuns(db).start(a, doc, now=T0)
    PgRuns(db).finish(a, FinishedRun(run, totals(), T0))
    window = {"start": T0 - timedelta(days=1), "end": T0 + timedelta(days=1)}
    with db.customer(b) as conn:
        assert pages_by_route(conn, b, **window) == {"cells": 0, "text_layer": 0, "ocr": 0, "blank": 0}
    with db.customer(a) as conn:
        assert pages_by_route(conn, a, **window)["ocr"] == 3
