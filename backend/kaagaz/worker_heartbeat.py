"""The worker's heartbeat, and the Docker healthcheck that reads it (#246 A3).

The worker touches HEARTBEAT_FILE on every turn of its loop and each time a running job says
"still working" (``kaagaz/worker_main.py``). ``python -m kaagaz.worker_heartbeat`` is the
worker's compose healthcheck: it exits 0 while the file is at most QUEUE_LEASE_SECONDS old,
else 1. No beat for a whole job lease means the worker is stuck: its job's lease has run out
and another copy may take the job. Docker only reports it (``docker compose ps``); nothing
restarts on it.

Standard library only, so the check starts fast and small every time it runs.
"""

import contextlib
import os
import sys
import time
from pathlib import Path

HEARTBEAT_FILE = Path("/tmp/kaagaz-worker-heartbeat")  # noqa: S108 - the container's own /tmp
# QUEUE_LEASE_SECONDS when the check cannot read it (the shipped .env.example value).
DEFAULT_MAX_AGE_SECONDS = 1200


def beat(path: Path = HEARTBEAT_FILE) -> None:
    """Touch the file. Never raises: a heartbeat must not stop the worker."""
    with contextlib.suppress(OSError):
        path.touch()


def fresh(path: Path, max_age_seconds: float, now: float) -> bool:
    """Whether ``path`` was touched at most ``max_age_seconds`` before ``now``; no file is not."""
    try:
        age = now - path.stat().st_mtime
    except OSError:
        return False
    return age <= max_age_seconds


def main(path: Path = HEARTBEAT_FILE, environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    try:
        max_age = int(env.get("QUEUE_LEASE_SECONDS", ""))
    except ValueError:
        max_age = DEFAULT_MAX_AGE_SECONDS
    return 0 if fresh(path, max_age, time.time()) else 1


if __name__ == "__main__":
    sys.exit(main())
