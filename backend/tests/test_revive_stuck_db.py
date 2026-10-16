"""The stuck-document sweep (stuck reading, 16 Oct 2026) against a real PostgreSQL: only a
document in progress, out of Trash and stale is queued again (one more try counted, its
stale progress cleared) or, with no try left, failed as ``stuck_no_job`` with its running
runs ended; a ready, failed, rejected, trashed or fresh one is never touched."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.db.documents import STUCK_NO_JOB
from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.revive import FAILED, REQUEUED, revive_stuck
from tests.db_support import insert_document, new_customer

pytestmark = pytest.mark.db

T0 = datetime(2026, 10, 16, 0, 30, tzinfo=UTC)
_ROW = (
    "SELECT status, failure_code, attempts, max_attempts, progress FROM documents"
    " WHERE customer_id = :c AND id = :d"
)


def _row(db: Database, customer: str, document: str) -> tuple[object, ...]:
    with db.customer(customer) as conn:
        return tuple(conn.execute(text(_ROW), {"c": customer, "d": document}).one())


def test_only_stale_documents_in_progress_are_queued_again_or_failed(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        lost = insert_document(conn, customer, status="received")
        reading = insert_document(conn, customer, status="clean", attempts=1, max_attempts=5)
        spent = insert_document(conn, customer, status="clean", attempts=5, max_attempts=5)
        ready = insert_document(conn, customer, status="ready")
        failed = insert_document(conn, customer, status="failed", failure_code="unreadable")
        trashed = insert_document(conn, customer, status="received", deleted_at=T0, deleted_by=admin)
        conn.execute(
            text("UPDATE documents SET progress = CAST(:p AS jsonb) WHERE customer_id = :c AND id = :d"),
            {"p": '{"step": "reading", "done": 0, "total": 1}', "c": customer, "d": reading},
        )
    run = PgRuns(db).start(customer, spent, now=T0)
    sent: list[tuple[Job, int]] = []

    def revive(stale_seconds: int) -> list[tuple[Job, str]]:
        # Every other customer counts as busy, so the shared database's other rows are left alone.
        return revive_stuck(
            db,
            lambda job, tries: sent.append((job, tries)),
            lambda c: c != customer,
            stale_seconds=stale_seconds,
            max_attempts=5,
        )

    assert revive(3600) == []  # nothing is an hour old
    done = {job.document_id: outcome for job, outcome in revive(0)}

    assert done == {lost: REQUEUED, reading: REQUEUED, spent: FAILED}
    assert sorted((job.document_id, tries) for job, tries in sent) == sorted([(lost, 0), (reading, 1)])
    assert _row(db, customer, lost) == ("received", None, 1, 5, None)
    assert _row(db, customer, reading) == ("clean", None, 2, 5, None)  # the stale "Reading page 1" is gone
    assert _row(db, customer, spent)[:2] == ("failed", STUCK_NO_JOB)
    with db.customer(customer) as conn:
        status = conn.execute(
            text("SELECT status, error_code FROM processing_runs WHERE customer_id = :c AND id = :r"),
            {"c": customer, "r": run},
        ).one()
    assert tuple(status) == ("failed", STUCK_NO_JOB)
    assert _row(db, customer, ready)[0] == "ready"
    assert _row(db, customer, failed)[:2] == ("failed", "unreadable")
    assert _row(db, customer, trashed)[:3] == ("received", None, 0)


def test_a_queue_that_is_down_rolls_the_count_back(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        lost = insert_document(conn, customer, status="received")

    def down(job: Job, tries: int) -> None:
        raise BackendUnavailable("queue")

    with pytest.raises(BackendUnavailable):
        revive_stuck(db, down, lambda c: c != customer, stale_seconds=0, max_attempts=5)
    assert _row(db, customer, lost)[:3] == ("received", None, 0)
