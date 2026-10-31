"""Email (0061, #457 phase 0) on a fresh database: up, down and up again; the downgrade
refuses while a customer is pending; 0014's status CHECKs with pending; existing users keep
no proven e-mail. What the links do: tests/test_api_email.py."""

import subprocess
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from kaagaz.db.sqlstate import sqlstate
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_migration_0004_db import _current, _head

pytestmark = pytest.mark.db
fresh = base.fresh
NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
_STATUS = text("UPDATE customers SET status = :s, disabled_at = :d, delete_after = :a WHERE id = :c")


def _set(db: Database, customer_id: str, status: str, disabled_at: datetime | None = None) -> None:
    """As the owner (the CLI's connection): the app may not change a status (0014)."""
    with db.engine.begin() as conn:
        delete_after = NOW if status == "deleting" else None
        conn.execute(_STATUS, {"s": status, "d": disabled_at, "a": delete_after, "c": customer_id})


def test_0061_up_down_up(fresh: tuple[str, Database]) -> None:
    # Changed on purpose (#457 with #460, 30 Oct 2026): 0061 follows production's 0061b now.
    url, db = fresh
    migrate(url, "0061b")
    at_0061b = base._schema(db)
    customer_id, user_id = new_customer(db)

    migrate(url, "0061")
    at_0061 = base._schema(db)
    assert "email_links" in base._names(at_0061, "relations")
    with db.customer(customer_id) as conn:  # an existing user is asked to confirm
        assert conn.execute(
            text("SELECT email_verified_at FROM users WHERE id = :u"), {"u": user_id}
        ).one() == (None,)

    base._downgrade(url, "0061b")
    assert base._schema(db) == at_0061b
    migrate(url, "0061")
    assert base._schema(db) == at_0061


def test_the_downgrade_refuses_while_a_customer_is_pending(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "head")
    customer_id, _ = new_customer(db)
    _set(db, customer_id, "pending")

    with pytest.raises(subprocess.CalledProcessError) as refused:
        base._downgrade(url, "0061b")
    assert b"approve or disable it before downgrading" in refused.value.stderr
    assert _current(url) == _head()  # one transaction

    _set(db, customer_id, "active")
    base._downgrade(url, "0061b")


@pytest.mark.parametrize(
    ("status", "disabled_at", "allowed"),
    [
        ("pending", None, True),
        ("pending", NOW, False),  # pending is not disabled
        ("active", None, True),
        ("active", NOW, False),
        ("disabled", NOW, True),
        ("disabled", None, False),
        ("deleting", NOW, True),
        ("deleting", None, False),
        ("asleep", None, False),
    ],
)
def test_the_status_checks_keep_0014s_rule_and_add_pending(
    db: Database, status: str, disabled_at: datetime | None, allowed: bool
) -> None:
    customer_id, _ = new_customer(db, name=f"Check {uuid.uuid4().hex[:6]}")
    if allowed:
        _set(db, customer_id, status, disabled_at)
        _set(db, customer_id, "active")  # nothing else in the shared database sees it
        return
    with pytest.raises(IntegrityError) as refused:
        _set(db, customer_id, status, disabled_at)
    assert sqlstate(refused.value) == "23514"  # check_violation
