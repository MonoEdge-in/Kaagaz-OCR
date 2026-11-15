"""Migration 0094 (#617 part 1): the GIN index on ``tsv_sound`` (built concurrently), the word
counts' two tables and the owner-rights candidate function. Up, down and up again: the schema,
bodies, comments and grants as before each time. The behaviour: test_search_bounded_db.py; the
grants and the function's rights: test_isolation_schema; separation: test_isolation_word_search."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0094 = _load("0094_bounded_search.py")
_ADDED = text(
    "SELECT (SELECT indexdef FROM pg_indexes WHERE indexname = 'pieces_tsv_sound'),"
    " (SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass('public.pieces_tsv_sound')),"
    " to_regclass('public.term_df') IS NOT NULL, to_regclass('public.customer_stats') IS NOT NULL,"
    " to_regprocedure('public.kaagaz_word_candidates(text, tsquery, uuid[], integer)') IS NOT NULL"
)


def _added(db: Database) -> tuple[object, ...]:
    with db.system() as conn:
        return tuple(conn.execute(_ADDED).one())


def test_the_revision_follows_this_bases_0082() -> None:
    assert (M0094.revision, M0094.down_revision) == ("0094", "0082")


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0082")
    at_0082 = _state(db)
    assert _added(db) == (None, None, False, False, False)

    migrate(url, "0094")
    at_0094 = _state(db)
    index, valid, *rest = _added(db)
    assert index == "CREATE INDEX pieces_tsv_sound ON public.pieces USING gin (tsv_sound)" and valid
    assert rest == [True, True, True]

    base._downgrade(url, "0082")
    assert _state(db) == at_0082
    assert _added(db) == (None, None, False, False, False)

    migrate(url, "0094")
    assert _state(db) == at_0094
