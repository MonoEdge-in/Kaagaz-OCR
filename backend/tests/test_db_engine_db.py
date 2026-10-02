"""kaagaz/db/engine.py against a real PostgreSQL: a failing statement never shows its
bound values, in the error or in the engine's own log lines (PRD 1 B5)."""

import logging

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.pool import QueuePool

from kaagaz.db.engine import Database, make_engine, normalise_url, unavailable
from kaagaz.ingestion.errors import BackendUnavailable
from tests.db_support import APP_ROLE, database_url, new_customer

pytestmark = pytest.mark.db

ROW_VALUE = "ABCDE1234F"  # made up; stands in for a value read from a document


def test_a_database_error_never_shows_its_bound_values(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy")  # the engine logs statements too
    customer, _ = new_customer(db)

    with pytest.raises(DBAPIError) as caught, db.customer(customer) as conn:
        conn.execute(text("SELECT CAST(:v AS text), 1/0"), {"v": ROW_VALUE})

    assert "division by zero" in str(caught.value)
    assert ROW_VALUE not in str(caught.value)
    assert ROW_VALUE not in repr(caught.value)
    assert "SELECT CAST" in caplog.text, "the engine did log the statement"
    assert ROW_VALUE not in caplog.text


def test_the_pool_has_explicit_limits() -> None:
    """A7a (PROVISIONAL numbers): 10 kept, 5 more when busy (15 at most, as before),
    and a wait of at most 10 seconds for a free one."""
    engine = make_engine("postgresql://unused@localhost:1/unused")
    assert isinstance(engine.pool, QueuePool)
    assert (engine.pool.size(), engine.pool.timeout(), engine.pool._max_overflow) == (10, 10, 5)


def test_no_free_connection_is_backend_unavailable_like_a_lost_database() -> None:
    """The upload service undoes and retries on BackendUnavailable: a pool wait that
    runs out must reach it the same way as an OperationalError."""
    caught: Exception | None = None
    try:
        with unavailable():
            raise PoolTimeout("QueuePool limit of size 10 overflow 5 reached")
    except Exception as error:  # noqa: BLE001 - the test looks at what came out
        caught = error
    assert isinstance(caught, BackendUnavailable)


def test_one_pooled_connection_keeps_no_role_or_customer_between_transactions(db: Database) -> None:
    """``SET LOCAL ROLE`` and the customer setting end with each transaction, after a
    commit and after a rollback, so the next checkout of the same connection starts clean."""
    customer, _ = new_customer(db)
    engine = create_engine(normalise_url(database_url()), pool_size=1, max_overflow=0, pool_timeout=0.2)
    one = Database(engine, APP_ROLE)
    check = text(
        "SELECT pg_backend_pid(), current_user, session_user, current_setting('kaagaz.customer_id', true)"
    )
    try:
        with one.customer(customer) as conn:
            pid, role, login, current = conn.execute(check).one()
            assert (role, current) == (APP_ROLE, customer)
            with pytest.raises(PoolTimeout), one.customer(customer):
                pass  # the only connection is in use
        clean = {(pid, login, login, None), (pid, login, login, "")}  # the same connection, as itself
        with engine.connect() as conn:  # after a commit
            assert tuple(conn.execute(check).one()) in clean
        with pytest.raises(DBAPIError), one.customer(customer) as conn:
            conn.execute(text("SELECT 1/0"))
        with engine.connect() as conn:  # after a rollback
            assert tuple(conn.execute(check).one()) in clean
    finally:
        engine.dispose()
