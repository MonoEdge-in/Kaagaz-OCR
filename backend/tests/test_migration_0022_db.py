"""Migration 0022 (PRD 5 pull request 2): the reader status table, the index of the pages
read locally and the operator's read of the reader status.

Up, down (0021's schema and comments exactly) and up again; each CHECK refuses a bad
report; the app may upsert a report but never delete one; the index holds only the
pages read locally. The function's grants are pinned in test_isolation_schema, and what
it shows in test_isolation_reader_status.

The fresh database and the schema snapshot come from test_migration_0002_db (a plain
import).
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import reader_status
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate, violation
from tests.test_migration_0004_db import _current, _head
from tests.test_migration_0007_db import _dictionary

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
T0 = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
_INDEX = text(
    "SELECT pg_get_indexdef(x.indexrelid) FROM pg_index x"
    " WHERE x.indexrelid = to_regclass('public.pages_read_locally')"
)


def test_0022_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0021")
    at_0021, comments_0021 = m0002._schema(database), _dictionary(database)

    migrate(url, "head")
    assert _current(url) == _head()
    at_head, comments_head = m0002._schema(database), _dictionary(database)
    assert "reader_status" in m0002._names(at_head, "relations")
    assert "kaagaz_op_reader" in m0002._names(at_head, "functions")
    assert {("reader_status", None), ("reader_status", "worker"), ("reader_status", "seen_at")} <= set(
        comments_head
    )
    with database.system() as conn:
        assert conn.execute(_INDEX).scalar_one() == (
            "CREATE INDEX pages_read_locally ON public.pages USING btree (customer_id, document_id)"
            " WHERE ('read_locally'::text = ANY (read_issues))"
        )

    m0002._downgrade(url, "0021")
    assert m0002._schema(database) == at_0021
    assert _dictionary(database) == comments_0021
    migrate(url, "head")
    assert m0002._schema(database) == at_head
    assert _dictionary(database) == comments_head


@pytest.mark.parametrize(
    ("worker", "state", "constraint"),
    [
        ("worker 1", "working", "reader_status_worker_check"),  # a space
        ("", "working", "reader_status_worker_check"),
        ("w" * 65, "working", "reader_status_worker_check"),
        ("worker-1", "slow", "reader_status_state_check"),  # "slow" is the screen's word, not a state
    ],
)
def test_a_bad_report_is_refused(db: Database, worker: str, state: str, constraint: str) -> None:
    with pytest.raises(DBAPIError) as refused:
        reader_status.report(db, worker, state, None)
    assert violation(refused.value) == ("23514", constraint)


def test_the_app_upserts_a_report_and_may_not_delete_one(db: Database) -> None:
    worker = f"w-{uuid.uuid4().hex[:12]}"
    reader_status.report(db, worker, "failing", T0)
    reader_status.report(db, worker, "down", T0)
    with db.system() as conn:  # as the app role
        rows = conn.execute(
            text("SELECT state, since FROM reader_status WHERE worker = :w"), {"w": worker}
        ).all()
        assert [tuple(row) for row in rows] == [("down", T0)]
    with pytest.raises(DBAPIError) as refused, db.system() as conn:
        conn.execute(text("DELETE FROM reader_status WHERE worker = :w"), {"w": worker})
    assert violation(refused.value)[0] == "42501"  # insufficient_privilege
