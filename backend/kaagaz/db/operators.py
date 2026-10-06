"""Platform operators (0015, PROVISIONAL): the API's calls, on ``OperatorDatabase.operator()``
connections. ``kaagaz_operator`` has no table rights, so each is a ``kaagaz_op_*``
function (which writes its own ``platform_audit`` row). One keyed statement each: O(log n).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, text


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
