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
  restarts it; the job goes back to the queue and, after the maximum number
  of receives, to the failure bin.

Logs are JSON lines holding ids, codes and counts only: never file names,
document text or an exception's message (it could quote a document). An
error is logged by its type name.

PROVISIONAL: the pause after an outage reuses QUEUE_WAIT_SECONDS rather than
adding a setting; back-off with jitter needs Vrushit's queue settings.
"""

import json
import logging
import signal
import sys
import threading
from datetime import UTC, datetime
from types import FrameType
from typing import Protocol

from kaagaz.adapters.queue import SqsQueue
from kaagaz.adapters.scanner import build_scanner
from kaagaz.adapters.storage import S3Storage
from kaagaz.db.engine import Database, make_engine
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.worker import Handler, Heartbeat, JobFailed, Worker
from kaagaz.settings import Settings

logger = logging.getLogger("kaagaz.worker")

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


class RunsJobs(Protocol):
    def run_once(self) -> bool: ...


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


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)

    # Imported here, not at the top: these modules pull in every reader, the
    # OCR engine and the AI client, and the loop above is tested without them.
    from kaagaz.ai.client import build_model_client
    from kaagaz.pipeline import build_processor

    db = Database(make_engine(settings.database_url), settings.db_app_role)
    storage = S3Storage(settings)
    queue = SqsQueue(settings)
    scanner = build_scanner(settings)
    model = build_model_client(settings)
    processor = build_processor(settings=settings, db=db, storage=storage, scanner=scanner, model=model)

    stop = threading.Event()
    install_stop_handlers(stop)
    storage.ensure_bucket()  # the laptop's first start; in AWS it only checks
    logger.info("worker started")
    run_until_stopped(
        Worker(queue, LoggedHandler(processor)),
        stop,
        pause_seconds=settings.queue_wait_seconds,
    )
    db.engine.dispose()
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
