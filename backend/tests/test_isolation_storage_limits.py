"""Storage limit per organisation (0061d, #516): customer B sees none of customer A's limit, and
the app writes no limit at all, not even its own (only the operator's function does)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.db_support import new_customer
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db


def test_another_customer_sees_no_limit_and_the_app_writes_none(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    _owner(db, "INSERT INTO org_storage_limits (customer_id, storage_gb) VALUES (:c, 7)", {"c": a})

    with db.customer(b) as conn:
        assert conn.execute(text("SELECT count(*) FROM org_storage_limits")).scalar_one() == 0
        seen = "SELECT count(*) FROM org_storage_limits WHERE customer_id = :c"
        assert conn.execute(text(seen), {"c": a}).scalar_one() == 0  # even by A's exact id
    with db.customer(a) as conn:
        assert conn.execute(text("SELECT storage_gb FROM org_storage_limits")).scalar_one() == 7

    for statement in (
        "UPDATE org_storage_limits SET storage_gb = 8 WHERE customer_id = :c",
        "DELETE FROM org_storage_limits WHERE customer_id = :c",
        "INSERT INTO org_storage_limits (customer_id, storage_gb) VALUES (:b, 9)",
        "SELECT updated_by FROM org_storage_limits",  # never who set it
        "UPDATE platform_ai_settings SET storage_gb = 9",
    ):
        for customer in (a, b):
            with pytest.raises(DBAPIError), db.customer(customer) as conn:
                conn.execute(text(statement), {"c": a, "b": b})
