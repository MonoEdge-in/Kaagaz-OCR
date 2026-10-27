"""#246 A3: the worker's heartbeat file and the compose healthcheck that reads it."""

import os
import threading
import time
from pathlib import Path

from kaagaz import worker_heartbeat
from kaagaz.ingestion.ports import Job
from kaagaz.worker_main import beating, run_until_stopped
from tests.test_compose_prod import has, service

JOB = Job(
    customer_id="00000000-0000-0000-0000-000000000001", document_id="00000000-0000-0000-0000-000000000002"
)


def test_beat_touches_the_file_and_never_raises(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    worker_heartbeat.beat(path)
    assert path.exists()
    worker_heartbeat.beat(tmp_path / "no-such-folder" / "heartbeat")  # an OSError is swallowed


def test_the_check_passes_only_while_the_file_is_younger_than_a_job_lease(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    lease = {"QUEUE_LEASE_SECONDS": "1200"}
    assert worker_heartbeat.main(path, lease) == 1  # no beat yet
    worker_heartbeat.beat(path)
    assert worker_heartbeat.main(path, lease) == 0
    old = time.time() - 1201
    os.utime(path, (old, old))
    assert worker_heartbeat.main(path, lease) == 1
    assert worker_heartbeat.main(path, {"QUEUE_LEASE_SECONDS": "1300"}) == 0
    # A missing or broken lease setting falls back to the shipped 1200 s.
    assert worker_heartbeat.main(path, {}) == 1


def test_every_turn_of_the_loop_beats() -> None:
    stop = threading.Event()
    beats: list[int] = []

    class TwoTurns:
        turns = 0

        def run_once(self) -> bool:
            self.turns += 1
            if self.turns == 2:
                stop.set()
            return False

    run_until_stopped(TwoTurns(), stop, pause_seconds=0.01, beat=lambda: beats.append(1))
    assert len(beats) == 2


def test_a_running_job_beats_each_time_it_says_still_working() -> None:
    calls: list[str] = []

    def handler(job: Job, heartbeat: object) -> None:
        assert callable(heartbeat)
        heartbeat()
        heartbeat()

    beating(handler, lambda: calls.append("beat"))(JOB, lambda: calls.append("lease"))
    assert calls == ["beat", "lease", "beat", "lease"]


def test_compose_runs_the_check_on_the_worker() -> None:
    worker = service("worker")
    assert has(worker, "healthcheck:")
    assert '      test: ["CMD", "python", "-m", "kaagaz.worker_heartbeat"]\n' in worker
    # Longer than the slowest start (models load, the queue is checked) before it counts.
    assert "      start_period: 5m\n" in worker
