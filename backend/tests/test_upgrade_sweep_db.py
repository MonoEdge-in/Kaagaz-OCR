"""PRD 5 Q7 against a real PostgreSQL: ``upgrade_oldest`` and ``mark_for_upgrade``.

* The oldest ready document with a page read locally, across active customers, is
  set back to ``received``, audited as ``document.read_upgrade`` with no actor, and
  queued in the main lane, in one transaction.
* A disabled customer's, a trashed or a not-ready document is passed over; a queue
  that is down rolls the mark back.
* The users' "read again" limits do not apply, and the sweep's own rows don't count
  against them.

The test database is shared, so this test's documents are dated 1990 to be the oldest.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, text

from kaagaz.db import customers
from kaagaz.db.documents import mark_for_reprocess, mark_for_upgrade
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.upgrade import upgrade_oldest
from tests.db_support import insert_document, new_customer

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


class LaneQueue:
    def __init__(self, *, down: bool = False) -> None:
        self.sent: list[tuple[Job, bool]] = []
        self.down = down

    def enqueue(self, job: Job, *, express: bool = False) -> None:
        if self.down:
            raise BackendUnavailable("queue")
        self.sent.append((job, express))


def read_locally(conn: Connection, customer_id: str, *, year: int, **columns: object) -> str:
    """A document from ``year`` whose page 1 the backup reader read."""
    doc = insert_document(conn, customer_id, created_at=datetime(year, 1, 1, tzinfo=UTC), **columns)
    conn.execute(
        text(
            "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, read_issues)"
            " VALUES (:c, :d, 1, 612, 792, 'pt', ARRAY['read_locally'])"
        ),
        {"c": customer_id, "d": doc},
    )
    return doc


def status_of(db: Database, customer_id: str, document_id: str) -> str:
    with db.customer(customer_id) as conn:
        return str(
            conn.execute(
                text("SELECT status FROM documents WHERE customer_id = :c AND id = :d"),
                {"c": customer_id, "d": document_id},
            ).scalar_one()
        )


def upgrade_audits(db: Database, customer_id: str) -> list[tuple[object, str]]:
    with db.customer(customer_id) as conn:
        rows = conn.execute(
            text(
                "SELECT actor_user_id, target_id FROM audit_log"
                " WHERE customer_id = :c AND action = 'document.read_upgrade'"
            ),
            {"c": customer_id},
        ).all()
    return [(row[0], str(row[1])) for row in rows]


def test_upgrade_sweep_marks_audits_and_queues_the_oldest_in_the_main_lane(db: Database) -> None:
    disabled, _ = new_customer(db)
    first, _ = new_customer(db)
    second, _ = new_customer(db)
    with db.customer(disabled) as conn:
        stopped = read_locally(conn, disabled, year=1980)
    with db.engine.begin() as conn:  # the owner, as the operator's CLI
        customers.set_status(conn, disabled, customers.DISABLED, now=NOW)
    with db.customer(first) as conn:
        trashed = read_locally(conn, first, year=1981, deleted_at=NOW)
        reading = read_locally(conn, first, year=1982, status="clean")
        newer = read_locally(conn, first, year=1991)
        plain = insert_document(conn, first, created_at=datetime(1983, 1, 1, tzinfo=UTC))  # read by Gemini
    with db.customer(second) as conn:
        oldest = read_locally(conn, second, year=1990)

    queue = LaneQueue()
    assert upgrade_oldest(db, queue) == Job(second, oldest)
    assert queue.sent == [(Job(second, oldest), False)]  # never the express lane
    assert status_of(db, second, oldest) == "received"
    assert upgrade_audits(db, second) == [(None, oldest)]

    assert upgrade_oldest(db, queue) == Job(first, newer)  # the next oldest, the rest passed over
    for customer, document, status in (
        (disabled, stopped, "ready"),
        (first, trashed, "ready"),
        (first, reading, "clean"),
        (first, plain, "ready"),
    ):
        assert status_of(db, customer, document) == status


def test_upgrade_sweep_with_the_queue_down_changes_nothing(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = read_locally(conn, customer, year=1970)
    with pytest.raises(BackendUnavailable):
        upgrade_oldest(db, LaneQueue(down=True))
    assert status_of(db, customer, doc) == "ready"
    assert upgrade_audits(db, customer) == []
    assert upgrade_oldest(db, LaneQueue()) == Job(customer, doc)  # tidy up for the other tests


def test_mark_for_upgrade_has_no_user_limits_and_is_not_counted_against_them(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer)
        assert mark_for_upgrade(conn, customer, doc) is True
        assert mark_for_upgrade(conn, customer, doc) is False  # now being read: nothing changes
        conn.execute(text("UPDATE documents SET status = 'ready' WHERE id = :d"), {"d": doc})
        assert mark_for_upgrade(conn, customer, doc) is True  # no cooldown
        conn.execute(text("UPDATE documents SET status = 'ready' WHERE id = :d"), {"d": doc})
        # The user's own read again is not held back by the sweep's.
        assert mark_for_reprocess(conn, customer, doc, per_hour=1) == "ready"
    assert len(upgrade_audits(db, customer)) == 2
