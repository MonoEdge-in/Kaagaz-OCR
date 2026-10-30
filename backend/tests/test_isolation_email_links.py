"""Email links (0061, #457 phase 0) never cross customers.

Customer B's transaction cannot read, use up or add customer A's links, even when it names
A's customer and link ids: row level security hides A's rows and its WITH CHECK refuses
them. B's own session asking for a link sends it to B's own user only.
"""

import hashlib
import secrets
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.adapters.email import MemoryMailer
from kaagaz.db import email_links as links_sql
from kaagaz.db.engine import Database
from tests.db_support import new_customer
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, NOW, Api

pytestmark = pytest.mark.db


def _hash() -> bytes:
    return hashlib.sha256(secrets.token_bytes(32)).digest()


def test_another_customers_links_cannot_be_read_used_or_added(db: Database) -> None:
    customer_a, user_a = new_customer(db, name="Org A links")
    customer_b, user_b = new_customer(db, name="Org B links")
    hash_a = _hash()
    with db.customer(customer_a) as conn:
        links_sql.add(conn, customer_a, user_a, "reset", hash_a, now=NOW, expires_at=NOW + timedelta(hours=1))
    with db.system() as conn:
        found = links_sql.lookup(conn, hash_a)
    assert found is not None and found.customer_id == customer_a

    with db.customer(customer_b) as conn:
        seen = conn.execute(
            text("SELECT count(*) FROM email_links WHERE customer_id = :a"), {"a": customer_a}
        )
        assert seen.scalar() == 0
        assert links_sql.use(conn, customer_a, found.link_id, now=NOW) is False  # even named
        assert links_sql.use(conn, customer_b, found.link_id, now=NOW) is False
    with pytest.raises(DBAPIError), db.customer(customer_b) as conn:  # WITH CHECK: not A's row
        links_sql.add(
            conn, customer_a, user_a, "reset", _hash(), now=NOW, expires_at=NOW + timedelta(hours=1)
        )
    with pytest.raises(DBAPIError), db.customer(customer_b) as conn:  # nor B's row on A's user
        links_sql.add(
            conn, customer_b, user_a, "reset", _hash(), now=NOW, expires_at=NOW + timedelta(hours=1)
        )

    with db.system() as conn:
        still = links_sql.lookup(conn, hash_a)
    assert still is not None and still.used_at is None  # A's link is untouched
    assert user_b != user_a


def test_a_session_asks_for_a_link_to_its_own_user_only(db: Database) -> None:
    mailer = MemoryMailer()
    settings = make_settings(
        email_enabled=True, email_from="no-reply@example.com", app_url="https://k.example.com"
    )
    api = Api(db, settings=settings, mailer=mailer)
    a = api.new_signed("member")
    b = api.new_signed("member")

    assert b.client.post("/api/auth/email/send-confirm", json={}, headers=CSRF).status_code == 200
    assert [sent.to for sent in mailer.sent] == [b.email]
    with db.customer(a.customer_id) as conn:
        count = conn.execute(
            text("SELECT count(*) FROM email_links WHERE customer_id = :a"), {"a": a.customer_id}
        )
        assert count.scalar() == 0
