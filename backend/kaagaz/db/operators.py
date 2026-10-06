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
    session_version: int
    totp_secret_enc: str | None
    totp_enabled: bool
    totp_last_step: int | None
    failed_logins: int


def begin_login(
    conn: Connection, email: str | None, oid: str | None, now: datetime, max_failures: int, lock_end: datetime
) -> OperatorAttempt | None:
    """Count one attempt by e-mail or by id, before any check. None: unknown or locked."""
    row = (
        conn.execute(
            text("SELECT * FROM kaagaz_op_begin_login(:e, :o, :now, :max, :lock_end)"),
            {"e": email, "o": oid, "now": now, "max": max_failures, "lock_end": lock_end},
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
    """Use up the code's step and clear the count. The session version, or None."""
    version = conn.execute(
        text("SELECT kaagaz_op_login_succeeded(:o, :step, :now)"),
        {"o": operator_id, "step": step, "now": now},
    ).scalar()
    return None if version is None else int(version)


def enrol(conn: Connection, oid: str, sealed: str, step: int, now: datetime, version: int) -> int | None:
    """Set two-step sign-in up with its first code. The session version, or None."""
    done = conn.execute(
        text("SELECT kaagaz_op_enrol(:o, :enc, :step, :now, :ver)"),
        {"o": oid, "enc": sealed, "step": step, "now": now, "ver": version},
    ).scalar()
    return None if done is None else int(done)


def session_operator(conn: Connection, operator_id: str) -> tuple[str, str, int] | None:
    """(email, name, session version) of the enabled, set-up operator, or None. Every request."""
    row = conn.execute(
        text("SELECT email, display_name, session_version FROM kaagaz_op_session(:o)"), {"o": operator_id}
    ).first()
    return None if row is None else (str(row[0]), str(row[1]), int(row[2]))


def logout(conn: Connection, operator_id: str) -> None:
    """End every session of this operator."""
    conn.execute(text("SELECT kaagaz_op_logout(:o)"), {"o": operator_id})
