"""Migration 0077a (#502, logout ends this browser's session) on PostgreSQL: up, down (0077's
schema exactly, with an ended session written) and up again, with the app's rights. The logout,
the check, the sweep and customer separation: test_guardrails_signin; the policy, grants and
owner-rights function: test_isolation_schema."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import APP_ROLE, migrate, new_customer
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0077A = _load("0077a_ended_sessions.py")
SESSION = "6b0e5f4c-1d2a-4c3b-9e8f-0a1b2c3d4e5f"
# What the app may do: read and add ended sessions, never change or delete one; run the sweep.
_RIGHTS = (
    "SELECT has_table_privilege(:r, 'ended_sessions', 'SELECT'),"
    " has_table_privilege(:r, 'ended_sessions', 'INSERT'),"
    " has_table_privilege(:r, 'ended_sessions', 'UPDATE'),"
    " has_table_privilege(:r, 'ended_sessions', 'DELETE'),"
    " has_function_privilege(:r, 'kaagaz_sweep_ended_sessions(timestamptz)', 'EXECUTE'),"
    " has_function_privilege('public', 'kaagaz_sweep_ended_sessions(timestamptz)', 'EXECUTE')"
)


def test_0077a_up_down_up_with_an_ended_session_and_the_apps_rights(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    assert (M0077A.revision, M0077A.down_revision) == ("0077a", "0077")
    migrate(url, "0077")
    before = base._schema(db)

    migrate(url, "0077a")
    after = base._schema(db)
    assert base._names(after, "relations") - base._names(before, "relations") == {
        "ended_sessions",
        "ended_sessions_pkey",
        "ended_sessions_expiry",
        "ended_sessions_user",
    }
    assert base._names(after, "functions") - base._names(before, "functions") == {
        "kaagaz_sweep_ended_sessions"
    }
    assert as_owner(db, _RIGHTS, {"r": APP_ROLE}) == [(True, True, False, False, True, False)]
    customer, user = new_customer(db)
    with db.customer(customer) as conn:  # as the app writes it at logout
        users_sql.end_session(conn, customer, user, SESSION, expires_at=datetime(2026, 11, 6, 22, tzinfo=UTC))
        assert conn.execute(text("SELECT count(*) FROM ended_sessions")).scalar_one() == 1

    base._downgrade(url, "0077")
    assert base._schema(db) == before
    migrate(url, "0077a")
    assert base._schema(db) == after
    assert as_owner(db, "SELECT count(*) FROM ended_sessions", {}) == [(0,)]  # the rows went with the table
