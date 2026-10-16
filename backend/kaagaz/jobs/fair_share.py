"""Fair share between customers (PRD 5 Q5), and the one-copy rule for the sweeps (Q3).

Both rest on PostgreSQL session advisory locks, taken with ``pg_try_advisory_lock``
on a connection of their own in AUTOCOMMIT, so no transaction is held open while a
job runs:

* ``first_free_lock(engine, keys)`` takes the first of ``keys`` no one holds and
  yields it (None when every one is held), then lets it go. A worker that crashes
  closes its connection, and PostgreSQL lets the lock go with it. A lock that
  cannot be let go (the connection broke) is dropped with the connection.
* ``FairShare`` gives a customer MAX_ACTIVE_PER_CUSTOMER (2) slots,
  ``kaagaz.fair:<customer>:<slot>`` for slot 0 to MAX-1. A worker takes a free slot
  for the whole job, whichever lane it came from. With none free, the job is handed
  back (``Deferred``, FAIR_SHARE_DEFER_SECONDS 30) without counting an attempt, and
  the worker takes the next one. So one customer's batch of 50 never blocks
  another customer's work.
* Each sweep between jobs takes ``kaagaz.sweep:<name>`` (``sweep_lock``), so with
  WORKER_REPLICAS copies only one runs it at a time (Q3).

The keys are hashed with ``hashtextextended(key, 0)``, as the other advisory locks
here (db/documents.py). Nothing is read from any table, so no customer's data and
no count of another customer's work is seen: a customer's slots say only "free" or
"taken", and the deferral code is the same fixed ``customer_busy`` for everyone.

Cost: one connection and up to MAX ``pg_try_advisory_lock`` calls per job, one
unlock after it.
"""

import contextlib
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from kaagaz.db.engine import unavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.worker import Deferred

# PRD 5 Q5: how long a job of a customer at MAX_ACTIVE_PER_CUSTOMER waits.
FAIR_SHARE_DEFER_SECONDS = 30
CUSTOMER_BUSY = "customer_busy"

_TRY_LOCK = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
_UNLOCK = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")

# Takes the first free key of the ones given, for the length of the block.
Locks = Callable[[Sequence[str]], AbstractContextManager[str | None]]


@contextmanager
def first_free_lock(engine: Engine, keys: Sequence[str]) -> Iterator[str | None]:
    """The first of ``keys`` this call could lock, held to the end of the block; None
    when every one is held elsewhere. A database that cannot be reached is
    BackendUnavailable. O(len(keys)) statements."""
    with unavailable():
        conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        held = None
        with unavailable():
            for key in keys:
                if conn.execute(_TRY_LOCK, {"key": key}).scalar_one():
                    held = key
                    break
        try:
            yield held
        finally:
            if held is not None:
                try:
                    conn.execute(_UNLOCK, {"key": held})
                except SQLAlchemyError:
                    # The connection broke: dropping it ends its session, and the lock with it.
                    conn.invalidate()
    finally:
        conn.close()


# PRD 5 Q3: takes the named sweep's lock for the block, and says whether this copy has it.
SweepLock = Callable[[str], AbstractContextManager[bool]]


def no_lock(name: str) -> AbstractContextManager[bool]:
    """Every pass runs: one copy alone (the tests' default)."""
    return contextlib.nullcontext(True)


def sweep_lock(engine: Engine) -> SweepLock:
    """The sweeps' lock: ``pg_try_advisory_lock(hashtextextended('kaagaz.sweep:<name>', 0))``
    on a connection of its own (``first_free_lock``), held for the pass. A copy that
    cannot get it skips that pass. A database that cannot be reached is
    BackendUnavailable, as the pass's own would be."""

    @contextlib.contextmanager
    def lock(name: str) -> Iterator[bool]:
        with first_free_lock(engine, [f"kaagaz.sweep:{name}"]) as held:
            yield held is not None

    return lock


class FairShare:
    """The worker's admission (PRD 5 Q5): ``slot(job)`` holds one of the customer's
    ``max_active`` slots for the block, or raises Deferred(30, "customer_busy")."""

    def __init__(self, locks: Locks, max_active: int) -> None:
        """``locks`` is ``first_free_lock`` bound to the engine (tests give their own)."""
        if max_active < 1:
            raise ValueError("max_active must be at least 1")
        self._locks = locks
        self._max_active = max_active

    @contextmanager
    def slot(self, job: Job) -> Iterator[None]:
        with self._locks(self._keys(job.customer_id)) as held:
            if held is None:
                raise Deferred(FAIR_SHARE_DEFER_SECONDS, CUSTOMER_BUSY)
            yield

    def busy(self, customer_id: str) -> bool:
        """Is any of the customer's slots held now, so a job of theirs is being read (the
        stuck-document sweep, kaagaz/jobs/revive.py)? Each slot is tried and let go at once.
        O(max_active) locks."""
        for key in self._keys(customer_id):
            with self._locks([key]) as held:
                if held is None:
                    return True
        return False

    def _keys(self, customer_id: str) -> list[str]:
        return [f"kaagaz.fair:{customer_id}:{slot}" for slot in range(self._max_active)]
