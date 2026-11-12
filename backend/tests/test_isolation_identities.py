"""Connected accounts (0062, #457 phase 1) never cross customers.

Customer B's transaction cannot list, approve, remove or add customer A's connections, even
when it names A's customer, user and connection ids: row level security hides A's rows and
its WITH CHECK refuses them.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import identities
from kaagaz.db.engine import Database
from tests.db_support import new_customer
from tests.test_db_identities import ISSUER, NOW, add, new_member, subject

pytestmark = pytest.mark.db


def test_another_customers_connections_cannot_be_read_approved_or_removed(db: Database) -> None:
    customer_a, _ = new_customer(db, name="Org A connections")
    customer_b, admin_b = new_customer(db, name="Org B connections")
    member_a = new_member(db, customer_a)
    identity_id = add(db, customer_a, member_a, subject())

    with db.customer(customer_b) as conn:
        assert identities.for_user(conn, customer_a, member_a) == []
        assert identities.pending(conn, customer_a) == []
        assert identities.providers_by_user(conn, customer_a) == {}
        assert identities.approve(conn, customer_a, identity_id, admin_b, NOW) is None
        assert identities.approve(conn, customer_b, identity_id, admin_b, NOW) is None
        assert identities.remove(conn, customer_a, identity_id) is None
        assert identities.remove(conn, customer_b, identity_id) is None
        assert identities.remove_all(conn, customer_a, member_a) == 0
        identities.touch_used(conn, customer_a, identity_id, NOW, hosted_domain=None)
    with pytest.raises(DBAPIError), db.customer(customer_b) as conn:  # WITH CHECK: not A's row
        identities.add(
            conn,
            customer_a,
            member_a,
            provider="microsoft",
            issuer=ISSUER,
            subject=subject(),
            email=None,
            approved=True,
            approved_by=None,
            now=NOW,
        )

    with db.customer(customer_a) as conn:
        rows = identities.for_user(conn, customer_a, member_a)
        assert [(str(row["id"]), row["status"], row["last_used_at"]) for row in rows] == [
            (identity_id, "pending", None)
        ]
        seen = conn.execute(
            text("SELECT count(*) FROM user_identities WHERE customer_id = :a"), {"a": customer_a}
        )
        assert seen.scalar_one() == 1
