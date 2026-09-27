"""The worker loop: graceful stop on SIGINT and SIGTERM, outages, and job logs.

No real services: the loop runs the real Worker over the in-memory lease
queue (tests/lease_queue.py). ``main()`` itself only wires the adapters
together and is not run here.
"""

import io
import json
import logging
import signal
import threading
import unittest
from collections.abc import Callable
from typing import Any

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.worker import Heartbeat, JobFailed, Worker
from kaagaz.worker_main import JsonFormatter, LoggedHandler, install_stop_handlers, run_until_stopped
from tests.lease_queue import FakeClock, LeaseQueue

FIRST = Job("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
SECOND = Job("11111111-1111-4111-8111-111111111111", "33333333-3333-4333-8333-333333333333")


def make_queue(*jobs: Job) -> LeaseQueue:
    queue = LeaseQueue(FakeClock(), lease_seconds=30.0, max_attempts=3)
    for job in jobs:
        queue.enqueue(job)
    return queue


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
            run_until_stopped(Worker(queue, handler), self.stop, pause_seconds=0.01)
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
        run_until_stopped(Worker(queue, lambda job, beat: None), self.stop, pause_seconds=0.01)
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
            Worker(queue, LoggedHandler(fail)).run_once()
        self.assertEqual([failed.code for failed in queue.failed.values()], ["too_many_pages"])


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
