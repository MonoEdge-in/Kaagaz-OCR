"""What a platform operator does to organisations (0016, super admin PR 3, PROVISIONAL):
the API's calls, on ``OperatorDatabase.operator()`` connections only. ``kaagaz_operator``
has no table rights, so each is one ``kaagaz_op_*`` function, which checks the operator,
names its one customer and writes its own ``platform_audit`` row.

A write returns an ``Outcome``: ``done`` (changed and audited), ``unchanged``,
``missing``, ``deleting``, ``step_up`` (the fresh code's step was not accepted) or
``signed_out``. The list returns counts only.

Time: each write is O(log n) on primary keys, except disabling or enabling a customer,
O(u) for its u users. ``list_customers`` is O(all users, documents and audit rows):
ponytail: counted on every call, per-customer counters are the upgrade if it gets slow.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, RowMapping, TextClause, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.sqlstate import UNIQUE_VIOLATION, sqlstate
from kaagaz.db.users import EmailTaken


@dataclass(frozen=True, slots=True)
class Outcome:
    code: str  # done, unchanged, missing, deleting, step_up or signed_out
    result_id: str | None  # the customer acted on or made


def list_customers(conn: Connection, operator_id: str) -> Sequence[RowMapping]:
    """Every customer with its status and counts, oldest first. Never a name of a
    document or a user."""
    return conn.execute(text("SELECT * FROM kaagaz_op_customers(:o)"), {"o": operator_id}).mappings().all()


def create_customer(
    conn: Connection, operator_id: str, *, name: str, admin_email: str, admin_name: str, password_hash: str
) -> Outcome:
    """A customer and its first admin, whose password is temporary. Raises EmailTaken."""
    return _write(
        conn,
        text("SELECT outcome, result_id FROM kaagaz_op_create_customer(:o, :name, :email, :admin, :hash)"),
        {"o": operator_id, "name": name, "email": admin_email, "admin": admin_name, "hash": password_hash},
    )


def set_customer_status(
    conn: Connection, operator_id: str, customer_id: str, status: str, *, now: datetime, step: int | None
) -> Outcome:
    """Disable (``step`` of a fresh code required) or enable one customer."""
    return _write(
        conn,
        text("SELECT outcome, result_id FROM kaagaz_op_set_customer_status(:o, :c, :s, :now, :step)"),
        {"o": operator_id, "c": customer_id, "s": status, "now": now, "step": step},
    )


def step_up_failed(conn: Connection, operator_id: str, *, locked: bool) -> None:
    """The audit row of a refused step-up code (``operator.locked`` when it began the lock)."""
    conn.execute(text("SELECT kaagaz_op_step_up_failed(:o, :locked)"), {"o": operator_id, "locked": locked})


def _write(conn: Connection, call: TextClause, params: dict[str, Any]) -> Outcome:
    try:
        row = conn.execute(call, params).one()
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION:
            raise EmailTaken from None
        raise
    return Outcome(str(row[0]), None if row[1] is None else str(row[1]))
