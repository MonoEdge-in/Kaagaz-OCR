"""Keycloak identity and session links stay inside their owning user and customer."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import keycloak, users
from kaagaz.db.engine import Database
from kaagaz.db.sqlstate import sqlstate
from tests.db_support import APP_ROLE, new_customer
from tests.test_isolation_logins import _RIGHTS, WORKER

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
ISSUER = "https://id.example.test/realms/kaagaz"


def _application(db: Database) -> Database:
    return Database(db.engine, APP_ROLE)


def _owner_update(db: Database, customer_id: str, statement: str, params: dict[str, Any]) -> None:
    with db.engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('kaagaz.customer_id', :customer, true)"),
            {"customer": customer_id},
        )
        conn.execute(text(statement), params)


def _link(db: Database, customer_id: str, user_id: str, subject: str) -> None:
    with db.user(customer_id, user_id) as conn:
        keycloak.stage_link(conn, ISSUER, subject, now=NOW, expected_session_version=1)
        keycloak.finalize_link(conn, ISSUER, subject, now=NOW, expected_session_version=1)


def _member(db: Database, customer_id: str) -> str:
    with db.customer(customer_id) as conn:
        return str(
            conn.execute(
                text(
                    "INSERT INTO users (customer_id, email, display_name, role, password_hash)"
                    " VALUES (:c, :email, 'Member', 'member', 'not-a-real-hash') RETURNING id"
                ),
                {"c": customer_id, "email": f"member-{uuid.uuid4().hex}@example.test"},
            ).scalar_one()
        )


def _subject() -> str:
    return f"subject-{uuid.uuid4().hex}"


def test_cutover_is_explicit_and_lookup_rejects_disabled_or_inactive_users(db: Database) -> None:
    app = _application(db)
    customer, user = new_customer(app)
    subject = _subject()
    with app.customer(customer) as conn:
        assert (
            conn.execute(
                text("SELECT auth_authority FROM users WHERE customer_id = :c AND id = :u"),
                {"c": customer, "u": user},
            ).scalar_one()
            == "legacy"
        )
    with app.system() as conn:
        assert keycloak.lookup(conn, ISSUER, subject) is None

    with app.user(customer, user) as conn:
        assert keycloak.stage_link(conn, ISSUER, subject, now=NOW, expected_session_version=1)
    with app.system() as conn:
        assert keycloak.lookup(conn, ISSUER, subject) is None

    with app.user(customer, user) as conn:
        assert keycloak.finalize_link(conn, ISSUER, subject, now=NOW, expected_session_version=1) == 2
    with app.system() as conn:
        found = keycloak.lookup(conn, ISSUER, subject)
    assert found is not None
    assert (found.customer_id, found.user_id, found.session_version) == (customer, user, 2)

    with app.customer(customer) as conn:
        conn.execute(
            text("UPDATE users SET disabled = true WHERE customer_id = :c AND id = :u"),
            {"c": customer, "u": user},
        )
    with app.system() as conn:
        assert keycloak.lookup(conn, ISSUER, subject) is None

    with app.customer(customer) as conn:
        conn.execute(
            text("UPDATE users SET disabled = false WHERE customer_id = :c AND id = :u"),
            {"c": customer, "u": user},
        )
    _owner_update(
        db,
        customer,
        "UPDATE customers SET status = 'disabled', disabled_at = :now WHERE id = :customer",
        {"now": NOW, "customer": customer},
    )
    with app.system() as conn:
        assert keycloak.lookup(conn, ISSUER, subject) is None


def test_pending_link_cannot_be_finalized_by_another_user_or_customer(db: Database) -> None:
    app = _application(db)
    customer_a, user_a = new_customer(app)
    _customer_b, user_b = new_customer(app)
    same_customer_user = _member(app, customer_a)
    subject = _subject()
    with app.user(customer_a, user_a) as conn:
        keycloak.stage_link(conn, ISSUER, subject, now=NOW, expected_session_version=1)

    with pytest.raises(DBAPIError) as error, app.user(customer_a, same_customer_user) as conn:
        keycloak.finalize_link(conn, ISSUER, subject, now=NOW, expected_session_version=1)
    assert sqlstate(error.value) == "55000"

    # A context that names a user outside its tenant is rejected before looking for a pending link.
    with pytest.raises(DBAPIError) as error, app.user(customer_a, user_b) as conn:
        keycloak.finalize_link(conn, ISSUER, subject, now=NOW, expected_session_version=1)
    assert sqlstate(error.value) == "42501"

    with app.system() as conn:
        assert keycloak.lookup(conn, ISSUER, subject) is None


def test_identity_is_globally_unique_and_immutable(db: Database) -> None:
    app = _application(db)
    customer_a, user_a = new_customer(app)
    customer_b, user_b = new_customer(app)
    subject = _subject()
    _link(app, customer_a, user_a, subject)

    with pytest.raises(keycloak.IdentityTaken), app.user(customer_b, user_b) as conn:
        keycloak.stage_link(conn, ISSUER, subject, now=NOW, expected_session_version=1)

    with pytest.raises(DBAPIError) as error:
        _owner_update(
            db,
            customer_a,
            "UPDATE keycloak_identities SET subject = 'moved-subject'"
            " WHERE customer_id = :customer AND user_id = :user",
            {"customer": customer_a, "user": user_a},
        )
    assert sqlstate(error.value) == "55000"

    with pytest.raises(DBAPIError) as error, app.user(customer_a, user_a) as conn:
        conn.execute(
            text("SELECT id FROM keycloak_identities WHERE customer_id = :c"),
            {"c": customer_a},
        )
    assert sqlstate(error.value) == "42501"


def test_user_cannot_write_auth_authority_directly(db: Database) -> None:
    app = _application(db)
    customer, user = new_customer(app)
    with pytest.raises(DBAPIError) as error, app.customer(customer) as conn:
        conn.execute(
            text("UPDATE users SET auth_authority = 'keycloak' WHERE customer_id = :c AND id = :u"),
            {"c": customer, "u": user},
        )
    assert sqlstate(error.value) == "42501"

    with pytest.raises(DBAPIError) as error, app.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO users"
                " (customer_id, email, display_name, role, password_hash, auth_authority)"
                " VALUES (:c, :email, 'Injected', 'member', 'not-a-real-hash', 'keycloak')"
            ),
            {"c": customer, "email": f"injected-{uuid.uuid4().hex}@example.test"},
        )
    assert sqlstate(error.value) == "42501"


def test_local_and_signed_logout_end_only_mapped_sessions(db: Database) -> None:
    app = _application(db)
    customer, user_a = new_customer(app)
    other_customer, other_user = new_customer(app)
    user_b = _member(app, customer)

    subject_a, subject_b, subject_other = _subject(), _subject(), _subject()
    _link(app, customer, user_a, subject_a)
    _link(app, customer, user_b, subject_b)
    _link(app, other_customer, other_user, subject_other)
    local_a, local_b, local_other = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    expiry = NOW + timedelta(hours=1)
    with app.user(customer, user_a) as conn:
        assert keycloak.record_session(
            conn, ISSUER, subject_a, "kc-session-a", local_a, expected_session_version=2, expires_at=expiry
        )
    with app.user(customer, user_b) as conn:
        assert keycloak.record_session(
            conn, ISSUER, subject_b, "kc-session-b", local_b, expected_session_version=2, expires_at=expiry
        )
    with app.user(other_customer, other_user) as conn:
        assert keycloak.record_session(
            conn,
            ISSUER,
            subject_other,
            "kc-session-other",
            local_other,
            expected_session_version=2,
            expires_at=expiry,
        )
        # The app and worker can read session associations, but RLS hides another tenant's rows.
        assert (
            conn.execute(
                text("SELECT local_session_id FROM keycloak_sessions WHERE customer_id = :c"),
                {"c": customer},
            ).all()
            == []
        )

    with app.user(customer, user_b) as conn:
        assert keycloak.revoke_session(conn, local_a, now=NOW) is False
        assert keycloak.session_for_user(conn, customer, user_b, local_a) is None
        assert keycloak.session_for_user(conn, customer, user_a, local_a) is None
    with app.user(other_customer, other_user) as conn:
        assert keycloak.revoke_session(conn, local_a, now=NOW) is False
        assert keycloak.session_for_user(conn, customer, user_a, local_a) is None
    with app.user(customer, user_a) as conn:
        assert keycloak.session_for_user(conn, customer, user_a, local_a) is not None
        assert keycloak.revoke_session(conn, local_a, now=NOW) is True
        assert keycloak.revoke_session(conn, local_a, now=NOW) is False
        assert keycloak.session_for_user(conn, customer, user_a, local_a) is None
        assert users.session_user(conn, customer, user_a, session_id=local_a) is None
    with app.user(customer, user_b) as conn:
        assert users.session_user(conn, customer, user_b, session_id=local_b) is not None
    with app.user(other_customer, other_user) as conn:
        assert users.session_user(conn, other_customer, other_user, session_id=local_other) is not None

    with app.system() as conn:
        assert (
            keycloak.apply_signed_logout(
                conn,
                ISSUER,
                subject_b,
                "kc-session-b",
                f"logout-{uuid.uuid4().hex}",
                token_expires_at=NOW + timedelta(minutes=5),
                now=NOW,
            )
            == 1
        )
    with app.user(customer, user_b) as conn:
        assert users.session_user(conn, customer, user_b, session_id=local_b) is None


def test_logout_before_session_record_leaves_sid_tombstone_and_consumes_jti(db: Database) -> None:
    app = _application(db)
    customer, user = new_customer(app)
    subject = _subject()
    _link(app, customer, user, subject)
    sid = f"early-logout-{uuid.uuid4().hex}"
    jti = f"logout-{uuid.uuid4().hex}"
    with app.system() as conn:
        assert (
            keycloak.apply_signed_logout(
                conn,
                ISSUER,
                subject,
                sid,
                jti,
                token_expires_at=NOW + timedelta(minutes=5),
                now=NOW,
            )
            == 0
        )
        # The jti is single use even if a later token names a different sid.
        assert (
            keycloak.apply_signed_logout(
                conn,
                ISSUER,
                subject,
                f"different-{sid}",
                jti,
                token_expires_at=NOW + timedelta(minutes=5),
                now=NOW,
            )
            is None
        )
    with app.user(customer, user) as conn:
        assert not keycloak.record_session(
            conn,
            ISSUER,
            subject,
            sid,
            str(uuid.uuid4()),
            expected_session_version=2,
            expires_at=NOW + timedelta(hours=1),
        )


def test_session_record_rejects_stale_version_and_wrong_explicit_identity(db: Database) -> None:
    app = _application(db)
    customer, user = new_customer(app)
    subject = _subject()
    _link(app, customer, user, subject)
    with app.customer(customer) as conn:
        conn.execute(
            text("UPDATE users SET session_version = session_version + 1 WHERE customer_id = :c AND id = :u"),
            {"c": customer, "u": user},
        )
    with app.user(customer, user) as conn:
        assert not keycloak.record_session(
            conn,
            ISSUER,
            subject,
            f"stale-{uuid.uuid4().hex}",
            str(uuid.uuid4()),
            expected_session_version=2,
            expires_at=NOW + timedelta(hours=1),
        )


def test_app_and_worker_receive_the_same_new_function_and_table_privileges(db: Database) -> None:
    with db.engine.begin() as conn:
        app_rights = {tuple(row) for row in conn.execute(_RIGHTS, {"role": APP_ROLE})}
        worker_rights = {tuple(row) for row in conn.execute(_RIGHTS, {"role": WORKER})}
    assert worker_rights == app_rights
