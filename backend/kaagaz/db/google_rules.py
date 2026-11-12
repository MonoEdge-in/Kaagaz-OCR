"""Google sign-in by organisation and role (#507, 0074): the organisation's rules, the
"passing link", and the writes of the admin's settings, approvals and promotions.

* **On** for an organisation: the server's ``GOOGLE_SIGNIN_ENABLED`` (the caller's part) AND
  ``COALESCE(customers.google_signin, platform_ai_settings.google_signin)`` (``Rules.on``).
* **The domain rule** (``domain_ok``): no domains, any account passes. Otherwise Google's
  verified email AND its ``hd`` claim AND the email's own domain must all be in the list; ``hd``
  alone is never trusted. Letter case does not count (the list is stored in lower case).
* **A passing link** (``passing_link``): approved, and the domain rule holds for its stored
  ``hosted_domain`` and ``email_at_link``. A link keeps ``hd`` only when Google verified the
  email (``kept_hosted_domain``), so a stored ``hosted_domain`` stands for "verified": with
  domains, a link without one never passes, and the password sign-in, the limited session, the
  lock-out guard and approval (the stored link) agree with Google's own sign-in and connect (the
  claims now). This module is the one rule; ``kaagaz.db.users`` and ``kaagaz.api`` call it.
* **Not checked yet** (``unchecked``, #507 review): a link made before 0074 has no ``hd``. While
  its email is inside the domains, it is "not checked yet" rather than outside: Google's next
  sign-in stores ``hd``, and the guard, approval, the limited session and the people list say so.
* ``hd`` arrives in lower case (``kaagaz.api.oidc``, the one place); the domains are stored so.

Every function takes a connection from ``Database.customer(customer_id)`` and also filters on
``customer_id`` (two locks). Each is one statement: O(log n), or O(connections of this customer)
for ``links``, O(users of this customer) for ``sign_out_linked``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, RowMapping, text

ROLES = ("admin", "reviewer", "member")
MAX_DOMAINS = 10
# Personal email domains an organisation can't name as its own (yahoo.* by its first label).
PERSONAL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "icloud.com",
        "rediffmail.com",
        "proton.me",
        "protonmail.com",
    }
)


def personal(domain: str) -> bool:
    lowered = domain.lower()
    return lowered in PERSONAL_DOMAINS or lowered.split(".", 1)[0] == "yahoo"


def email_domain(email: str | None) -> str | None:
    """The part after the last ``@``, in lower case; None without one."""
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].lower() or None


def domain_ok(
    domains: Iterable[str],
    hosted_domain: str | None,
    email_at_link: str | None,
    *,
    email_verified: bool = True,
) -> bool:
    """No domains: True. Otherwise a verified email whose domain AND ``hd`` are both listed."""
    allowed = {domain.lower() for domain in domains}
    if not allowed:
        return True
    if not email_verified or hosted_domain is None:
        return False
    return hosted_domain in allowed and email_domain(email_at_link) in allowed


def unchecked(domains: Iterable[str], hosted_domain: str | None, email_at_link: str | None) -> bool:
    """A stored link with no ``hd`` yet (from before 0074) whose email is inside the domains: not
    checked yet, until Google's next sign-in stores ``hd``."""
    allowed = {domain.lower() for domain in domains}
    return hosted_domain is None and email_domain(email_at_link) in allowed


def kept_hosted_domain(hosted_domain: str | None, email_verified: bool) -> str | None:
    """The ``hd`` a link stores (at connect and at each sign-in): Google's, only with a verified
    email; else None, which no domain rule lets in."""
    return hosted_domain if email_verified else None


def passing_link(
    status: str, domains: Iterable[str], hosted_domain: str | None, email_at_link: str | None
) -> bool:
    """An approved connection that the domain rule lets in."""
    return status == "approved" and domain_ok(domains, hosted_domain, email_at_link)


def ordered(roles: Iterable[str]) -> list[str]:
    """``roles`` once each, in ``ROLES`` order."""
    given = set(roles)
    return [role for role in ROLES if role in given]


@dataclass(frozen=True, slots=True)
class Rules:
    on: bool  # the organisation's value in force, without the server's switch
    allowed_roles: tuple[str, ...]
    only_roles: tuple[str, ...]
    domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Link:
    id: str
    user_id: str
    role: str
    status: str
    hosted_domain: str | None
    email_at_link: str | None

    def passes(self, domains: Iterable[str]) -> bool:
        return passing_link(self.status, domains, self.hosted_domain, self.email_at_link)

    def unchecked(self, domains: Iterable[str]) -> bool:
        return unchecked(domains, self.hosted_domain, self.email_at_link)


def rules(conn: Connection, customer_id: str) -> Rules | None:
    """The organisation's Google rules; None for no such customer."""
    row = conn.execute(
        text(
            "SELECT COALESCE(c.google_signin, p.google_signin) AS on_, c.google_roles, c.google_only_roles,"
            " c.google_domains FROM customers c CROSS JOIN platform_ai_settings p WHERE c.id = :c"
        ),
        {"c": customer_id},
    ).first()
    if row is None:
        return None
    return Rules(bool(row[0]), tuple(ordered(row[1])), tuple(ordered(row[2])), tuple(row[3]))


def save_rules(
    conn: Connection,
    customer_id: str,
    *,
    allowed_roles: Sequence[str],
    only_roles: Sequence[str],
    domains: Sequence[str],
) -> None:
    """Replace the three rules (the CHECKs hold them: only inside allowed, at most 10 domains)."""
    conn.execute(
        text(
            "UPDATE customers SET google_roles = :allowed, google_only_roles = :only,"
            " google_domains = :domains WHERE id = :c"
        ),
        {
            "c": customer_id,
            "allowed": list(allowed_roles),
            "only": list(only_roles),
            "domains": list(domains),
        },
    )


_LINKS = (
    "SELECT i.id, i.user_id, u.role, i.status, i.hosted_domain, i.email_at_link FROM user_identities i"
    " JOIN users u ON u.customer_id = i.customer_id AND u.id = i.user_id"
    " WHERE i.customer_id = :c AND i.provider = 'google'"
)


def _link(row: Sequence[object]) -> Link:
    hosted, email = row[4], row[5]
    return Link(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        str(row[3]),
        None if hosted is None else str(hosted),
        None if email is None else str(email),
    )


def links(conn: Connection, customer_id: str, *, user_id: str | None = None) -> list[Link]:
    """This customer's Google connections (only ``user_id``'s, when given), each with its user's role."""
    if user_id is None:
        rows = conn.execute(text(_LINKS), {"c": customer_id})
    else:
        rows = conn.execute(text(_LINKS + " AND i.user_id = :u"), {"c": customer_id, "u": user_id})
    return [_link(row) for row in rows]


def link(conn: Connection, customer_id: str, identity_id: str) -> Link | None:
    row = conn.execute(text(_LINKS + " AND i.id = :i"), {"c": customer_id, "i": identity_id}).first()
    return None if row is None else _link(row)


def sign_out_linked(conn: Connection, customer_id: str, roles: Iterable[str], *, keep: str) -> int:
    """Raise ``session_version`` of every user of these roles with an approved connection, but
    ``keep`` (the saver stays signed in); how many."""
    result = conn.execute(
        text(
            "UPDATE users AS u SET session_version = u.session_version + 1"
            " WHERE u.customer_id = :c AND u.role = ANY(:roles) AND u.id <> :keep"
            " AND EXISTS (SELECT 1 FROM user_identities i WHERE i.customer_id = u.customer_id"
            " AND i.user_id = u.id AND i.status = 'approved')"
        ),
        {"c": customer_id, "roles": list(roles), "keep": keep},
    )
    return int(result.rowcount)


def reapprove(conn: Connection, customer_id: str, user_id: str) -> Sequence[RowMapping]:
    """Every approved connection of this user waits for an admin again; the ones changed."""
    return (
        conn.execute(
            text(
                "UPDATE user_identities SET status = 'pending', approved_at = NULL, approved_by = NULL"
                " WHERE customer_id = :c AND user_id = :u AND status = 'approved' RETURNING id, provider"
            ),
            {"c": customer_id, "u": user_id},
        )
        .mappings()
        .all()
    )
