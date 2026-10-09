"""PRD 5 Q7, isolation: the upgrade sweep reads and changes each customer's documents
inside that customer's own transaction only.

* ``mark_for_upgrade`` on customer A's connection with B's document id changes nothing
  and writes no audit row, for either customer.
* ``oldest_read_locally`` on A's connection never finds B's document, even when it is
  the oldest in the database.
* ``upgrade_oldest`` marks and audits the chosen document under its own customer only.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.documents import mark_for_upgrade, oldest_read_locally
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.upgrade import upgrade_oldest
from tests.db_support import new_customer
from tests.test_upgrade_sweep_db import LaneQueue, read_locally, status_of, upgrade_audits

pytestmark = pytest.mark.db


def test_isolation_upgrade_sweep_never_reaches_another_customer(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    with db.customer(b) as conn:
        b_doc = read_locally(conn, b, year=1960)
    with db.customer(a) as conn:
        a_doc = read_locally(conn, a, year=1961)

    with db.customer(a) as conn:
        assert mark_for_upgrade(conn, a, b_doc) is False
        assert mark_for_upgrade(conn, b, b_doc) is False  # naming B on A's connection: RLS hides it
        found = oldest_read_locally(conn, a)
        assert found is not None and found[0] == a_doc
        assert oldest_read_locally(conn, b) is None
    assert status_of(db, b, b_doc) == "ready"
    assert upgrade_audits(db, a) == upgrade_audits(db, b) == []

    assert upgrade_oldest(db, LaneQueue()) == Job(b, b_doc)
    assert (upgrade_audits(db, b), upgrade_audits(db, a)) == ([(None, b_doc)], [])
    assert status_of(db, a, a_doc) == "ready"
    with db.customer(a) as conn:  # B's audit row is not A's to see
        seen = conn.execute(
            text("SELECT count(*) FROM audit_log WHERE action = 'document.read_upgrade' AND target_id = :d"),
            {"d": b_doc},
        ).scalar_one()
    assert seen == 0
    assert upgrade_oldest(db, LaneQueue()) == Job(a, a_doc)  # tidy up for the other tests
