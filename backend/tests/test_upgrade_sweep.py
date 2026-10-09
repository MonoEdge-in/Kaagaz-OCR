"""PRD 5 Q7: the upgrade sweep's timing and logs (``UpgradeLocalReads``), no database.

* While the reader works: one document every 3 minutes (20 an hour). While it fails
  or is down: one every 15 minutes, the probe. With a key problem: none.
* With WORKER_REPLICAS copies, each copy waits that many times as long, so together
  they keep to 20 an hour.
* A copy that cannot get the sweep lock skips the pass; a database or queue error is
  logged by its type and the jobs go on; the log holds the document's ids and a code.

* The oldest-first pick passes over a document the sweep sent in the last
  UPGRADE_RETRY_AFTER_HOURS (6), so one the main reader fails on again can't starve
  the rest.

``upgrade_oldest`` itself is checked against PostgreSQL in tests/test_upgrade_sweep_db.py.
"""

import contextlib
import logging
import unittest
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import Connection
from sqlalchemy.exc import ProgrammingError

from kaagaz.db.documents import UPGRADE_RETRY_AFTER_HOURS, oldest_read_locally
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.upgrade import PROBE_EVERY, UPGRADE_EVERY, UpgradeLocalReads

START = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)
JOB = Job("aaaaaaaa-0000-4000-8000-00000000000a", "00000000-0000-4000-8000-0000000000a1")


class Recorded:
    """A connection that notes the one statement it is given and finds nothing."""

    def __init__(self) -> None:
        self.sql = ""
        self.params: dict[str, object] = {}

    def execute(self, statement: object, params: dict[str, object]) -> "Recorded":
        self.sql, self.params = str(statement), params
        return self

    def first(self) -> None:
        return None


class Idle:
    def __init__(self) -> None:
        self.turns = 0

    def run_once(self) -> bool:
        self.turns += 1
        return False


class Sweep:
    """An UpgradeLocalReads on a hand clock, noting each pass."""

    def __init__(self, state: str = "working", *, copies: int = 1, error: Exception | None = None) -> None:
        self.now, self.state, self.passes, self.error = START, state, 0, error
        self.jobs = Idle()
        self.sweep = UpgradeLocalReads(
            self.jobs, self.upgrade, lambda: self.state, lambda: self.now, copies=copies
        )

    def upgrade(self) -> Job | None:
        self.passes += 1
        if self.error is not None:
            raise self.error
        return JOB

    def passes_over(self, span: timedelta, step: timedelta = timedelta(seconds=30)) -> int:
        """Turn every ``step`` for ``span``; the passes made."""
        before, end = self.passes, self.now + span
        while self.now < end:
            self.sweep.run_once()
            self.now += step
        return self.passes - before


class UpgradeSweepTest(unittest.TestCase):
    def test_upgrade_sweep_sends_20_an_hour_while_the_reader_works(self) -> None:
        self.assertEqual(UPGRADE_EVERY, timedelta(minutes=3))
        self.assertEqual(Sweep().passes_over(timedelta(hours=1)), 20)

    def test_upgrade_sweep_probes_every_15_minutes_while_the_reader_is_down(self) -> None:
        self.assertEqual(PROBE_EVERY, timedelta(minutes=15))
        for state in ("down", "failing"):
            with self.subTest(state=state):
                self.assertEqual(Sweep(state).passes_over(timedelta(hours=1)), 4)

    def test_upgrade_sweep_sends_nothing_with_a_key_problem(self) -> None:
        sweep = Sweep("key_problem")
        self.assertEqual(sweep.passes_over(timedelta(hours=1)), 0)
        self.assertEqual(sweep.jobs.turns, 120)  # the jobs go on

    def test_upgrade_sweep_copies_share_the_20_an_hour(self) -> None:
        self.assertEqual(Sweep(copies=3).passes_over(timedelta(hours=1)), 7)  # 3 copies: about 20

    def test_upgrade_sweep_with_the_reader_not_gemini_does_nothing(self) -> None:
        jobs = Idle()
        UpgradeLocalReads(jobs, None, lambda: "working", lambda: START).run_once()
        self.assertEqual(jobs.turns, 1)

    def test_upgrade_sweep_skips_a_pass_another_copy_holds(self) -> None:
        @contextlib.contextmanager
        def busy(name: str) -> Iterator[bool]:
            self.assertEqual(name, "upgrade_local_reads")
            yield False

        sweep = Sweep()
        UpgradeLocalReads(sweep.jobs, sweep.upgrade, lambda: "working", lambda: START, lock=busy).run_once()
        self.assertEqual((sweep.passes, sweep.jobs.turns), (0, 1))

    def test_upgrade_sweep_errors_are_logged_by_type_and_the_jobs_go_on(self) -> None:
        for error in (BackendUnavailable("queue"), ProgrammingError("SELECT", {}, Exception("x"))):
            with self.subTest(error=type(error).__name__):
                sweep = Sweep(error=error)
                with self.assertLogs("kaagaz.worker", logging.ERROR) as logs:
                    sweep.sweep.run_once()
                self.assertEqual(sweep.jobs.turns, 1)
                (record,) = logs.records
                self.assertEqual(
                    (record.getMessage(), record.__dict__["code"]),
                    ("read upgrade failed", type(error).__name__),
                )

    def test_upgrade_sweep_logs_the_documents_ids_and_a_code_only(self) -> None:
        for state, code in (("working", "read_upgrade"), ("down", "read_upgrade_probe")):
            with self.subTest(state=state), self.assertLogs("kaagaz.worker", logging.INFO) as logs:
                Sweep(state).sweep.run_once()
            (record,) = logs.records
            fields = {key: record.__dict__[key] for key in ("customer_id", "document_id", "code")}
            self.assertEqual(record.getMessage(), "read upgrade queued")
            self.assertEqual(
                fields, {"customer_id": JOB.customer_id, "document_id": JOB.document_id, "code": code}
            )

    def test_upgrade_pick_passes_over_a_document_the_sweep_sent_lately(self) -> None:
        conn = Recorded()
        self.assertIsNone(oldest_read_locally(cast(Connection, conn), JOB.customer_id))
        self.assertEqual(UPGRADE_RETRY_AFTER_HOURS, 6)
        self.assertEqual(conn.params, {"c": JOB.customer_id, "hours": UPGRADE_RETRY_AFTER_HOURS})
        self.assertIn("NOT EXISTS (SELECT 1 FROM audit_log a WHERE a.customer_id = :c", conn.sql)
        self.assertIn("a.action = 'document.read_upgrade' AND a.target_id = d.id::text", conn.sql)
        self.assertIn("a.created_at > now() - make_interval(hours => :hours)", conn.sql)

    def test_copies_must_be_at_least_one(self) -> None:
        with self.assertRaises(ValueError):
            UpgradeLocalReads(Idle(), None, lambda: "working", lambda: START, copies=0)


if __name__ == "__main__":
    unittest.main()
