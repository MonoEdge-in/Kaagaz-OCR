"""Customers and their users: sign-in lookups, the lockout counter, two-step
codes, session versions and the admin's user list.

Why these are separate from the API: the SQL stays in one place, plain and
reviewable. Every function except the two named below takes a connection from
``Database.customer(customer_id)`` and also filters on ``customer_id`` (two
locks, docs/architecture.md).

The exceptions have no customer yet:

* ``login_lookup`` (``Database.system()``): signing in starts from an e-mail only.
  It calls the SECURITY DEFINER function ``kaagaz_login_lookup``, which returns one row.
* ``release_stale_signup`` (any connection): frees the e-mail of a stale sign-up (0063).
* ``create_customer``: calls ``kaagaz_create_customer``: the CLI, on its owner
  connection ``cli._owner``, so its ``platform_audit`` row is in the same transaction;
  self sign-up (``POST /auth/signup``), inside ``Database.system()``. With email on (#457),
  self sign-up calls ``kaagaz_create_pending_customer`` (0061): the customer waits as
  ``pending`` for an operator's approval.

E-mails are unique across all customers (the ``users.email`` constraint), so
adding a taken e-mail raises ``EmailTaken`` even when another customer holds it.

Why every write is one statement (PRD 1 A2 to A5, council record WP1.3): two
requests for the same user can arrive at once, so nothing reads a value and
writes it back later. Each change is a single UPDATE whose WHERE holds the
condition, and PostgreSQL re-checks that condition after waiting for a row
lock. Times always come in as ``now`` from the injected clock, never SQL
``now()``, so tests move the clock instead of sleeping.

Every admin-facing statement (list, create, update, unlock,
reset_totp, set_delete_forever) returns the same columns: the ``AdminUser``
columns plus ``locked_until``, ``totp_enabled_at``, ``totp_off_at``, ``must_change_password``,
``session_version``, ``delete_forever_allowed_at`` and ``two_step_required`` (0068). None
means there is no such user in this customer (404).

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
    auth_authority: str
    disabled: bool
    session_version: int
    must_change_password: bool
    totp_secret_enc: str | None
    totp_enabled_at: datetime | None
    totp_last_step: int | None
    # The admin turned two-step sign-in off themselves (0061b); None while their organisation
    # requires it for admins (0068, #481), so they set it up at this sign-in.
    totp_off_at: datetime | None
    failed_logins: int
    # False while the customer is disabled or being deleted (0014): sign-in is refused.
    customer_active: bool
    # A sign-up waiting for an operator's approval (0061): refused with its own 403.
    customer_pending: bool = False


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
    conn: Connection,
    *,
    name: str,
    admin_email: str,
    admin_name: str,
    password_hash: str,
    pending: bool = False,
) -> str:
    """Create a customer and its first admin. ``conn``: the CLI's owner connection, or
    ``Database.system()`` for self sign-up. ``pending``: it waits for an operator (0061).
    Raises EmailTaken if the admin's e-mail is in use."""
    function = "kaagaz_create_pending_customer" if pending else "kaagaz_create_customer"
    try:
        customer_id: object = conn.execute(
            text(f"SELECT {function}(:name, :email, :admin_name, :hash)"),
            {"name": name, "email": admin_email.lower(), "admin_name": admin_name, "hash": password_hash},
        ).scalar_one()
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION:
            raise EmailTaken from None
        raise
    return str(customer_id)


def release_stale_signup(conn: Connection, email: str) -> bool:
    """Before a user with ``email`` is made: a pending sign-up older than 24 hours whose only
    user has this e-mail and never proved it is deleted (0063), so it no longer holds the
    e-mail. True when one was. Any connection: the SECURITY DEFINER function checks it all."""
    return bool(conn.execute(text("SELECT kaagaz_release_stale_signup(:e)"), {"e": email}).scalar())


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

    ``customer_active`` comes in the same statement (0014), so the sign-in can
    refuse a disabled customer's user like a disabled user, before any reply
    says the password was right.

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
                " RETURNING" + _ATTEMPT_COLUMNS
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
    return None if row is None else _attempt(row)


def peek_attempt(conn: Connection, customer_id: str, user_id: str, *, now: datetime) -> AttemptRow | None:
    """``begin_attempt``'s columns, read without counting anything: a provider sign-in's first
    step, which the provider itself proved (#457). None means locked (or not here). O(log n)."""
    row = (
        conn.execute(
            text(
                "SELECT" + _ATTEMPT_COLUMNS + " FROM users AS u WHERE u.customer_id = :c AND u.id = :u"
                " AND (u.locked_until IS NULL OR u.locked_until <= :now)"
            ),
            {"c": customer_id, "u": user_id, "now": now},
        )
        .mappings()
        .first()
    )
    return None if row is None else _attempt(row)


# What a sign-in needs, with whether the customer is active or a pending sign-up (0014, 0061).
# "Turned off" counts only while the organisation does not require two-step for admins (0068).
_ATTEMPT_COLUMNS = (
    " u.id, u.customer_id, u.password_hash, u.role, u.auth_authority, u.disabled, u.session_version,"
    " u.must_change_password, u.totp_secret_enc, u.totp_enabled_at, u.totp_last_step,"
    " CASE WHEN EXISTS (SELECT 1 FROM customers c WHERE c.id = u.customer_id"
    " AND c.admin_two_step_required) THEN NULL ELSE u.totp_off_at END AS totp_off_at,"
    " u.failed_logins, EXISTS (SELECT 1 FROM customers c"
    " WHERE c.id = u.customer_id AND c.status = 'active') AS customer_active,"
    " EXISTS (SELECT 1 FROM customers c"
    " WHERE c.id = u.customer_id AND c.status = 'pending') AS customer_pending"
)


# The admin-facing columns (the docstring above). ``two_step_required`` (0068, #481): while the
# organisation requires two-step for admins, an admin's "turned off" does not count (views).
_ADMIN_COLUMNS = (
    " u.id, u.email, u.display_name, u.role, u.disabled, u.created_at,"
    " u.locked_until, u.totp_enabled_at, u.totp_off_at, u.must_change_password,"
    " u.session_version, u.delete_forever_allowed_at,"
    " CASE WHEN u.auth_authority <> 'keycloak' THEN 'not_moved'"
    " WHEN u.last_login_at IS NULL THEN 'waiting' ELSE 'connected' END AS company_state,"
    " EXISTS (SELECT 1 FROM customers c"
    " WHERE c.id = u.customer_id AND c.admin_two_step_required) AS two_step_required, u.management"
)


def _attempt(row: RowMapping) -> AttemptRow:
    last_step = row["totp_last_step"]
    return AttemptRow(
        user_id=str(row["id"]),
        customer_id=str(row["customer_id"]),
        password_hash=str(row["password_hash"]),
        role=str(row["role"]),
        auth_authority=str(row["auth_authority"]),
        disabled=bool(row["disabled"]),
        session_version=int(row["session_version"]),
        must_change_password=bool(row["must_change_password"]),
        totp_secret_enc=row["totp_secret_enc"],
        totp_enabled_at=row["totp_enabled_at"],
        totp_last_step=None if last_step is None else int(last_step),
        totp_off_at=row["totp_off_at"],
        failed_logins=int(row["failed_logins"]),
        customer_active=bool(row["customer_active"]),
        customer_pending=bool(row["customer_pending"]),
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
    """An admin ends a lock early: the count goes back to 0, and the block on opening locked files
    with it (PRD 7, #605). O(log n)."""
    return (
        conn.execute(
            text(
                "UPDATE users AS u SET failed_logins = 0, locked_until = NULL,"  # noqa: S608 - a fixed fragment, _ADMIN_COLUMNS
                " locked_open_failures = 0, locked_open_until = NULL"
                " WHERE u.customer_id = :c AND u.id = :u"
                " RETURNING" + _ADMIN_COLUMNS
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


def end_session(
    conn: Connection, customer_id: str, user_id: str, session_id: str, *, expires_at: datetime
) -> None:
    """Logout (0076): this one session ends at once; the user's other browsers stay signed in.
    The row is swept once ``expires_at``, the cookie's own end, has passed. O(log n)."""
    conn.execute(
        text(
            "INSERT INTO ended_sessions (customer_id, session_id, user_id, expires_at)"
            " VALUES (:c, :s, :u, :e) ON CONFLICT DO NOTHING"
        ),
        {"c": customer_id, "s": session_id, "u": user_id, "e": expires_at},
    )


def sweep_ended_sessions(conn: Connection, before: datetime) -> int:
    """The worker's hourly sweep (0076): delete the ended sessions whose cookie expired by
    ``before``, in every organisation; how many. ``conn`` comes from ``Database.system()``."""
    swept: object = conn.execute(text("SELECT kaagaz_sweep_ended_sessions(:b)"), {"b": before}).scalar_one()
    if not isinstance(swept, int):
        raise TypeError("the sweep did not return a count")
    return swept


# --- two-step codes ---------------------------------------------------------------------


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
    """Clear the two-step code, and "turned off" with it (0061b): an admin sets one up at
    their next sign-in. Raises ``session_version``, so every session ends. O(log n)."""
    return (
        conn.execute(
            text(
                "UPDATE users AS u SET totp_secret_enc = NULL, totp_enabled_at = NULL,"  # noqa: S608 - a fixed fragment, _ADMIN_COLUMNS
                " totp_last_step = NULL, totp_off_at = NULL, session_version = u.session_version + 1"
                " WHERE u.customer_id = :c AND u.id = :u AND u.auth_authority = 'legacy'"
                " RETURNING" + _ADMIN_COLUMNS
            ),
            {"c": customer_id, "u": user_id},
        )
        .mappings()
        .first()
    )


# --- who is signed in, and the admin's user list ---------------------------------------------


def session_user(
    conn: Connection, customer_id: str, user_id: str, *, session_id: str | None = None
) -> RowMapping | None:
    """The signed-in user with their customer's name, or None. One query per request.

    None too while the customer is not active (0014): every sign-in starts its
    session through here (``api.auth.load_user``) and every request checks it,
    so a disabled customer's sessions end at their next request.

    With the idle timeout (0040) in the same query: ``idle_hours``, the organisation's else
    the platform's (None: no idle limit), and ``seen_at``, the later of ``last_seen_at`` and
    ``last_login_at``. ``two_step_required``: the organisation requires two-step sign-in for its
    admins (0068, #481); while it does, an admin's "turned off" does not count.

    With ``session_id`` (a request's, 0076): None too once logout ended that session, by one
    probe of ``ended_sessions``' primary key in the same statement."""
    ended = (
        ""
        if session_id is None
        else " AND NOT EXISTS (SELECT 1 FROM ended_sessions e"
        " WHERE e.customer_id = u.customer_id AND e.session_id = CAST(:sid AS uuid))"
    )
    return (
        conn.execute(
            text(
                "SELECT u.id, u.email, u.display_name, u.role, u.auth_authority, u.disabled,"  # noqa: S608 - fixed fragments
                " u.session_version, u.must_change_password, u.totp_enabled_at IS NOT NULL AS totp_enabled,"
                " u.totp_off_at IS NOT NULL AND NOT c.admin_two_step_required AS two_step_turned_off,"
                " c.admin_two_step_required AS two_step_required,"
                " u.delete_forever_allowed_at IS NOT NULL AS delete_forever_allowed,"
                " u.email_verified_at IS NOT NULL AS email_confirmed,"
                " c.id AS customer_id, c.name AS customer_name,"
                " GREATEST(u.last_seen_at, u.last_login_at) AS seen_at,"
                " COALESCE(s.idle_hours, p.session_idle_hours) AS idle_hours"
                " FROM users u JOIN customers c ON c.id = u.customer_id"
                " LEFT JOIN org_session_settings s ON s.customer_id = c.id"
                " LEFT JOIN platform_ai_settings p ON true"
                " WHERE u.customer_id = :c AND u.id = :u AND c.status = 'active'" + ended
            ),
            {"c": customer_id, "u": user_id} | ({} if session_id is None else {"sid": session_id}),
        )
        .mappings()
        .first()
    )


def contact(conn: Connection, customer_id: str, user_id: str) -> tuple[str, str] | None:
    """This customer's user's (e-mail, name), for an email to them (#457), whatever the
    customer's status. None if there is no such user. O(log n)."""
    row = conn.execute(
        text("SELECT email, display_name FROM users WHERE customer_id = :c AND id = :u"),
        {"c": customer_id, "u": user_id},
    ).first()
    return None if row is None else (str(row[0]), str(row[1]))


def admin_contacts(conn: Connection, customer_id: str, *, management: bool = False) -> list[tuple[str, str]]:
    """Every enabled admin's (e-mail, name) in this customer, for "Waiting for your approval"
    (#457 phase 1); with ``management``, Management's too ("WhatsApp was turned on", #603).
    O(users of this customer)."""
    rows = conn.execute(
        text(
            "SELECT email, display_name FROM users"
            " WHERE customer_id = :c AND (role = 'admin' OR (:m AND management)) AND NOT disabled"
            " ORDER BY email"
        ),
        {"c": customer_id, "m": management},
    )
    return [(str(row[0]), str(row[1])) for row in rows]


def access_watchers(conn: Connection, customer_id: str, *, but: str) -> list[tuple[str, str]]:
    """Every enabled Management person's and admin's (e-mail, name) but ``but``'s, for "access was
    given" (PRD 7, #601). O(users of this customer)."""
    rows = conn.execute(
        text(
            "SELECT email, display_name FROM users WHERE customer_id = :c AND (management OR role = 'admin')"
            " AND NOT disabled AND id <> :but ORDER BY email"
        ),
        {"c": customer_id, "but": but},
    )
    return [(str(row[0]), str(row[1])) for row in rows]


def awaiting_confirm(conn: Connection, customer_id: str, user_id: str) -> tuple[str, str] | None:
    """The (e-mail, name) of an enabled user whose e-mail is not confirmed yet and whose
    customer is a sign-up waiting for an operator (0061), for "Send the link again" (#457);
    else None. O(log n)."""
    row = conn.execute(
        text(
            "SELECT u.email, u.display_name FROM users AS u"
            " JOIN customers AS c ON c.id = u.customer_id AND c.status = 'pending'"
            " WHERE u.customer_id = :c AND u.id = :u AND NOT u.disabled AND u.email_verified_at IS NULL"
        ),
        {"c": customer_id, "u": user_id},
    ).first()
    return None if row is None else (str(row[0]), str(row[1]))


def confirm_email(conn: Connection, customer_id: str, user_id: str, *, now: datetime) -> None:
    """The user proved their e-mail with a link (0061); the first proof's time is kept. O(log n)."""
    conn.execute(
        text(
            "UPDATE users AS u SET email_verified_at = COALESCE(u.email_verified_at, :now)"
            " WHERE u.customer_id = :c AND u.id = :u"
        ),
        {"c": customer_id, "u": user_id, "now": now},
    )


def touch_seen(conn: Connection, customer_id: str, user_id: str, *, now: datetime) -> None:
    """The session was used now (0040; the caller keeps it to once a minute). O(log n)."""
    conn.execute(
        text("UPDATE users AS u SET last_seen_at = :now WHERE u.customer_id = :c AND u.id = :u"),
        {"c": customer_id, "u": user_id, "now": now},
    )


def list_users(conn: Connection, customer_id: str) -> Sequence[RowMapping]:
    return (
        conn.execute(
            text(
                "SELECT" + _ADMIN_COLUMNS + " FROM users u WHERE u.customer_id = :c"
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
                    "INSERT INTO users AS u"  # noqa: S608 - a fixed fragment, _ADMIN_COLUMNS
                    " (customer_id, email, display_name, role, password_hash, must_change_password)"
                    " VALUES (:c, :email, :name, :role, :hash, :must_change)"
                    " RETURNING" + _ADMIN_COLUMNS
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
) -> RowMapping | None:
    """Change only the fields given (None keeps the stored value). None if no such user.

    Disabling a user or changing the role raises
    ``session_version`` (every session ends). The role counts even though every
    request re-checks it: an admin demoted and later made admin again must not
    get their old cookies back. A role change also clears the
    Delete forever grant (``set_delete_forever``), so it never survives a promotion."""
    return (
        conn.execute(
            text(
                "UPDATE users AS u SET"
                " role = COALESCE(CAST(:role AS text), u.role),"
                " disabled = COALESCE(CAST(:disabled AS boolean), u.disabled),"
                " session_version = u.session_version + CASE"
                "   WHEN CAST(:disabled AS boolean) IS TRUE"
                "   OR (CAST(:role AS text) IS NOT NULL AND CAST(:role AS text) <> u.role) THEN 1"
                "   ELSE 0 END,"
                " delete_forever_allowed_at = CASE"
                "   WHEN CAST(:role AS text) IS NOT NULL AND CAST(:role AS text) <> u.role THEN NULL"
                "   ELSE u.delete_forever_allowed_at END"
                " WHERE u.customer_id = :c AND u.id = :u"
                " RETURNING" + _ADMIN_COLUMNS
            ),
            {
                "c": customer_id,
                "u": user_id,
                "role": role,
                "disabled": disabled,
            },
        )
        .mappings()
        .first()
    )


def set_delete_forever(
    conn: Connection, customer_id: str, user_id: str, *, allowed: bool, now: datetime
) -> RowMapping | None:
    """Allow (from ``now``, or keep the first time) or stop a member's or reviewer's
    Delete forever. None for an admin or no such user (``role_of`` tells which):
    the role is checked in the WHERE, so a promotion racing this can't keep a
    grant. Sessions stay: every request reads the grant again. O(log n)."""
    return (
        conn.execute(
            text(
                "UPDATE users AS u SET delete_forever_allowed_at = CASE WHEN CAST(:allowed AS boolean)"  # noqa: S608 - a fixed fragment, _ADMIN_COLUMNS
                "   THEN COALESCE(u.delete_forever_allowed_at, :now) ELSE NULL END"
                " WHERE u.customer_id = :c AND u.id = :u AND u.role <> 'admin'"
                " RETURNING" + _ADMIN_COLUMNS
            ),
            {"c": customer_id, "u": user_id, "allowed": allowed, "now": now},
        )
        .mappings()
        .first()
    )


def set_management(conn: Connection, customer_id: str, user_id: str, *, on: bool) -> RowMapping | None:
    """Management on or off for an enabled user (PRD 7, #601); None for a switched-off one or no
    such user. O(log n)."""
    return (
        conn.execute(
            text(
                "UPDATE users AS u SET management = :on"  # noqa: S608 - a fixed fragment, _ADMIN_COLUMNS
                " WHERE u.customer_id = :c AND u.id = :u AND NOT u.disabled"
                " RETURNING" + _ADMIN_COLUMNS
            ),
            {"c": customer_id, "u": user_id, "on": on},
        )
        .mappings()
        .first()
    )


def role_of(conn: Connection, customer_id: str, user_id: str) -> str | None:
    """This customer's user's role, or None if there is no such user. O(log n)."""
    role: object = conn.execute(
        text("SELECT role FROM users WHERE customer_id = :c AND id = :u"), {"c": customer_id, "u": user_id}
    ).scalar()
    return None if role is None else str(role)


def auth_authority(conn: Connection, customer_id: str, user_id: str) -> str | None:
    """Read one user's explicit sign-in authority inside their customer transaction."""
    authority: object = conn.execute(
        text("SELECT auth_authority FROM users WHERE customer_id = :c AND id = :u"),
        {"c": customer_id, "u": user_id},
    ).scalar_one_or_none()
    return None if authority is None else str(authority)
