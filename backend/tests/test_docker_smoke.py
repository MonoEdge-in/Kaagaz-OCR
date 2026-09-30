"""Docker-only checks of the ocr service's network (security A4).

NOT RUN on the build laptop, which has no Docker: every test here is skipped
unless ``docker`` is on PATH and ``KAAGAZ_DOCKER_TESTS=1`` is set. They need the
stack running with the paddle profile, from the repository root:

    docker compose --profile paddle up -d
    KAAGAZ_DOCKER_TESTS=1 python -m pytest tests/test_docker_smoke.py     (from backend/)

Each check runs a one-line TCP connect inside a running container.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKER = shutil.which("docker")
CONNECT = "import socket, sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=5)"

pytestmark = pytest.mark.skipif(
    DOCKER is None or os.environ.get("KAAGAZ_DOCKER_TESTS") != "1",
    reason="needs Docker and KAAGAZ_DOCKER_TESTS=1 (NOT RUN on the build laptop)",
)


def connects(service: str, host: str, port: int) -> bool:
    """Whether a TCP connect from inside the running ``service`` to host:port succeeds."""
    assert DOCKER is not None
    command = [DOCKER, "compose", "--profile", "paddle", "exec", "-T", service, "python", "-c", CONNECT]
    result = subprocess.run(  # noqa: S603 - fixed arguments; the docker binary found on PATH
        [*command, host, str(port)], cwd=ROOT, capture_output=True, timeout=120, check=False
    )
    return result.returncode == 0


def test_the_ocr_container_cannot_reach_the_database() -> None:
    assert connects("worker", "postgres", 5432)  # the database is up, so the next check means something
    assert not connects("ocr", "postgres", 5432)


def test_the_worker_reaches_the_ocr_service() -> None:
    assert connects("worker", "ocr", 8080)
