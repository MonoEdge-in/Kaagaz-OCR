"""PRD 5 Q3: WORKER_REPLICAS copies of the worker run, and each sweep between jobs runs
in one copy at a time.

* docker-compose.prod.yml runs ``deploy.replicas: ${WORKER_REPLICAS:-3}`` workers.
* A copy that cannot get a sweep's lock skips that pass and goes on with its jobs;
  its clock still moves on, so it tries again only at the next due time.
* Each sweep asks for its own lock name.

The real lock (``sweep_lock``, a Postgres advisory lock) is checked in
tests/test_worker_sweeps_lock_db.py.
"""

import contextlib
import re
import unittest
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kaagaz.search.indexing import MissingPass
from kaagaz.search.story_vectors import StoryKey, StoryPass
from kaagaz.worker_main import EMBED_EVERY, PURGE_EVERY, STORY_EVERY, EmbedMissing, PurgeHourly, StoryVectors
from tests.test_compose_ocr import service

ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class Locks:
    """The sweep lock: ``busy`` names are held by another copy; every ask is noted."""

    def __init__(self, *busy: str) -> None:
        self.busy = set(busy)
        self.asked: list[str] = []

    @contextlib.contextmanager
    def __call__(self, name: str) -> Iterator[bool]:
        self.asked.append(name)
        yield name not in self.busy


class Idle:
    def __init__(self) -> None:
        self.turns = 0

    def run_once(self) -> bool:
        self.turns += 1
        return False


class SweepLockTest(unittest.TestCase):
    def test_a_purge_another_copy_holds_is_skipped_until_the_next_hour(self) -> None:
        clock, locks, jobs, purged = Clock(), Locks("purge"), Idle(), []

        def purge() -> int:
            purged.append(1)
            return 0

        sweep = PurgeHourly(jobs, purge, clock, lock=locks)
        sweep.run_once()
        sweep.run_once()  # not due again
        locks.busy.clear()
        clock.now += PURGE_EVERY
        sweep.run_once()
        self.assertEqual((locks.asked, len(purged), jobs.turns), (["purge", "purge"], 1, 3))

    def test_a_missing_vector_pass_another_copy_holds_is_skipped(self) -> None:
        clock, locks, jobs, passes = Clock(), Locks("embed_missing"), Idle(), []

        def embed(after: tuple[str, str] | None) -> MissingPass:
            passes.append(after)
            return MissingPass(0, None)

        sweep = EmbedMissing(jobs, embed, clock, lock=locks)
        sweep.run_once()
        sweep.run_once()
        locks.busy.clear()
        clock.now += EMBED_EVERY
        sweep.run_once()
        self.assertEqual((locks.asked, passes, jobs.turns), (["embed_missing", "embed_missing"], [None], 3))

    def test_a_story_vector_pass_another_copy_holds_is_skipped(self) -> None:
        clock, locks, jobs, passes = Clock(), Locks("story_vectors"), Idle(), []

        def embed(after: StoryKey | None) -> StoryPass:
            passes.append(after)
            return StoryPass(0, 0, None)

        sweep = StoryVectors(jobs, embed, clock, lock=locks)
        sweep.run_once()
        locks.busy.clear()
        clock.now += STORY_EVERY - timedelta(seconds=1)
        sweep.run_once()  # not due yet
        clock.now += timedelta(seconds=1)
        sweep.run_once()
        self.assertEqual((locks.asked, passes, jobs.turns), (["story_vectors", "story_vectors"], [None], 3))


def test_compose_prod_runs_worker_replicas_copies_of_the_worker() -> None:
    worker = service("worker", "docker-compose.prod.yml")
    assert re.search(r"^    deploy:\n      replicas: \$\{WORKER_REPLICAS:-3\}$", worker, re.MULTILINE)
    assert "container_name" not in service("worker")  # a fixed name would allow one copy only


if __name__ == "__main__":
    unittest.main()
