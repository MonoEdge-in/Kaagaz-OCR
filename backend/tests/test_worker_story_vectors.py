"""The worker's StoryVectors wrapper with fakes: when it runs a pass, the cursor it
hands on, the longer wait while the model is down, and that no database, model or
value error stops the jobs or crashes the worker, logged by type or code only. The
real pass is tested in test_story_vectors_db.py."""

import unittest
from datetime import timedelta

from sqlalchemy.exc import ProgrammingError

from kaagaz.ai.client import ModelOutputError, ModelUnavailable
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.search.story_vectors import StoryKey, StoryPass
from kaagaz.worker_main import STORY_EVERY, JsonFormatter, StoryVectors
from tests.test_worker_main import START, _Clock, _Jobs

KEY = ("c", "u", "f")


class _Sweep:
    """Records the cursor of each run; returns ``done``, or raises ``error``."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.done = StoryPass(3, 2, KEY)
        self.afters: list[StoryKey | None] = []

    def __call__(self, after: StoryKey | None) -> StoryPass:
        self.afters.append(after)
        if self.error is not None:
            raise self.error
        return self.done


def _minutes_of_passes(worker: StoryVectors, clock: _Clock, sweep: _Sweep, until: int) -> list[int]:
    """Turn every 5 minutes for ``until`` minutes: the minutes (from START) a pass ran."""
    ran = []
    for minutes in range(0, until, 5):
        clock.now = START + timedelta(minutes=minutes)
        before = len(sweep.afters)
        worker.run_once()
        if len(sweep.afters) > before:
            ran.append(minutes)
    return ran


class StoryVectorsTest(unittest.TestCase):
    def test_a_pass_runs_on_the_first_turn_then_at_most_every_ten_minutes_after_the_cursor(self) -> None:
        clock, jobs, sweep = _Clock(), _Jobs(), _Sweep()
        worker = StoryVectors(jobs, sweep, clock)
        runs = []
        with self.assertLogs("kaagaz.worker", "INFO") as logs:
            for minutes in (0, 5, 9, 10, 19, 20):
                clock.now = START + timedelta(minutes=minutes)
                self.assertTrue(worker.run_once())
                runs.append(len(sweep.afters))
                sweep.done = StoryPass(1, 1, None)  # the end: the next pass starts again
        self.assertEqual(runs, [1, 1, 1, 2, 2, 3])
        self.assertEqual(sweep.afters, [None, KEY, None])
        self.assertEqual(jobs.calls, 6)
        self.assertEqual(logs.records[0].getMessage(), "story vectors: 2 of 3 stories")

    def test_with_the_model_off_it_never_runs(self) -> None:
        jobs = _Jobs()
        worker = StoryVectors(jobs, None, _Clock())
        self.assertTrue(worker.run_once())
        self.assertEqual(jobs.calls, 1)

    def test_a_model_that_is_down_doubles_the_wait_up_to_an_hour_and_keeps_the_cursor(self) -> None:
        clock, jobs, sweep = _Clock(), _Jobs(), _Sweep()
        worker = StoryVectors(jobs, sweep, clock)
        worker.run_once()  # minute 0 works: the cursor is KEY
        sweep.error = ModelUnavailable("unreachable")
        with self.assertLogs("kaagaz.worker", "WARNING"):
            down = _minutes_of_passes(worker, clock, sweep, 300)
        # 10 minutes after the pass that worked, then 10, 20, 40, and at most 60.
        self.assertEqual(down, [10, 20, 40, 80, 140, 200, 260])
        self.assertEqual(sweep.afters, [None] + [KEY] * 7)  # a failed pass keeps the cursor
        self.assertEqual(jobs.calls, 1 + 60)  # the jobs ran on every turn

        sweep.error = None
        clock.now = START + timedelta(minutes=320)
        worker.run_once()  # back: the wait is ten minutes again
        clock.now += STORY_EVERY
        worker.run_once()
        self.assertEqual(len(sweep.afters), 10)

    def test_every_error_is_logged_by_type_or_code_and_the_jobs_go_on(self) -> None:
        cases = [
            (ProgrammingError("UPDATE ...", {}, Exception("secret story")), "ERROR", "ProgrammingError"),
            (BackendUnavailable("database"), "ERROR", "BackendUnavailable"),
            (ValueError("secret story"), "ERROR", "ValueError"),
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
                self.assertEqual((len(sweep.afters), jobs.calls), (1, 2))
