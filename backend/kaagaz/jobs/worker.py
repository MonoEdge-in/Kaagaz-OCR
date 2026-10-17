"""Run jobs from the queue, one at a time.

``run_once`` takes one job, runs the handler and settles the job:

* the handler returns: the job is acked (done).
* the handler raises JobFailed(retryable=False): the job is buried in the
  failure bin with its code, and never retried.
* the handler raises JobFailed(retryable=True): the job is handed back (``defer``,
  counted as an attempt) to come out again after ``retry_delay``: 1, 2, 4, 8, then
  15 minutes (council 16 Oct 2026; it used to wait out the whole lease).
* the lease is lost (LeaseLost from a heartbeat, ack or bury): the job has
  already been handed to another delivery, so this worker leaves it alone.
  That is normal, not an error.
* any other exception from the handler is that job's crash (council 17 Oct 2026: a
  damaged upload crashed every worker that took it, for ~100 minutes): it is logged
  CRITICAL (kaagaz/worker_main.py LoggedHandler) and the job is handed back counted,
  after RETRY_FIRST_SECONDS (60 s), so a file that always crashes reaches its last try
  in minutes and the worker takes the next job. A crash outside the handler (``admit``,
  ``on_attempt``, ``on_exhausted``, the queue), a MemoryError, or CRASHES_IN_A_ROW (3)
  handler crashes in a row, each of a different job, still end the process. A database
  that cannot be reached is BackendUnavailable("database"), never a crash, so an outage
  costs no try.
* the heartbeat renews the lease at most once every LEASE_RENEW_SECONDS (15 s)
  on the worker's clock: a beat after every page or embedding batch
  would be hundreds of queue calls on a long document. The settings' AR1
  checks leave room for that wait.
* each delivery up to ``max_attempts`` (JOB_MAX_ATTEMPTS, 5) runs the handler,
  after ``on_attempt(job, attempt)`` records the try on the document, so the
  file list can say "Try 2 of 5".
* a delivery past ``max_attempts`` does not run the handler:
  ``on_exhausted(job)`` marks the document failed, then the job is buried as
  ``retries_exhausted``. Without this, the queue would move the job to the
  failure bin by itself and nothing would mark the document, which would show
  "Reading…" for good. It covers retryable failures and crashes alike.
* if marking fails (the database is down), the error propagates and the job
  is not buried: its next delivery tries the marking again. That is why the
  queue's own maximum receives must be above ``max_attempts + 1``
  (kaagaz/worker_main.py checks it at start-up), and it is 10 on the laptop.
* ``admit(job)`` (PRD 5 Q5) runs before ``on_attempt`` and holds for the whole
  try: the customer's fair-share slot (kaagaz/jobs/fair_share.py). It raises
  Deferred when the job must wait (the customer is at MAX_ACTIVE_PER_CUSTOMER, or
  the reader has a key problem), and the job is handed back with
  ``queue.defer(receipt, seconds)``: no try is recorded and no attempt counted,
  and the worker takes the next job.
* a handler's ``BackendUnavailable("gemini_key")`` (a wrong key or a spent cap,
  PRD 5 G6) is not a try either: the job is handed back for
  KEY_PROBLEM_DEFER_SECONDS (300) and the error goes on up, so the loop still
  pauses. Every other BackendUnavailable goes up as before and the lease runs out.

PROVISIONAL: the retry back-off has no jitter; that needs the queue settings, which are Vrushit's.

Why one bad job cannot block the others: while a job is leased it is hidden,
and each ``run_once`` handles exactly one job, so the jobs behind a failing one
are still handed out. (The test queue also puts a returning job at the back of
the line; a standard SQS queue does not promise order, but still hides leased
jobs.)
"""

import contextlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.ports import LeaseLost, WorkQueue
from kaagaz.settings import LEASE_RENEW_SECONDS

# Called by a long-running handler to say "still working", which renews the
# lease so the job is not handed to another worker.
Heartbeat = Callable[[], None]
Handler = Callable[[Job, Heartbeat], None]

RETRIES_EXHAUSTED = "retries_exhausted"

# PRD 5 Q5(b), G6: BackendUnavailable's name for a wrong key or a spent cap
# (kaagaz.pipeline), and how long its job waits: the fix is a person's.
KEY_PROBLEM = "gemini_key"
KEY_PROBLEM_DEFER_SECONDS = 300

# Holds what a job needs for its whole try (its fair-share slot), or raises Deferred.
Admit = Callable[[Job], AbstractContextManager[object]]


class RunsJobs(Protocol):
    """A Worker, or a sweep wrapped around one (kaagaz/worker_main.py)."""

    def run_once(self) -> bool: ...


RETRY_FIRST_SECONDS = 60
RETRY_MOST_SECONDS = 900


# Handler crashes in a row that end the process: one bad file is that job's, three in a
# row (different jobs) is the process's (council 17 Oct 2026).
CRASHES_IN_A_ROW = 3


def database_unreachable(error: Exception) -> bool:
    """A database error that means "cannot reach it" (most calls already raise
    BackendUnavailable("database"), kaagaz/db/engine.py ``unavailable``; this catches
    the ones that do not), so an outage is not counted as a try."""
    return isinstance(error, (OperationalError, InterfaceError, PoolTimeout)) or (
        isinstance(error, DBAPIError) and error.connection_invalidated
    )


def retry_delay(attempt: int) -> int:
    """Seconds before a retryable failure's next try: 60 doubling each attempt, at most 900
    (SQS's longest delay). O(1)."""
    return min(RETRY_FIRST_SECONDS * 2 ** max(0, attempt - 1), RETRY_MOST_SECONDS)


class JobFailed(Exception):
    """A handler's way to report a failure. ``code`` is short and safe to log."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class Deferred(Exception):
    """Not now: hand the job back to the queue for ``seconds`` without running it
    (PRD 5 Q5: its customer is at MAX_ACTIVE_PER_CUSTOMER, or the reader has a key
    problem). ``code`` is short and safe to log, and never names another customer."""

    def __init__(self, seconds: int, code: str) -> None:
        super().__init__(code)
        self.seconds = seconds
        self.code = code


class Superseded(Exception):
    """A newer run of this document has started (the run fence), so this run's results
    are not saved and it does not mark the document ready. Not a failure: the newer
    run does the work."""


class Worker:
    def __init__(
        self,
        queue: WorkQueue,
        handler: Handler,
        *,
        max_attempts: int,
        on_exhausted: Callable[[Job], None],
        on_attempt: Callable[[Job, int], None] | None = None,
        clock: Callable[[], float] | None = None,
        admit: Admit | None = None,
    ) -> None:
        """``max_attempts`` is how many times the handler may run for one job;
        ``on_exhausted`` marks a job's document when a delivery comes after that;
        ``on_attempt`` records each try before the handler runs. ``clock`` (monotonic
        seconds, time.monotonic in worker_main) spaces the lease renewals: a heartbeat
        less than LEASE_RENEW_SECONDS after the receive or the last renewal does not
        call the queue. A clock that steps back renews at once. With no clock, every
        heartbeat renews. ``admit`` holds the job's fair-share slot around the try, or raises
        Deferred (none: every job is let in)."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._queue = queue
        self._handler = handler
        self._max_attempts = max_attempts
        self._on_exhausted = on_exhausted
        self._on_attempt = on_attempt
        self._clock = clock
        self._admit = admit
        self._crashes = 0  # handler crashes in a row, of different jobs
        self._last_crash: Job | None = None

    def run_once(self) -> bool:
        """Process one job. Returns False when the queue had nothing ready.

        O(1) queue calls, plus the handler's own time (or ``on_exhausted``'s).
        """
        delivery = self._queue.receive()
        if delivery is None:
            return False
        clock = self._clock
        renewed = 0.0 if clock is None else clock()

        def heartbeat() -> None:
            nonlocal renewed
            if clock is not None:
                now = clock()
                if 0 <= now - renewed < LEASE_RENEW_SECONDS:
                    return
                renewed = now
            self._queue.extend(delivery.receipt)

        try:
            if delivery.attempt > self._max_attempts:
                self._on_exhausted(delivery.job)
                self._queue.bury(delivery.receipt, RETRIES_EXHAUSTED)
                return True
            try:
                with self._admit(delivery.job) if self._admit is not None else contextlib.nullcontext():
                    if self._on_attempt is not None:
                        self._on_attempt(delivery.job, delivery.attempt)
                    try:
                        self._handler(delivery.job, heartbeat)
                    except JobFailed as failure:
                        self._crashes, self._last_crash = 0, None
                        if not failure.retryable:
                            self._queue.bury(delivery.receipt, failure.code)
                        else:
                            # Council 16 Oct 2026: back soon (1, 2, 4, 8, then 15 minutes), each
                            # counted, rather than after the whole lease with the row frozen.
                            self._queue.defer(delivery.receipt, retry_delay(delivery.attempt), counted=True)
                        return True
                    except (Deferred, BackendUnavailable, LeaseLost):
                        raise
                    except Exception as error:  # noqa: BLE001 - one job's crash, see the docstring
                        if database_unreachable(error):
                            raise BackendUnavailable("database") from None
                        self._crashed(delivery.job, delivery.receipt, error)
                        return True
                    self._crashes, self._last_crash = 0, None
                    self._queue.ack(delivery.receipt)
            except Deferred as deferred:
                self._queue.defer(delivery.receipt, deferred.seconds)
            except BackendUnavailable as error:
                if str(error) == KEY_PROBLEM:
                    with contextlib.suppress(LeaseLost):  # another delivery owns it: nothing to hand back
                        self._queue.defer(delivery.receipt, KEY_PROBLEM_DEFER_SECONDS)
                raise
        except LeaseLost:
            pass  # another delivery owns the job now
        return True

    def _crashed(self, job: Job, receipt: str, error: Exception) -> None:
        """A handler crash (council 17 Oct 2026): the job is handed back counted, after
        RETRY_FIRST_SECONDS, so a file that crashes its parser every time reaches
        ``on_exhausted`` in minutes, not after JOB_MAX_ATTEMPTS whole leases. The process
        goes on, unless it is a MemoryError or the CRASHES_IN_A_ROW-th crash in a row of
        different jobs (one file crashing on each of its tries is that file's): then the fault
        is the process's, so the job is handed back and the error goes on up.

        ponytail: a native crash (a segfault, or the OOM killer's SIGKILL) never reaches
        Python, so it bypasses this. The content check already runs in a child process with
        time and memory limits (kaagaz/scanning/isolated.py); the upgrade path is the same for
        the parsers."""
        if job != self._last_crash:
            self._crashes, self._last_crash = self._crashes + 1, job
        with contextlib.suppress(LeaseLost):  # another delivery owns it: nothing to hand back
            self._queue.defer(receipt, RETRY_FIRST_SECONDS, counted=True)
        if isinstance(error, MemoryError) or self._crashes >= CRASHES_IN_A_ROW:
            raise error
