"""Migration 0103's comments, privilege parity, reversible legacy grants and safety guard."""

import subprocess
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.db import keycloak
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import APP_ROLE, migrate, new_customer
from tests.test_isolation_logins import _RIGHTS, WORKER
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0103 = _load("0103_keycloak_identity.py")


def test_app_and_worker_rights_match_and_the_new_dictionary_entries_are_complete(db: Database) -> None:
    with db.engine.begin() as conn:
        app_rights = {tuple(row) for row in conn.execute(_RIGHTS, {"role": APP_ROLE})}
        worker_rights = {tuple(row) for row in conn.execute(_RIGHTS, {"role": WORKER})}
        missing = conn.execute(
            text(
                "SELECT c.relname, a.attname FROM pg_class c"
                " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped"
                " WHERE c.relnamespace = 'public'::regnamespace"
                " AND c.relname IN ('keycloak_identities', 'keycloak_sessions',"
                " 'keycloak_session_revocations', 'keycloak_logout_jtis', 'keycloak_flows')"
                " AND (obj_description(c.oid, 'pg_class') IS NULL"
                " OR col_description(c.oid, a.attnum) IS NULL)"
            )
        ).all()
        authority_comment = conn.execute(
            text(
                "SELECT col_description('public.users'::regclass, a.attnum)"
                " FROM pg_attribute a WHERE a.attrelid = 'public.users'::regclass"
                " AND a.attname = 'auth_authority'"
            )
        ).scalar_one()
    assert worker_rights == app_rights
    assert missing == []
    assert authority_comment


def test_downgrade_restores_user_insert_and_update_grants(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0103")
    customer, _ = new_customer(db)

    base._downgrade(url, "0100")
    with db.customer(customer) as conn:
        created = conn.execute(
            text(
                "INSERT INTO users (customer_id, email, display_name, role, password_hash)"
                " VALUES (:c, :email, 'After downgrade', 'member', 'not-a-real-hash') RETURNING id"
            ),
            {"c": customer, "email": "after-downgrade@example.test"},
        ).scalar_one()
        statement = (
            "UPDATE users SET display_name = 'Updated after downgrade' WHERE customer_id = :c AND id = :u"
        )
        conn.execute(text(statement), {"c": customer, "u": created})
        assert (
            conn.execute(
                text("SELECT display_name FROM users WHERE customer_id = :c AND id = :u"),
                {"c": customer, "u": created},
            ).scalar_one()
            == "Updated after downgrade"
        )

    migrate(url, "0103")


def test_downgrade_refuses_to_turn_a_mapped_user_back_into_legacy(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0103")
    customer, user = new_customer(db)
    now = datetime.now(UTC)
    with db.user(customer, user) as conn:
        keycloak.stage_link(
            conn,
            "https://id.example.test/realm",
            "subject",
            now=now,
            expected_session_version=1,
        )
        keycloak.finalize_link(
            conn,
            "https://id.example.test/realm",
            "subject",
            now=now,
            expected_session_version=1,
        )

    with pytest.raises(subprocess.CalledProcessError) as refused:
        base._downgrade(url, "0100")
    assert b"cannot downgrade while Keycloak users" in refused.value.stderr
    with db.engine.begin() as conn:  # the owner: the app role may not read alembic_version
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0103"
