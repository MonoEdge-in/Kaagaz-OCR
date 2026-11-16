"""Migration 0086 (PRD 7, #595, 14 Nov 2026): the access views and the three owner-rights functions
behind them. Each view holds every column of its table (a later migration that adds one must
re-create the view); up, down (the schema as at 0085) and up again on a fresh database."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0086 = _load("0086_access_views.py")
_COLUMNS = (
    "SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute"
    " WHERE attrelid = CAST(:r AS regclass) AND attnum > 0 AND NOT attisdropped ORDER BY attnum"
)
_OURS = (
    "SELECT (SELECT count(*) FROM pg_class WHERE relname IN ('visible_documents', 'visible_folders')),"
    " (SELECT count(*) FROM pg_proc WHERE proname IN"
    " ('kaagaz_access', 'kaagaz_shared_with_me', 'kaagaz_lock_open_to_me')),"
    " (SELECT count(*) FROM pg_type WHERE typname = 'kaagaz_access_set')"
)


def _columns(db: Database, relation: str) -> list[tuple[str, str]]:
    with db.system() as conn:
        return [(str(name), str(kind)) for name, kind in conn.execute(text(_COLUMNS), {"r": relation})]


def test_the_revision_follows_0085() -> None:
    assert (M0086.revision, M0086.down_revision) == ("0086", "0085")


@pytest.mark.parametrize(
    ("view", "table"), [("visible_documents", "documents"), ("visible_folders", "folders")]
)
def test_each_view_holds_every_column_of_its_table(db: Database, view: str, table: str) -> None:
    """``SELECT t.*`` is fixed when the view is made: a column added to the table later is
    missing from the view until a migration makes the view again."""
    assert _columns(db, view) == _columns(db, table)


def test_each_view_reads_as_its_reader_and_is_no_barrier(db: Database) -> None:
    """security_invoker: the organisation rule (and PR 9's) holds inside; no security_barrier:
    the view folds into each statement (#595)."""
    with db.system() as conn:
        options: dict[str, list[str]] = dict(
            conn.execute(
                text(
                    "SELECT relname, reloptions FROM pg_class"
                    " WHERE relname IN ('visible_documents', 'visible_folders')"
                )
            ).all()
        )
    assert options == {
        "visible_documents": ["security_invoker=true"],
        "visible_folders": ["security_invoker=true"],
    }


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0085")
    at_0085 = _state(db)
    migrate(url, "0086")
    at_0086 = _state(db)
    with db.system() as conn:
        assert tuple(conn.execute(text(_OURS)).one()) == (2, 3, 1)

    base._downgrade(url, "0085")
    assert _state(db) == at_0085
    with db.system() as conn:
        assert tuple(conn.execute(text(_OURS)).one()) == (0, 0, 0)

    migrate(url, "0086")
    assert _state(db) == at_0086
