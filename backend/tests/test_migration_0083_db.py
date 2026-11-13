"""Migration 0083 (#587, 14 Nov 2026): the workers' role (``kaagaz_worker``) gets exactly
``kaagaz_app``'s rights, and their login (``kaagaz_jobs``) may switch only into it. Up, down
(every right of the worker role revoked) and up again: the schema, comments and grants as before
each time. The roles stay on a downgrade (they belong to the whole server, as 0067's logins).
What the role and the login may do: tests/test_isolation_logins.py."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import APP_ROLE, migrate
from tests.test_isolation_logins import _RIGHTS, WORKER
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0083 = _load("0083_worker_role.py")


def _rights(db: Database, role: str) -> set[tuple[str, ...]]:
    with db.engine.begin() as conn:  # the catalog, as the owner
        return {tuple(row) for row in conn.execute(_RIGHTS, {"role": role})}


def test_the_revision_follows_0082() -> None:
    assert (M0083.revision, M0083.down_revision) == ("0083", "0082")


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0082")
    at_0082 = _state(db)

    migrate(url, "0083")
    at_0083 = _state(db)
    assert _rights(db, WORKER) == _rights(db, APP_ROLE)
    with db.engine.begin() as conn:  # the owner may switch into it, as the tests do
        conn.execute(text(f"SET LOCAL ROLE {WORKER}"))

    base._downgrade(url, "0082")
    assert _state(db) == at_0082
    assert _rights(db, WORKER) == set()

    migrate(url, "0083")  # the roles already exist: altered, and granted again
    assert _state(db) == at_0083
    assert _rights(db, WORKER) == _rights(db, APP_ROLE)
