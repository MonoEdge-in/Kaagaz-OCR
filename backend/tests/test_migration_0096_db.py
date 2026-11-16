"""Migration 0096 (PRD 7 PR 13, #605; 0094 on its branch, renumbered at merge): the locked-file
pop-ups' owner-rights functions. Up, down (the schema as at 0095) and up again on a fresh database,
with their comments. What they return: test_isolation_lock_popups (with the per-person rule on,
too); their rights: test_isolation_schema."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0096 = _load("0096_locked_places.py")
_FUNCTIONS = (
    "SELECT p.proname, obj_description(p.oid, 'pg_proc') FROM pg_proc p"
    " WHERE p.proname IN ('kaagaz_admin_place_files', 'kaagaz_admin_place_locked', 'kaagaz_admin_locked_for')"
    " ORDER BY p.proname"
)


def test_the_revision_follows_0095() -> None:
    assert (M0096.revision, M0096.down_revision) == ("0096", "0095")


def _functions(db: Database) -> list[tuple[str, str | None]]:
    with db.system() as conn:
        return [(str(name), comment) for name, comment in conn.execute(text(_FUNCTIONS))]


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0095")
    at_0095 = _state(db)
    migrate(url, "0096")
    at_0096 = _state(db)
    found = _functions(db)
    assert [name for name, _ in found] == [
        "kaagaz_admin_locked_for",
        "kaagaz_admin_place_files",
        "kaagaz_admin_place_locked",
    ]
    assert all(comment and "An Admin" in comment for _, comment in found)

    base._downgrade(url, "0095")
    assert _state(db) == at_0095
    assert _functions(db) == []

    migrate(url, "0096")
    assert _state(db) == at_0096
