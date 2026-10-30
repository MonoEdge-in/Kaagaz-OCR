"""Connected accounts' SQL (kaagaz/db/identities.py, #457 phase 1) on a real database."""

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from kaagaz.db import identities
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from tests.db_support import name_session, new_customer, wait_until_blocked

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
ISSUER = "https://accounts.google.com"


def new_member(db: Database, customer_id: str, *, name: str = "Ramesh Kumar") -> str:
    with db.customer(customer_id) as conn:
        row = users_sql.create_user(
            conn,
            customer_id,
            email=f"member-{uuid.uuid4().hex}@example.com",
            display_name=name,
            role="member",
            password_hash="not-a-real-hash",
            must_change_password=False,
        )
    return str(row["id"])


def subject() -> str:
    return f"made-up-sub-{uuid.uuid4().hex}"


def add(
    db: Database,
    customer_id: str,
    user_id: str,
    sub: str,
    *,
    approved: bool = False,
    email: str | None = "Ramesh.K@Example.com",
) -> str:
    with db.customer(customer_id) as conn:
        return identities.add(
            conn,
            customer_id,
            user_id,
            provider="google",
            issuer=ISSUER,
            subject=sub,
            email=email,
            approved=approved,
            approved_by=None,
            now=NOW,
        )


def test_add_then_lookup(db: Database) -> None:
    customer_id, user_id = new_customer(db)
    sub = subject()
    identity_id = add(db, customer_id, user_id, sub)
    with db.system() as conn:
        found = identities.lookup(conn, "google", ISSUER, sub)
        assert identities.lookup(conn, "google", ISSUER, subject()) is None
    assert found == identities.IdentityRow(identity_id, user_id, customer_id, "google", "pending")


def test_add_same_identity_for_another_user_raises_identity_taken(db: Database) -> None:
    """Review focus 4: two users connect one Google account at the same moment. The second
    insert waits on the first's unique key, then gets IdentityTaken, never a 500."""
    customer_a, user_a = new_customer(db)
    customer_b, user_b = new_customer(db)
    sub = subject()
    outcome: list[BaseException | str] = []

    def second() -> None:
        try:
            with db.customer(customer_b) as conn:
                name_session(conn, "identity-race")
                identities.add(
                    conn,
                    customer_b,
                    user_b,
                    provider="google",
                    issuer=ISSUER,
                    subject=sub,
                    email=None,
                    approved=False,
                    approved_by=None,
                    now=NOW,
                )
            outcome.append("added")
        except BaseException as error:  # noqa: BLE001 - handed to the test thread below
            outcome.append(error)

    with db.customer(customer_a) as conn:
        identities.add(
            conn,
            customer_a,
            user_a,
            provider="google",
            issuer=ISSUER,
            subject=sub,
            email=None,
            approved=False,
            approved_by=None,
            now=NOW,
        )
        racer = threading.Thread(target=second)
        racer.start()
        wait_until_blocked(db, application_name="identity-race")
    racer.join(10)
    assert len(outcome) == 1 and isinstance(outcome[0], identities.IdentityTaken)
    assert outcome[0].has_provider is False  # the account is someone's: "taken"


def test_a_second_google_for_one_user_raises_identity_taken(db: Database) -> None:
    customer_id, user_id = new_customer(db)
    add(db, customer_id, user_id, subject())
    with pytest.raises(identities.IdentityTaken) as taken:
        add(db, customer_id, user_id, subject())
    assert taken.value.has_provider is True  # one of each provider a user: "has_provider"


def test_add_stores_email_lowercased(db: Database) -> None:
    customer_id, user_id = new_customer(db)
    add(db, customer_id, user_id, subject(), email="Ramesh.K@Gmail.com")
    with db.customer(customer_id) as conn:
        rows = identities.for_user(conn, customer_id, user_id)
    assert [row["email_at_link"] for row in rows] == ["ramesh.k@gmail.com"]


def test_approve_only_pending(db: Database) -> None:
    customer_id, admin_id = new_customer(db)
    member_id = new_member(db, customer_id)
    identity_id = add(db, customer_id, member_id, subject())
    with db.customer(customer_id) as conn:
        approved = identities.approve(conn, customer_id, identity_id, admin_id, NOW)
        again = identities.approve(conn, customer_id, identity_id, admin_id, NOW + timedelta(minutes=1))
        rows = identities.for_user(conn, customer_id, member_id)
    assert approved is not None and str(approved["user_id"]) == member_id
    assert again is None
    assert [row["status"] for row in rows] == ["approved"]


def test_pending_lists_waiting_connections_with_their_people(db: Database) -> None:
    customer_id, _ = new_customer(db)
    waiting = new_member(db, customer_id, name="Ramesh Kumar")
    approved = new_member(db, customer_id, name="Sita Rao")
    identity_id = add(db, customer_id, waiting, subject())
    add(db, customer_id, approved, subject(), approved=True)
    with db.customer(customer_id) as conn:
        rows = identities.pending(conn, customer_id)
        by_user = identities.providers_by_user(conn, customer_id)
    assert [(str(row["id"]), row["display_name"], row["provider"]) for row in rows] == [
        (identity_id, "Ramesh Kumar", "google")
    ]
    assert by_user == {approved: ["google"]}


def test_remove_with_user_id_refuses_other_users_row(db: Database) -> None:
    customer_id, admin_id = new_customer(db)
    member_id = new_member(db, customer_id)
    identity_id = add(db, customer_id, member_id, subject())
    with db.customer(customer_id) as conn:
        assert identities.remove(conn, customer_id, identity_id, user_id=admin_id) is None
        removed = identities.remove(conn, customer_id, identity_id, user_id=member_id)
        assert identities.for_user(conn, customer_id, member_id) == []
    assert removed is not None and removed["provider"] == "google"


def test_remove_all_counts(db: Database) -> None:
    customer_id, admin_id = new_customer(db)
    member_id = new_member(db, customer_id)
    with db.customer(customer_id) as conn:
        for provider in ("google", "microsoft"):
            identities.add(
                conn,
                customer_id,
                member_id,
                provider=provider,
                issuer=ISSUER,
                subject=subject(),
                email=None,
                approved=True,
                approved_by=admin_id,
                now=NOW,
            )
    with db.customer(customer_id) as conn:
        assert identities.remove_all(conn, customer_id, member_id) == 2
        assert identities.remove_all(conn, customer_id, member_id) == 0


def test_touch_used_stamps_last_used(db: Database) -> None:
    customer_id, user_id = new_customer(db)
    identity_id = add(db, customer_id, user_id, subject(), approved=True)
    with db.customer(customer_id) as conn:
        identities.touch_used(conn, customer_id, identity_id, NOW + timedelta(hours=1))
        rows = identities.for_user(conn, customer_id, user_id)
    assert [row["last_used_at"] for row in rows] == [NOW + timedelta(hours=1)]


def test_email_belongs_to_other(db: Database) -> None:
    customer_a, admin_a = new_customer(db)
    _, admin_b = new_customer(db)
    with db.customer(customer_a) as conn:
        found = users_sql.contact(conn, customer_a, admin_a)
    assert found is not None
    with db.system() as conn:
        assert identities.email_belongs_to_other(conn, found[0].upper(), admin_b) is True
        assert identities.email_belongs_to_other(conn, found[0], admin_a) is False
