"""The sign-in statements in kaagaz.db.users never touch another customer's user.

Customer B uses customer A's user id with every statement, and even names A's
customer as the filter: row level security still hides A's row, so nothing is
counted, changed or read, and A's row is exactly as it was. The helpers are the
ones tests/test_users_db.py uses.
"""

import pytest
from sqlalchemy.exc import NoResultFound

from kaagaz.db import users
from kaagaz.db.engine import Database
from tests.test_users_db import LOCK, MAX, NOW, _state, _user

pytestmark = pytest.mark.db


def test_another_customers_user_is_never_touched(db: Database) -> None:
    customer_a, user_a = _user(db)
    customer_b, _ = _user(db)
    before = _state(db, customer_a, user_a)
    with db.customer(customer_b) as conn:
        assert (
            users.begin_attempt(conn, customer_b, user_a, now=NOW, max_failures=MAX, lock_minutes=LOCK)
            is None
        )
        # Even with A's id as the filter, B's transaction cannot see A's rows (row level security).
        assert (
            users.begin_attempt(conn, customer_a, user_a, now=NOW, max_failures=MAX, lock_minutes=LOCK)
            is None
        )
        assert users.unlock(conn, customer_b, user_a) is None
        assert users.reset_totp(conn, customer_b, user_a) is None
        assert users.update_user(conn, customer_b, user_a, role=None, disabled=True) is None
        assert users.accept_totp_step(conn, customer_b, user_a, 1) is False
        assert users.session_user(conn, customer_b, user_a) is None
        users.attempt_succeeded(conn, customer_b, user_a, now=NOW)
    with pytest.raises(NoResultFound), db.customer(customer_b) as conn:
        users.bump_session_version(conn, customer_b, user_a)
    assert _state(db, customer_a, user_a) == before
