"""What a platform operator does to organisations (0016, super admin PR 3; deleting
one: 0018, PR 4) and to their admins (0017, PR 3b), PROVISIONAL: the API's calls, on
``OperatorDatabase.operator()`` connections only. ``kaagaz_operator`` has no table
rights, so each is one ``kaagaz_op_*`` function, which checks the operator, names its
one customer (and its one admin) and writes its own ``platform_audit`` row.

A write returns an ``Outcome``: ``done`` (changed and audited), ``unchanged``,
``missing``, ``deleting``, ``step_up`` (the fresh code's step was not accepted) or
``signed_out``; enabling also ``pending`` (0061: Approve it instead); approving one
``not_pending`` or ``unconfirmed`` (no admin proved their e-mail); scheduling a deletion
also ``not_disabled`` or ``name_mismatch`` (audited), cancelling one ``not_deleting`` or
``purging`` (its grace period is over). The organisation list returns counts only, and
each one's auto-filing grant and switch (0025) and whether an admin proved their e-mail
(0061); the admin list, one customer's admins only.

Time: each write is O(log n) on primary keys, except disabling or enabling a customer,
O(u) for its u users. ``list_customers`` is O(all users, documents and audit rows):
ponytail: counted on every call, per-customer counters are the upgrade if it gets slow.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Connection, RowMapping, TextClause, text

Change = Literal["disable", "enable", "unlock", "reset_totp"]


@dataclass(frozen=True, slots=True)
class Outcome:
    code: str  # done, unchanged, missing, deleting, step_up, taken or signed_out
    result_id: str | None  # the customer or admin acted on or made


def list_customers(conn: Connection, operator_id: str) -> Sequence[RowMapping]:
    """Every customer with its status and counts, oldest first. Never a name of a
    document or a user."""
    return conn.execute(text("SELECT * FROM kaagaz_op_customers(:o)"), {"o": operator_id}).mappings().all()


def create_customer(
    conn: Connection, operator_id: str, *, name: str, admin_email: str, admin_name: str, password_hash: str
) -> Outcome:
    """A customer and its first admin, whose password is temporary. ``taken``: the e-mail
    is in use (audited, nothing made)."""
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


def approve_customer(conn: Connection, operator_id: str, customer_id: str) -> Outcome:
    """Approve one pending customer whose admin proved their e-mail (0061): it becomes active."""
    return _write(
        conn,
        text("SELECT outcome, result_id FROM kaagaz_op_approve_customer(:o, :c)"),
        {"o": operator_id, "c": customer_id},
    )


def set_org_autofile(conn: Connection, operator_id: str, customer_id: str, *, granted: bool) -> Outcome:
    """Allow or stop auto-filing for one customer (feature 28, R1); stopping it also switches
    it off. Audited in both logs when it changes."""
    return _write(
        conn,
        text("SELECT outcome, result_id FROM kaagaz_op_set_org_autofile(:o, :c, :granted)"),
        {"o": operator_id, "c": customer_id, "granted": granted},
    )


def admin_two_step(conn: Connection, operator_id: str, customer_id: str) -> bool | None:
    """Whether one customer requires two-step sign-in for its admins (0068, #481). None for an
    unknown customer or an operator who is not enabled. O(log n)."""
    params = {"o": operator_id, "c": customer_id}
    found = conn.execute(text("SELECT kaagaz_op_admin_two_step(:o, :c)"), params).scalar()
    return None if found is None else bool(found)


def set_admin_two_step(
    conn: Connection, operator_id: str, customer_id: str, *, required: bool, step: int
) -> Outcome:
    """Require two-step sign-in for one customer's admins, or stop (``step`` of a fresh code
    required). Audited in both logs when it changes. O(log n)."""
    params = {"o": operator_id, "c": customer_id, "r": required, "step": step}
    code = conn.execute(text("SELECT kaagaz_op_set_admin_two_step(:o, :c, :r, :step)"), params).scalar_one()
    return Outcome(str(code), customer_id)


def google_signin(conn: Connection, operator_id: str, customer_id: str | None) -> RowMapping | None:
    """Google sign-in (0074, #507): org_on (None: follows the platform; None without a customer),
    platform_on, and the organisation's only_roles and domains. None for an unknown customer or
    an operator who is not enabled. O(log n)."""
    params = {"o": operator_id, "c": customer_id}
    return conn.execute(text("SELECT * FROM kaagaz_op_google_signin(:o, :c)"), params).mappings().first()


def set_google_signin(
    conn: Connection, operator_id: str, customer_id: str | None, *, on: bool | None, step: int
) -> Outcome:
    """Turn Google sign-in on or off for the platform (no customer) or one customer (None: follow
    the platform), ``step`` of a fresh code required. A change of the value in force signs out the
    users with an approved connection there. Audited. O(users of the organisations touched)."""
    params = {"o": operator_id, "c": customer_id, "on": on, "step": step}
    code = conn.execute(text("SELECT kaagaz_op_set_google_signin(:o, :c, :on, :step)"), params).scalar_one()
    return Outcome(str(code), customer_id)


def schedule_delete(conn: Connection, operator_id: str, customer_id: str, *, name: str, step: int) -> Outcome:
    """Schedule the deletion of one disabled customer whose name is ``name`` exactly
    (``step`` of a fresh code required): removed for good after the grace period (0018)."""
    return _write(
        conn,
        text("SELECT outcome, result_id FROM kaagaz_op_schedule_delete(:o, :c, :step, :name)"),
        {"o": operator_id, "c": customer_id, "step": step, "name": name},
    )


def cancel_delete(conn: Connection, operator_id: str, customer_id: str, *, step: int) -> Outcome:
    """Cancel a scheduled deletion during its grace period (``step`` required): disabled again."""
    return _write(
        conn,
        text("SELECT outcome, result_id FROM kaagaz_op_cancel_delete(:o, :c, :step)"),
        {"o": operator_id, "c": customer_id, "step": step},
    )


def list_admins(conn: Connection, operator_id: str, customer_id: str) -> Sequence[RowMapping]:
    """The admins (role admin only) of one customer. Empty: no such customer."""
    return (
        conn.execute(text("SELECT * FROM kaagaz_op_admins(:o, :c)"), {"o": operator_id, "c": customer_id})
        .mappings()
        .all()
    )


def create_admin(
    conn: Connection,
    operator_id: str,
    customer_id: str,
    *,
    email: str,
    name: str,
    password_hash: str,
    step: int,
) -> Outcome:
    """One more admin of this customer, with a temporary password (``step`` of a fresh code
    required). ``taken``: the e-mail is in use (audited, nothing made)."""
    return _write(
        conn,
        text("SELECT outcome, result_id FROM kaagaz_op_create_admin(:o, :c, :email, :name, :hash, :step)"),
        {
            "o": operator_id,
            "c": customer_id,
            "email": email,
            "name": name,
            "hash": password_hash,
            "step": step,
        },
    )


def change_admin(
    conn: Connection, operator_id: str, customer_id: str, user_id: str, change: Change, *, step: int | None
) -> Outcome:
    """Disable, enable, unlock or reset the two-step code (``step`` required) of one
    admin of this customer. ``missing`` for anyone else, a member included."""
    return _write(
        conn,
        text("SELECT outcome, result_id FROM kaagaz_op_change_admin(:o, :c, :u, :change, :step)"),
        {"o": operator_id, "c": customer_id, "u": user_id, "change": change, "step": step},
    )


def begin_step_up(
    conn: Connection, operator_id: str, *, now: datetime, max_failures: int, lock_end: datetime
) -> tuple[str | None, int | None, int] | None:
    """Count one step-up attempt on the step-up's own count, before the code is checked:
    (sealed secret, last step used, failures including this one). None: locked or disabled."""
    row = conn.execute(
        text("SELECT * FROM kaagaz_op_begin_step_up(:o, :now, :max, :lock_end)"),
        {"o": operator_id, "now": now, "max": max_failures, "lock_end": lock_end},
    ).first()
    return None if row is None else (row[0], None if row[1] is None else int(row[1]), int(row[2]))


def step_up_failed(conn: Connection, operator_id: str, *, locked: bool) -> None:
    """The audit row of a refused step-up code (``operator.step_up_locked`` when it began the lock)."""
    conn.execute(text("SELECT kaagaz_op_step_up_failed(:o, :locked)"), {"o": operator_id, "locked": locked})


def _write(conn: Connection, call: TextClause, params: dict[str, Any]) -> Outcome:
    row = conn.execute(call, params).one()
    return Outcome(str(row[0]), None if row[1] is None else str(row[1]))


def record_move_everyone(
    conn: Connection, operator_id: str, customer_id: str, *, moved: int, attention: int, step: int
) -> Outcome:
    """Use up the fresh code (``step``), then write the audit rows for a Move everyone run (0105)."""
    return _write(
        conn,
        text("SELECT outcome, result_id FROM kaagaz_op_move_everyone(:o, :c, :moved, :attention, :step)"),
        {"o": operator_id, "c": customer_id, "moved": moved, "attention": attention, "step": step},
    )
