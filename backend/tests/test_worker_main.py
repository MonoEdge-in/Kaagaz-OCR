"""The worker loop: graceful stop on SIGINT and SIGTERM, outages, job logs, and
the trash purge at most once an hour.

No real services: the loop runs the real Worker over the in-memory lease
queue (tests/lease_queue.py). ``main()`` itself only wires the adapters
together and is not run here.
"""

import io
import json
import logging
import re
import signal
import threading
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.exc import ProgrammingError

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.ports import QueueSetupError
from kaagaz.jobs.worker import Handler, Heartbeat, JobFailed, Worker
from kaagaz.settings import Settings, SettingsError
from kaagaz.worker_main import (
    JsonFormatter,
    LoggedHandler,
    MarkExhausted,
    PurgeHourly,
    RecordAttempt,
    check_queue_redrive,
    install_stop_handlers,
    run_until_stopped,
)
from tests.lease_queue import FakeClock, LeaseQueue

FIRST = Job("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
SECOND = Job("11111111-1111-4111-8111-111111111111", "33333333-3333-4333-8333-333333333333")
MAX_ATTEMPTS = 3


def make_queue(*jobs: Job) -> LeaseQueue:
    queue = LeaseQueue(FakeClock(), lease_seconds=30.0, max_attempts=MAX_ATTEMPTS + 3)
    for job in jobs:
        queue.enqueue(job)
    return queue


def make_worker(queue: LeaseQueue, handler: Handler) -> Worker:
    return Worker(queue, handler, max_attempts=MAX_ATTEMPTS, on_exhausted=lambda job: None)


class _ScriptedWorker:
    """run_once runs the next step of a script; stops the loop when it ends."""

    def __init__(self, stop: threading.Event, steps: list[Callable[[], bool]]) -> None:
        self._stop = stop
        self._steps = steps
        self.calls = 0

    def run_once(self) -> bool:
        step = self._steps[self.calls]
        self.calls += 1
        if self.calls == len(self._steps):
            self._stop.set()
        return step()


class StopSignalTest(unittest.TestCase):
    def setUp(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.addCleanup(signal.signal, signum, signal.getsignal(signum))
        self.stop = threading.Event()
        install_stop_handlers(self.stop)

    def _signal_during_job(self, signum: int) -> None:
        queue = make_queue(FIRST, SECOND)
        handled: list[Job] = []

        def handler(job: Job, heartbeat: Heartbeat) -> None:
            signal.raise_signal(signum)  # arrives while the job is running
            handled.append(job)  # the job still runs to its end

        with self.assertLogs("kaagaz.worker", "INFO"):
            run_until_stopped(make_worker(queue, handler), self.stop, pause_seconds=0.01)
        self.assertTrue(self.stop.is_set())
        self.assertEqual(handled, [FIRST])  # the second job was not started
        self.assertEqual(queue.pending(), 1)  # the first was acked, the second waits

    def test_sigterm_finishes_the_current_job_then_stops(self) -> None:
        self._signal_during_job(signal.SIGTERM)

    def test_sigint_finishes_the_current_job_then_stops(self) -> None:
        self._signal_during_job(signal.SIGINT)

    def test_a_stop_before_the_loop_starts_runs_nothing(self) -> None:
        queue = make_queue(FIRST)
        self.stop.set()
        run_until_stopped(make_worker(queue, lambda job, beat: None), self.stop, pause_seconds=0.01)
        self.assertEqual(queue.pending(), 1)


class LoopTest(unittest.TestCase):
    def test_idle_loop_keeps_asking_until_stopped(self) -> None:
        stop = threading.Event()
        worker = _ScriptedWorker(stop, [lambda: False] * 3)
        run_until_stopped(worker, stop, pause_seconds=0.01)
        self.assertEqual(worker.calls, 3)

    def test_an_outage_pauses_and_carries_on(self) -> None:
        stop = threading.Event()

        def outage() -> bool:
            raise BackendUnavailable("queue")

        worker = _ScriptedWorker(stop, [outage, lambda: True])
        with self.assertLogs("kaagaz.worker", "WARNING") as logs:
            run_until_stopped(worker, stop, pause_seconds=0.01)
        self.assertEqual(worker.calls, 2)
        self.assertEqual(logs.records[0].__dict__["code"], "queue")

    def test_a_bug_is_not_swallowed(self) -> None:
        stop = threading.Event()

        def bug() -> bool:
            raise KeyError("bug")

        with self.assertRaises(KeyError):
            run_until_stopped(_ScriptedWorker(stop, [bug, lambda: True]), stop, pause_seconds=0.01)


class LoggedHandlerTest(unittest.TestCase):
    def test_done_is_logged_with_ids(self) -> None:
        handler = LoggedHandler(lambda job, beat: None)
        with self.assertLogs("kaagaz.worker", "INFO") as logs:
            handler(FIRST, lambda: None)
        (record,) = logs.records
        self.assertEqual(
            (record.__dict__["customer_id"], record.__dict__["document_id"], record.__dict__["outcome"]),
            (FIRST.customer_id, FIRST.document_id, "done"),
        )

    def test_failure_is_logged_with_its_code_and_re_raised(self) -> None:
        for retryable, outcome in ((False, "failed"), (True, "retry")):

            def fail(job: Job, beat: Heartbeat, retryable: bool = retryable) -> None:
                raise JobFailed("corrupt_file", retryable=retryable)

            with self.subTest(retryable=retryable):
                with self.assertLogs("kaagaz.worker", "WARNING") as logs, self.assertRaises(JobFailed):
                    LoggedHandler(fail)(FIRST, lambda: None)
                fields = logs.records[0].__dict__
                self.assertEqual(
                    (fields["code"], fields["outcome"], fields["retryable"]),
                    ("corrupt_file", outcome, retryable),
                )

    def test_the_real_worker_still_buries_a_permanent_failure(self) -> None:
        queue = make_queue(FIRST)

        def fail(job: Job, beat: Heartbeat) -> None:
            raise JobFailed("too_many_pages", retryable=False)

        with self.assertLogs("kaagaz.worker", "WARNING"):
            make_worker(queue, LoggedHandler(fail)).run_once()
        self.assertEqual([failed.code for failed in queue.failed.values()], ["too_many_pages"])


class MarkExhaustedTest(unittest.TestCase):
    def test_the_document_is_marked_and_logged_with_ids_and_the_code_only(self) -> None:
        marked: list[tuple[str, str]] = []
        with self.assertLogs("kaagaz.worker", "WARNING") as logs:
            MarkExhausted(lambda customer_id, document_id: marked.append((customer_id, document_id)))(FIRST)
        self.assertEqual(marked, [(FIRST.customer_id, FIRST.document_id)])
        (record,) = logs.records
        fields = record.__dict__
        self.assertEqual(
            (fields["customer_id"], fields["document_id"], fields["outcome"], fields["code"]),
            (FIRST.customer_id, FIRST.document_id, "failed", "retries_exhausted"),
        )

    def test_the_real_worker_marks_the_last_delivery(self) -> None:
        clock = FakeClock()
        queue = LeaseQueue(clock, lease_seconds=30.0, max_attempts=MAX_ATTEMPTS + 3)
        queue.enqueue(FIRST)
        marked: list[tuple[str, str]] = []

        def down(job: Job, beat: Heartbeat) -> None:
            raise JobFailed("scanner_down", retryable=True)

        on_exhausted = MarkExhausted(
            lambda customer_id, document_id: marked.append((customer_id, document_id))
        )
        worker = Worker(queue, LoggedHandler(down), max_attempts=MAX_ATTEMPTS, on_exhausted=on_exhausted)
        with self.assertLogs("kaagaz.worker", "WARNING"):
            for _ in range(MAX_ATTEMPTS + 1):
                worker.run_once()
                clock.advance(30.0)
        self.assertEqual(marked, [(FIRST.customer_id, FIRST.document_id)])
        self.assertEqual([failed.code for failed in queue.failed.values()], ["retries_exhausted"])


START = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


class _Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class _Jobs:
    """A worker that only counts its turns."""

    def __init__(self) -> None:
        self.calls = 0

    def run_once(self) -> bool:
        self.calls += 1
        return True


class _Purge:
    """Counts its runs; returns ``purged``, or raises ``error`` when one is given."""

    def __init__(self, purged: int = 0, error: Exception | None = None) -> None:
        self.purged = purged
        self.error = error
        self.runs = 0

    def __call__(self) -> int:
        self.runs += 1
        if self.error is not None:
            raise self.error
        return self.purged


class PurgeHourlyTest(unittest.TestCase):
    def test_the_purge_runs_on_the_first_turn_then_at_most_once_an_hour(self) -> None:
        clock, jobs, purge = _Clock(), _Jobs(), _Purge(purged=2)
        worker = PurgeHourly(jobs, purge, clock)
        runs = []
        with self.assertLogs("kaagaz.worker", "INFO") as logs:
            for minutes in (0, 30, 59, 60, 119, 120):
                clock.now = START + timedelta(minutes=minutes)
                self.assertTrue(worker.run_once())
                runs.append(purge.runs)
        self.assertEqual(runs, [1, 1, 1, 2, 2, 3])
        self.assertEqual(jobs.calls, 6)
        self.assertEqual(logs.records[0].getMessage(), "trash purged: 2 families")

    def test_a_purge_that_fails_is_not_tried_again_within_the_hour(self) -> None:
        clock, jobs, purge = _Clock(), _Jobs(), _Purge(error=BackendUnavailable("storage"))
        worker = PurgeHourly(jobs, purge, clock)
        with self.assertRaises(BackendUnavailable):
            worker.run_once()
        clock.now = START + timedelta(minutes=59)
        self.assertTrue(worker.run_once())
        self.assertEqual((purge.runs, jobs.calls), (1, 1))

    def test_a_database_bug_in_the_purge_is_logged_and_the_jobs_go_on(self) -> None:
        """Any SQLAlchemyError (a bad statement, say) must not stop the jobs, and must
        not crash the worker: the first turn after a restart purges again, so a crash
        here would repeat on every restart."""
        clock, jobs = _Clock(), _Jobs()
        purge = _Purge(error=ProgrammingError("DELETE ...", {}, Exception("secret row value")))
        worker = PurgeHourly(jobs, purge, clock)
        with self.assertLogs("kaagaz.worker", "ERROR") as logs:
            self.assertTrue(worker.run_once())
        self.assertEqual((purge.runs, jobs.calls), (1, 1))
        (record,) = logs.records
        self.assertEqual(
            (record.getMessage(), record.__dict__["code"]), ("trash purge failed", "ProgrammingError")
        )
        self.assertNotIn("secret row value", JsonFormatter().format(record))
        clock.now = START + timedelta(minutes=30)
        self.assertTrue(worker.run_once())  # not tried again within the hour
        self.assertEqual((purge.runs, jobs.calls), (1, 2))

    def test_the_loop_pauses_after_a_failed_purge_and_goes_on_with_jobs(self) -> None:
        stop = threading.Event()
        jobs = _ScriptedWorker(stop, [lambda: True, lambda: True])
        purge = _Purge(error=BackendUnavailable("storage"))
        with self.assertLogs("kaagaz.worker", "WARNING") as logs:
            run_until_stopped(PurgeHourly(jobs, purge, _Clock()), stop, pause_seconds=0.01)
        self.assertEqual((purge.runs, jobs.calls), (1, 2))
        self.assertEqual(logs.records[0].__dict__["code"], "storage")


class AttemptsTest(unittest.TestCase):
    def test_each_try_is_recorded_with_the_worker_maximum(self) -> None:
        recorded: list[tuple[str, str, int, int]] = []
        record = RecordAttempt(lambda c, d, attempt, most: recorded.append((c, d, attempt, most)), 5)
        record(FIRST, 2)
        self.assertEqual(recorded, [(FIRST.customer_id, FIRST.document_id, 2, 5)])

    def test_the_queue_must_leave_a_delivery_to_mark_and_one_spare(self) -> None:
        for max_receives in (1, 5, 6):
            with self.subTest(max_receives=max_receives), self.assertRaises(QueueSetupError) as ctx:
                check_queue_redrive(max_receives, 5)
            self.assertEqual(ctx.exception.code, "queue_redrive_too_low")
        check_queue_redrive(7, 5)  # 5 tries, one delivery to mark, one spare

    def test_the_shipped_queue_allows_the_shipped_attempts(self) -> None:
        """.env.example's JOB_MAX_ATTEMPTS and elasticmq.conf's maxReceiveCount agree
        with check_queue_redrive, so a fresh laptop's worker starts."""
        attempts = Settings.from_env(TrashDaysSettingTest._shipped()).job_max_attempts
        conf = (ENV_EXAMPLE.parent / "infra" / "elasticmq" / "elasticmq.conf").read_text(encoding="utf-8")
        match = re.search(r"maxReceiveCount\s*=\s*(\d+)", conf)
        assert match is not None
        self.assertEqual(attempts, 5)
        check_queue_redrive(int(match.group(1)), attempts)


class TrashDaysSettingTest(unittest.TestCase):
    @staticmethod
    def _shipped() -> dict[str, str]:
        # The two secrets are placeholders in .env.example that must fail on purpose.
        values = {"SESSION_SECRET": "x" * 32, "TOTP_ENCRYPTION_KEY": "y" * 32}
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                name, _, value = line.partition("=")
                values.setdefault(name.strip(), value.strip())
        return values

    def test_the_shipped_settings_keep_trash_for_30_days(self) -> None:
        self.assertEqual(Settings.from_env(self._shipped()).trash_days, 30)

    def test_a_missing_trash_days_stops_start_up_with_its_name(self) -> None:
        env = {name: value for name, value in self._shipped().items() if name != "TRASH_DAYS"}
        with self.assertRaisesRegex(SettingsError, "TRASH_DAYS"):
            Settings.from_env(env)


class JsonFormatterTest(unittest.TestCase):
    def _format(self, **extra: Any) -> dict[str, Any]:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        test_logger = logging.getLogger("kaagaz.worker.test_format")
        test_logger.addHandler(handler)
        test_logger.propagate = False
        self.addCleanup(test_logger.removeHandler, handler)
        test_logger.warning("job failed", extra=extra)
        return dict(json.loads(stream.getvalue()))

    def test_one_json_line_with_safe_fields_only(self) -> None:
        line = self._format(document_id="d-1", code="corrupt_file", file_name="PAN ABCDE1234F.pdf")
        self.assertEqual(line["message"], "job failed")
        self.assertEqual(line["level"], "WARNING")
        self.assertEqual((line["document_id"], line["code"]), ("d-1", "corrupt_file"))
        self.assertNotIn("file_name", line)
        self.assertNotIn("ABCDE1234F", json.dumps(line))

    def test_an_exception_is_logged_by_type_only(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        test_logger = logging.getLogger("kaagaz.worker.test_exc")
        test_logger.addHandler(handler)
        test_logger.propagate = False
        self.addCleanup(test_logger.removeHandler, handler)
        try:
            raise ValueError("invoice text ABCDE1234F")
        except ValueError:
            test_logger.exception("step failed")
        line = json.loads(stream.getvalue())
        self.assertEqual(line["error"], "ValueError")
        self.assertNotIn("ABCDE1234F", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
