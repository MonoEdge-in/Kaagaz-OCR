"""Customers and their users: sign-in lookups and the admin's user list.

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

Each call is one indexed statement: O(log n) in the number of users, except
``list_users``, which is O(users of this customer).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import IntegrityError

_UNIQUE_VIOLATION = "23505"


class EmailTaken(Exception):
    """A user with this e-mail already exists (in any customer)."""


@dataclass(frozen=True, slots=True)
class LoginRow:
    user_id: str
    customer_id: str
    password_hash: str
    role: str
    disabled: bool


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
        if _sqlstate(error) == _UNIQUE_VIOLATION:
            raise EmailTaken from None
        raise
    return str(customer_id)


def session_user(conn: Connection, customer_id: str, user_id: str) -> RowMapping | None:
    """The signed-in user with their customer's name, or None. One query per request."""
    return (
        conn.execute(
            text(
                "SELECT u.id, u.email, u.display_name, u.role, u.disabled,"
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
                "SELECT u.id, u.email, u.display_name, u.role, u.disabled, u.created_at"
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
) -> RowMapping:
    """Add a user to this customer. Raises EmailTaken on a taken e-mail; the caller's
    transaction is then unusable and must end (leave the ``with`` block)."""
    try:
        return (
            conn.execute(
                text(
                    "INSERT INTO users AS u (customer_id, email, display_name, role, password_hash)"
                    " VALUES (:c, :email, :name, :role, :hash)"
                    " RETURNING u.id, u.email, u.display_name, u.role, u.disabled, u.created_at"
                ),
                {
                    "c": customer_id,
                    "email": email.lower(),
                    "name": display_name,
                    "role": role,
                    "hash": password_hash,
                },
            )
            .mappings()
            .one()
        )
    except IntegrityError as error:
        if _sqlstate(error) == _UNIQUE_VIOLATION:
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
    """Change only the fields given (None keeps the stored value). None if no such user."""
    return (
        conn.execute(
            text(
                "UPDATE users AS u SET"
                " role = COALESCE(CAST(:role AS text), u.role),"
                " disabled = COALESCE(CAST(:disabled AS boolean), u.disabled),"
                " password_hash = COALESCE(CAST(:hash AS text), u.password_hash)"
                " WHERE u.customer_id = :c AND u.id = :u"
                " RETURNING u.id, u.email, u.display_name, u.role, u.disabled, u.created_at"
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


def _sqlstate(error: IntegrityError) -> str | None:
    state = getattr(error.orig, "sqlstate", None)
    return state if isinstance(state, str) else None
