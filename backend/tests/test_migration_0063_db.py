"""A sign-up that never confirmed its email stops holding it (0063, #457 review): only a
pending, unconfirmed, one-user sign-up older than 24 hours is released. Through the API:
tests/test_api_email.py."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate

pytestmark = pytest.mark.db
fresh = base.fresh


def _sign_up(
    db: Database, *, hours_old: int, pending: bool = True, confirmed: bool = False
) -> tuple[str, str]:
    """A sign-up's organisation and its admin's e-mail, made ``hours_old`` hours ago."""
    email = f"owner-{uuid.uuid4().hex[:12]}@example.com"
    with db.system() as conn:
        customer_id = users_sql.create_customer(
            conn,
            name="Mehta Textiles",
            admin_email=email,
            admin_name="Ramesh",
            password_hash="x",
            pending=pending,
        )
    with db.engine.begin() as conn:  # as the owner: the app may not move a creation time
        conn.execute(
            text("UPDATE customers SET created_at = now() - :age WHERE id = :c"),
            {"age": timedelta(hours=hours_old), "c": customer_id},
        )
        if confirmed:
            conn.execute(
                text("UPDATE users SET email_verified_at = now() WHERE customer_id = :c"), {"c": customer_id}
            )
    return customer_id, email


def _release(db: Database, email: str) -> bool:
    with db.system() as conn:
        return users_sql.release_stale_signup(conn, email)


def _exists(db: Database, customer_id: str) -> bool:
    with db.engine.begin() as conn:
        return (
            conn.execute(text("SELECT count(*) FROM customers WHERE id = :c"), {"c": customer_id}).scalar()
            == 1
        )


def test_an_unconfirmed_sign_up_older_than_a_day_is_released(db: Database) -> None:
    customer_id, email = _sign_up(db, hours_old=25)
    assert _release(db, email.upper()) is True
    assert not _exists(db, customer_id)
    assert _release(db, email) is False  # nothing left to release
    with db.system() as conn:  # the e-mail is free again
        assert users_sql.login_lookup(conn, email) is None


@pytest.mark.parametrize(
    "kind",
    ["fresh", "confirmed", "active", "second_user"],
)
def test_anything_else_is_never_touched(db: Database, kind: str) -> None:
    customer_id, email = _sign_up(
        db, hours_old=23 if kind == "fresh" else 25, pending=kind != "active", confirmed=kind == "confirmed"
    )
    if kind == "second_user":
        with db.customer(customer_id) as conn:
            users_sql.create_user(
                conn,
                customer_id,
                email=f"second-{uuid.uuid4().hex[:8]}@example.com",
                display_name="Sita",
                role="member",
                password_hash="x",
                must_change_password=True,
            )
    assert _release(db, email) is False
    assert _exists(db, customer_id)


@pytest.mark.isolation
def test_releasing_one_sign_up_leaves_every_other_customer_alone(db: Database) -> None:
    stale, email = _sign_up(db, hours_old=25)
    other_stale, _ = _sign_up(db, hours_old=25)
    active, _ = _sign_up(db, hours_old=25, pending=False)
    with db.customer(active) as conn:  # called from another customer's transaction too
        assert conn.execute(text("SELECT kaagaz_release_stale_signup(:e)"), {"e": email}).scalar() is True
    assert not _exists(db, stale)
    assert _exists(db, other_stale) and _exists(db, active)


def test_0063_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0062")
    at_0062 = base._schema(db)
    migrate(url, "0063")
    at_0063 = base._schema(db)
    assert "kaagaz_release_stale_signup" in base._names(at_0063, "functions")
    base._downgrade(url, "0062")
    assert base._schema(db) == at_0062
    migrate(url, "0063")
    assert base._schema(db) == at_0063
