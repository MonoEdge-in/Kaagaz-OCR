"""Read one page again and Keep as is (PRD 5 F5), in kaagaz.db.documents against a
real PostgreSQL (migration 0021).

A page is marked only on this customer's document out of Trash; the worker reads the
marks in page order; a failure for good (``set_failed``, ``mark_exhausted``) clears them
in its own transaction, behind the run fence, so a request after it keeps its mark.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments, accept_page, request_page_read_again
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.runs import PgRuns
from kaagaz.jobs.worker import Superseded
from kaagaz.scanning.gate import DocumentStatus
from tests.db_support import new_customer, new_trashed_document
from tests.test_pipeline_store_db import content, new_document, rows

pytestmark = pytest.mark.db

T0 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)
MARKS = "SELECT page_no, read_again_requested_at FROM pages WHERE document_id = :d ORDER BY page_no"
ACCEPTED = "SELECT page_no, accepted_by::text, accepted_at FROM pages WHERE document_id = :d ORDER BY page_no"


def _read(db: Database) -> tuple[str, str, str]:
    """(customer, its admin, a document saved with pages 1 and 2)."""
    customer, user = new_customer(db)
    doc = new_document(db, customer)
    PgPipelineStore(db).save(content(customer, doc))
    return customer, user, doc


def test_a_page_is_marked_to_be_read_again_and_the_worker_finds_it(db: Database) -> None:
    customer, _, doc = _read(db)
    documents = PgDocuments(db)
    assert documents.pages_to_read_again(customer, doc) == ()

    with db.customer(customer) as conn:
        assert request_page_read_again(conn, customer, doc, 2, now=T0)
        assert request_page_read_again(conn, customer, doc, 1, now=T1)
        assert not request_page_read_again(conn, customer, doc, 3, now=T1)  # no page 3

    assert documents.pages_to_read_again(customer, doc) == (1, 2)
    assert rows(db, customer, MARKS, doc) == [
        {"page_no": 1, "read_again_requested_at": T1},
        {"page_no": 2, "read_again_requested_at": T0},
    ]


def test_no_page_of_a_document_in_trash_or_of_another_customer_is_marked(db: Database) -> None:
    customer, user, doc = _read(db)
    other, _ = new_customer(db)
    binned = new_trashed_document(db, customer, deleted_at=T0)
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit)"
                " VALUES (:c, :d, 1, 612, 792, 'pt')"
            ),
            {"c": customer, "d": binned},
        )
        assert not request_page_read_again(conn, customer, binned, 1, now=T0)
        assert not accept_page(conn, customer, binned, 1, user_id=user, now=T0)
    with db.customer(other) as conn:
        assert not request_page_read_again(conn, other, doc, 1, now=T0)

    assert PgDocuments(db).pages_to_read_again(other, doc) == ()
    assert PgDocuments(db).pages_to_read_again(customer, doc) == ()
    assert PgDocuments(db).pages_to_read_again(customer, binned) == ()


def test_a_failure_for_good_clears_the_marks_with_it_behind_the_run_fence(db: Database) -> None:
    """FY2: set_failed clears the marks in its own transaction, so a request made once the
    failure has committed keeps its mark; a stale delivery changes nothing at all."""
    customer, _, doc = _read(db)
    with db.customer(customer) as conn:
        assert request_page_read_again(conn, customer, doc, 1, now=T0)
    old = PgRuns(db).start(customer, doc, now=T0)
    new = PgRuns(db).start(customer, doc, now=T1)
    documents = PgDocuments(db)

    with pytest.raises(Superseded):
        documents.set_failed(customer, doc, "unreadable", run_id=old)
    assert documents.pages_to_read_again(customer, doc) == (1,)
    documents.set_failed(customer, doc, "unreadable", run_id=new)
    assert documents.pages_to_read_again(customer, doc) == ()

    with db.customer(customer) as conn:  # the request the API accepts once it sees "failed"
        assert request_page_read_again(conn, customer, doc, 2, now=T1)
    assert documents.pages_to_read_again(customer, doc) == (2,)  # nothing left to wipe it


def test_the_last_try_failing_clears_the_marks_too(db: Database) -> None:
    customer, _, doc = _read(db)
    documents = PgDocuments(db)
    with db.customer(customer) as conn:
        assert request_page_read_again(conn, customer, doc, 1, now=T0)
    for attempt in range(1, 6):  # changed on purpose (issue 407): each try in turn, to the last
        documents.record_attempt(customer, doc, attempt, 5)
    documents.mark_exhausted(customer, doc, now=T1, max_attempts=5)
    assert documents.get_status(customer, doc) is DocumentStatus.FAILED
    assert documents.pages_to_read_again(customer, doc) == ()


def test_keep_as_is_names_who_and_when_on_that_page_only(db: Database) -> None:
    customer, user, doc = _read(db)
    other, _ = new_customer(db)
    with db.customer(customer) as conn:
        assert accept_page(conn, customer, doc, 2, user_id=user, now=T1)
        assert not accept_page(conn, customer, doc, 3, user_id=user, now=T1)
    with db.customer(other) as conn:
        assert not accept_page(conn, other, doc, 1, user_id=user, now=T1)

    assert rows(db, customer, ACCEPTED, doc) == [
        {"page_no": 1, "accepted_by": None, "accepted_at": None},
        {"page_no": 2, "accepted_by": user, "accepted_at": T1},
    ]
