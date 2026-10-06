"""Platform operators (0015, super admin PR 2, PROVISIONAL): their sign-in and ``platform_audit``.

Two kinds of connection, never mixed:

* ``OperatorDatabase.operator()`` (the API): ``kaagaz_operator`` has no table
  rights, so everything goes through the ``kaagaz_op_*`` functions. Each one
  that settles an outcome writes its own ``platform_audit`` row.
* The owner connection (``cli._owner``, the server's command line): plain SQL
  on ``operators`` and ``platform_audit``, with ``operator_id`` NULL in the
  audit row.

Every call is one statement on a primary or unique key: O(log n).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, TextClause, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.sqlstate import UNIQUE_VIOLATION, sqlstate
from kaagaz.db.users import EmailTaken


@dataclass(frozen=True, slots=True, kw_only=True)
class OperatorAttempt:
    """A counted sign-in attempt; ``failed_logins`` includes it."""

    operator_id: str
    email: str
    display_name: str
    password_hash: str
    disabled: bool
    session_version: int
    totp_secret_enc: str | None
    totp_enabled: bool
    totp_last_step: int | None
    failed_logins: int


@dataclass(frozen=True, slots=True)
class SessionOperator:
    email: str
    display_name: str
    session_version: int


# --- the API, as kaagaz_operator ------------------------------------------------------


def begin_login(
    conn: Connection,
    *,
    email: str | None = None,
    operator_id: str | None = None,
    now: datetime,
    max_failures: int,
    lock_end: datetime,
) -> OperatorAttempt | None:
    """Count one attempt by e-mail or by id, before any check. None: unknown or locked."""
    row = (
        conn.execute(
            text("SELECT * FROM kaagaz_op_begin_login(:e, :o, :now, :max, :lock_end)"),
            {"e": email, "o": operator_id, "now": now, "max": max_failures, "lock_end": lock_end},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    step = row["totp_last_step"]
    return OperatorAttempt(
        operator_id=str(row["operator_id"]),
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        password_hash=str(row["password_hash"]),
        disabled=bool(row["disabled"]),
        session_version=int(row["session_version"]),
        totp_secret_enc=row["totp_secret_enc"],
        totp_enabled=bool(row["totp_enabled"]),
        totp_last_step=None if step is None else int(step),
        failed_logins=int(row["failed_logins"]),
    )


def login_failed(conn: Connection, operator_id: str, *, locked: bool) -> None:
    conn.execute(text("SELECT kaagaz_op_login_failed(:o, :locked)"), {"o": operator_id, "locked": locked})


def login_succeeded(conn: Connection, operator_id: str, step: int, now: datetime) -> int | None:
    """Use up the code's step and clear the count. The session version, or None."""
    version = conn.execute(
        text("SELECT kaagaz_op_login_succeeded(:o, :step, :now)"),
        {"o": operator_id, "step": step, "now": now},
    ).scalar()
    return None if version is None else int(version)


def enrol(
    conn: Connection, operator_id: str, *, secret_enc: str, step: int, now: datetime, version: int
) -> int | None:
    """Set up two-step sign-in with its first code. The session version, or None."""
    done = conn.execute(
        text("SELECT kaagaz_op_enrol(:o, :enc, :step, :now, :ver)"),
        {"o": operator_id, "enc": secret_enc, "step": step, "now": now, "ver": version},
    ).scalar()
    return None if done is None else int(done)


def session_operator(conn: Connection, operator_id: str) -> SessionOperator | None:
    """The enabled, set-up operator behind a session, or None. Every request."""
    row = conn.execute(
        text("SELECT email, display_name, session_version FROM kaagaz_op_session(:o)"), {"o": operator_id}
    ).first()
    return None if row is None else SessionOperator(str(row[0]), str(row[1]), int(row[2]))


def logout(conn: Connection, operator_id: str) -> None:
    """End every session of this operator."""
    conn.execute(text("SELECT kaagaz_op_logout(:o)"), {"o": operator_id})


# --- the command line, as the table owner ------------------------------------------------

RESET_TOTP = text(
    "UPDATE operators SET totp_secret_enc = NULL, totp_enabled_at = NULL, totp_last_step = NULL,"
    " session_version = session_version + 1 WHERE email = :e RETURNING id"
)
UNLOCK = text("UPDATE operators SET failed_logins = 0, locked_until = NULL WHERE email = :e RETURNING id")


def create(conn: Connection, *, email: str, display_name: str, password_hash: str) -> str:
    """A new operator, not yet set up for two-step. Raises EmailTaken."""
    try:
        new_id = conn.execute(
            text(
                "INSERT INTO operators (email, display_name, password_hash) VALUES (:e, :n, :h) RETURNING id"
            ),
            {"e": email.strip().lower(), "n": display_name, "h": password_hash},
        ).scalar_one()
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION:
            raise EmailTaken from None
        raise
    return str(new_id)


def change(conn: Connection, statement: TextClause, email: str) -> str | None:
    """``RESET_TOTP`` (also ends every session) or ``UNLOCK``. The id, or None."""
    found = conn.execute(statement, {"e": email.strip().lower()}).scalar()
    return None if found is None else str(found)


def record(
    conn: Connection,
    action: str,
    *,
    target_type: str,
    target_id: str,
    subject_customer_id: str | None = None,
) -> None:
    """One ``platform_audit`` row for a command-line action (no operator)."""
    conn.execute(
        text(
            "INSERT INTO platform_audit (action, subject_customer_id, target_type, target_id)"
            " VALUES (:a, :c, :tt, :tid)"
        ),
        {"a": action, "c": subject_customer_id, "tt": target_type, "tid": target_id},
    )
