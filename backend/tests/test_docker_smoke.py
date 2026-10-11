"""A Docker-only check of the model files baked into the backend image.

NOT RUN on the build laptop, which has no Docker: the test here is skipped
unless ``docker`` is on PATH and ``KAAGAZ_DOCKER_TESTS=1`` is set. It needs the
stack running, from the repository root:

    docker compose up -d
    KAAGAZ_DOCKER_TESTS=1 python -m pytest tests/test_docker_smoke.py     (from backend/)

The check runs one line of Python inside the running worker.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKER = shutil.which("docker")

pytestmark = pytest.mark.skipif(
    DOCKER is None or os.environ.get("KAAGAZ_DOCKER_TESTS") != "1",
    reason="needs Docker and KAAGAZ_DOCKER_TESTS=1 (NOT RUN on the build laptop)",
)


def runs(service: str, code: str, *args: str) -> bool:
    """Whether ``python -c code args`` exits 0 inside the running ``service``."""
    assert DOCKER is not None
    command = [DOCKER, "compose", "exec", "-T", service, "python", "-c", code]
    result = subprocess.run(  # noqa: S603 - fixed arguments; the docker binary found on PATH
        [*command, *args], cwd=ROOT, capture_output=True, timeout=120, check=False
    )
    return result.returncode == 0


def test_the_worker_image_holds_the_devanagari_model() -> None:
    # Fetched at build time (backend/Dockerfile): the worker never downloads it.
    assert runs(
        "worker",
        "from kaagaz.reading.ocr import devanagari_model_path; assert devanagari_model_path().is_file()",
    )
