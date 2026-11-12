"""The reading pause keeps organisations apart (#478, 0066): one organisation's counter and holds
are never seen, written, counted or released from another's connection, and one organisation at
its limit never pauses another."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import reading_days
from kaagaz.db.engine import Database
from kaagaz.jobs.reading_pause import ReadingPause, ReadingPaused
from tests.db_support import new_customer
from tests.test_migration_0023_db import as_owner
from tests.test_reading_pause_db import document, held, set_limit

pytestmark = pytest.mark.db


def test_one_organisations_counter_and_holds_never_reach_another(db: Database) -> None:
    now = datetime.now(UTC)
    day = reading_days.india_day(now)
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    set_limit(db, a, 1)
    set_limit(db, b, 1)
    with db.customer(a) as conn:
        reading_days.count(conn, a, day, pages=1, cost_usd=0.5)
    waiting = document(db, a)
    pause = ReadingPause(db, clock=lambda: now)
    with pytest.raises(ReadingPaused), pause.admit(a, waiting):
        pass

    with db.customer(b) as conn:
        assert conn.execute(text("SELECT customer_id FROM reading_days")).all() == []  # A's row unseen
        theirs = reading_days.state(conn, b, day)
        assert theirs.pages_today == 0  # A's pages never count for B
        # Changed on purpose (#478 review): the release finds the documents, then ends each hold.
        assert reading_days.waiting(conn, a, 10) == []  # B's connection finds none of A's holds
        reading_days.unhold(conn, a, waiting)  # nor ends one
        reading_days.end_stale_holds(conn, a)
        reading_days.count(conn, b, day, pages=0, cost_usd=0.0)
        assert (
            conn.execute(text("SELECT count(*) FROM documents WHERE reading_held IS NOT NULL")).scalar() == 0
        )
    with pause.admit(b, document(db, b)):
        pass  # A at its limit never pauses B
    assert held(db, a, waiting) == "daily_limit"
    assert as_owner(
        db, "SELECT pages FROM reading_days WHERE customer_id = :c AND day = :d", {"c": a, "d": day}
    ) == [(1,)]

    with pytest.raises(DBAPIError), db.customer(b) as conn:  # nor can B write a row as A's
        reading_days.count(conn, a, day, pages=5, cost_usd=0.0)
