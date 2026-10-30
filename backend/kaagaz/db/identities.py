"""Connected accounts (0062, #457 phase 1): a Google account connected to a Kaagaz user.

Every function except the two below takes a connection from ``Database.customer(customer_id)``
and also filters on ``customer_id`` (two locks, docs/architecture.md). The exceptions have no
customer yet and take a ``Database.system()`` connection:

* ``lookup``: a provider sign-in arrives with the provider's key only (provider, issuer,
  subject), so it calls the SECURITY DEFINER function ``kaagaz_identity_lookup``.
* ``email_belongs_to_other``: the "already belongs" rule asks whether an e-mail is another
  user's sign-in e-mail in any customer, through ``kaagaz_email_belongs_to_other``.

The key (provider, issuer, subject) is unique across every customer and a user has at most
one connection of each provider: ``add`` turns either unique violation into
``IdentityTaken``, so two people connecting one account at the same moment get one winner
and one refusal, never a 500. The email is stored in lower case, as a label only.

Each call is one indexed statement: O(log n), except ``pending`` and ``providers_by_user``,
which are O(connections of this customer).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.sqlstate import UNIQUE_VIOLATION, sqlstate


class IdentityTaken(Exception):
    """That outside account is already connected (to anyone), or the user already has one of
    that provider."""


@dataclass(frozen=True, slots=True)
class IdentityRow:
    id: str
    user_id: str
    customer_id: str
    provider: str
    status: str


def lookup(conn: Connection, provider: str, issuer: str, subject: str) -> IdentityRow | None:
    """The connection with this key, in any customer, approved or not. ``conn`` comes from
    ``Database.system()``."""
    row = conn.execute(
        text("SELECT identity_id, user_id, customer_id, status FROM kaagaz_identity_lookup(:p, :i, :s)"),
        {"p": provider, "i": issuer, "s": subject},
    ).first()
    if row is None:
        return None
    return IdentityRow(str(row[0]), str(row[1]), str(row[2]), provider, str(row[3]))


def email_belongs_to_other(conn: Connection, email: str, user_id: str) -> bool:
    """``email`` (any case) is the sign-in e-mail of a user other than ``user_id``, in any
    customer. ``conn`` comes from ``Database.system()``."""
    return bool(
        conn.execute(
            text("SELECT kaagaz_email_belongs_to_other(:e, :u)"), {"e": email, "u": user_id}
        ).scalar_one()
    )


def add(
    conn: Connection,
    customer_id: str,
    user_id: str,
    *,
    provider: str,
    issuer: str,
    subject: str,
    email: str | None,
    approved: bool,
    approved_by: str | None,
    now: datetime,
) -> str:
    """A new connection, pending or approved at once; its id. Raises ``IdentityTaken``; the
    caller's transaction is then unusable and must end (leave the ``with`` block)."""
    try:
        identity_id: object = conn.execute(
            text(
                "INSERT INTO user_identities (customer_id, user_id, provider, issuer, subject,"
                " email_at_link, status, linked_at, approved_at, approved_by)"
                " VALUES (:c, :u, :p, :i, :s, :e, :status, :now, :approved_at, :by) RETURNING id"
            ),
            {
                "c": customer_id,
                "u": user_id,
                "p": provider,
                "i": issuer,
                "s": subject,
                "e": None if email is None else email.lower(),
                "status": "approved" if approved else "pending",
                "now": now,
                "approved_at": now if approved else None,
                "by": approved_by if approved else None,
            },
        ).scalar_one()
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION:
            raise IdentityTaken from None
        raise
    return str(identity_id)


def for_user(conn: Connection, customer_id: str, user_id: str) -> Sequence[RowMapping]:
    """The user's own connections, approved or pending."""
    return (
        conn.execute(
            text(
                "SELECT id, provider, email_at_link, status, linked_at, last_used_at FROM user_identities"
                " WHERE customer_id = :c AND user_id = :u ORDER BY provider"
            ),
            {"c": customer_id, "u": user_id},
        )
        .mappings()
        .all()
    )


def pending(conn: Connection, customer_id: str) -> Sequence[RowMapping]:
    """The connections waiting for an admin, oldest first, with their person."""
    return (
        conn.execute(
            text(
                "SELECT i.id, i.user_id, u.display_name, u.email AS user_email, i.provider,"
                " i.email_at_link, i.linked_at"
                " FROM user_identities i JOIN users u ON u.customer_id = i.customer_id AND u.id = i.user_id"
                " WHERE i.customer_id = :c AND i.status = 'pending' ORDER BY i.linked_at, i.id"
            ),
            {"c": customer_id},
        )
        .mappings()
        .all()
    )


def approve(
    conn: Connection, customer_id: str, identity_id: str, approver_id: str, now: datetime
) -> RowMapping | None:
    """Approve a pending connection; the row, or None when there is no such pending one."""
    return (
        conn.execute(
            text(
                "UPDATE user_identities SET status = 'approved', approved_at = :now, approved_by = :by"
                " WHERE customer_id = :c AND id = :i AND status = 'pending'"
                " RETURNING id, user_id, provider, email_at_link, status, linked_at"
            ),
            {"c": customer_id, "i": identity_id, "by": approver_id, "now": now},
        )
        .mappings()
        .first()
    )


def remove(
    conn: Connection,
    customer_id: str,
    identity_id: str,
    *,
    user_id: str | None = None,
    status: str | None = None,
) -> RowMapping | None:
    """Delete one connection (only ``user_id``'s, or only one with this ``status``, when
    given); the row, or None."""
    return (
        conn.execute(
            text(
                "DELETE FROM user_identities WHERE customer_id = :c AND id = :i"
                " AND (CAST(:u AS uuid) IS NULL OR user_id = CAST(:u AS uuid))"
                " AND (CAST(:s AS text) IS NULL OR status = CAST(:s AS text))"
                " RETURNING id, user_id, provider, email_at_link, status, linked_at"
            ),
            {"c": customer_id, "i": identity_id, "u": user_id, "s": status},
        )
        .mappings()
        .first()
    )


def remove_all(conn: Connection, customer_id: str, user_id: str) -> int:
    """Delete every connection of this user; how many there were."""
    result = conn.execute(
        text("DELETE FROM user_identities WHERE customer_id = :c AND user_id = :u"),
        {"c": customer_id, "u": user_id},
    )
    return int(result.rowcount)


def touch_used(conn: Connection, customer_id: str, identity_id: str, now: datetime) -> None:
    """The connection was just used to sign in."""
    conn.execute(
        text("UPDATE user_identities SET last_used_at = :now WHERE customer_id = :c AND id = :i"),
        {"c": customer_id, "i": identity_id, "now": now},
    )


def providers_by_user(conn: Connection, customer_id: str) -> dict[str, list[str]]:
    """{user id: the providers of their approved connections}, for the admin's people list."""
    rows = conn.execute(
        text(
            "SELECT user_id, provider FROM user_identities"
            " WHERE customer_id = :c AND status = 'approved' ORDER BY user_id, provider"
        ),
        {"c": customer_id},
    )
    found: dict[str, list[str]] = {}
    for user_id, provider in rows:
        found.setdefault(str(user_id), []).append(str(provider))
    return found
