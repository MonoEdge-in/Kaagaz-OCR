"""Connected accounts (0062, #457 phase 1) on the database: up, down and up again; one outside
account per Kaagaz user across every customer; one of each provider per user; the two
SECURITY DEFINER functions; row level security; auto-approval off by default. What the
routes do: tests/test_api_oidc_*.py."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from kaagaz.db.sqlstate import UNIQUE_VIOLATION, sqlstate
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer

pytestmark = pytest.mark.db
fresh = base.fresh
NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
ISSUER = "https://accounts.google.com"
_ADD = text(
    "INSERT INTO user_identities (customer_id, user_id, provider, issuer, subject, email_at_link,"
    " status, linked_at) VALUES (:c, :u, :p, :i, :s, :e, 'pending', :now) RETURNING id"
)


def _add(conn: Connection, customer_id: str, user_id: str, subject: str, provider: str = "google") -> str:
    row = conn.execute(
        _ADD,
        {
            "c": customer_id,
            "u": user_id,
            "p": provider,
            "i": ISSUER,
            "s": subject,
            "e": "ramesh.k@example.com",
            "now": NOW,
        },
    ).one()
    return str(row[0])


def _subject() -> str:
    return f"made-up-sub-{uuid.uuid4().hex}"


def test_0062_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0061")
    at_0061 = base._schema(db)

    migrate(url, "0062")
    at_0062 = base._schema(db)
    assert "user_identities" in base._names(at_0062, "relations")

    base._downgrade(url, "0061")
    assert base._schema(db) == at_0061
    migrate(url, "0062")
    assert base._schema(db) == at_0062


def test_identity_unique_across_customers(db: Database) -> None:
    customer_a, user_a = new_customer(db)
    customer_b, user_b = new_customer(db)
    subject = _subject()
    with db.customer(customer_a) as conn:
        _add(conn, customer_a, user_a, subject)
    with pytest.raises(IntegrityError) as taken, db.customer(customer_b) as conn:
        _add(conn, customer_b, user_b, subject)
    assert sqlstate(taken.value) == UNIQUE_VIOLATION


def test_one_provider_per_user(db: Database) -> None:
    customer_id, user_id = new_customer(db)
    with db.customer(customer_id) as conn:
        _add(conn, customer_id, user_id, _subject())
    with pytest.raises(IntegrityError) as second, db.customer(customer_id) as conn:
        _add(conn, customer_id, user_id, _subject())
    assert sqlstate(second.value) == UNIQUE_VIOLATION
    with db.customer(customer_id) as conn:  # another provider is allowed
        _add(conn, customer_id, user_id, _subject(), provider="microsoft")


def test_lookup_finds_across_customers_and_returns_status(db: Database) -> None:
    customer_id, user_id = new_customer(db)
    subject = _subject()
    with db.customer(customer_id) as conn:
        identity_id = _add(conn, customer_id, user_id, subject)
    with db.system() as conn:
        found = conn.execute(
            text("SELECT * FROM kaagaz_identity_lookup('google', :i, :s)"), {"i": ISSUER, "s": subject}
        ).all()
        missing = conn.execute(
            text("SELECT * FROM kaagaz_identity_lookup('google', :i, :s)"), {"i": ISSUER, "s": _subject()}
        ).all()
    assert [tuple(str(value) for value in row) for row in found] == [
        (identity_id, user_id, customer_id, "pending")
    ]
    assert missing == []


def test_email_belongs_to_other_is_case_insensitive_and_ignores_self(db: Database) -> None:
    customer_id, user_id = new_customer(db)
    with db.customer(customer_id) as conn:
        email = str(conn.execute(text("SELECT email FROM users WHERE id = :u"), {"u": user_id}).scalar_one())
    belongs = text("SELECT kaagaz_email_belongs_to_other(:e, :u)")
    with db.system() as conn:
        assert conn.execute(belongs, {"e": email.upper(), "u": str(uuid.uuid4())}).scalar_one() is True
        assert conn.execute(belongs, {"e": email.upper(), "u": user_id}).scalar_one() is False
        assert conn.execute(belongs, {"e": "nobody@example.com", "u": user_id}).scalar_one() is False


def test_rls_hides_other_customers_identities(db: Database) -> None:
    customer_a, user_a = new_customer(db)
    customer_b, _ = new_customer(db)
    with db.customer(customer_a) as conn:
        identity_id = _add(conn, customer_a, user_a, _subject())
    with db.customer(customer_b) as conn:
        seen = conn.execute(text("SELECT count(*) FROM user_identities WHERE id = :i"), {"i": identity_id})
        assert seen.scalar_one() == 0
        gone = conn.execute(text("DELETE FROM user_identities WHERE id = :i"), {"i": identity_id})
        assert gone.rowcount == 0
    with db.customer(customer_a) as conn:
        kept = conn.execute(text("SELECT count(*) FROM user_identities WHERE id = :i"), {"i": identity_id})
        assert kept.scalar_one() == 1


def test_approved_needs_its_time(db: Database) -> None:
    customer_id, user_id = new_customer(db)
    with pytest.raises(IntegrityError) as refused, db.customer(customer_id) as conn:
        identity_id = _add(conn, customer_id, user_id, _subject())
        conn.execute(text("UPDATE user_identities SET status = 'approved' WHERE id = :i"), {"i": identity_id})
    assert sqlstate(refused.value) == "23514"  # check_violation


def test_auto_approve_defaults_off(db: Database) -> None:
    customer_id, _ = new_customer(db)
    with db.customer(customer_id) as conn:
        assert (
            conn.execute(
                text("SELECT auto_approve_connections FROM customers WHERE id = :c"), {"c": customer_id}
            ).scalar_one()
            is False
        )
