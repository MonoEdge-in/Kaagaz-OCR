"""Alembic environment: migrations are plain SQL, so there is no model metadata.

The URL comes from DATABASE_URL only, so no connection string is ever written
into a file (CLAUDE.md never-do 4). Migrations run as the connecting user (the
table owner), never as the restricted app role.

A database ahead of the code (#246 M2): ``alembic upgrade head`` on a database whose
revision this code does not have (an older image after a rollback, or a revert of a pull
request that carried a migration) changes nothing, logs ``database_ahead`` and exits 0, so
the older release starts on the newer schema. That is safe only while migrations only add
(expand/contract, M1). Any other command on such a database fails as before.

Downgrades are refused (#246 G5) unless KAAGAZ_ALLOW_DOWNGRADE=1: a ``downgrade()`` drops
what its migration added, and the rows in it with it. The deploy never runs one; a person
runs one on purpose, with the variable set, after a dump.
"""

import json
import os
import sys

from alembic import context
from sqlalchemy import create_engine

from kaagaz.db.engine import normalise_url


def _url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    return normalise_url(url)


def run_offline() -> None:
    context.configure(url=_url(), literal_binds=True, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


# How long a migration waits for a table another session holds before it fails.
# A migration that takes a busy table otherwise queues every request behind its
# lock; failing fast leaves the running app alone, and it can be run again when
# quiet. Short enough for a request to notice, long enough for one to finish.
LOCK_TIMEOUT = "10s"
ALLOW_DOWNGRADE = "KAAGAZ_ALLOW_DOWNGRADE"


def _unknown_heads() -> list[str]:
    """The database's revisions this code has no file for: empty unless the database is ahead."""
    known = {script.revision for script in context.script.walk_revisions()}
    return sorted(head for head in context.get_context().get_current_heads() if head not in known)


def _targets_code_head() -> bool:
    """Whether the command's target is this code's head (``upgrade head``: alembic hands
    env.py the head's revision id, not the word)."""
    target = context.get_revision_argument()
    targets = set(target) if isinstance(target, tuple) else {target}
    return targets == set(context.script.get_heads())


def _goes_down() -> bool:
    """Whether this command would take any migration down. Alembic works out its steps from
    the database's heads before running them; it has no public way to ask, hence the
    private call (alembic 1.x)."""
    migration = context.get_context()
    steps = migration._migrations_fn(migration.get_current_heads(), migration)
    return any(getattr(step, "is_downgrade", False) for step in steps)


def _say_database_ahead(heads: list[str]) -> None:
    """One JSON line on stderr, like the app's own log lines: revision ids only."""
    line = {
        "level": "WARNING",
        "logger": "kaagaz.migrate",
        "message": "the database is ahead of this code; nothing to migrate",
        "code": "database_ahead",
        "database": heads,
        "code_head": sorted(context.script.get_heads()),
    }
    sys.stderr.write(json.dumps(line) + "\n")


def run_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            # SET LOCAL: inside the migration's own transaction, gone when it ends.
            context.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            ahead = _unknown_heads()
            if ahead and _targets_code_head():
                _say_database_ahead(ahead)
            elif os.environ.get(ALLOW_DOWNGRADE, "").strip() != "1" and _goes_down():
                raise SystemExit(
                    f"refusing to downgrade: a downgrade drops data; set {ALLOW_DOWNGRADE}=1 to run one on purpose"
                )
            else:
                context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
