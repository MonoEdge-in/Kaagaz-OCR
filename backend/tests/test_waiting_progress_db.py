"""PRD 5 Q6 against a real PostgreSQL: ``clear_progress(..., waiting="reader")`` writes
``{"step": "waiting"}`` on a document still in progress, fenced like a plain clear."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from kaagaz.pipeline import Progress
from tests.db_support import insert_document, new_customer

pytestmark = pytest.mark.db

T0, T1 = datetime(2026, 10, 9, 10, 0, tzinfo=UTC), datetime(2026, 10, 9, 10, 5, tzinfo=UTC)


def progress_of(db: Database, customer_id: str, document_id: str) -> object:
    with db.customer(customer_id) as conn:
        return conn.execute(
            text("SELECT progress FROM documents WHERE customer_id = :c AND id = :d"),
            {"c": customer_id, "d": document_id},
        ).scalar_one()


def test_clear_progress_waiting_for_the_reader_writes_the_waiting_step(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc, ready = insert_document(conn, customer, status="clean"), insert_document(conn, customer)
    old = PgRuns(db).start(customer, doc, now=T0)
    new = PgRuns(db).start(customer, doc, now=T1)
    documents = PgDocuments(db)
    documents.set_progress(customer, doc, Progress("reading", 1, 5), run_id=new)

    documents.clear_progress(customer, doc, run_id=old, waiting="reader")  # a stale delivery: nothing
    assert progress_of(db, customer, doc) == {"step": "reading", "done": 1, "total": 5}
    documents.clear_progress(customer, doc, run_id=new, waiting="reader")
    assert progress_of(db, customer, doc) == {"step": "waiting"}
    documents.clear_progress(customer, ready, waiting="reader")  # finished: left alone
    assert progress_of(db, customer, ready) is None
    documents.clear_progress(customer, doc)  # the plain clear still clears
    assert progress_of(db, customer, doc) is None


def test_clear_progress_waits_for_nothing_but_the_reader(db: Database) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(ValueError, match="waiting"):
        PgDocuments(db).clear_progress(customer, "00000000-0000-4000-8000-000000000001", waiting="storage")
