"""The upgrade sweep (PRD 5 Q7): pages read locally are read again by the main reader.

While Gemini was down for longer than READER_OUTAGE_FALLBACK_MINUTES, pages were read
by RapidOCR and marked ``read_locally``. Once it is back, ``UpgradeLocalReads`` (built
like EmbedMissing, between jobs) queues those documents for a read again, oldest first:

* ``upgrade_oldest`` takes the one document with a locally read page uploaded first,
  across every active customer (one indexed query per customer, ``oldest_read_locally``),
  marks it (``mark_for_upgrade``: the family lock, back to ``received``, audited as
  ``document.read_upgrade`` with no actor) and queues it in the main lane, in one
  transaction, so a queue that is down changes nothing. Never the express lane: the
  sweep never pushes ahead of a customer's own upload. An organisation whose reading is
  paused (#478, kaagaz/jobs/reading_pause.py) is passed over, so a ready document is never
  turned into a held one (review, 3 Nov 2026).
* One document every UPGRADE_EVERY (3 minutes: 20 an hour) while the reader's gate is
  ``working``. While it is ``failing`` or ``down``, one every PROBE_EVERY (15 minutes):
  that read is the probe that finds the reader back. With a key problem it sends none,
  since the fix is a person's (G6).
* With ``copies`` (WORKER_REPLICAS) copies of the worker, each waits ``copies`` times as
  long between its passes, so together they keep to 20 an hour. The pass runs under the
  sweep lock (Q3), so two copies never pass at once.
* A database or queue error is logged by its type and the jobs go on, as in
  EmbedMissing. Logs hold the document's ids and a code only.

Cost per pass: one statement for the C customers, then one status lookup and one
``oldest_read_locally`` per customer, and the pause's ``state`` for one that has a document,
then 3 statements and one send for the one marked.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from kaagaz.ai.openrouter import KEY_PROBLEM, WORKING
from kaagaz.db import reading_days
from kaagaz.db.customers import is_active
from kaagaz.db.documents import mark_for_upgrade, oldest_read_locally
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job, JobQueue
from kaagaz.jobs.fair_share import SweepLock, no_lock
from kaagaz.jobs.reading_pause import pause_reason
from kaagaz.jobs.worker import RunsJobs
from kaagaz.search.indexing import customer_ids

logger = logging.getLogger("kaagaz.worker")

# PRD 5 Q7, V9: one document every 3 minutes (20 an hour) while the reader works; one
# every 15 minutes, as a probe, while it is down.
UPGRADE_EVERY = timedelta(minutes=3)
PROBE_EVERY = timedelta(minutes=15)


def upgrade_oldest(db: Database, queue: JobQueue) -> Job | None:
    """Mark the oldest ready document with a locally read page, of any active customer,
    for a read again and queue it in the main lane; its job, or None when there is none
    (or it was taken by "read again" in the moment since). A paused organisation's are passed
    over. Database and queue errors propagate: the caller logs them."""
    with db.system() as conn:
        customers = customer_ids(conn)
    day = reading_days.india_day(datetime.now(UTC))  # the live counter's day (kaagaz.ai.usage)
    oldest: tuple[datetime, str, str] | None = None
    for customer in customers:
        with db.customer(customer) as conn:
            found = oldest_read_locally(conn, customer) if is_active(conn, customer) else None
            if found is not None and pause_reason(reading_days.state(conn, customer, day)) is not None:
                found = None
        if found is not None and (oldest is None or (found[1], customer, found[0]) < oldest):
            oldest = (found[1], customer, found[0])
    if oldest is None:
        return None
    _, customer, document = oldest
    job = Job(customer, document)
    with db.customer(customer) as conn:
        if not mark_for_upgrade(conn, customer, document):
            return None
        queue.enqueue(job)  # the main lane; last, so a queue that is down rolls the mark back
    return job


class UpgradeLocalReads:
    """Wraps the worker: before a turn, runs ``upgrade`` (``upgrade_oldest`` bound to the
    database and queue) when due on ``clock``: UPGRADE_EVERY after the last pass while
    ``reader_state()`` (the gate's state) is ``working``, PROBE_EVERY while it is not,
    each times ``copies``. The first turn runs it. With a key problem no document is
    sent. ``upgrade`` None (the reader is not Gemini) does nothing. The time is noted
    first, so a pass that fails waits too. O(1) per turn, plus one pass when due."""

    def __init__(
        self,
        worker: RunsJobs,
        upgrade: Callable[[], Job | None] | None,
        reader_state: Callable[[], str],
        clock: Callable[[], datetime],
        *,
        copies: int = 1,
        lock: SweepLock = no_lock,
    ) -> None:
        if copies < 1:
            raise ValueError("copies must be at least 1")
        self._worker = worker
        self._upgrade = upgrade
        self._reader_state = reader_state
        self._clock = clock
        self._copies = copies
        self._lock = lock
        self._next: datetime | None = None

    def run_once(self) -> bool:
        now = self._clock()
        if self._upgrade is not None and (self._next is None or now >= self._next):
            state = self._reader_state()
            self._next = now + (UPGRADE_EVERY if state == WORKING else PROBE_EVERY) * self._copies
            if state != KEY_PROBLEM:
                self._pass(self._upgrade, state)
        return self._worker.run_once()

    def _pass(self, upgrade: Callable[[], Job | None], state: str) -> None:
        try:
            with self._lock("upgrade_local_reads") as mine:
                job = upgrade() if mine else None
        except (SQLAlchemyError, BackendUnavailable) as error:
            logger.error("read upgrade failed", extra={"code": type(error).__name__})
            return
        if job is not None:
            code = "read_upgrade" if state == WORKING else "read_upgrade_probe"
            logger.info(
                "read upgrade queued",
                extra={"customer_id": job.customer_id, "document_id": job.document_id, "code": code},
            )
