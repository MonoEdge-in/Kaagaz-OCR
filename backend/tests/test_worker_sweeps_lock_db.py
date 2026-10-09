"""PRD 5 Q3 against a real PostgreSQL: a sweep's lock (``sweep_lock``) is held by one
copy at a time, each sweep's on its own, and is let go after the pass."""

import pytest

from kaagaz.db.engine import Database
from kaagaz.worker_main import sweep_lock

pytestmark = pytest.mark.db


def test_worker_sweeps_lock_lets_one_copy_run_a_sweep_at_a_time(db: Database) -> None:
    first_copy, second_copy = sweep_lock(db.engine), sweep_lock(db.engine)
    with first_copy("purge") as mine:
        assert mine is True
        with second_copy("purge") as theirs:
            assert theirs is False  # skips this pass
        with second_copy("embed_missing") as other_sweep:
            assert other_sweep is True  # each sweep has its own lock
    with second_copy("purge") as mine_now:
        assert mine_now is True  # let go after the pass
