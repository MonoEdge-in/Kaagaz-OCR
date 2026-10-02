"""The worker's StoryVectors wrapper with fakes: when it runs a pass, and that no
database or model error stops the jobs or crashes the worker, logged by type or
code only. The real pass is tested in test_story_vectors_db.py."""

import unittest
from datetime import timedelta

from sqlalchemy.exc import ProgrammingError

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.search.story_vectors import StoryPass
from kaagaz.worker_main import STORY_EVERY, JsonFormatter, StoryVectors
from tests.test_worker_main import START, _Clock, _Jobs


class _Sweep:
    """Counts its runs; returns a pass, or raises ``error``."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.runs = 0

    def __call__(self) -> StoryPass:
        self.runs += 1
        if self.error is not None:
            raise self.error
        return StoryPass(found=3, written=2)


class StoryVectorsTest(unittest.TestCase):
    def test_a_pass_runs_on_the_first_turn_then_at_most_every_ten_minutes(self) -> None:
        clock, jobs, sweep = _Clock(), _Jobs(), _Sweep()
        worker = StoryVectors(jobs, sweep, clock)
        runs = []
        with self.assertLogs("kaagaz.worker", "INFO") as logs:
            for minutes in (0, 5, 9, 10, 19, 20):
                clock.now = START + timedelta(minutes=minutes)
                self.assertTrue(worker.run_once())
                runs.append(sweep.runs)
        self.assertEqual(runs, [1, 1, 1, 2, 2, 3])
        self.assertEqual(jobs.calls, 6)
        self.assertEqual(logs.records[0].getMessage(), "story vectors: 2 of 3 stories")

    def test_with_the_model_off_it_never_runs(self) -> None:
        jobs = _Jobs()
        worker = StoryVectors(jobs, None, _Clock())
        self.assertTrue(worker.run_once())
        self.assertEqual(jobs.calls, 1)

    def test_every_error_is_logged_by_type_or_code_and_the_jobs_go_on(self) -> None:
        cases = [
            (ProgrammingError("UPDATE ...", {}, Exception("secret story")), "ERROR", "ProgrammingError"),
            (BackendUnavailable("database"), "ERROR", "BackendUnavailable"),
            (ModelUnavailable("timeout"), "WARNING", "timeout"),
            (ModelOutputError("embedding_count"), "WARNING", "embedding_count"),
        ]
        for error, level, code in cases:
            with self.subTest(code=code):
                clock, jobs, sweep = _Clock(), _Jobs(), _Sweep(error)
                worker = StoryVectors(jobs, sweep, clock)
                with self.assertLogs("kaagaz.worker", "WARNING") as logs:
                    self.assertTrue(worker.run_once())
                (record,) = logs.records
                self.assertEqual((record.levelname, record.__dict__["code"]), (level, code))
                self.assertNotIn("secret story", JsonFormatter().format(record))
                clock.now = START + STORY_EVERY - timedelta(seconds=1)
                self.assertTrue(worker.run_once())  # a failed pass waits too
                self.assertEqual((sweep.runs, jobs.calls), (1, 2))
