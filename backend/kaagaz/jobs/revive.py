"""The stuck-document sweep (stuck reading, 16 Oct 2026): a document still in progress that
no job is working on is queued again, or failed once it has no try left.

A document can be left ``received`` or ``clean`` with no job for it: the API stopped between
saving an upload and queueing its job, a "read again" whose job was taken (and skipped as
settled) before its transaction committed, or a last delivery that found fewer tries recorded
than JOB_MAX_ATTEMPTS. Nothing else would ever look at it again. ``ReviveStuck`` (built like
UpgradeLocalReads, between jobs) runs ``revive_stuck`` every REVIVE_EVERY:

* A document is stuck when nothing was written on it (each try and each progress step write
  it) for ``stale_seconds``: QUEUE_LEASE_SECONDS x JOB_MAX_ATTEMPTS, every try's lease
  (PROVISIONAL), and its customer has no fair-share slot held, so none of its jobs is being
  read. A job that waits for a slot leaves its customer busy, so a long batch is never touched.
  A job handed back under the platform's documents-at-once cap (package S) holds no slot, so
  its hand-back writes on its document instead (``PgDocuments.still_waiting``).
* With a try left, one more try is counted on it, its stale progress cleared, and its job
  queued in the main lane carrying the tries before it, in one transaction (a queue that is
  down rolls the count back). Its next delivery is that try, so the count only goes up.
* With no try left, it is failed with ``stuck_no_job``, its runs ended, as the last
  delivery does (``mark_exhausted``). So each document is queued again at most
  JOB_MAX_ATTEMPTS times, and then settled: never a loop.
* At most REVIVE_PASS_DOCUMENTS a pass, longest stuck first in each customer. With a key
  problem (the reader's gate) no pass runs: those documents wait for a person's fix.
* A duplicate job is harmless: a settled document's delivery is skipped, and a newer run
  fences an older one (kaagaz.pipeline).

A database or queue error is logged by its type and the jobs go on, as in UpgradeLocalReads.
Logs hold ids and a code only. Each customer is read in its own transaction as that customer.
Cost per pass: one statement for the C customers, then up to M slot locks and one query per
customer, then 1 to 3 statements and one send per document.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from kaagaz.ai.openrouter import KEY_PROBLEM
from kaagaz.db.documents import fail_stuck, requeue_stuck, stuck_documents
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.fair_share import SweepLock, no_lock
from kaagaz.jobs.worker import RunsJobs
from kaagaz.search.indexing import customer_ids

logger = logging.getLogger("kaagaz.worker")

# PROVISIONAL (stuck reading, 16 Oct 2026): how often the sweep runs, and how many documents
# one pass may queue again or fail.
REVIVE_EVERY = timedelta(minutes=10)
REVIVE_PASS_DOCUMENTS = 20

REQUEUED = "stuck_requeued"
FAILED = "stuck_no_job"

# Queues a job carrying the tries it used before (SqsQueue.enqueue's ``tries``).
Send = Callable[[Job, int], None]


def revive_stuck(
    db: Database,
    send: Send,
    busy: Callable[[str], bool],
    *,
    stale_seconds: int,
    max_attempts: int,
    limit: int = REVIVE_PASS_DOCUMENTS,
) -> list[tuple[Job, str]]:
    """Queue again, or fail, at most ``limit`` stuck documents (the module notes) of customers
    not ``busy``; each one's job and what was done (REQUEUED or FAILED). Database and queue
    errors propagate: the caller logs them."""
    with db.system() as conn:
        customers = customer_ids(conn)
    done: list[tuple[Job, str]] = []
    for customer in customers:
        if len(done) >= limit:
            break
        if busy(customer):
            continue
        with db.customer(customer) as conn:
            stuck = stuck_documents(conn, customer, stale_seconds=stale_seconds, limit=limit - len(done))
        for document, attempts in stuck:
            job = Job(customer, document)
            with db.customer(customer) as conn:
                if attempts >= max_attempts:
                    now = datetime.now(UTC)
                    marked = fail_stuck(
                        conn,
                        customer,
                        document,
                        stale_seconds=stale_seconds,
                        max_attempts=max_attempts,
                        now=now,
                    )
                    if marked:
                        done.append((job, FAILED))
                    continue
                attempt = requeue_stuck(
                    conn, customer, document, stale_seconds=stale_seconds, max_attempts=max_attempts
                )
                if attempt is not None:
                    send(job, attempt - 1)  # last, so a queue that is down rolls the count back
                    done.append((job, REQUEUED))
    return done


class ReviveStuck:
    """Wraps the worker: before a turn, runs ``revive`` (``revive_stuck`` bound to the database,
    the queue, the fair share and the settings) at most every REVIVE_EVERY on ``clock``, under
    ``lock("revive_stuck")``, unless ``reader_state()`` is a key problem. The first turn runs
    it. The time is noted first, so a pass that fails waits too. O(1) per turn, plus one pass."""

    def __init__(
        self,
        worker: RunsJobs,
        revive: Callable[[], list[tuple[Job, str]]],
        reader_state: Callable[[], str],
        clock: Callable[[], datetime],
        *,
        lock: SweepLock = no_lock,
    ) -> None:
        self._worker = worker
        self._revive = revive
        self._reader_state = reader_state
        self._clock = clock
        self._lock = lock
        self._next: datetime | None = None

    def run_once(self) -> bool:
        now = self._clock()
        if self._next is None or now >= self._next:
            self._next = now + REVIVE_EVERY
            if self._reader_state() != KEY_PROBLEM:
                self._pass()
        return self._worker.run_once()

    def _pass(self) -> None:
        try:
            with self._lock("revive_stuck") as mine:
                done = self._revive() if mine else []
        except (SQLAlchemyError, BackendUnavailable) as error:
            logger.error("stuck documents failed", extra={"code": type(error).__name__})
            return
        for job, code in done:
            logger.warning(
                "stuck document %s",
                "queued again" if code == REQUEUED else "failed",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": code},
            )
