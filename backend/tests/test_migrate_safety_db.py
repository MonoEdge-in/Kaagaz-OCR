"""#246 M2 and G5: what ``alembic`` does on a database the deploy may meet.

M2: ``alembic upgrade head`` (the migrate container) on a database ahead of the code (an
older image after a rollback, or a revert of a migration's pull request) changes nothing,
logs ``database_ahead`` and exits 0, so that release starts. Any other target still fails.

G5: a downgrade is refused unless KAAGAZ_ALLOW_DOWNGRADE=1, and refused before anything
changes; with it, it runs as before.

Each test gets the fresh database ``tests/test_migration_0002_db.py`` makes and drops.
"""

import json
import os
import subprocess
import sys

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.db_support import BACKEND, migrate
from tests.test_migration_0002_db import fresh  # noqa: F401 - the fixture

pytestmark = pytest.mark.db

AHEAD = "9999"  # a revision no file has: what a newer release left behind


def _alembic(url: str, *args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed command; the arguments are the test's own
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env={
            **{k: v for k, v in os.environ.items() if k != "KAAGAZ_ALLOW_DOWNGRADE"},
            "DATABASE_URL": url,
            **extra_env,
        },
        check=False,
        capture_output=True,
        text=True,
    )


def _version(database: Database) -> str:
    # Changed on purpose (#484, 4 Nov 2026): below 0071, a merge, the table holds two rows.
    with database.engine.connect() as conn:
        return " ".join(sorted(conn.execute(text("SELECT version_num FROM alembic_version")).scalars()))


def _below_head() -> str:
    """The revision under head (the first, when head merges two: alembic calls ``downgrade -1``
    from a merge an ambiguous walk, so the tests name it; #484, 0071 merges 0070 and 0061c)."""
    config = Config()
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    (head,) = scripts.get_heads()
    below = scripts.get_revision(head).down_revision
    return str(below[0] if isinstance(below, tuple) else below)


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


def test_a_downgrade_is_refused_without_the_switch_and_changes_nothing(fresh: tuple[str, Database]) -> None:  # noqa: F811
    url, database = fresh
    migrate(url)
    head = _version(database)
    for switch in ({}, {"KAAGAZ_ALLOW_DOWNGRADE": "0"}, {"KAAGAZ_ALLOW_DOWNGRADE": "yes"}):
        refused = _alembic(url, "downgrade", _below_head(), **switch)
        assert refused.returncode != 0
        assert "refusing to downgrade" in refused.stderr and "KAAGAZ_ALLOW_DOWNGRADE=1" in refused.stderr
        assert _version(database) == head


def test_a_downgrade_runs_with_the_switch(fresh: tuple[str, Database]) -> None:  # noqa: F811
    url, database = fresh
    migrate(url)
    head = _version(database)
    done = _alembic(url, "downgrade", _below_head(), KAAGAZ_ALLOW_DOWNGRADE="1")
    assert done.returncode == 0, done.stderr
    assert _version(database) < head
    assert _alembic(url, "upgrade", "head").returncode == 0  # an upgrade never needs it
    assert _version(database) == head


def test_a_command_with_no_target_runs_as_before(fresh: tuple[str, Database]) -> None:  # noqa: F811
    """``alembic current`` (the migration tests use it) has no target: no head check and no
    downgrade check, so it prints the revision once, as without this env.py."""
    url, database = fresh
    migrate(url)
    done = _alembic(url, "current")
    assert done.returncode == 0, done.stderr
    # Changed on purpose (#484): 0071 merges 0061c, so alembic adds "(mergepoint)".
    assert done.stdout.replace(" (mergepoint)", "").split() == [_version(database), "(head)"]
