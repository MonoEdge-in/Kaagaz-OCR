"""#246 M2 and G5: what ``alembic`` does on a database the deploy may meet.

M2: ``alembic upgrade head`` (the migrate container) on a database ahead of the code (an
older image after a rollback, or a revert of a migration's pull request) changes nothing,
logs ``database_ahead`` and exits 0, so that release starts. Any other target still fails.

Each test gets the fresh database ``tests/test_migration_0002_db.py`` makes and drops.
"""

import json
import os
import subprocess
import sys

from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.db_support import BACKEND, migrate
from tests.test_migration_0002_db import fresh  # noqa: F401 - the fixture

AHEAD = "9999"  # a revision no file has: what a newer release left behind


def _alembic(url: str, *args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed command; the arguments are the test's own
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url, **extra_env},
        check=False,
        capture_output=True,
        text=True,
    )


def _version(database: Database) -> str:
    with database.engine.connect() as conn:
        return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())


def _set_version(database: Database, revision: str) -> None:
    with database.engine.begin() as conn:  # the owner: the migrations' own table
        conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": revision})


def test_upgrade_head_on_a_database_ahead_changes_nothing_and_exits_0(fresh: tuple[str, Database]) -> None:  # noqa: F811
    url, database = fresh
    migrate(url)
    _set_version(database, AHEAD)

    done = _alembic(url, "upgrade", "head")

    assert done.returncode == 0, done.stderr
    lines = [json.loads(line) for line in done.stderr.splitlines() if line.startswith("{")]
    assert len(lines) == 1
    assert lines[0]["code"] == "database_ahead" and lines[0]["database"] == [AHEAD]
    assert lines[0]["code_head"] == [max(lines[0]["code_head"])]  # this code's one head
    assert _version(database) == AHEAD


def test_any_other_target_on_a_database_ahead_still_fails(fresh: tuple[str, Database]) -> None:  # noqa: F811
    url, database = fresh
    migrate(url)
    _set_version(database, AHEAD)
    assert _alembic(url, "upgrade", "0001").returncode != 0
    assert _version(database) == AHEAD


def test_a_database_at_this_codes_head_is_left_as_it_is(fresh: tuple[str, Database]) -> None:  # noqa: F811
    url, database = fresh
    migrate(url)
    head = _version(database)
    done = _alembic(url, "upgrade", "head")
    assert done.returncode == 0, done.stderr
    assert "database_ahead" not in done.stderr
    assert _version(database) == head
