"""The content check (kaagaz/scanning/content.py) in a child process with OS limits.

A parser bug in pdfminer or pdfium (a native crash, a hang, a blown memory budget) then
ends only the child, never the worker, and the file is ``unscannable`` (fail-closed):

* the parent runs ``python -m kaagaz.scanning.isolated`` with the file on stdin, a wall
  clock of WALL_SECONDS and a minimal environment (none of the app's secrets);
* the child limits its own memory (MEMORY_BYTES) and CPU time (CPU_SECONDS) first, then
  prints exactly one word: ``ok``, ``blocked_content`` or ``unscannable``;
* anything else (a non-zero exit, a signal, a timeout, other output) is ``unscannable``,
  logged at WARNING with the reason code only, never the file's content.

PROVISIONAL: the limits are Vrushit's to tune.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from kaagaz.scanning.content import Finding, inspect

logger = logging.getLogger(__name__)

WALL_SECONDS = 45.0
CPU_SECONDS = 30
MEMORY_BYTES = 3 * 512 * 1024 * 1024  # 1.5 GiB

_COMMAND = [sys.executable, "-m", "kaagaz.scanning.isolated"]
_BACKEND = Path(__file__).resolve().parents[2]  # where ``-m kaagaz...`` imports from
_ENV_KEYS = frozenset({"PATH", "PYTHONPATH", "HOME", "LANG", "SYSTEMROOT"})
_ANSWERS = {b"ok": None, b"blocked_content": Finding.BLOCKED, b"unscannable": Finding.UNSCANNABLE}


def _child_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in _ENV_KEYS or key.startswith("LC_")}


def inspect_isolated(data: bytes) -> Finding | None:
    """``content.inspect(data)``, run in a child process; ``unscannable`` if it fails.

    ponytail: one new process per file (~0.3 s start-up); a warm pool if upload rates
    ever make that matter."""
    try:
        done = subprocess.run(  # noqa: S603 (a fixed command; the file goes on stdin)
            _COMMAND,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # never buffered: a file could make a parser log gigabytes
            timeout=WALL_SECONDS,
            env=_child_env(),
            cwd=_BACKEND,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("content check failed: timeout")
        return Finding.UNSCANNABLE
    except OSError:
        logger.warning("content check failed: could not start")
        return Finding.UNSCANNABLE
    if done.returncode == 0 and done.stdout in _ANSWERS:
        return _ANSWERS[done.stdout]
    if done.returncode < 0:
        logger.warning("content check failed: signal %d", -done.returncode)
    else:
        logger.warning("content check failed: exit code %d", done.returncode)  # 0: other output
    return Finding.UNSCANNABLE


def _child() -> None:
    # ponytail: Windows (laptops only) has no rlimits; the parent's wall clock still applies.
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS + 1))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    finding = inspect(sys.stdin.buffer.read())
    sys.stdout.write("ok" if finding is None else finding.value)


if __name__ == "__main__":
    _child()
