"""A read document becomes its family's current version, or not (PRD 1 F6).

PROVISIONAL (plan 7.3): the highest ready ``version_no`` is current, so versions
may finish reading in any order. PRD section 11 says "the last one to reach
ready becomes current"; under this rule an older version that finishes last
stays not current. ``mark_ready`` takes the family lock first, so two versions
finishing at the same moment queue, and a family never has 0 or 2 current rows.
"""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, text

from kaagaz.db.documents import PgDocuments, mark_ready
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.db.runs import PgRuns, is_run_open
from kaagaz.jobs.worker import Superseded
from kaagaz.pipeline import DocumentContent
from tests.db_support import insert_document, name_session, new_customer, wait_until_blocked

pytestmark = pytest.mark.db


def _versions(conn: Connection, customer: str, *statuses: str) -> list[str]:
    """One family, version 1 first, each version with its status. Only version 1
    starts current, as an upload leaves it (a new version is inserted not current)."""
    first = insert_document(conn, customer, status=statuses[0])
    rest = [
        insert_document(conn, customer, family_id=first, version_no=number, is_current=False, status=status)
        for number, status in enumerate(statuses[1:], start=2)
    ]
    return [first, *rest]


def _state(db: Database, customer: str, ids: list[str]) -> list[tuple[str, bool, int | None]]:
    """(status, is_current, page_count) of each id, in the order given."""
    with db.customer(customer) as conn:
        rows = {
            str(row[0]): (str(row[1]), bool(row[2]), row[3])
            for row in conn.execute(
                text(
                    "SELECT id, status, is_current, page_count FROM documents"
                    " WHERE customer_id = :c AND id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"c": customer, "ids": ids},
            )
        }
    return [rows[document] for document in ids]


def test_a_first_version_becomes_ready_and_current_with_its_page_count(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        first = insert_document(conn, customer, status="clean", failure_code="scanner_busy")

    PgDocuments(db).mark_ready(customer, first, 3)

    assert _state(db, customer, [first]) == [("ready", True, 3)]
    with db.customer(customer) as conn:
        failure_code: object = conn.execute(
            text("SELECT failure_code FROM documents WHERE customer_id = :c AND id = :d"),
            {"c": customer, "d": first},
        ).scalar_one()
    assert failure_code is None


def test_a_newer_version_that_becomes_ready_takes_over_as_current(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        first, second = _versions(conn, customer, "ready", "clean")

    PgDocuments(db).mark_ready(customer, second, 2)

    assert _state(db, customer, [first, second]) == [("ready", False, None), ("ready", True, 2)]


def test_an_older_version_that_finishes_after_a_newer_one_is_ready_but_not_current(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        first, second, third = _versions(conn, customer, "clean", "clean", "clean")
    documents = PgDocuments(db)

    documents.mark_ready(customer, third, 1)
    documents.mark_ready(customer, first, 1)
    documents.mark_ready(customer, second, 1)
    documents.mark_ready(customer, third, 1)  # a job delivered twice changes nothing

    assert _state(db, customer, [first, second, third]) == [
        ("ready", False, 1),
        ("ready", False, 1),
        ("ready", True, 1),
    ]


def test_a_newer_version_still_reading_does_not_stop_an_older_one_becoming_current(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        first, second, third = _versions(conn, customer, "ready", "clean", "received")

    PgDocuments(db).mark_ready(customer, second, 1)

    assert _state(db, customer, [first, second, third]) == [
        ("ready", False, None),
        ("ready", True, 1),
        ("received", False, None),
    ]


def test_a_rejected_version_stays_rejected_and_the_current_one_stays_current(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        first, second = _versions(conn, customer, "ready", "rejected")

    PgDocuments(db).mark_ready(customer, second, 1)

    assert _state(db, customer, [first, second]) == [("ready", True, None), ("rejected", False, None)]


def test_a_missing_or_other_customers_document_changes_nothing(db: Database) -> None:
    customer, _ = new_customer(db)
    other, _ = new_customer(db)
    with db.customer(other) as conn:
        [theirs] = _versions(conn, other, "clean")

    documents = PgDocuments(db)
    documents.mark_ready(customer, theirs, 1)
    documents.mark_ready(customer, str(uuid.uuid4()), 1)

    assert _state(db, other, [theirs]) == [("clean", True, None)]


@pytest.mark.parametrize("newer_first", [True, False])
def test_two_versions_finishing_at_once_leave_exactly_one_current(db: Database, newer_first: bool) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        first, second, third = _versions(conn, customer, "ready", "clean", "clean")
    ahead, behind = (third, second) if newer_first else (second, third)
    session = f"ready-{uuid.uuid4().hex[:16]}"

    def finish_behind() -> None:
        with db.customer(customer) as conn:
            name_session(conn, session)
            mark_ready(conn, customer, behind, 1)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            mark_ready(conn, customer, ahead, 1)
            finishing = pool.submit(finish_behind)
            wait_until_blocked(db, application_name=session)
        finishing.result(timeout=10)

    assert _state(db, customer, [first, second, third]) == [
        ("ready", False, None),
        ("ready", False, 1),
        ("ready", True, 1),
    ]


def test_ready_first_keeps_its_run_open_and_progress_and_a_read_again_refuses_its_writes(
    db: Database,
) -> None:
    """Ready first (16 Oct 2026): ready with the run still open (its understanding goes on) and
    the progress kept; a plain ready clears it; once a read again set the document back to
    received, the run's second save and its ready are refused (Superseded), changing nothing."""
    customer, _ = new_customer(db)
    marker = {"step": "understanding", "done": 0, "total": 2, "ready_at": "2026-10-16T10:00:00+00:00"}
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, status="clean")
        conn.execute(
            text("UPDATE documents SET progress = CAST(:p AS jsonb) WHERE customer_id = :c AND id = :d"),
            {"p": json.dumps(marker), "c": customer, "d": document},
        )
    run_id = PgRuns(db).start(customer, document, now=datetime(2026, 10, 16, 10, 0, tzinfo=UTC))
    documents = PgDocuments(db)

    documents.mark_ready(customer, document, 1, open_run=run_id)

    def state() -> tuple[str, object, bool]:
        with db.customer(customer) as conn:
            status, progress = conn.execute(
                text("SELECT status, progress FROM documents WHERE customer_id = :c AND id = :d"),
                {"c": customer, "d": document},
            ).one()
            return str(status), progress, is_run_open(conn, customer, document, run_id)

    assert state() == ("ready", marker, True)
    with db.customer(customer) as conn:  # a read again, as mark_for_reprocess leaves it
        conn.execute(
            text(
                "UPDATE documents SET status = 'received', progress = NULL WHERE customer_id = :c AND id = :d"
            ),
            {"c": customer, "d": document},
        )
    with pytest.raises(Superseded):
        PgPipelineStore(db).save(
            DocumentContent(customer, document, [], [], [], [], reader=""), run_id=run_id
        )
    with pytest.raises(Superseded):
        documents.mark_ready(customer, document, 1, open_run=run_id)
    assert state() == ("received", None, True)
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "UPDATE documents SET status = 'clean', progress = CAST(:p AS jsonb)"
                " WHERE customer_id = :c AND id = :d"
            ),
            {"p": json.dumps(marker), "c": customer, "d": document},
        )
    documents.mark_ready(customer, document, 1)  # no run: ready, and the progress cleared
    assert state() == ("ready", None, True)
