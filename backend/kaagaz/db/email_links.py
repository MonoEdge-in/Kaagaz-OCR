"""Email links (0061, #457 phase 0): confirm your email, an invite, a password reset.

A link is stored only as the SHA-256 hash of its token, with its purpose, its expiry and
the time it was used. ``add`` and ``use`` take a connection from
``Database.customer(customer_id)`` and also filter on ``customer_id`` (two locks,
docs/architecture.md). ``lookup`` has no customer yet: a clicked link arrives with its
token only, so it calls the SECURITY DEFINER function ``kaagaz_email_link_lookup``
inside ``Database.system()``, as signing in finds an e-mail.

Single use: ``use`` is one UPDATE whose WHERE holds "not used, not expired", so two
requests with the same link at once act once. A new link of one purpose for one user
marks the older unused ones used (``add``), so only the newest works. Setting a password,
or an admin disabling a user or resetting their password, ends all of them (``void_all``).

Each call is one or two indexed statements: O(log n).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, text


@dataclass(frozen=True, slots=True)
class LinkRow:
    link_id: str
    customer_id: str
    user_id: str
    purpose: str
    expires_at: datetime
    used_at: datetime | None


def add(
    conn: Connection,
    customer_id: str,
    user_id: str,
    purpose: str,
    token_hash: bytes,
    *,
    now: datetime,
    expires_at: datetime,
) -> None:
    """A new link; the user's older unused links of this purpose stop working."""
    params = {"c": customer_id, "u": user_id, "p": purpose, "now": now}
    conn.execute(
        text(
            "UPDATE email_links SET used_at = :now"
            " WHERE customer_id = :c AND user_id = :u AND purpose = :p AND used_at IS NULL"
        ),
        params,
    )
    conn.execute(
        text(
            "INSERT INTO email_links (customer_id, user_id, purpose, token_hash, created_at, expires_at)"
            " VALUES (:c, :u, :p, :h, :now, :exp)"
        ),
        {**params, "h": token_hash, "exp": expires_at},
    )


def lookup(conn: Connection, token_hash: bytes) -> LinkRow | None:
    """The link with this hash, whatever its state. ``conn`` comes from ``Database.system()``."""
    row = conn.execute(
        text(
            "SELECT link_id, customer_id, user_id, purpose, expires_at, used_at"
            " FROM kaagaz_email_link_lookup(:h)"
        ),
        {"h": token_hash},
    ).first()
    if row is None:
        return None
    return LinkRow(str(row[0]), str(row[1]), str(row[2]), str(row[3]), row[4], row[5])


def use(conn: Connection, customer_id: str, link_id: str, *, now: datetime) -> bool:
    """Mark the link used, only while it is unused and not expired. False: it is not."""
    row = conn.execute(
        text(
            "UPDATE email_links SET used_at = :now"
            " WHERE customer_id = :c AND id = :l AND used_at IS NULL AND expires_at > :now"
            " RETURNING id"
        ),
        {"c": customer_id, "l": link_id, "now": now},
    ).first()
    return row is not None


def void_all(conn: Connection, customer_id: str, user_id: str, *, now: datetime) -> None:
    """Every open link of this user, of any purpose, stops working: their password was just
    set, or an admin disabled them or reset their password. O(log n)."""
    conn.execute(
        text(
            "UPDATE email_links SET used_at = :now"
            " WHERE customer_id = :c AND user_id = :u AND used_at IS NULL"
        ),
        {"c": customer_id, "u": user_id, "now": now},
    )
