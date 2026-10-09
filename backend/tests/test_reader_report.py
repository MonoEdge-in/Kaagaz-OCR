"""PRD 5 U9, V4: each worker copy reports its reader's state every 30 seconds.

* The gate's ``since`` (its monotonic clock) is reported as a wall-clock time.
* A database that is down, or a report it refuses, is logged by type; the worker goes on.
* The copy's name fits reader_status's CHECK (``^[A-Za-z0-9_.-]{1,64}$``).
* The reports run on a thread of their own until the worker stops.
"""

import logging
import re
import threading
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.worker_main import READER_REPORT_SECONDS, keep_reporting, report_reader_state, worker_name

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


def reported(state: str, since: float | None) -> list[tuple[str, datetime | None]]:
    """What one report sends for a gate at (state, since), 130 s on the gate's clock."""
    sent: list[tuple[str, datetime | None]] = []

    def report(state: str, since: datetime | None) -> None:
        sent.append((state, since))

    report_reader_state(report, lambda: (state, since), monotonic=lambda: 130.0, wall_clock=lambda: NOW)
    return sent


class ReaderReportTest(unittest.TestCase):
    def test_since_is_turned_into_a_wall_clock_time(self) -> None:
        self.assertEqual(reported("down", 100.0), [("down", NOW - timedelta(seconds=30))])
        self.assertEqual(reported("working", None), [("working", None)])

    def test_a_report_that_fails_is_logged_by_type_and_the_worker_goes_on(self) -> None:
        for error in (BackendUnavailable("database"), IntegrityError("INSERT", {}, Exception("x"))):

            def report(state: str, since: datetime | None, error: Exception = error) -> None:
                raise error

            with (
                self.subTest(error=type(error).__name__),
                self.assertLogs("kaagaz.worker", logging.WARNING) as logs,
            ):
                report_reader_state(report, lambda: ("working", None))
            (record,) = logs.records
            self.assertEqual(
                (record.getMessage(), record.__dict__["code"]),
                ("reader status not reported", type(error).__name__),
            )

    def test_the_worker_name_fits_the_reader_status_check(self) -> None:
        for hostname, name in (
            ("3f2a9c1b0d4e", "3f2a9c1b0d4e"),  # a container's id
            ("ip-10-0-0-1.ec2.internal", "ip-10-0-0-1.ec2.internal"),
            ("my host/\N{LATIN SMALL LETTER U WITH DIAERESIS}", "my-host--"),
            ("x" * 80, "x" * 64),
            ("", "worker"),
        ):
            self.assertEqual(worker_name(hostname), name)
            self.assertRegex(worker_name(hostname), re.compile(r"^[A-Za-z0-9_.-]{1,64}$"))

    def test_reports_run_on_their_own_thread_every_30_seconds_until_stopped(self) -> None:
        self.assertEqual(READER_REPORT_SECONDS, 30.0)
        stop, beats, twice = threading.Event(), [], threading.Event()

        def beat() -> None:
            beats.append(1)
            if len(beats) == 2:
                twice.set()

        thread = keep_reporting(stop, beat, 0.01)
        self.assertTrue(twice.wait(5))
        stop.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(thread.daemon)


if __name__ == "__main__":
    unittest.main()
