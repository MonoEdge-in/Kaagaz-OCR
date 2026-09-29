"""Customers and their users: sign-in lookups, the lockout counter, two-step
codes, session versions and the admin's user list.

Why these are separate from the API: the SQL stays in one place, plain and
reviewable. Every function except the two named below takes a connection from
``Database.customer(customer_id)`` and also filters on ``customer_id`` (two
locks, docs/architecture.md).

The exceptions use ``Database.system()``, because there is no customer yet:

* ``login_lookup``: signing in starts from an e-mail only. It calls the
  SECURITY DEFINER function ``kaagaz_login_lookup``, which returns one row.
* ``create_customer``: calls ``kaagaz_create_customer`` (the CLI only).

E-mails are unique across all customers (the ``users.email`` constraint), so
adding a taken e-mail raises ``EmailTaken`` even when another customer holds it.

Why every write is one statement (PRD 1 A2 to A5, council record WP1.3): two
requests for the same user can arrive at once, so nothing reads a value and
writes it back later. Each change is a single UPDATE whose WHERE holds the
condition, and PostgreSQL re-checks that condition after waiting for a row
lock. Times always come in as ``now`` from the injected clock, never SQL
``now()``, so tests move the clock instead of sleeping.

Every admin-facing statement (list, create, update, set_password, unlock,
reset_totp) returns the same columns: the ``AdminUser`` columns plus
``locked_until``, ``totp_enabled_at``, ``must_change_password`` and
``session_version``. None means there is no such user in this customer (404).

Each call is one indexed statement: O(log n) in the number of users, except
``list_users``, which is O(users of this customer).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.sqlstate import UNIQUE_VIOLATION, sqlstate


class EmailTaken(Exception):
    """A user with this e-mail already exists (in any customer)."""


@dataclass(frozen=True, slots=True)
class LoginRow:
    user_id: str
    customer_id: str
    password_hash: str
    role: str
    disabled: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class AttemptRow:
    """What a sign-in needs once the attempt is counted. ``failed_logins`` already
    includes this attempt; the caller clears it with ``attempt_succeeded``."""

    user_id: str
    customer_id: str
    password_hash: str
    role: str
    disabled: bool
    session_version: int
    must_change_password: bool
    totp_secret_enc: str | None
    totp_enabled_at: datetime | None
    totp_last_step: int | None
    failed_logins: int


def login_lookup(conn: Connection, email: str) -> LoginRow | None:
    """Find a user by e-mail for signing in. ``conn`` comes from ``Database.system()``."""
    row = conn.execute(
        text("SELECT user_id, customer_id, password_hash, role, disabled FROM kaagaz_login_lookup(:email)"),
        {"email": email.strip().lower()},
    ).first()
    if row is None:
        return None
    return LoginRow(
        user_id=str(row[0]),
        customer_id=str(row[1]),
        password_hash=str(row[2]),
        role=str(row[3]),
        disabled=bool(row[4]),
    )


def create_customer(
    conn: Connection, *, name: str, admin_email: str, admin_name: str, password_hash: str
) -> str:
    """Create a customer and its first admin. ``conn`` comes from ``Database.system()``.
    Raises EmailTaken if the admin's e-mail is in use."""
    try:
        customer_id: object = conn.execute(
            text("SELECT kaagaz_create_customer(:name, :email, :admin_name, :hash)"),
            {"name": name, "email": admin_email.lower(), "admin_name": admin_name, "hash": password_hash},
        ).scalar_one()
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION:
            raise EmailTaken from None
        raise
    return str(customer_id)


# --- signing in: the lockout counter ------------------------------------------------


def begin_attempt(
    conn: Connection,
    customer_id: str,
    user_id: str,
    *,
    now: datetime,
    max_failures: int,
    lock_minutes: int,
) -> AttemptRow | None:
    """Count one sign-in attempt BEFORE the password is checked, and return the
    columns the check needs. None means the account is locked (or not here).

    One atomic UPDATE, so parallel wrong passwords are all counted:

    * a live lock (``locked_until > now``) matches no row: the attempt is refused
      and the lock is never extended;
    * an expired lock (``locked_until <= now``) starts the count again at 1;
    * the attempt that reaches ``max_failures`` sets ``locked_until = now + lock``
      and is still returned, so a right password on that attempt succeeds
      (``attempt_succeeded`` clears the lock). The next attempt is refused.

    PROVISIONAL (plan 7.5, PRD A3): anyone who knows an e-mail can keep that
    account locked; an admin can unlock it (``unlock``).

    O(log n): one indexed row.
    """
    row = (
        conn.execute(
            text(
                "UPDATE users AS u SET"
                " failed_logins = CASE WHEN u.locked_until <= :now THEN 1 ELSE u.failed_logins + 1 END,"
                " locked_until = CASE"
                "   WHEN (CASE WHEN u.locked_until <= :now THEN 1 ELSE u.failed_logins + 1 END) >= :max"
                "   THEN CAST(:lock_end AS timestamptz) ELSE NULL END"
                " WHERE u.customer_id = :c AND u.id = :u"
                " AND (u.locked_until IS NULL OR u.locked_until <= :now)"
                " RETURNING u.id, u.customer_id, u.password_hash, u.role, u.disabled, u.session_version,"
                " u.must_change_password, u.totp_secret_enc, u.totp_enabled_at, u.totp_last_step,"
                " u.failed_logins"
            ),
            {
                "c": customer_id,
                "u": user_id,
                "now": now,
                "max": max_failures,
                "lock_end": now + timedelta(minutes=lock_minutes),
            },
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    last_step = row["totp_last_step"]
    return AttemptRow(
        user_id=str(row["id"]),
        customer_id=str(row["customer_id"]),
        password_hash=str(row["password_hash"]),
        role=str(row["role"]),
        disabled=bool(row["disabled"]),
        session_version=int(row["session_version"]),
        must_change_password=bool(row["must_change_password"]),
        totp_secret_enc=row["totp_secret_enc"],
        totp_enabled_at=row["totp_enabled_at"],
        totp_last_step=None if last_step is None else int(last_step),
        failed_logins=int(row["failed_logins"]),
    )


def attempt_succeeded(conn: Connection, customer_id: str, user_id: str, *, now: datetime) -> None:
    """The attempt counted by ``begin_attempt`` was right: clear the count and any
    lock, and stamp ``last_login_at``. O(log n)."""
    conn.execute(
        text(
            "UPDATE users AS u SET failed_logins = 0, locked_until = NULL, last_login_at = :now"
            " WHERE u.customer_id = :c AND u.id = :u"
        ),
        {"c": customer_id, "u": user_id, "now": now},
    )


def unlock(conn: Connection, customer_id: str, user_id: str) -> RowMapping | None:
    """An admin ends a lock early: the count goes back to 0. O(log n)."""
    return (
        conn.execute(
            text(
                "UPDATE users AS u SET failed_logins = 0, locked_until = NULL"
                " WHERE u.customer_id = :c AND u.id = :u"
                " RETURNING u.id, u.email, u.display_name, u.role, u.disabled, u.created_at,"
                " u.locked_until, u.totp_enabled_at, u.must_change_password, u.session_version"
            ),
            {"c": customer_id, "u": user_id},
        )
        .mappings()
        .first()
    )


# --- sessions and passwords ------------------------------------------------------------


def bump_session_version(conn: Connection, customer_id: str, user_id: str) -> int:
    """End every session of this user ("sign out everywhere"); returns the new
    version. Raises NoResultFound if there is no such user (callers pass the
    signed-in user). O(log n)."""
    version: object = conn.execute(
        text(
            "UPDATE users AS u SET session_version = u.session_version + 1"
            " WHERE u.customer_id = :c AND u.id = :u"
            " RETURNING u.session_version"
        ),
        {"c": customer_id, "u": user_id},
    ).scalar_one()
    if not isinstance(version, int):
        raise TypeError("session_version is not an integer")
    return version


def set_password(
    conn: Connection, customer_id: str, user_id: str, *, password_hash: str, must_change: bool
) -> RowMapping | None:
    """A new password: the user's own (``must_change=False``) or a temporary one
    from an admin (``must_change=True``). Raises ``session_version``, so every
    other session ends. O(log n)."""
    return (
        conn.execute(
            text(
                "UPDATE users AS u SET password_hash = :hash, must_change_password = :must_change,"
                " session_version = u.session_version + 1"
                " WHERE u.customer_id = :c AND u.id = :u"
                " RETURNING u.id, u.email, u.display_name, u.role, u.disabled, u.created_at,"
                " u.locked_until, u.totp_enabled_at, u.must_change_password, u.session_version"
            ),
            {"c": customer_id, "u": user_id, "hash": password_hash, "must_change": must_change},
        )
        .mappings()
        .first()
    )


# --- two-step codes ---------------------------------------------------------------------


def enrol_totp(
    conn: Connection,
    customer_id: str,
    user_id: str,
    *,
    secret_enc: str,
    step: int,
    now: datetime,
    ver: int,
) -> bool:
    """Turn on two-step sign-in with a sealed secret whose first code (``step``) was
    just checked. False unless the user is an enabled, unlocked admin with no
    pending password change, not yet enrolled, and still on session version
    ``ver`` (the enrol token's), so an enrol token works once and only while
    nothing has changed. O(log n)."""
    row = conn.execute(
        text(
            "UPDATE users AS u SET totp_secret_enc = :enc, totp_enabled_at = :now, totp_last_step = :step"
            " WHERE u.customer_id = :c AND u.id = :u"
            " AND u.totp_enabled_at IS NULL AND u.session_version = :ver"
            " AND NOT u.disabled AND u.role = 'admin' AND NOT u.must_change_password"
            " AND (u.locked_until IS NULL OR u.locked_until <= :now)"
            " RETURNING u.id"
        ),
        {"c": customer_id, "u": user_id, "enc": secret_enc, "step": step, "now": now, "ver": ver},
    ).first()
    return row is not None


def accept_totp_step(conn: Connection, customer_id: str, user_id: str, step: int) -> bool:
    """Record ``step`` as the last used code. False if that step or a later one was
    already used, or two-step sign-in is not on: a code is never accepted twice,
    even by two requests at once. O(log n)."""
    row = conn.execute(
        text(
            "UPDATE users AS u SET totp_last_step = :step"
            " WHERE u.customer_id = :c AND u.id = :u AND u.totp_enabled_at IS NOT NULL"
            " AND (u.totp_last_step IS NULL OR u.totp_last_step < :step)"
            " RETURNING u.id"
        ),
        {"c": customer_id, "u": user_id, "step": step},
    ).first()
    return row is not None


def reset_totp(conn: Connection, customer_id: str, user_id: str) -> RowMapping | None:
    """Turn two-step sign-in off, so the next admin sign-in enrols again. Raises
    ``session_version``, so every session ends. O(log n)."""
    return (
        conn.execute(
            text(
                "UPDATE users AS u SET totp_secret_enc = NULL, totp_enabled_at = NULL,"
                " totp_last_step = NULL, session_version = u.session_version + 1"
                " WHERE u.customer_id = :c AND u.id = :u"
                " RETURNING u.id, u.email, u.display_name, u.role, u.disabled, u.created_at,"
                " u.locked_until, u.totp_enabled_at, u.must_change_password, u.session_version"
            ),
            {"c": customer_id, "u": user_id},
        )
        .mappings()
        .first()
    )


# --- who is signed in, and the admin's user list ---------------------------------------------


def session_user(conn: Connection, customer_id: str, user_id: str) -> RowMapping | None:
    """The signed-in user with their customer's name, or None. One query per request."""
    return (
        conn.execute(
            text(
                "SELECT u.id, u.email, u.display_name, u.role, u.disabled,"
                " u.session_version, u.must_change_password,"
                " c.id AS customer_id, c.name AS customer_name"
                " FROM users u JOIN customers c ON c.id = u.customer_id"
                " WHERE u.customer_id = :c AND u.id = :u"
            ),
            {"c": customer_id, "u": user_id},
        )
        .mappings()
        .first()
    )


def list_users(conn: Connection, customer_id: str) -> Sequence[RowMapping]:
    return (
        conn.execute(
            text(
                "SELECT u.id, u.email, u.display_name, u.role, u.disabled, u.created_at,"
                " u.locked_until, u.totp_enabled_at, u.must_change_password, u.session_version"
                " FROM users u WHERE u.customer_id = :c"
                " ORDER BY u.created_at, u.email"
            ),
            {"c": customer_id},
        )
        .mappings()
        .all()
    )


def create_user(
    conn: Connection,
    customer_id: str,
    *,
    email: str,
    display_name: str,
    role: str,
    password_hash: str,
    must_change_password: bool,
) -> RowMapping:
    """Add a user to this customer. ``must_change_password`` is true when an admin
    creates the user (a temporary password). Raises EmailTaken on a taken e-mail;
    the caller's transaction is then unusable and must end (leave the ``with``
    block)."""
    try:
        return (
            conn.execute(
                text(
                    "INSERT INTO users AS u"
                    " (customer_id, email, display_name, role, password_hash, must_change_password)"
                    " VALUES (:c, :email, :name, :role, :hash, :must_change)"
                    " RETURNING u.id, u.email, u.display_name, u.role, u.disabled, u.created_at,"
                    " u.locked_until, u.totp_enabled_at, u.must_change_password, u.session_version"
                ),
                {
                    "c": customer_id,
                    "email": email.lower(),
                    "name": display_name,
                    "role": role,
                    "hash": password_hash,
                    "must_change": must_change_password,
                },
            )
            .mappings()
            .one()
        )
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION:
            raise EmailTaken from None
        raise


def update_user(
    conn: Connection,
    customer_id: str,
    user_id: str,
    *,
    role: str | None,
    disabled: bool | None,
    password_hash: str | None,
) -> RowMapping | None:
    """Change only the fields given (None keeps the stored value). None if no such user.

    Disabling a user or setting a password raises ``session_version`` (every
    session ends). A password set here is an admin's, so it is temporary and
    ``must_change_password`` is set."""
    return (
        conn.execute(
            text(
                "UPDATE users AS u SET"
                " role = COALESCE(CAST(:role AS text), u.role),"
                " disabled = COALESCE(CAST(:disabled AS boolean), u.disabled),"
                " password_hash = COALESCE(CAST(:hash AS text), u.password_hash),"
                " must_change_password = (CAST(:hash AS text) IS NOT NULL) OR u.must_change_password,"
                " session_version = u.session_version + CASE"
                "   WHEN CAST(:hash AS text) IS NOT NULL OR CAST(:disabled AS boolean) IS TRUE THEN 1"
                "   ELSE 0 END"
                " WHERE u.customer_id = :c AND u.id = :u"
                " RETURNING u.id, u.email, u.display_name, u.role, u.disabled, u.created_at,"
                " u.locked_until, u.totp_enabled_at, u.must_change_password, u.session_version"
            ),
            {
                "c": customer_id,
                "u": user_id,
                "role": role,
                "disabled": disabled,
                "hash": password_hash,
            },
        )
        .mappings()
        .first()
    )
