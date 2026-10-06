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
* Anything else is a bug and ends the process on purpose (no blanket
  ``except Exception``, as in kaagaz/jobs/worker.py). docker-compose.yml
  restarts it; the job goes back to the queue, and the delivery after its
  last attempt (JOB_MAX_ATTEMPTS) marks the document failed
  (``retries_exhausted``) and buries the job.
* Each attempt is written on the document (``attempts``, ``max_attempts``),
  so the file list can say "Try 2 of 5".
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
  at a time.
* With the model on, pieces left without a meaning vector (the model was down
  while their document was read) get one later: at most 64 pieces every 10
  minutes, between jobs (``EmbedMissing``, kaagaz/search/indexing.py). A model
  that is down waits 10 minutes, then twice as long each time, up to an hour.
  Like the purge, it never stops the jobs or crashes the worker.
* With the model on, stories left without a meaning vector (the model was off
  or down when they were saved) get one later: at most 32 every 10 minutes,
  between jobs, waiting longer while the model is down (``StoryVectors``,
  kaagaz/search/story_vectors.py). Like the purge, it never stops the jobs or
  crashes the worker.

Logs are JSON lines holding ids, codes and counts only: never file names,
document text or an exception's message (it could quote a document). An
error is logged by its type name.

PROVISIONAL: the pause after an outage reuses QUEUE_WAIT_SECONDS rather than
adding a setting; back-off with jitter needs Vrushit's queue settings.
"""

import functools
import json
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import FrameType
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.exc import SQLAlchemyError

from kaagaz.adapters.queue import SqsQueue
from kaagaz.adapters.scanner import build_scanner
from kaagaz.adapters.storage import S3Storage
from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database, make_engine
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.ports import QueueSetupError, WorkQueue
from kaagaz.jobs.purge import CustomerStorage, purge_deleted_customers, purge_expired
from kaagaz.jobs.worker import RETRIES_EXHAUSTED, Handler, Heartbeat, JobFailed, Worker
from kaagaz.search.indexing import MissingPass, embed_missing
from kaagaz.search.story_vectors import StoryKey, StoryPass, embed_missing_stories
from kaagaz.settings import Settings

if TYPE_CHECKING:
    from kaagaz.ai.client import ModelClient

logger = logging.getLogger("kaagaz.worker")

# How often the trash purge runs, at most (PRD 1 F5).
PURGE_EVERY = timedelta(hours=1)
# The missing-vector pass (council 2 Oct 2026, PROVISIONAL): how often, at most;
# the longest wait while the model is down; how many pieces a pass may embed.
EMBED_EVERY = timedelta(minutes=10)
EMBED_BACKOFF_MOST = timedelta(hours=1)
EMBED_PASS_PIECES = 64
# The story vector sweep (council 3 Oct 2026, PROVISIONAL): how often, at most, and
# how many stories one pass may embed.
STORY_EVERY = timedelta(minutes=10)
STORY_BACKOFF_MOST = timedelta(hours=1)
STORY_PASS_STORIES = 32

# The only extra fields a log line may carry. Anything else passed in
# ``extra`` is dropped, so a careless call cannot log document data.
_SAFE_FIELDS = ("customer_id", "document_id", "code", "retryable", "outcome")


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
        logger.info("job done", extra={**ids, "outcome": "done"})


class RecordAttempt:
    """The worker's ``on_attempt``: writes the try on the document (``record`` is
    PgDocuments.record_attempt), with the worker's maximum, before the handler runs."""

    def __init__(self, record: Callable[[str, str, int, int], None], max_attempts: int) -> None:
        self._record = record
        self._max_attempts = max_attempts

    def __call__(self, job: Job, attempt: int) -> None:
        self._record(job.customer_id, job.document_id, attempt, self._max_attempts)


def check_queue_redrive(max_receives: int, max_attempts: int) -> None:
    """Refuse a queue whose own maximum receives would move a job to the failure
    bin before the worker has marked its document: it must be above
    ``max_attempts + 1`` (every attempt, one delivery to mark, one spare for a
    database outage while marking). Raises QueueSetupError."""
    if max_receives <= max_attempts + 1:
        raise QueueSetupError("queue_redrive_too_low")


class MarkExhausted:
    """The worker's ``on_exhausted``: marks the job's document (``mark`` is
    PgDocuments.mark_exhausted) and logs it, with ids and the code only."""

    def __init__(self, mark: Callable[[str, str], None]) -> None:
        self._mark = mark

    def __call__(self, job: Job) -> None:
        self._mark(job.customer_id, job.document_id)
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


def job_worker(
    queue: WorkQueue,
    handler: Handler,
    *,
    max_attempts: int,
    mark_exhausted: Callable[[str, str], None],
    record_attempt: Callable[[str, str, int, int], None],
) -> Worker:
    """The Worker _serve runs: each outcome logged, each try recorded, the delivery after
    the last attempt marked, and a lease renewed at most every LEASE_RENEW_SECONDS on
    time.monotonic."""
    return Worker(
        queue,
        LoggedHandler(handler),
        max_attempts=max_attempts,
        on_exhausted=MarkExhausted(mark_exhausted),
        on_attempt=RecordAttempt(record_attempt, max_attempts),
        clock=time.monotonic,
    )


class RunsJobs(Protocol):
    def run_once(self) -> bool: ...


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
    O(1) per turn, plus the purge's own cost once an hour.
    """

    def __init__(self, worker: RunsJobs, purge: Callable[[], int], clock: Callable[[], datetime]) -> None:
        self._worker = worker
        self._purge = purge
        self._clock = clock
        self._last: datetime | None = None

    def run_once(self) -> bool:
        now = self._clock()
        if self._last is None or now - self._last >= PURGE_EVERY:
            self._last = now
            try:
                logger.info("trash purged: %d families", self._purge())
            except SQLAlchemyError as error:
                logger.error("trash purge failed", extra={"code": type(error).__name__})
        return self._worker.run_once()


class EmbedMissing:
    """Wraps the worker: before a turn, runs ``embed`` (``embed_missing`` bound to
    the database, the model and EMBED_PASS_PIECES) with its cursor, at most every
    EMBED_EVERY on ``clock``. The first turn runs it. ``embed`` None (the model is
    off) does nothing.

    The cursor is kept here between passes (a restart starts from the first
    customer again). A pass the model was down for waits EMBED_EVERY, then twice
    as long each time it is down again, up to EMBED_BACKOFF_MOST; a pass it
    answers sets the wait back. A database error (SQLAlchemyError, or
    BackendUnavailable when the database cannot be reached) is logged by its
    type and swallowed, as in PurgeHourly: the jobs go on, and the next pass is
    EMBED_EVERY later. Logs hold ids, codes and counts only.
    O(1) per turn, plus one pass's own cost at most every EMBED_EVERY.
    """

    def __init__(
        self,
        worker: RunsJobs,
        embed: Callable[[tuple[str, str] | None], MissingPass] | None,
        clock: Callable[[], datetime],
    ) -> None:
        self._worker = worker
        self._embed = embed
        self._clock = clock
        self._next: datetime | None = None
        self._backoff = EMBED_EVERY
        self._after: tuple[str, str] | None = None

    def run_once(self) -> bool:
        now = self._clock()
        if self._embed is not None and (self._next is None or now >= self._next):
            self._next = now + EMBED_EVERY  # noted first: a pass that fails waits too
            try:
                done = self._embed(self._after)
            except (SQLAlchemyError, BackendUnavailable) as error:
                logger.error("missing vectors failed", extra={"code": type(error).__name__})
            else:
                self._after = done.after
                if done.skipped is not None:
                    customer, document, code = done.skipped
                    logger.warning(
                        "vectors skipped",
                        extra={"customer_id": customer, "document_id": document, "code": code},
                    )
                if done.down is None:
                    self._backoff = EMBED_EVERY
                else:
                    logger.warning("model unavailable, vectors wait", extra={"code": done.down})
                    self._next = now + self._backoff
                    self._backoff = min(self._backoff * 2, EMBED_BACKOFF_MOST)
                logger.info("missing vectors: %d pieces", done.written)
        return self._worker.run_once()


class StoryVectors:
    """Wraps the worker: before a turn, runs ``embed`` (``embed_missing_stories`` bound
    to the database, the model and STORY_PASS_STORIES) with its cursor, at most every
    STORY_EVERY on ``clock``. The first turn runs it. ``embed`` None (the model is
    off) does nothing.

    The cursor is kept here between passes (a restart starts from the first story
    again). The time is noted first, so a pass that fails waits STORY_EVERY too. A
    pass the model failed (ModelUnavailable, ModelOutputError) waits STORY_EVERY,
    then twice as long each time it fails again, up to STORY_BACKOFF_MOST; a pass
    it answers sets the wait back. A database error (SQLAlchemyError, or
    BackendUnavailable when the database cannot be reached) or a ValueError is
    logged by its type and swallowed, as in PurgeHourly: the jobs go on. Logs hold
    codes and counts only, never a story. O(1) per turn, plus one pass at most
    every STORY_EVERY.
    """

    def __init__(
        self,
        worker: RunsJobs,
        embed: Callable[[StoryKey | None], StoryPass] | None,
        clock: Callable[[], datetime],
    ) -> None:
        self._worker = worker
        self._embed = embed
        self._clock = clock
        self._next: datetime | None = None
        self._backoff = STORY_EVERY
        self._after: StoryKey | None = None

    def run_once(self) -> bool:
        now = self._clock()
        if self._embed is not None and (self._next is None or now >= self._next):
            self._next = now + STORY_EVERY
            try:
                done = self._embed(self._after)
            except (SQLAlchemyError, BackendUnavailable, ValueError) as error:
                logger.error("story vectors failed", extra={"code": type(error).__name__})
            except (ModelUnavailable, ModelOutputError) as error:
                logger.warning("model unavailable, story vectors wait", extra={"code": error.code})
                self._next = now + self._backoff
                self._backoff = min(self._backoff * 2, STORY_BACKOFF_MOST)
            else:
                self._after = done.after
                self._backoff = STORY_EVERY
                logger.info("story vectors: %d of %d stories", done.written, done.found)
        return self._worker.run_once()


def _purge(db: Database, storage: CustomerStorage, trash_days: int) -> int:
    """The hourly turn: old Trash (its count is returned and logged by PurgeHourly), then
    the organisations whose deletion is due (logged here)."""
    families = purge_expired(db, storage, _utc_now, trash_days)
    logger.info("deleted organisations: %d families", purge_deleted_customers(db, storage, _utc_now))
    return families


def _story_pass(db: Database, model: "ModelClient", after: StoryKey | None) -> StoryPass:
    return embed_missing_stories(db, model, limit=STORY_PASS_STORIES, after=after)


def _utc_now() -> datetime:
    return datetime.now(UTC)


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


def run_until_stopped(worker: RunsJobs, stop: threading.Event, *, pause_seconds: float) -> None:
    """Run jobs until ``stop`` is set, checking it between jobs.

    An idle loop does not spin: ``run_once`` waits on the queue's long poll.
    """
    while not stop.is_set():
        try:
            worker.run_once()
        except BackendUnavailable as error:
            logger.warning("backend unavailable, pausing", extra={"code": str(error) or "backend"})
            stop.wait(pause_seconds)


def worker_model(settings: Settings) -> "ModelClient | None":
    """The worker's model client, or None with the model off. Its chat calls (the card
    and the invoice windows) time out after MODEL_EXTRACT_TIMEOUT_SECONDS, which the
    lease allows twice over (settings, AR1); the rest after MODEL_TIMEOUT_SECONDS."""
    from kaagaz.ai.client import build_model_client

    return build_model_client(settings, chat_timeout_seconds=settings.model_extract_timeout_seconds)


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
    from kaagaz.pipeline import build_processor

    db = Database(make_engine(settings.database_url), settings.db_app_role)
    storage = S3Storage(settings)
    queue = SqsQueue(settings)
    max_receives = queue.max_receives()  # once; no redrive policy stops the worker here
    check_queue_redrive(max_receives, settings.job_max_attempts)
    scanner = build_scanner(settings)
    model = worker_model(settings)
    processor = build_processor(settings=settings, db=db, storage=storage, scanner=scanner, model=model)

    stop = threading.Event()
    install_stop_handlers(stop)
    storage.ensure_bucket()  # the laptop's first start; in AWS it only checks
    logger.info("worker started")
    documents = PgDocuments(db)
    jobs = job_worker(
        queue,
        processor,
        max_attempts=settings.job_max_attempts,
        # The last delivery also ends the document's runs still running (AR11), on the worker's clock.
        mark_exhausted=lambda customer, document: documents.mark_exhausted(
            customer, document, now=_utc_now(), max_attempts=settings.job_max_attempts
        ),
        record_attempt=documents.record_attempt,
    )
    purge = functools.partial(_purge, db, storage, settings.trash_days)
    embed = None if model is None else functools.partial(embed_missing, db, model, limit=EMBED_PASS_PIECES)
    stories: Callable[[StoryKey | None], StoryPass] | None = None
    if model is not None:
        stories = functools.partial(_story_pass, db, model)
    pieces = EmbedMissing(PurgeHourly(jobs, purge, _utc_now), embed, _utc_now)
    worker = StoryVectors(pieces, stories, _utc_now)
    run_until_stopped(worker, stop, pause_seconds=settings.queue_wait_seconds)
    db.engine.dispose()
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
