"""Run jobs from the queue, one at a time.

``run_once`` takes one job, runs the handler and settles the job:

* the handler returns: the job is acked (done).
* the handler raises JobFailed(retryable=False): the job is buried in the
  failure bin with its code, and never retried.
* the handler raises JobFailed(retryable=True): nothing is called; the lease
  runs out and the queue hands the job out again later. The lease length acts
  as a fixed back-off.
* the lease is lost (LeaseLost from a heartbeat, ack or bury): the job has
  already been handed to another delivery, so this worker leaves it alone.
  That is normal, not an error.
* anything else is a bug and propagates. The job is not acked, so the queue
  hands it out again. There is no blanket ``except Exception`` here on purpose.
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

PROVISIONAL: the retry back-off is the fixed lease length. Exponential
back-off with jitter needs the queue settings, which are Vrushit's.

Why one bad job cannot block the others: while a job is leased it is hidden,
and each ``run_once`` handles exactly one job, so the jobs behind a failing one
are still handed out. (The test queue also puts a returning job at the back of
the line; a standard SQS queue does not promise order, but still hides leased
jobs.)
"""

import contextlib
from collections.abc import Callable
from contextlib import AbstractContextManager

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
                        if not failure.retryable:
                            self._queue.bury(delivery.receipt, failure.code)
                        return True
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
