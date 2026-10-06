"""The sign-in statements in kaagaz.db.users against a real PostgreSQL (marker ``db``).

What is proved here (PRD 1 A2 to A5, plan section 9.5):

* ``begin_attempt`` counts an attempt *before* the password is checked, in one
  statement, so parallel wrong passwords are all counted; a live lock is never
  extended; an expired lock starts the count again at 1.
* ``enrol_totp`` and ``accept_totp_step`` are conditional UPDATEs, so of two
  racing requests exactly one wins.
* Every admin-facing statement returns the same columns, and raising
  ``session_version`` happens where the PRD says (A4).

Concurrency tests force the order with ``db_support.wait_until_blocked``: the
second transaction is started in a thread and is seen waiting on the row lock
before the first commits, so no test depends on timing.
"""

import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import NoResultFound

from kaagaz.db import users
from kaagaz.db.engine import Database
from tests.db_support import name_session, new_customer, wait_until_blocked

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
MAX = 5
LOCK = 15
SEALED = "v1:bm90LWEtcmVhbC1zZWFsZWQtc2VjcmV0"

ADMIN_COLUMNS = {
    "id",
    "email",
    "display_name",
    "role",
    "disabled",
    "created_at",
    "locked_until",
    "totp_enabled_at",
    "must_change_password",
    "session_version",
    "delete_forever_allowed_at",
}

# --- helpers ---------------------------------------------------------------------------


def _user(db: Database, role: str = "admin", *, must_change_password: bool = False) -> tuple[str, str]:
    """A new customer and one user in it. Returns (customer_id, user_id)."""
    customer_id, _ = new_customer(db)
    with db.customer(customer_id) as conn:
        row = users.create_user(
            conn,
            customer_id,
            email=f"{role}-{uuid.uuid4().hex[:12]}@example.test",
            display_name=f"Test {role}",
            role=role,
            password_hash="scrypt$not-a-real-hash",
            must_change_password=must_change_password,
        )
    return customer_id, str(row["id"])


def _state(db: Database, customer_id: str, user_id: str) -> dict[str, Any]:
    with db.customer(customer_id) as conn:
        row = (
            conn.execute(
                text(
                    "SELECT failed_logins, locked_until, session_version, must_change_password,"
                    " totp_secret_enc, totp_enabled_at, totp_last_step, last_login_at, password_hash,"
                    " disabled, role"
                    " FROM users WHERE customer_id = :c AND id = :u"
                ),
                {"c": customer_id, "u": user_id},
            )
            .mappings()
            .one()
        )
    return dict(row)


def _set(db: Database, customer_id: str, user_id: str, **columns: Any) -> None:
    allowed = {"failed_logins", "locked_until", "disabled", "must_change_password", "role", "session_version"}
    assert set(columns) <= allowed
    assignments = ", ".join(f"{name} = :{name}" for name in sorted(columns))
    with db.customer(customer_id) as conn:
        conn.execute(
            text(f"UPDATE users SET {assignments} WHERE customer_id = :c AND id = :u"),  # noqa: S608 - names from the fixed set above
            {"c": customer_id, "u": user_id, **columns},
        )


def _attempt(db: Database, customer_id: str, user_id: str, now: datetime = NOW) -> users.AttemptRow | None:
    with db.customer(customer_id) as conn:
        return users.begin_attempt(conn, customer_id, user_id, now=now, max_failures=MAX, lock_minutes=LOCK)


def _enrol(db: Database, customer_id: str, user_id: str, *, step: int = 100, ver: int = 1) -> bool:
    with db.customer(customer_id) as conn:
        return users.enrol_totp(conn, customer_id, user_id, secret_enc=SEALED, step=step, now=NOW, ver=ver)


def _race[T](
    db: Database,
    customer_id: str,
    first: Callable[[Connection], T],
    second: Callable[[Connection], T],
) -> tuple[T, T]:
    """Run ``first`` in an open transaction, start ``second`` in another one and wait
    until it is blocked on a lock, then commit ``first``. Returns both results."""
    name = f"b1-race-{uuid.uuid4().hex[:12]}"

    def blocked() -> T:
        with db.customer(customer_id) as conn:
            name_session(conn, name)
            return second(conn)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer_id) as conn:
            result = first(conn)
            later: Future[T] = pool.submit(blocked)
            wait_until_blocked(db, application_name=name)
        return result, later.result(timeout=10)


# --- begin_attempt: the lockout counter -------------------------------------------------------


def test_begin_attempt_counts_before_the_password_check_and_returns_the_sign_in_columns(db: Database) -> None:
    customer_id, user_id = _user(db)
    row = _attempt(db, customer_id, user_id)
    assert row == users.AttemptRow(
        user_id=user_id,
        customer_id=customer_id,
        password_hash="scrypt$not-a-real-hash",
        role="admin",
        disabled=False,
        session_version=1,
        must_change_password=False,
        totp_secret_enc=None,
        totp_enabled_at=None,
        totp_last_step=None,
        failed_logins=1,
        customer_active=True,
    )
    assert _state(db, customer_id, user_id)["failed_logins"] == 1


def test_the_attempt_that_reaches_the_limit_still_gets_its_password_checked(db: Database) -> None:
    customer_id, user_id = _user(db)
    rows = [_attempt(db, customer_id, user_id) for _ in range(MAX)]
    assert [row.failed_logins if row else None for row in rows] == [1, 2, 3, 4, 5]
    assert _state(db, customer_id, user_id)["locked_until"] == NOW + timedelta(minutes=LOCK)


def test_the_attempt_after_the_limit_is_refused_even_before_a_password(db: Database) -> None:
    customer_id, user_id = _user(db)
    for _ in range(MAX):
        assert _attempt(db, customer_id, user_id) is not None
    assert _attempt(db, customer_id, user_id, NOW + timedelta(seconds=1)) is None
    assert _state(db, customer_id, user_id)["failed_logins"] == MAX


def test_a_live_lock_is_never_extended(db: Database) -> None:
    customer_id, user_id = _user(db)
    for _ in range(MAX):
        _attempt(db, customer_id, user_id)
    locked_until = NOW + timedelta(minutes=LOCK)
    for minutes in (1, 7, 14):
        assert _attempt(db, customer_id, user_id, NOW + timedelta(minutes=minutes)) is None
    state = _state(db, customer_id, user_id)
    assert state["locked_until"] == locked_until
    assert state["failed_logins"] == MAX


def test_the_lock_ends_at_locked_until_exactly(db: Database) -> None:
    customer_id, user_id = _user(db)
    for _ in range(MAX):
        _attempt(db, customer_id, user_id)
    locked_until = NOW + timedelta(minutes=LOCK)
    assert _attempt(db, customer_id, user_id, locked_until - timedelta(microseconds=1)) is None
    assert _attempt(db, customer_id, user_id, locked_until) is not None


def test_an_expired_lock_starts_the_count_again_at_one(db: Database) -> None:
    customer_id, user_id = _user(db)
    for _ in range(MAX):
        _attempt(db, customer_id, user_id)
    later = NOW + timedelta(minutes=LOCK, seconds=1)
    row = _attempt(db, customer_id, user_id, later)
    assert row is not None
    assert row.failed_logins == 1
    state = _state(db, customer_id, user_id)
    assert (state["failed_logins"], state["locked_until"]) == (1, None)


def test_after_an_expired_lock_it_takes_the_full_count_to_lock_again(db: Database) -> None:
    customer_id, user_id = _user(db)
    for _ in range(MAX):
        _attempt(db, customer_id, user_id)
    later = NOW + timedelta(minutes=LOCK)
    for expected in range(1, MAX + 1):
        row = _attempt(db, customer_id, user_id, later)
        assert row is not None and row.failed_logins == expected
    assert _state(db, customer_id, user_id)["locked_until"] == later + timedelta(minutes=LOCK)
    assert _attempt(db, customer_id, user_id, later) is None


def test_a_limit_of_one_locks_on_the_first_attempt(db: Database) -> None:
    customer_id, user_id = _user(db)
    with db.customer(customer_id) as conn:
        row = users.begin_attempt(conn, customer_id, user_id, now=NOW, max_failures=1, lock_minutes=LOCK)
    assert row is not None and row.failed_logins == 1
    assert _state(db, customer_id, user_id)["locked_until"] == NOW + timedelta(minutes=LOCK)


def test_attempt_succeeded_clears_the_count_and_stamps_the_sign_in(db: Database) -> None:
    customer_id, user_id = _user(db)
    for _ in range(MAX - 1):
        _attempt(db, customer_id, user_id)
    assert _attempt(db, customer_id, user_id) is not None  # the 5th: locks unless it succeeds
    with db.customer(customer_id) as conn:
        users.attempt_succeeded(conn, customer_id, user_id, now=NOW)
    state = _state(db, customer_id, user_id)
    assert (state["failed_logins"], state["locked_until"], state["last_login_at"]) == (0, None, NOW)
    row = _attempt(db, customer_id, user_id)
    assert row is not None and row.failed_logins == 1


def test_begin_attempt_returns_the_current_sign_in_columns(db: Database) -> None:
    customer_id, user_id = _user(db)
    assert _enrol(db, customer_id, user_id, step=100) is True
    _set(db, customer_id, user_id, must_change_password=True, session_version=4, disabled=True)
    row = _attempt(db, customer_id, user_id)
    assert row is not None
    assert (row.session_version, row.must_change_password, row.disabled) == (4, True, True)
    assert (row.totp_secret_enc, row.totp_enabled_at, row.totp_last_step) == (SEALED, NOW, 100)


def test_parallel_wrong_passwords_are_all_counted(db: Database) -> None:
    customer_id, user_id = _user(db)

    def attempt(conn: Connection) -> users.AttemptRow | None:
        return users.begin_attempt(conn, customer_id, user_id, now=NOW, max_failures=MAX, lock_minutes=LOCK)

    first, second = _race(db, customer_id, attempt, attempt)
    assert first is not None and first.failed_logins == 1
    assert second is not None and second.failed_logins == 2  # it saw the first one's count
    assert _state(db, customer_id, user_id)["failed_logins"] == 2


def test_a_parallel_attempt_behind_the_locking_one_is_refused(db: Database) -> None:
    customer_id, user_id = _user(db)
    _set(db, customer_id, user_id, failed_logins=MAX - 1)

    def attempt(conn: Connection) -> users.AttemptRow | None:
        return users.begin_attempt(conn, customer_id, user_id, now=NOW, max_failures=MAX, lock_minutes=LOCK)

    first, second = _race(db, customer_id, attempt, attempt)
    assert first is not None and first.failed_logins == MAX
    assert second is None  # re-checked after the first committed its lock
    state = _state(db, customer_id, user_id)
    assert (state["failed_logins"], state["locked_until"]) == (MAX, NOW + timedelta(minutes=LOCK))


def test_many_parallel_attempts_are_counted_exactly(db: Database) -> None:
    customer_id, user_id = _user(db)

    def attempt(_: int) -> users.AttemptRow | None:
        with db.customer(customer_id) as conn:
            return users.begin_attempt(
                conn, customer_id, user_id, now=NOW, max_failures=100, lock_minutes=LOCK
            )

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(attempt, range(16)))
    counts = sorted(row.failed_logins for row in rows if row is not None)
    assert counts == list(range(1, 17))
    assert _state(db, customer_id, user_id)["failed_logins"] == 16


# --- enrol_totp and accept_totp_step: conditional, one winner -----------------------------------


def test_enrol_totp_sets_the_three_columns_once(db: Database) -> None:
    customer_id, user_id = _user(db)
    assert _enrol(db, customer_id, user_id, step=100) is True
    state = _state(db, customer_id, user_id)
    assert (state["totp_secret_enc"], state["totp_enabled_at"], state["totp_last_step"]) == (SEALED, NOW, 100)
    assert _enrol(db, customer_id, user_id, step=101) is False  # the enrol token is one-time
    assert _state(db, customer_id, user_id)["totp_last_step"] == 100


def test_enrol_totp_needs_the_same_session_version(db: Database) -> None:
    customer_id, user_id = _user(db)
    with db.customer(customer_id) as conn:
        assert users.bump_session_version(conn, customer_id, user_id) == 2
    assert _enrol(db, customer_id, user_id, ver=1) is False
    assert _enrol(db, customer_id, user_id, ver=2) is True


@pytest.mark.parametrize(
    "columns",
    [
        {"role": "member"},
        {"role": "reviewer"},
        {"disabled": True},
        {"must_change_password": True},
        {"locked_until": NOW + timedelta(minutes=1)},
    ],
)
def test_enrol_totp_is_refused_for_a_user_who_may_not_enrol(db: Database, columns: dict[str, Any]) -> None:
    customer_id, user_id = _user(db)
    _set(db, customer_id, user_id, **columns)
    assert _enrol(db, customer_id, user_id) is False
    assert _state(db, customer_id, user_id)["totp_secret_enc"] is None


def test_enrol_totp_is_allowed_once_a_lock_has_passed(db: Database) -> None:
    customer_id, user_id = _user(db)
    _set(db, customer_id, user_id, locked_until=NOW)
    assert _enrol(db, customer_id, user_id) is True


def test_two_racing_enrolments_have_one_winner(db: Database) -> None:
    customer_id, user_id = _user(db)

    def enrol(conn: Connection) -> bool:
        return users.enrol_totp(conn, customer_id, user_id, secret_enc=SEALED, step=100, now=NOW, ver=1)

    assert _race(db, customer_id, enrol, enrol) == (True, False)


def test_accept_totp_step_only_moves_forward(db: Database) -> None:
    customer_id, user_id = _user(db)
    assert _enrol(db, customer_id, user_id, step=100) is True

    def accept(step: int) -> bool:
        with db.customer(customer_id) as conn:
            return users.accept_totp_step(conn, customer_id, user_id, step)

    assert [accept(step) for step in (100, 99, 101, 101, 103, 102)] == [
        False,
        False,
        True,
        False,
        True,
        False,
    ]
    assert _state(db, customer_id, user_id)["totp_last_step"] == 103


def test_accept_totp_step_accepts_any_step_when_none_is_stored(db: Database) -> None:
    customer_id, user_id = _user(db)
    assert _enrol(db, customer_id, user_id, step=100) is True
    with db.customer(customer_id) as conn:
        conn.execute(text("UPDATE users SET totp_last_step = NULL WHERE id = :u"), {"u": user_id})
        assert users.accept_totp_step(conn, customer_id, user_id, 1) is True


def test_accept_totp_step_is_refused_without_two_step_set_up(db: Database) -> None:
    customer_id, user_id = _user(db)
    with db.customer(customer_id) as conn:
        assert users.accept_totp_step(conn, customer_id, user_id, 100) is False
    assert _state(db, customer_id, user_id)["totp_last_step"] is None


def test_two_requests_racing_with_the_same_code_have_one_winner(db: Database) -> None:
    customer_id, user_id = _user(db)
    assert _enrol(db, customer_id, user_id, step=100) is True

    def accept(conn: Connection) -> bool:
        return users.accept_totp_step(conn, customer_id, user_id, 101)

    assert _race(db, customer_id, accept, accept) == (True, False)
    assert _state(db, customer_id, user_id)["totp_last_step"] == 101


def test_a_racing_later_code_still_wins_after_an_earlier_one(db: Database) -> None:
    customer_id, user_id = _user(db)
    assert _enrol(db, customer_id, user_id, step=100) is True
    result = _race(
        db,
        customer_id,
        lambda conn: users.accept_totp_step(conn, customer_id, user_id, 101),
        lambda conn: users.accept_totp_step(conn, customer_id, user_id, 102),
    )
    assert result == (True, True)
    assert _state(db, customer_id, user_id)["totp_last_step"] == 102


# --- session versions, passwords, unlock, 2FA reset --------------------------------------------------


def test_bump_session_version_returns_the_new_version(db: Database) -> None:
    customer_id, user_id = _user(db)
    with db.customer(customer_id) as conn:
        assert users.bump_session_version(conn, customer_id, user_id) == 2
        assert users.bump_session_version(conn, customer_id, user_id) == 3
    assert _state(db, customer_id, user_id)["session_version"] == 3


@pytest.mark.parametrize("must_change", [True, False])
def test_set_password_sets_the_flag_and_raises_the_version(db: Database, must_change: bool) -> None:
    customer_id, user_id = _user(db, must_change_password=not must_change)
    with db.customer(customer_id) as conn:
        row = users.set_password(
            conn, customer_id, user_id, password_hash="scrypt$new", must_change=must_change
        )
    assert row is not None
    assert set(row.keys()) == ADMIN_COLUMNS
    assert (row["must_change_password"], row["session_version"]) == (must_change, 2)
    assert _state(db, customer_id, user_id)["password_hash"] == "scrypt$new"


def test_unlock_clears_the_count_and_the_lock(db: Database) -> None:
    customer_id, user_id = _user(db)
    for _ in range(MAX):
        _attempt(db, customer_id, user_id)
    with db.customer(customer_id) as conn:
        row = users.unlock(conn, customer_id, user_id)
    assert row is not None
    assert set(row.keys()) == ADMIN_COLUMNS
    assert row["locked_until"] is None
    state = _state(db, customer_id, user_id)
    assert (state["failed_logins"], state["locked_until"], state["session_version"]) == (0, None, 1)
    attempt = _attempt(db, customer_id, user_id, NOW + timedelta(seconds=1))
    assert attempt is not None and attempt.failed_logins == 1


def test_reset_totp_clears_the_three_columns_and_raises_the_version(db: Database) -> None:
    customer_id, user_id = _user(db)
    assert _enrol(db, customer_id, user_id, step=100) is True
    with db.customer(customer_id) as conn:
        row = users.reset_totp(conn, customer_id, user_id)
    assert row is not None
    assert set(row.keys()) == ADMIN_COLUMNS
    assert (row["totp_enabled_at"], row["session_version"]) == (None, 2)
    state = _state(db, customer_id, user_id)
    assert (state["totp_secret_enc"], state["totp_enabled_at"], state["totp_last_step"]) == (None, None, None)
    assert _enrol(db, customer_id, user_id, ver=2) is True  # can enrol again


@pytest.mark.parametrize("name", ["set_password", "unlock", "reset_totp", "update_user"])
def test_admin_statements_return_none_for_a_user_that_is_not_there(db: Database, name: str) -> None:
    customer_id, _ = _user(db)
    missing = str(uuid.uuid4())
    with db.customer(customer_id) as conn:
        if name == "set_password":
            row = users.set_password(conn, customer_id, missing, password_hash="x", must_change=True)
        elif name == "update_user":
            row = users.update_user(conn, customer_id, missing, role=None, disabled=True, password_hash=None)
        elif name == "unlock":
            row = users.unlock(conn, customer_id, missing)
        else:
            row = users.reset_totp(conn, customer_id, missing)
    assert row is None


# --- create, update, list, session_user -----------------------------------------------------------------


def test_create_user_stores_the_must_change_flag_and_returns_the_admin_columns(db: Database) -> None:
    customer_id, user_id = _user(db, "member", must_change_password=True)
    with db.customer(customer_id) as conn:
        rows = users.list_users(conn, customer_id)
    row = next(row for row in rows if str(row["id"]) == user_id)
    assert set(row.keys()) == ADMIN_COLUMNS
    assert (row["must_change_password"], row["session_version"], row["locked_until"]) == (True, 1, None)
    assert row["totp_enabled_at"] is None
    assert all(set(item.keys()) == ADMIN_COLUMNS for item in rows)


def test_create_user_returns_the_admin_columns(db: Database) -> None:
    customer_id, _ = new_customer(db)
    with db.customer(customer_id) as conn:
        row = users.create_user(
            conn,
            customer_id,
            email=f"new-{uuid.uuid4().hex[:12]}@example.test",
            display_name="New",
            role="member",
            password_hash="scrypt$x",
            must_change_password=False,
        )
    assert set(row.keys()) == ADMIN_COLUMNS
    assert row["must_change_password"] is False


def test_update_user_disabling_raises_the_version(db: Database) -> None:
    customer_id, user_id = _user(db, "member")
    with db.customer(customer_id) as conn:
        row = users.update_user(conn, customer_id, user_id, role=None, disabled=True, password_hash=None)
    assert row is not None
    assert set(row.keys()) == ADMIN_COLUMNS
    assert (row["disabled"], row["session_version"], row["must_change_password"]) == (True, 2, False)


def test_update_user_setting_a_password_raises_the_version_and_asks_for_a_new_one(db: Database) -> None:
    customer_id, user_id = _user(db, "member")
    with db.customer(customer_id) as conn:
        row = users.update_user(
            conn, customer_id, user_id, role=None, disabled=None, password_hash="scrypt$y"
        )
    assert row is not None
    assert (row["session_version"], row["must_change_password"]) == (2, True)
    assert _state(db, customer_id, user_id)["password_hash"] == "scrypt$y"


def test_update_user_changing_the_role_raises_the_version(db: Database) -> None:
    customer_id, user_id = _user(db, "member")
    with db.customer(customer_id) as conn:
        row = users.update_user(
            conn, customer_id, user_id, role="reviewer", disabled=None, password_hash=None
        )
    assert row is not None
    assert (row["role"], row["session_version"], row["must_change_password"]) == ("reviewer", 2, False)


def test_update_user_enabling_or_the_same_role_keeps_the_version(db: Database) -> None:
    customer_id, user_id = _user(db, "member")
    _set(db, customer_id, user_id, disabled=True)
    with db.customer(customer_id) as conn:
        row = users.update_user(conn, customer_id, user_id, role="member", disabled=False, password_hash=None)
    assert row is not None
    assert (row["role"], row["disabled"], row["session_version"], row["must_change_password"]) == (
        "member",
        False,
        1,
        False,
    )


def test_session_user_has_the_version_and_the_must_change_flag(db: Database) -> None:
    customer_id, user_id = _user(db, "reviewer", must_change_password=True)
    with db.customer(customer_id) as conn:
        users.bump_session_version(conn, customer_id, user_id)
        row = users.session_user(conn, customer_id, user_id)
    assert row is not None
    assert (row["session_version"], row["must_change_password"], row["role"]) == (2, True, "reviewer")
    assert str(row["customer_id"]) == customer_id


def test_list_users_shows_a_lock(db: Database) -> None:
    customer_id, user_id = _user(db, "member")
    for _ in range(MAX):
        _attempt(db, customer_id, user_id)
    with db.customer(customer_id) as conn:
        rows = users.list_users(conn, customer_id)
    row = next(row for row in rows if str(row["id"]) == user_id)
    assert row["locked_until"] == NOW + timedelta(minutes=LOCK)


def test_bump_session_version_raises_for_a_user_that_is_not_there(db: Database) -> None:
    customer_id, _ = _user(db)
    with pytest.raises(NoResultFound), db.customer(customer_id) as conn:
        users.bump_session_version(conn, customer_id, str(uuid.uuid4()))


# --- Delete forever, granted (council 4 Oct 2026) ------------------------------------------------


def _grant(db: Database, customer_id: str, user_id: str, allowed: bool, now: datetime = NOW) -> Any:
    with db.customer(customer_id) as conn:
        return users.set_delete_forever(conn, customer_id, user_id, allowed=allowed, now=now)


def test_set_delete_forever_grants_keeps_the_first_time_and_stops(db: Database) -> None:
    customer_id, user_id = _user(db, "reviewer")
    granted = _grant(db, customer_id, user_id, True)
    assert set(granted.keys()) == ADMIN_COLUMNS
    assert granted["delete_forever_allowed_at"] == NOW
    later = NOW + timedelta(days=1)
    assert _grant(db, customer_id, user_id, True, later)["delete_forever_allowed_at"] == NOW
    stopped = _grant(db, customer_id, user_id, False)
    assert (stopped["delete_forever_allowed_at"], stopped["session_version"]) == (None, 1)  # sessions stay


def test_set_delete_forever_never_touches_an_admin_or_a_missing_user(db: Database) -> None:
    customer_id, admin_id = _user(db, "admin")
    missing = str(uuid.uuid4())
    assert _grant(db, customer_id, admin_id, True) is None
    assert _grant(db, customer_id, missing, True) is None
    with db.customer(customer_id) as conn:
        assert users.role_of(conn, customer_id, admin_id) == "admin"
        assert users.role_of(conn, customer_id, missing) is None


def test_a_role_change_clears_the_grant_and_the_same_role_keeps_it(db: Database) -> None:
    customer_id, user_id = _user(db, "member")
    _grant(db, customer_id, user_id, True)
    with db.customer(customer_id) as conn:
        same = users.update_user(conn, customer_id, user_id, role="member", disabled=None, password_hash=None)
        assert same is not None and same["delete_forever_allowed_at"] == NOW
        moved = users.update_user(conn, customer_id, user_id, role="admin", disabled=None, password_hash=None)
    assert moved is not None and moved["delete_forever_allowed_at"] is None
