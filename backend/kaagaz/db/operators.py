"""Platform operators (0015, PROVISIONAL): the API's calls, on ``OperatorDatabase.operator()``
connections. ``kaagaz_operator`` has no table rights, so each is a ``kaagaz_op_*``
function (which writes its own ``platform_audit`` row). One keyed statement each: O(log n).

The command line (``cli._owner``, the table owner) uses plain SQL on ``operators``
and ``platform_audit`` instead, with ``operator_id`` NULL in the audit row.
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
    totp_secret_enc: str | None  # issued by the command line; None: none yet
    totp_last_step: int | None
    failed_logins: int


def begin_login(
    conn: Connection, email: str, now: datetime, max_failures: int, lock_end: datetime
) -> OperatorAttempt | None:
    """Count one attempt, before any check. None: unknown or locked."""
    row = (
        conn.execute(
            text("SELECT * FROM kaagaz_op_begin_login(:e, :now, :max, :lock_end)"),
            {"e": email, "now": now, "max": max_failures, "lock_end": lock_end},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    values = {**row, "operator_id": str(row["operator_id"])}
    step = values["totp_last_step"]
    return OperatorAttempt(**{**values, "totp_last_step": None if step is None else int(step)})


def login_failed(conn: Connection, operator_id: str, *, locked: bool) -> None:
    conn.execute(text("SELECT kaagaz_op_login_failed(:o, :locked)"), {"o": operator_id, "locked": locked})


def login_succeeded(conn: Connection, operator_id: str, step: int, now: datetime) -> int | None:
    """Use up the code's step (the first one ever also confirms the secret) and clear the
    count. The session version, or None: not this step, disabled or no secret."""
    version = conn.execute(
        text("SELECT kaagaz_op_login_succeeded(:o, :step, :now)"),
        {"o": operator_id, "step": step, "now": now},
    ).scalar()
    return None if version is None else int(version)


def session_operator(conn: Connection, operator_id: str) -> tuple[str, str, int] | None:
    """(email, name, session version) of the enabled, set-up operator, or None. Every request."""
    row = conn.execute(
        text("SELECT email, display_name, session_version FROM kaagaz_op_session(:o)"), {"o": operator_id}
    ).first()
    return None if row is None else (str(row[0]), str(row[1]), int(row[2]))


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
