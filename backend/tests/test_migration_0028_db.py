"""Migration 0028 (stuck reading, 16 Oct 2026) against a real PostgreSQL: the worker's
progress writes for Dynamic's Careful (``rereading``) and for a reader that is down
(``waiting``) are kept, an unknown step is still refused, and down then up again works."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from kaagaz.pipeline import Progress
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer
from tests.test_migration_0004_db import _current, _head

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name

T0 = datetime(2026, 10, 16, 5, 0, tzinfo=UTC)
_PROGRESS = "SELECT progress FROM documents WHERE customer_id = :c AND id = :d"


def test_rereading_and_waiting_progress_are_kept(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        doc = insert_document(conn, customer, status="clean")
    run = PgRuns(db).start(customer, doc, now=T0)
    documents = PgDocuments(db)

    documents.set_progress(customer, doc, Progress("rereading", 0, 1), run_id=run)
    with db.customer(customer) as conn:
        stored: dict[str, object] = conn.execute(text(_PROGRESS), {"c": customer, "d": doc}).scalar_one()
    assert (stored["step"], stored["done"], stored["total"]) == ("rereading", 0, 1)

    documents.clear_progress(customer, doc, run_id=run, waiting="reader")
    with db.customer(customer) as conn:
        assert conn.execute(text(_PROGRESS), {"c": customer, "d": doc}).scalar_one() == {"step": "waiting"}
        with pytest.raises(IntegrityError):
            conn.execute(
                text("UPDATE documents SET progress = CAST(:p AS jsonb) WHERE customer_id = :c AND id = :d"),
                {"p": '{"step": "guessing", "done": 0, "total": 1}', "c": customer, "d": doc},
            )


def test_0028_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0027")
    at_0027 = m0002._schema(database)
    migrate(url, "head")
    assert _current(url) == _head()
    migrate(url, "0027")
    assert m0002._schema(database) == at_0027
    migrate(url, "head")
    assert _current(url) == _head()
