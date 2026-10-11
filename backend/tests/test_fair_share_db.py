"""PRD 5 Q5 against a real PostgreSQL: the fair-share slots are session advisory locks.

* A customer has MAX_ACTIVE_PER_CUSTOMER slots across every worker copy (each copy
  is its own connection here); the next job waits (Deferred 30, ``customer_busy``),
  and another customer's slots are its own.
* A slot is let go when the job ends, and when its connection dies (a worker that
  crashed): PostgreSQL drops a session's advisory locks with it.
"""

import contextlib
import functools

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.fair_share import FairShare, first_free_lock
from kaagaz.jobs.worker import Deferred
from tests.db_support import new_customer

pytestmark = pytest.mark.db


def fair_share(db: Database) -> FairShare:
    return FairShare(functools.partial(first_free_lock, db.engine), 2)


def test_fair_share_gives_each_customer_two_slots_across_the_copies(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    share = fair_share(db)
    with contextlib.ExitStack() as copies:
        copies.enter_context(share.slot(Job(a, "d1")))
        copies.enter_context(share.slot(Job(a, "d2")))
        with pytest.raises(Deferred) as waited, share.slot(Job(a, "d3")):
            pass
        assert (waited.value.seconds, waited.value.code) == (30, "customer_busy")
        with share.slot(Job(b, "d4")):  # B's slots are its own
            pass
    with share.slot(Job(a, "d3")):  # both of A's were let go
        pass


def test_fair_share_slot_is_let_go_when_its_connection_dies(db: Database) -> None:
    a, _ = new_customer(db)
    share = fair_share(db)
    key = f"kaagaz.fair:{a}:0"
    with share.slot(Job(a, "d1")):
        with db.engine.connect() as admin:  # the worker copy crashes
            killed = admin.execute(
                text(
                    "SELECT pg_terminate_backend(l.pid) FROM pg_locks l WHERE l.locktype = 'advisory'"
                    " AND ((l.classid::bigint << 32) | l.objid::bigint) = hashtextextended(:key, 0)"
                ),
                {"key": key},
            ).all()
        assert len(killed) == 1
        with first_free_lock(db.engine, [key]) as held:
            assert held == key  # free again without waiting for the job
    # Leaving the block lets go of a lock on a dead connection without an error.
