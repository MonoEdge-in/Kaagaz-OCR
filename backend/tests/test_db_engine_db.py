"""kaagaz/db/engine.py against a real PostgreSQL: a failing statement never shows its
bound values, in the error or in the engine's own log lines (PRD 1 B5)."""

import logging

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.db_support import new_customer

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
