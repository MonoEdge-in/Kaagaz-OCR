"""A bug that ends the worker (PRD 1, B5): one critical log line with the error's type
and nothing else, then exit code 1. Never the message or the traceback: a database
error's message can quote row values. No database or queue is needed.
"""

import contextlib
import json
import logging
import unittest
from unittest import mock

from sqlalchemy.exc import IntegrityError, OperationalError

from kaagaz import worker_main
from kaagaz.ingestion import sniff
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.ports import LeaseLost, QueueSetupError
from kaagaz.jobs.worker import Deferred, Heartbeat, JobFailed
from kaagaz.settings import Settings
from tests.settings_factory import make_settings

ROW_VALUE = "ABCDE1234F"  # made up; stands in for a value read from a document


class WorkerCrashTest(unittest.TestCase):
    def test_a_crash_is_logged_by_its_type_only_and_exits_with_1(self) -> None:
        error = IntegrityError(
            "INSERT INTO extracted_values (value_text) VALUES (%(v)s)",
            {"v": ROW_VALUE},
            Exception(f"violates check constraint\nDETAIL:  Failing row contains ({ROW_VALUE})."),
        )
        self.assertIn(ROW_VALUE, str(error))  # the row value is in the error itself

        with (
            mock.patch.object(Settings, "from_env", return_value=make_settings()),
            mock.patch.object(worker_main, "configure_logging"),
            mock.patch.object(worker_main, "make_engine", side_effect=error),
            self.assertLogs("kaagaz.worker", "DEBUG") as logs,
            self.assertRaises(SystemExit) as ended,
        ):
            worker_main.main()

        self.assertEqual(ended.exception.code, 1)
        (record,) = logs.records
        self.assertEqual(
            (record.levelname, record.getMessage(), record.__dict__["code"]),
            ("CRITICAL", "worker crashed", "IntegrityError"),
        )
        self.assertIsNone(record.exc_info)
        line = worker_main.JsonFormatter().format(record)
        self.assertNotIn(ROW_VALUE, line)
        self.assertNotIn("Traceback", line)

    def test_a_queue_setup_mistake_names_the_fix(self) -> None:
        """A queue without a failure bin, or with too low a maximum, stops the worker
        with one line whose code says what to change, not just "ValueError"."""
        with (
            mock.patch.object(Settings, "from_env", return_value=make_settings()),
            mock.patch.object(worker_main, "configure_logging"),
            mock.patch.object(
                worker_main, "make_engine", side_effect=QueueSetupError("queue_redrive_too_low")
            ),
            self.assertLogs("kaagaz.worker", "DEBUG") as logs,
            self.assertRaises(SystemExit) as ended,
        ):
            worker_main.main()

        self.assertEqual(ended.exception.code, 1)
        (record,) = logs.records
        self.assertEqual(
            (record.levelname, record.getMessage(), record.__dict__["code"]),
            ("CRITICAL", "worker cannot start: queue setup", "queue_redrive_too_low"),
        )


class JobCrashLogTest(unittest.TestCase):
    """Council 17 Oct 2026: a handler's crash is logged once, CRITICAL "job crashed", with
    the job's ids, the error's type and where in our code it was raised. Never the
    message: a parser's error can quote the document."""

    JOB = Job("cust-a", "doc-1")

    def crash_records(self, error: BaseException) -> list[logging.LogRecord]:
        def handler(job: Job, beat: Heartbeat) -> None:
            raise error

        with self.assertLogs("kaagaz.worker", "DEBUG") as logs:
            logging.getLogger("kaagaz.worker").debug("marker")  # assertLogs needs one line
            with contextlib.suppress(BaseException):
                worker_main.LoggedHandler(handler)(self.JOB, lambda: None)
        return [record for record in logs.records if record.getMessage() == "job crashed"]

    def test_a_crash_is_logged_by_its_type_and_where_never_its_message(self) -> None:
        (record,) = self.crash_records(RuntimeError("Sharma Traders INV-1042"))
        fields = {key: record.__dict__[key] for key in ("customer_id", "document_id", "code", "outcome")}
        self.assertEqual(
            (record.levelname, fields),
            (
                "CRITICAL",
                {
                    "customer_id": "cust-a",
                    "document_id": "doc-1",
                    "code": "RuntimeError",
                    "outcome": "crashed",
                },
            ),
        )
        self.assertRegex(record.__dict__["where"], r"^worker_main\.py:__call__:\d+$")
        line = json.loads(worker_main.JsonFormatter().format(record))
        self.assertIn("where", line)
        self.assertNotIn("Sharma", json.dumps(line))

    def test_where_is_the_innermost_frame_in_our_own_code(self) -> None:
        try:  # not assertRaises: it drops the traceback
            sniff.detect_type(None)  # type: ignore[arg-type]
        except AttributeError as error:
            where = worker_main.crash_site(error)
        else:
            self.fail("detect_type(None) raised nothing")
        self.assertRegex(where, r"^sniff\.py:detect_type:\d+$")

    def test_the_settled_outcomes_are_not_crashes(self) -> None:
        for error in (
            Deferred(30, "customer_busy"),
            BackendUnavailable("database"),
            LeaseLost("receipt"),
            JobFailed("broken_file", retryable=True),
            OperationalError("SELECT 1", {}, Exception("server closed the connection")),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(self.crash_records(error), [])


if __name__ == "__main__":
    unittest.main()
