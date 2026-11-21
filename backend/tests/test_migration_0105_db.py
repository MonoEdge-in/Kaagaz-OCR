"""Migration 0105 (#737): the one read of a customer's Keycloak links, and how Kaagaz uses it."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.db import keycloak
from kaagaz.db.engine import Database
from tests.db_support import APP_ROLE, new_customer
from tests.test_isolation_logins import WORKER

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
ISSUER = "https://id.example.test/realms/kaagaz-test"


def _subject() -> str:
    return f"subject-{uuid.uuid4().hex}"  # unique: a link's issuer and subject are unique for good


def _linked(db: Database, customer: str, user: str, subject: str) -> None:
    with db.user(customer, user) as conn:
        keycloak.link_account(conn, ISSUER, subject, now=NOW)


def test_the_function_is_commented_and_both_roles_may_call_it_but_not_the_public(db: Database) -> None:
    with db.engine.begin() as conn:
        comment = conn.execute(
            text("SELECT obj_description('public.kaagaz_keycloak_subjects()'::regprocedure, 'pg_proc')")
        ).scalar_one()
        rights = {
            role: conn.execute(
                text("SELECT has_function_privilege(:r, 'public.kaagaz_keycloak_subjects()', 'EXECUTE')"),
                {"r": role},
            ).scalar_one()
            for role in (APP_ROLE, WORKER)
        }
        public = conn.execute(
            text("SELECT has_function_privilege('public', 'public.kaagaz_keycloak_subjects()', 'EXECUTE')")
        ).scalar_one()
    assert comment
    assert rights == {APP_ROLE: True, WORKER: True} and public is False


def test_subjects_are_the_current_customers_active_links_only(db: Database) -> None:
    mine, my_admin = new_customer(db)
    other, other_admin = new_customer(db)
    mine_subject = _subject()
    _linked(db, mine, my_admin, mine_subject)
    _linked(db, other, other_admin, _subject())
    with db.customer(mine) as conn:
        assert keycloak.subjects(conn, ISSUER) == {my_admin: mine_subject}
        assert keycloak.subjects(conn, "https://another.example.test/realms/x") == {}
        assert keycloak.subject_of(conn, other_admin, ISSUER) is None
    with db.system() as conn:  # no customer: nothing
        assert keycloak.subjects(conn, ISSUER) == {}


def test_a_pending_link_is_not_listed_and_people_say_who_is_linked(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        member = str(
            conn.execute(
                text(
                    "INSERT INTO users (customer_id, email, display_name, role, password_hash)"
                    " VALUES (:c, 'm-' || gen_random_uuid() || '@example.test', 'Member', 'member', 'x')"
                    " RETURNING id"
                ),
                {"c": customer},
            ).scalar_one()
        )
    with db.user(customer, member) as conn:  # staged but never finalized
        keycloak.stage_link(conn, ISSUER, _subject(), now=NOW, expected_session_version=1)
    admin_subject = _subject()
    _linked(db, customer, admin, admin_subject)
    with db.customer(customer) as conn:
        assert keycloak.subjects(conn, ISSUER) == {admin: admin_subject}
        people = {p.user_id: p for p in keycloak.people(conn, customer, ISSUER)}
    assert people[admin].linked is True and people[member].linked is False
    assert people[member].disabled is False and people[member].name == "Member"


def test_a_second_link_for_the_same_person_or_subject_is_refused(db: Database) -> None:
    customer, admin = new_customer(db)
    other, other_admin = new_customer(db)
    shared = _subject()
    _linked(db, customer, admin, shared)
    with pytest.raises(keycloak.IdentityTaken):
        _linked(db, other, other_admin, shared)  # the subject belongs to someone else
    with pytest.raises(Exception, match="no active legacy user"):
        _linked(db, customer, admin, _subject())  # this person is already on Keycloak
