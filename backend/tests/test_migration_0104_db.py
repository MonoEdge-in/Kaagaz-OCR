"""Migration 0104: the Keycloak policies, and that a colleague's link or session is never seen."""

import uuid

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer

pytestmark = pytest.mark.db
fresh = base.fresh

_POLICIES = text(
    "SELECT tablename, policyname, permissive FROM pg_policies WHERE schemaname = 'public'"
    " AND tablename IN ('keycloak_identities', 'keycloak_sessions', 'keycloak_flows')"
)


def _policies(db: Database) -> set[tuple[str, str, str]]:
    with db.engine.begin() as conn:
        return {tuple(row) for row in conn.execute(_POLICIES)}


def test_upgrade_adds_and_downgrade_removes_the_policies(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0103")
    assert ("keycloak_flows", "customer_only", "PERMISSIVE") not in _policies(db)
    migrate(url, "0104")
    assert _policies(db) == {
        ("keycloak_identities", "customer_only", "PERMISSIVE"),
        ("keycloak_identities", "own_user_only", "RESTRICTIVE"),
        ("keycloak_sessions", "customer_only", "PERMISSIVE"),
        ("keycloak_sessions", "own_user_only", "RESTRICTIVE"),
        ("keycloak_flows", "customer_only", "PERMISSIVE"),
    }
    base._downgrade(url, "0103")
    assert {name for _, name, _ in _policies(db)} == {"customer_only"}
    assert ("keycloak_flows", "customer_only", "PERMISSIVE") not in _policies(db)
    migrate(url, "0104")


def test_a_user_sees_no_other_users_session_rows(db: Database) -> None:
    customer, user = new_customer(db)
    with db.engine.begin() as conn:  # the owner, past the policies, sets the rows up
        identity = conn.execute(
            text(
                "INSERT INTO keycloak_identities (customer_id, user_id, issuer, subject, status, linked_at)"
                " VALUES (:c, :u, 'https://id.example.test/realm', :s, 'pending', now()) RETURNING id"
            ),
            {"c": customer, "u": user, "s": uuid.uuid4().hex},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO keycloak_sessions (customer_id, user_id, identity_id, keycloak_session_id,"
                " local_session_id, expires_at) VALUES (:c, :u, :i, 'sid-0104', gen_random_uuid(),"
                " now() + interval '1 hour')"
            ),
            {"c": customer, "u": user, "i": identity},
        )
        other = conn.execute(
            text(
                "INSERT INTO users (customer_id, email, display_name, role, password_hash)"
                " VALUES (:c, 'colleague@example.test', 'Colleague', 'member', 'not-a-real-hash')"
                " RETURNING id"
            ),
            {"c": customer},
        ).scalar_one()
    count = "SELECT count(*) FROM keycloak_sessions"
    with db.user(customer, user) as conn:
        assert conn.execute(text(count)).scalar_one() == 1
    with db.user(customer, str(other)) as conn:  # a colleague in the same customer
        assert conn.execute(text(count)).scalar_one() == 0
    with db.customer(customer) as conn:  # no user set: fails closed
        assert conn.execute(text(count)).scalar_one() == 0
