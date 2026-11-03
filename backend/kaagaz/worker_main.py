"""The worker process: ``python -m kaagaz.worker_main``.

It builds the real adapters from the settings, then runs one job at a time
until it is told to stop:

* SIGINT (Ctrl+C) or SIGTERM (``docker compose stop``) sets a stop flag. The
  job in hand is finished and settled first, then the loop ends. A signal
  never cuts a job in half.
* Storage, the queue or the database being down (BackendUnavailable) does not
  end the process: it is logged, the loop pauses for ``queue_wait_seconds``
  and tries again. A job that was running is not acked, so the queue hands
  it out again once its lease runs out.
* A job's handler that crashes (any other exception) is that job's: it is logged
  CRITICAL "job crashed" with its type and the code location (``where``), and the job is
  handed back counted after 60 s (kaagaz/jobs/worker.py, council 17 Oct 2026); the
  delivery after its last attempt (JOB_MAX_ATTEMPTS) marks the document failed
  (``retries_exhausted``) and buries the job. A crash outside the handler, a
  MemoryError, or 3 handler crashes in a row (of different jobs) end the process on purpose;
  docker-compose.yml restarts it.
* Each attempt is written on the document (``attempts``, ``max_attempts``),
  so the file list can say "Try 2 of 5".
* A job with a ``run_id`` is an Ensemble run after the scan (``dispatch``, kaagaz/jobs/
  ensemble.py), one with a ``check_id`` an operator's accuracy check (#463, kaagaz/jobs/
  accuracy.py), every other one the document's reading. A run's tries are written on the
  run, and its last delivery fails the run (``retries_exhausted``), never the document; a
  check's last delivery fails the check, and its tries are written nowhere.
* The queue's maximum receives is read from the queue once, at start-up. It
  must be above JOB_MAX_ATTEMPTS + 1, so a delivery is left to mark the
  document even if the database is down at the first try. A queue without a
  redrive policy, or with too low a maximum, stops the worker before it takes
  any job, and the one crash line names the fix (QueueSetupError's code).
* Trash older than ``TRASH_DAYS`` is purged at most once an hour, between
  jobs (``PurgeHourly``, kaagaz/jobs/purge.py). The hour is measured on the
  injected clock. Several workers may purge at once: each family is taken by
  one of them only. A database error in the purge is logged by its type and
  the jobs go on: the purge can never stop the worker, or crash it again on
  every restart. The same turn then removes the organisations whose deletion
  is due (super admin PR 4, ``purge_deleted_customers``), a batch of families
  at a time, and sweeps their storage once more a day after each purge.
* With the model on, pieces left without a meaning vector (the model was down
  while their document was read, or the job's indexing ran out of time) get one later: at
  most 32 pieces a minute, only on a turn the queue had nothing for, so never before an
  upload (``EmbedMissing``, kaagaz/search/indexing.py; never stuck, 17 Oct 2026). A model
  that is down waits 1, 2, 4, 8, then 15 minutes (``retry_delay``).
  Like the purge, it never stops the jobs or crashes the worker.
* With the model on, stories left without a meaning vector (the model was off
  or down when they were saved) get one later: at most 32 a minute, on an idle
  turn, waiting the same way while the model is down (``StoryVectors``,
  kaagaz/search/story_vectors.py). Like the purge, it never stops the jobs or
  crashes the worker.
* Documents read before version spotting get their text fingerprint once, a pass of 50 a
  minute on idle turns, never making a suggestion (``FingerprintOld``, File management
  section 7); it stops for good once a pass finds none.
* WORKER_REPLICAS copies of this process run at once (PRD 5 Q3). Each sweep
  between jobs (the purge, EmbedMissing, StoryVectors, UpgradeLocalReads) takes
  its Postgres advisory lock ``kaagaz.sweep:<name>`` first (``sweep_lock``), on a
  connection of its own, and a copy that cannot get it skips that pass: only one
  copy runs a sweep at a time.
* With OCR_ENGINE=gemini, documents with pages the backup reader read during an
  outage are read again by Gemini once it is back, oldest first, at most 20 an hour
  across the copies, in the main lane (``UpgradeLocalReads``, kaagaz/jobs/upgrade.py,
  PRD 5 Q7).
* With OCR_ENGINE=gemini, the model catalogue (model choice, council 11 Oct 2026, MC1)
  is refreshed from OpenRouter's lists once a day, between jobs (``RefreshCatalog``,
  kaagaz/jobs/catalog.py), under the sweep lock. Keyless mode never runs it.
* Documents still in progress that no job is working on (nothing written on them for
  QUEUE_LEASE_SECONDS x JOB_MAX_ATTEMPTS, and no slot of their customer held) are queued
  again with one more try counted, or failed as ``stuck_no_job`` with no try left, at most
  20 every 10 minutes, under the sweep lock (``ReviveStuck``, kaagaz/jobs/revive.py,
  stuck reading, 16 Oct 2026).
* Documents held by an organisation's reading pause (#478) are queued again once it ends (a new
  India day, a raised limit or budget), looked at every 5 minutes under the sweep lock
  (``ReleaseHeld``, kaagaz/jobs/reading_pause.py); the first turn after a start does it. With
  EMAIL_ENABLED and OPS_ALERT_EMAIL, the super admin is emailed when one pauses.
* Before a job runs, ``admission`` (PRD 5 Q5) hands it back to the queue, with no
  attempt counted: for KEY_PROBLEM_DEFER_SECONDS (300) while the reader's gate
  has a key problem, and for 30 s when its customer already has
  MAX_ACTIVE_PER_CUSTOMER documents being read in any copy of the worker (the
  fair-share slots, kaagaz/jobs/fair_share.py), so one customer's batch never
  blocks another's. The worker then takes the next job. It is handed back for 15 s too
  while the platform already reads the super admin's most documents at once (package S,
  0053; at most WORKER_REPLICAS), the reading slots, kaagaz/jobs/fair_share.py ReadingCap.

Logs are JSON lines holding ids, codes and counts only: never file names,
document text or an exception's message (it could quote a document). An
error is logged by its type name.

PROVISIONAL: the pause after an outage reuses QUEUE_WAIT_SECONDS rather than
adding a setting; back-off with jitter needs Vrushit's queue settings.
"""

import contextlib
import functools
import json
import logging
import os
import re
import signal
import socket
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from types import FrameType
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from kaagaz import worker_heartbeat
from kaagaz.adapters.queue import SqsQueue
from kaagaz.adapters.scanner import build_scanner
from kaagaz.adapters.storage import S3Storage
from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.ai.openrouter import KEY_PROBLEM as READER_KEY_PROBLEM
from kaagaz.ai.openrouter import OpenRouterClient, process_gate
from kaagaz.db import ai_settings, reader_status, versions
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database, make_engine
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.catalog import RefreshCatalog, Refreshed, last_refreshed, refresh
from kaagaz.jobs.fair_share import (
    PLATFORM_BUSY,
    FairShare,
    ReadingCap,
    SweepLock,
    first_free_lock,
    no_lock,
    sweep_lock,
)
from kaagaz.jobs.ports import LeaseLost, QueueSetupError, WorkQueue
from kaagaz.jobs.purge import (
    CustomerStorage,
    purge_deleted_customers,
    purge_expired,
    purge_upload_sessions,
    sweep_purged_customers,
)
from kaagaz.jobs.reading_pause import ReleaseHeld, mail_alert, release_held
from kaagaz.jobs.revive import ReviveStuck, revive_stuck
from kaagaz.jobs.upgrade import UpgradeLocalReads, upgrade_oldest
from kaagaz.jobs.worker import (
    KEY_PROBLEM_DEFER_SECONDS,
    RETRIES_EXHAUSTED,
    Admit,
    Deferred,
    Handler,
    Heartbeat,
    JobFailed,
    RunsJobs,
    Worker,
    database_unreachable,
    retry_delay,
)
from kaagaz.search.indexing import MissingPass, customer_ids, embed_missing
from kaagaz.search.story_vectors import StoryKey, StoryPass, embed_missing_stories
from kaagaz.settings import Settings

if TYPE_CHECKING:
    from kaagaz.ai.client import ModelClient

logger = logging.getLogger("kaagaz.worker")

# How often the trash purge runs, at most (PRD 1 F5).
PURGE_EVERY = timedelta(hours=1)
# The missing-vector pass (council 2 Oct 2026, PROVISIONAL): how often, at most, and how many
# pieces a pass may embed. Never stuck (17 Oct 2026): a pass runs only on a turn the queue had
# nothing for, so it is short (32 pieces, one batch: about 2 minutes at MODEL_RPM 60 over 4
# processes), and a model that is down waits retry_delay (1, 2, 4, 8, then 15 minutes).
EMBED_EVERY = timedelta(minutes=1)
EMBED_PASS_PIECES = 32
# The story vector sweep (council 3 Oct 2026, PROVISIONAL): the same, for stories.
STORY_EVERY = timedelta(minutes=1)
STORY_PASS_STORIES = 32
# File management section 7: the one-time fingerprint sweep, a pass a minute on idle turns.
FINGERPRINT_EVERY = timedelta(minutes=1)
FINGERPRINT_PASS = 50

# The only extra fields a log line may carry. Anything else passed in
# ``extra`` is dropped, so a careless call cannot log document data.
_PACKAGE = os.path.dirname(os.path.abspath(__file__)) + os.sep  # kaagaz/, for crash_site
_SAFE_FIELDS = ("customer_id", "document_id", "code", "retryable", "outcome", "where", "step", "seconds")


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message and safe fields."""

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _SAFE_FIELDS:
            if field in record.__dict__:
                line[field] = record.__dict__[field]
        if record.exc_info and record.exc_info[0] is not None:
            line["error"] = record.exc_info[0].__name__
        return json.dumps(line, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    quiet_parser_logs()


# The PDF libraries log parsed objects, stacks and names from inside the file (document
# content) at ERROR, WARNING and DEBUG.
_PARSER_LOGGERS = ("pdfminer", "pdfplumber")


def quiet_parser_logs() -> None:
    """Only CRITICAL from the PDF libraries reaches the log (council 17 Oct 2026)."""
    for name in _PARSER_LOGGERS:
        logging.getLogger(name).setLevel(logging.CRITICAL)


class LoggedHandler:
    """Wraps the job handler to log each job's outcome, with ids and codes only."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler

    def __call__(self, job: Job, heartbeat: Heartbeat) -> None:
        ids = {"customer_id": job.customer_id, "document_id": job.document_id}
        try:
            self._handler(job, heartbeat)
        except JobFailed as failure:
            outcome = "retry" if failure.retryable else "failed"
            logger.warning(
                "job %s",
                outcome,
                extra={**ids, "outcome": outcome, "code": failure.code, "retryable": failure.retryable},
            )
            raise
        except (Deferred, BackendUnavailable, LeaseLost):
            raise
        except Exception as error:  # logged here, re-raised: the Worker settles it
            if database_unreachable(error):
                raise  # an outage, not a crash: the Worker makes it BackendUnavailable
            extra = {**ids, "outcome": "crashed", "code": type(error).__name__, "where": crash_site(error)}
            logger.critical("job crashed", extra=extra)
            raise
        logger.info("job done", extra={**ids, "outcome": "done"})


def crash_site(error: BaseException) -> str:
    """Where ``error`` was raised in our own code: the innermost kaagaz frame of its
    traceback, as ``file.py:function:line``. A code location only, never a message or
    any document data. ``unknown`` with no kaagaz frame."""
    for frame in reversed(traceback.extract_tb(error.__traceback__)):
        if os.path.abspath(frame.filename).startswith(_PACKAGE):
            return f"{os.path.basename(frame.filename)}:{frame.name}:{frame.lineno}"
    return "unknown"


class RecordAttempt:
    """The worker's ``on_attempt``: writes the try on the document (``record`` is
    PgDocuments.record_attempt), with the worker's maximum, before the handler runs; an
    Ensemble run's try on the run (``record_run``: EnsembleRuns.record_attempt)."""

    def __init__(
        self,
        record: Callable[[str, str, int, int], None],
        max_attempts: int,
        record_run: Callable[[str, str, int], None] | None = None,
    ) -> None:
        self._record = record
        self._max_attempts = max_attempts
        self._record_run = record_run

    def __call__(self, job: Job, attempt: int) -> None:
        if job.check_id is not None:  # an accuracy check (#463) keeps no tries
            return
        if job.run_id is None:
            self._record(job.customer_id, job.document_id, attempt, self._max_attempts)
        elif self._record_run is not None:
            self._record_run(job.customer_id, job.run_id, attempt)


def check_queue_redrive(max_receives: int, max_attempts: int) -> None:
    """Refuse a queue whose own maximum receives would move a job to the failure
    bin before the worker has marked its document: it must be above
    ``max_attempts + 1`` (every attempt, one delivery to mark, one spare for a
    database outage while marking). Raises QueueSetupError."""
    if max_receives <= max_attempts + 1:
        raise QueueSetupError("queue_redrive_too_low")


class MarkExhausted:
    """The worker's ``on_exhausted``: marks the job's document (``mark`` is
    PgDocuments.mark_exhausted), or an Ensemble run's run (``mark_run``:
    EnsembleRuns.exhausted; the document stays as it is), or an accuracy check (``mark_check``:
    AccuracyChecks.exhausted, required, #463 review: a check is never left running), and logs
    it, with ids and the code only."""

    def __init__(
        self,
        mark: Callable[[str, str], None],
        mark_run: Callable[[str, str], None] | None = None,
        *,
        mark_check: Callable[[str, str], None],
    ) -> None:
        self._mark = mark
        self._mark_run = mark_run
        self._mark_check = mark_check

    def __call__(self, job: Job) -> None:
        if job.check_id is not None:  # an accuracy check (#463): the check fails
            self._mark_check(job.customer_id, job.check_id)
        elif job.run_id is None:
            self._mark(job.customer_id, job.document_id)
        elif self._mark_run is not None:
            self._mark_run(job.customer_id, job.run_id)
        logger.warning(
            "job %s",
            "failed",
            extra={
                "customer_id": job.customer_id,
                "document_id": job.document_id,
                "outcome": "failed",
                "code": RETRIES_EXHAUSTED,
            },
        )


STOPPING = "stopping"  # a job handed back by a worker that is stopping
FEATURE_OFF = "feature_off"  # Scanning with AI switched off by the super admin (0038, 0039)
FEATURE_OFF_DEFER_SECONDS = 60
PLATFORM_BUSY_PAUSE_SECONDS = 2.0  # package S: an idle copy's wait after a platform_busy hand-back


def admission(
    reader_state: Callable[[], str],
    slot: Admit,
    stopping: Callable[[], bool] = lambda: False,
    scanning_on: Callable[[str], bool] = lambda _customer: True,
    pause: Callable[[float], object] = lambda _seconds: None,
) -> Admit:
    """The worker's ``admit`` (PRD 5 Q5): once ``stopping()`` (SIGTERM came while the queue
    was polled), the job is handed straight back for another worker (council 16 Oct 2026),
    never started and then cut off by Docker; while ``reader_state()`` (the gate's state) is
    ``key_problem``, every job is handed back for KEY_PROBLEM_DEFER_SECONDS; while
    ``scanning_on(job.customer_id)`` is false (the super admin switched Scanning with AI off
    for every organisation, 0038, or for the job's own, 0039; asked again for every job),
    the job waits FEATURE_OFF_DEFER_SECONDS, uncounted; otherwise
    ``slot(job)`` (FairShare.slot) holds the customer's slot for the try, or hands the
    job back. Each hand-back is logged at debug with the job's ids and a fixed code,
    never another customer's count. After a ``platform_busy`` hand-back (package S) the
    worker waits PLATFORM_BUSY_PAUSE_SECONDS on ``pause`` (the stop event's wait), so idle
    copies under a lowered cap do not spin on the queue. O(1) besides the slot."""

    @contextlib.contextmanager
    def admit(job: Job) -> Iterator[None]:
        ids = {"customer_id": job.customer_id, "document_id": job.document_id}
        with contextlib.ExitStack() as held:
            try:
                if stopping():
                    raise Deferred(0, STOPPING)
                if reader_state() == READER_KEY_PROBLEM:
                    raise Deferred(KEY_PROBLEM_DEFER_SECONDS, READER_KEY_PROBLEM)
                if not scanning_on(job.customer_id):
                    raise Deferred(FEATURE_OFF_DEFER_SECONDS, FEATURE_OFF)
                held.enter_context(slot(job))
            except Deferred as deferred:
                logger.debug("job deferred", extra={**ids, "outcome": "deferred", "code": deferred.code})
                if deferred.code == PLATFORM_BUSY:
                    pause(PLATFORM_BUSY_PAUSE_SECONDS)
                raise
            yield

    return admit


def beating(handler: Handler, beat: Callable[[], None]) -> Handler:
    """``handler`` with ``beat`` (the heartbeat file, #246 A3) called each time its job says
    "still working", before the lease is renewed: a long job keeps the worker healthy."""

    def handle(job: Job, heartbeat: Heartbeat) -> None:
        def both() -> None:
            beat()
            heartbeat()

        handler(job, both)

    return handle


def dispatch(pipeline: Handler, ensemble: Handler, accuracy: Handler) -> Handler:
    """Each job to its handler: an Ensemble run's (``run_id``) to ``ensemble``, an accuracy
    check's (``check_id``, #463; required, #463 review: never read as a document) to
    ``accuracy``, every other to ``pipeline``."""

    def handle(job: Job, heartbeat: Heartbeat) -> None:
        if job.check_id is not None:
            accuracy(job, heartbeat)
            return
        (pipeline if job.run_id is None else ensemble)(job, heartbeat)

    return handle


def job_worker(
    queue: WorkQueue,
    handler: Handler,
    *,
    max_attempts: int,
    mark_exhausted: Callable[[str, str], None],
    record_attempt: Callable[[str, str, int, int], None],
    mark_check_exhausted: Callable[[str, str], None],
    admit: Admit | None = None,
    mark_run_exhausted: Callable[[str, str], None] | None = None,
    record_run_attempt: Callable[[str, str, int], None] | None = None,
) -> Worker:
    """The Worker _serve runs: each outcome logged, each try recorded, the delivery after
    the last attempt marked, a lease renewed at most every LEASE_RENEW_SECONDS on
    time.monotonic, and ``admit`` (``admission``) around each try. An Ensemble run's try and
    last delivery go to ``record_run_attempt`` and ``mark_run_exhausted``; an accuracy check's
    last delivery to ``mark_check_exhausted`` (#463 review: required)."""
    return Worker(
        queue,
        LoggedHandler(handler),
        max_attempts=max_attempts,
        on_exhausted=MarkExhausted(mark_exhausted, mark_run_exhausted, mark_check=mark_check_exhausted),
        on_attempt=RecordAttempt(record_attempt, max_attempts, record_run_attempt),
        clock=time.monotonic,
        admit=admit,
    )


class PurgeHourly:
    """Wraps the worker: before a turn, runs ``purge`` (returns how many families
    it purged) if an hour has passed on ``clock`` since the last run. The first
    turn runs it.

    The time is noted before the purge starts, so a purge that fails is not
    tried again until the next hour; the jobs go on meanwhile. Storage or the
    database being down (BackendUnavailable) raises to the loop, which pauses
    as for any outage. Any other database error (SQLAlchemyError, a bug in a
    statement, say) is logged by its type and swallowed here: the purge is
    housekeeping, and it must not stop the jobs, or, since the first turn after
    a restart purges, crash the worker on every restart.
    The pass runs under ``lock("purge")`` (PRD 5 Q3): a copy that cannot get it
    skips this hour's.
    O(1) per turn, plus the purge's own cost once an hour.
    """

    def __init__(
        self,
        worker: RunsJobs,
        purge: Callable[[], int],
        clock: Callable[[], datetime],
        *,
        lock: SweepLock = no_lock,
    ) -> None:
        self._worker = worker
        self._purge = purge
        self._clock = clock
        self._lock = lock
        self._last: datetime | None = None

    def run_once(self) -> bool:
        now = self._clock()
        if self._last is None or now - self._last >= PURGE_EVERY:
            self._last = now
            try:
                with self._lock("purge") as mine:
                    if mine:
                        logger.info("trash purged: %d families", self._purge())
            except SQLAlchemyError as error:
                logger.error("trash purge failed", extra={"code": type(error).__name__})
        return self._worker.run_once()


class EmbedMissing:
    """Wraps the worker: after a turn the queue had nothing for (never stuck, 17 Oct 2026:
    never before an upload), runs ``embed`` (``embed_missing`` bound to the database, the
    model and EMBED_PASS_PIECES) with its cursor, at most every EMBED_EVERY on ``clock``.
    A turn that ran a pass counts as busy, so the next sweep waits for the next idle turn.
    ``embed`` None (the model is off) does nothing.

    The cursor is kept here between passes (a restart starts from the first
    customer again). A pass the model was down for waits ``retry_delay`` of the passes
    down in a row (1, 2, 4, 8, then 15 minutes); a pass it answers sets it back. A
    database error (SQLAlchemyError, or
    BackendUnavailable when the database cannot be reached) is logged by its
    type and swallowed, as in PurgeHourly: the jobs go on, and the next pass is
    EMBED_EVERY later. Logs hold ids, codes and counts only. The pass runs under
    ``lock("embed_missing")`` (PRD 5 Q3): a copy that cannot get it skips it.
    O(1) per turn, plus one pass's own cost at most every EMBED_EVERY.
    """

    def __init__(
        self,
        worker: RunsJobs,
        embed: Callable[[tuple[str, str] | None], MissingPass] | None,
        clock: Callable[[], datetime],
        *,
        lock: SweepLock = no_lock,
    ) -> None:
        self._worker = worker
        self._embed = embed
        self._clock = clock
        self._lock = lock
        self._next: datetime | None = None
        self._downs = 0  # passes the model was down for, in a row
        self._after: tuple[str, str] | None = None

    def run_once(self) -> bool:
        if self._worker.run_once():
            return True
        now = self._clock()
        if self._embed is None or (self._next is not None and now < self._next):
            return False
        self._next = now + EMBED_EVERY  # noted first: a pass that fails waits too
        done = None
        try:
            with self._lock("embed_missing") as mine:
                done = self._embed(self._after) if mine else None
        except (SQLAlchemyError, BackendUnavailable) as error:
            logger.error("missing vectors failed", extra={"code": type(error).__name__})
            return True
        if done is not None:
            self._note(done, now)
        return done is not None

    def _note(self, done: MissingPass, now: datetime) -> None:
        """Keep the pass's cursor and wait, and log it."""
        self._after = done.after
        if done.skipped is not None:
            customer, document, code = done.skipped
            logger.warning(
                "vectors skipped",
                extra={"customer_id": customer, "document_id": document, "code": code},
            )
        if done.down is None:
            self._downs = 0
            if done.written >= EMBED_PASS_PIECES:
                # A full pass: more are waiting (Ready comes before vectors, fast scan), so the
                # next idle turn runs again rather than in EMBED_EVERY (council 16 Oct 2026).
                self._next = now
        else:
            self._downs += 1
            logger.warning("model unavailable, vectors wait", extra={"code": done.down})
            self._next = now + timedelta(seconds=retry_delay(self._downs))
        logger.info("missing vectors: %d pieces", done.written)


class StoryVectors:
    """Wraps the worker: after a turn the queue had nothing for (as EmbedMissing), runs
    ``embed`` (``embed_missing_stories`` bound to the database, the model and
    STORY_PASS_STORIES) with its cursor, at most every STORY_EVERY on ``clock``. A turn
    that ran a pass counts as busy. ``embed`` None (the model is off) does nothing.

    The cursor is kept here between passes (a restart starts from the first story
    again). The time is noted first, so a pass that fails waits STORY_EVERY too. A
    pass the model failed (ModelUnavailable, ModelOutputError) waits ``retry_delay`` of
    the passes failed in a row (1, 2, 4, 8, then 15 minutes); a pass it answers sets it
    back. A database error (SQLAlchemyError, or
    BackendUnavailable when the database cannot be reached) or a ValueError is
    logged by its type and swallowed, as in PurgeHourly: the jobs go on. Logs hold
    codes and counts only, never a story. The pass runs under ``lock("story_vectors")``
    (PRD 5 Q3): a copy that cannot get it skips it. O(1) per turn, plus one pass at
    most every STORY_EVERY.
    """

    def __init__(
        self,
        worker: RunsJobs,
        embed: Callable[[StoryKey | None], StoryPass] | None,
        clock: Callable[[], datetime],
        *,
        lock: SweepLock = no_lock,
    ) -> None:
        self._worker = worker
        self._embed = embed
        self._clock = clock
        self._lock = lock
        self._next: datetime | None = None
        self._downs = 0  # passes the model failed, in a row
        self._after: StoryKey | None = None

    def run_once(self) -> bool:
        if self._worker.run_once():
            return True
        now = self._clock()
        if self._embed is None or (self._next is not None and now < self._next):
            return False
        self._next = now + STORY_EVERY
        try:
            with self._lock("story_vectors") as mine:
                done = self._embed(self._after) if mine else None
        except (SQLAlchemyError, BackendUnavailable, ValueError) as error:
            logger.error("story vectors failed", extra={"code": type(error).__name__})
        except (ModelUnavailable, ModelOutputError) as error:
            self._downs += 1
            logger.warning("model unavailable, story vectors wait", extra={"code": error.code})
            self._next = now + timedelta(seconds=retry_delay(self._downs))
        else:
            if done is None:
                return False
            self._after = done.after
            self._downs = 0
            logger.info("story vectors: %d of %d stories", done.written, done.found)
        return True


class FingerprintOld:
    """Wraps the worker: the one-time fingerprint sweep (File management section 7, the
    EmbedMissing pattern). After a turn the queue had nothing for, at most every
    FINGERPRINT_EVERY, runs ``sweep`` (``fingerprint_missing``: how many it found) under
    ``lock("fingerprints")``; a full pass runs again on the next idle turn, and the first
    pass that finds none ends the sweep for this process (new documents get theirs when they
    are read). It never makes a suggestion. A database error is logged by its type and
    swallowed, as in EmbedMissing. O(1) per turn, plus one pass at most every minute."""

    def __init__(
        self,
        worker: RunsJobs,
        sweep: Callable[[], int],
        clock: Callable[[], datetime],
        *,
        lock: SweepLock = no_lock,
    ) -> None:
        self._worker = worker
        self._sweep = sweep
        self._clock = clock
        self._lock = lock
        self._next: datetime | None = None
        self._done = False

    def run_once(self) -> bool:
        if self._worker.run_once():
            return True
        now = self._clock()
        if self._done or (self._next is not None and now < self._next):
            return False
        self._next = now + FINGERPRINT_EVERY  # noted first: a pass that fails waits too
        try:
            with self._lock("fingerprints") as mine:
                found = self._sweep() if mine else None
        except (SQLAlchemyError, BackendUnavailable) as error:
            logger.error("fingerprint sweep failed", extra={"code": type(error).__name__})
            return True
        if found is None:
            return False
        self._done = found == 0
        if found >= FINGERPRINT_PASS:
            self._next = now  # more are waiting
        logger.info("fingerprints: %d documents", found)
        return True


def fingerprint_missing(db: Database, limit: int = FINGERPRINT_PASS) -> int:
    """One pass of the sweep: at most ``limit`` ready documents never fingerprinted, across
    the customers in id order, each in its own transaction (``versions.fill``); how many it
    found. Database errors propagate (FingerprintOld logs them)."""
    with db.system() as conn:
        customers = customer_ids(conn)
    found = 0
    for customer in customers:
        with db.customer(customer) as conn:
            documents = versions.missing(conn, customer, limit - found)
        for document in documents:
            with db.customer(customer) as conn:
                versions.fill(conn, customer, document)
        found += len(documents)
        if found >= limit:
            break
    return found


def _purge(db: Database, storage: CustomerStorage, trash_days: int) -> int:
    """The hourly turn: old Trash (its count is returned and logged by PurgeHourly), then
    the organisations whose deletion is due, and their second sweep a day later (logged here)."""
    families = purge_expired(db, storage, _utc_now, trash_days)
    logger.info("abandoned uploads removed: %d", purge_upload_sessions(db, storage, _utc_now))
    logger.info("deleted organisations: %d families", purge_deleted_customers(db, storage, _utc_now))
    logger.info("purged organisations swept again: %d", sweep_purged_customers(db, storage, _utc_now))
    return families


def _story_pass(db: Database, model: "ModelClient", after: StoryKey | None) -> StoryPass:
    return embed_missing_stories(db, model, limit=STORY_PASS_STORIES, after=after)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# PRD 5 U9, V4: each copy reports its reader's state this often, on a thread of its own,
# so a copy busy with a long job still reports (Health says "not reporting" after 2 minutes).
READER_REPORT_SECONDS = 30.0
_WORKER_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def worker_name(hostname: str) -> str:
    """This copy's name for its reader reports: the host name (a container's id under
    Docker), with anything but letters, digits, ``.``, ``-`` and ``_`` made ``-``, at
    most 64 characters, as reader_status's CHECK asks; ``worker`` for an empty one."""
    return _WORKER_NAME_UNSAFE.sub("-", hostname)[:64] or "worker"


def report_reader_state(
    report: Callable[[str, datetime | None], None],
    status: Callable[[], tuple[str, float | None]],
    *,
    monotonic: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], datetime] = _utc_now,
) -> None:
    """One reader report (U9): the gate's ``status()``, its ``since`` (on the gate's
    monotonic clock) turned into a wall-clock time. A database that is down, or a report
    it refuses, is logged by its type and the worker goes on. O(1)."""
    state, since = status()
    when = None if since is None else wall_clock() - timedelta(seconds=max(0.0, monotonic() - since))
    try:
        report(state, when)
    except (SQLAlchemyError, BackendUnavailable) as error:
        logger.warning("reader status not reported", extra={"code": type(error).__name__})


def keep_reporting(stop: threading.Event, beat: Callable[[], None], every: float) -> threading.Thread:
    """Run ``beat`` now and then every ``every`` seconds until ``stop`` is set, on a daemon
    thread of its own (a worker that dies takes it with it). Returns the thread."""

    def loop() -> None:
        beat()
        while not stop.wait(every):
            beat()

    thread = threading.Thread(target=loop, name="reader-status", daemon=True)
    thread.start()
    return thread


def start_reader_reports(
    stop: threading.Event, db: Database, status: Callable[[], tuple[str, float | None]], hostname: str
) -> threading.Thread:
    """This copy's reader reports (U9, V4): the gate's ``health`` (``status``) into reader_status
    under this host's worker name, now and every READER_REPORT_SECONDS until ``stop``."""
    report = functools.partial(reader_status.report, db, worker_name(hostname))
    return keep_reporting(stop, lambda: report_reader_state(report, status), READER_REPORT_SECONDS)


def install_stop_handlers(stop: threading.Event) -> None:
    """SIGINT and SIGTERM set ``stop``. Must be called from the main thread."""

    def request_stop(signum: int, frame: FrameType | None) -> None:
        if not stop.is_set():
            logger.info(
                "stop requested, finishing the current job", extra={"code": signal.Signals(signum).name}
            )
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def run_until_stopped(
    worker: RunsJobs, stop: threading.Event, *, pause_seconds: float, beat: Callable[[], None] = lambda: None
) -> None:
    """Run jobs until ``stop`` is set, checking it between jobs. ``beat`` (the heartbeat file,
    #246 A3) is called at the start of every turn.

    An idle loop does not spin: ``run_once`` waits on the queue's long poll.
    """
    while not stop.is_set():
        beat()
        try:
            worker.run_once()
        except BackendUnavailable as error:
            logger.warning("backend unavailable, pausing", extra={"code": str(error) or "backend"})
            stop.wait(pause_seconds)


def _documents_at_once(db: Database, customer_id: str) -> int | None:
    """The super admin's documents-at-once cap (package S, 0053; None: WORKER_REPLICAS), read
    for each job in its customer's transaction: one statement."""
    with db.customer(customer_id) as conn:
        value = conn.execute(text("SELECT documents_at_once FROM platform_ai_settings")).scalar_one()
        return None if value is None else int(value)


def _scanning_on(db: Database, customer_id: str) -> bool:
    """The Scanning with AI switch (0038) and the job's organisation's own (0039), read for
    each job: one statement."""
    with db.customer(customer_id) as conn:
        return ai_settings.switch_on(conn, "scan_ai", customer_id)


def worker_model(settings: Settings, db: Database | None = None) -> "ModelClient | None":
    """The worker's model client, or None with the model off. Its chat calls (the card
    and the invoice windows) time out after MODEL_EXTRACT_TIMEOUT_SECONDS, which the
    lease allows twice over (settings, AR1); the rest after MODEL_TIMEOUT_SECONDS. Its
    requests use the shared database RPM scheduler when one is available."""
    from kaagaz.ai.client import build_model_client

    return build_model_client(settings, chat_timeout_seconds=settings.model_extract_timeout_seconds, db=db)


def catalog_refresh(settings: Settings, db: Database) -> Callable[[], Refreshed] | None:
    """The daily catalogue refresh (``RefreshCatalog``), or None unless scans are read by a
    model (OCR_ENGINE=gemini, which needs OPENROUTER_API_KEY): keyless mode never runs it."""
    if settings.ocr_engine != "gemini":
        return None
    client = OpenRouterClient(settings, gate=process_gate(settings, database=db))
    return lambda: refresh(db, client, settings, today=_utc_now().date())


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    # A bug still ends the process (docker-compose.yml restarts it), but with one
    # line naming the error's type. Python's own last words would be the traceback
    # and the message, and a database error's message can quote row values.
    try:
        _serve(settings)
    except QueueSetupError as error:
        # A setup mistake, not a bug: the code says what to fix (jobs/ports.py).
        logger.critical("worker cannot start: queue setup", extra={"code": error.code})
        sys.exit(1)
    except Exception as error:  # noqa: BLE001 - the process boundary, see the comment above
        logger.critical("worker crashed", extra={"code": type(error).__name__})
        sys.exit(1)


def _serve(settings: Settings) -> None:
    """Build the worker and run jobs until it is told to stop."""
    # Imported here, not at the top: these modules pull in every reader, the
    # OCR engine and the AI client, and the loop above is tested without them.
    from kaagaz.jobs.accuracy import AccuracyChecks
    from kaagaz.jobs.ensemble import EnsembleRuns
    from kaagaz.pipeline import build_processor
    from kaagaz.reading.ocr import GEMINI
    from kaagaz.reading.wiring import build_ocr_engine

    # The worker's PostgreSQL timeouts (#246 A1): long, so a big document's save or a sweep is
    # never cut, but a stuck statement, lock wait or open transaction still ends.
    engine = make_engine(
        settings.database_url,
        statement_timeout_seconds=settings.db_worker_statement_timeout_seconds,
        lock_timeout_seconds=settings.db_worker_lock_timeout_seconds,
        idle_in_transaction_timeout_seconds=settings.db_idle_in_transaction_timeout_seconds,
    )
    db = Database(engine, settings.db_app_role)
    storage = S3Storage(settings)
    queue = SqsQueue(settings)
    max_receives = queue.max_receives()  # once; no redrive policy stops the worker here
    check_queue_redrive(max_receives, settings.job_max_attempts)
    scanner = build_scanner(settings)
    model = worker_model(settings, db)
    alert = None  # #478: the super admin's email when an organisation's reading pauses
    if settings.email_enabled and settings.email_from and settings.ops_alert_email:
        from kaagaz.adapters.email import SesMailer

        alert = mail_alert(SesMailer(settings.ses_region, settings.email_from), settings.ops_alert_email)
    processor = build_processor(
        settings=settings, db=db, storage=storage, scanner=scanner, model=model, alert=alert
    )
    # Ensemble after the scan reads with a scan reader of its own (the same gate and pool).
    reader = build_ocr_engine(settings, db=db) if settings.ocr_engine == GEMINI else None
    ensemble = EnsembleRuns(db, storage, reader, settings=settings, clock=_utc_now)
    accuracy = AccuracyChecks(db, storage, reader, settings=settings)  # #463, the same reader

    stop = threading.Event()
    install_stop_handlers(stop)
    storage.ensure_bucket()  # the laptop's first start; in AWS it only checks
    logger.info("worker started")
    documents = PgDocuments(db)
    gate = process_gate(settings, database=db)  # the one every model client of this process shares
    # Health reads ``health`` (issue 407: OCR_MODEL broken shows); the jobs act on ``status``.
    reports = start_reader_reports(stop, db, gate.health, socket.gethostname())
    fair_share = FairShare(functools.partial(first_free_lock, db.engine), settings.max_active_per_customer)
    reading = ReadingCap(
        db.engine,
        settings.worker_replicas,
        functools.partial(_documents_at_once, db),
        documents.still_waiting,
    )

    @contextlib.contextmanager
    def slots(job: Job) -> Iterator[None]:
        """The customer's fair-share slot, then one of the platform's reading slots (package S)."""
        with fair_share.slot(job), reading.slot(job):
            yield

    jobs = job_worker(
        queue,
        beating(dispatch(processor, ensemble, accuracy), worker_heartbeat.beat),
        max_attempts=settings.job_max_attempts,
        # The last delivery also ends the document's runs still running (AR11), on the worker's clock.
        mark_exhausted=lambda customer, document: documents.mark_exhausted(
            customer, document, now=_utc_now(), max_attempts=settings.job_max_attempts
        ),
        record_attempt=documents.record_attempt,
        admit=admission(
            lambda: gate.status()[0],
            slots,
            stop.is_set,
            functools.partial(_scanning_on, db) if settings.ocr_engine == GEMINI else lambda _customer: True,
            stop.wait,
        ),
        mark_run_exhausted=ensemble.exhausted,
        record_run_attempt=ensemble.record_attempt,
        mark_check_exhausted=accuracy.exhausted,
    )
    purge = functools.partial(_purge, db, storage, settings.trash_days)
    embed = None if model is None else functools.partial(embed_missing, db, model, limit=EMBED_PASS_PIECES)
    stories: Callable[[StoryKey | None], StoryPass] | None = None
    if model is not None:
        stories = functools.partial(_story_pass, db, model)
    lock = sweep_lock(db.engine)
    pieces = EmbedMissing(PurgeHourly(jobs, purge, _utc_now, lock=lock), embed, _utc_now, lock=lock)
    stories_done = StoryVectors(pieces, stories, _utc_now, lock=lock)
    vectors = FingerprintOld(stories_done, functools.partial(fingerprint_missing, db), _utc_now, lock=lock)
    # Q7: only Gemini's pages are ever read locally (its outage fallback).
    upgrade = functools.partial(upgrade_oldest, db, queue) if settings.ocr_engine == "gemini" else None
    upgrades = UpgradeLocalReads(
        vectors, upgrade, lambda: gate.status()[0], _utc_now, copies=settings.worker_replicas, lock=lock
    )
    catalog = RefreshCatalog(
        upgrades,
        catalog_refresh(settings, db),
        _utc_now,
        lock=lock,
        last_refreshed=lambda: last_refreshed(db),
    )
    revive = functools.partial(
        revive_stuck,
        db,
        lambda job, tries: queue.enqueue(job, tries=tries),
        fair_share.busy,
        stale_seconds=settings.queue_lease_seconds * settings.job_max_attempts,
        max_attempts=settings.job_max_attempts,
    )
    revived = ReviveStuck(catalog, revive, lambda: gate.status()[0], _utc_now, lock=lock)
    release = functools.partial(release_held, db, queue.enqueue)
    worker = ReleaseHeld(revived, lambda: release(now=_utc_now()), _utc_now, lock=lock)
    run_until_stopped(worker, stop, pause_seconds=settings.queue_wait_seconds, beat=worker_heartbeat.beat)
    reports.join(READER_REPORT_SECONDS)  # stop is set: at most the report in flight is left
    db.engine.dispose()
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
