"""Migration 0024 (operator insights, council 11 Oct 2026, F1-F4): four read-only operator
functions and the index of assistant messages, built CONCURRENTLY.

Up, down (0023's schema exactly) and up again; a leftover of a failed build (an index of
that name, as 0020's test makes one) is dropped and built again; every new function and
the index carry a comment (0007's rule for indexes). Grants and pinned signatures:
test_isolation_schema; what the functions return: test_operator_insights_db and
test_isolation_operator_insights.
"""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate
from tests.test_migration_0004_db import _current, _head
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
FUNCTIONS = {"kaagaz_op_daily", "kaagaz_op_org_health", "kaagaz_op_quality", "kaagaz_op_model_board"}
INDEX = "messages_answers"
_INDEX = text(
    "SELECT pg_get_indexdef(x.indexrelid), x.indisvalid FROM pg_index x"
    " WHERE x.indexrelid = to_regclass('public.messages_answers')"
)


def _index(database: Database) -> list[tuple[Any, ...]]:
    with database.system() as conn:
        return [tuple(row) for row in conn.execute(_INDEX)]


def test_0024_up_down_up_and_a_leftover_index_is_built_again(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0023")
    at_0023 = m0002._schema(database)
    assert _index(database) == []
    assert not m0002._names(at_0023, "functions") & FUNCTIONS

    migrate(url, "head")
    assert _current(url) == _head()
    at_head = m0002._schema(database)
    assert m0002._names(at_head, "functions") >= FUNCTIONS
    assert _index(database) == [
        (
            "CREATE INDEX messages_answers ON public.messages USING btree (customer_id, created_at)"
            " WHERE (role = 'assistant'::text)",
            True,
        )
    ]

    m0002._downgrade(url, "0023")
    assert m0002._schema(database) == at_0023
    with database.engine.begin() as conn:  # as a failed run would leave it
        conn.execute(text(f"CREATE INDEX {INDEX} ON messages (id)"))
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_every_new_function_and_the_index_have_a_comment(db: Database) -> None:
    comments = _owner(
        db,
        "SELECT p.proname::text, obj_description(p.oid, 'pg_proc') FROM pg_proc p"
        " WHERE p.pronamespace = 'public'::regnamespace AND p.proname = ANY(CAST(:f AS text[]))"
        " UNION ALL SELECT c.relname::text, obj_description(c.oid, 'pg_class') FROM pg_class c"
        " WHERE c.relnamespace = 'public'::regnamespace AND c.relname = :i",
        {"f": sorted(FUNCTIONS), "i": INDEX},
    )
    assert sorted(name for name, _ in comments) == sorted(FUNCTIONS | {INDEX})
    assert [name for name, comment in comments if not (comment or "").strip()] == []
