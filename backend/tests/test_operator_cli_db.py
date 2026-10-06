"""The platform operator's command line (super admin, follow-up to PR 2) against PostgreSQL.

create-operator (the password twice, at a hidden prompt, with the users' rules),
reset-operator-2fa and unlock-operator, and the ``platform_audit`` rows that
these and create-customer and disable-customer write (no operator: the CLI).
"""

import io
import uuid
from datetime import timedelta

import pytest

from kaagaz.api.auth import verify_password
from kaagaz.cli import main
from kaagaz.db.engine import Database
from tests.test_api_support import PASSWORD, Api
from tests.test_operator_auth_db import _Operator, _owner

pytestmark = pytest.mark.db

_LOCK = "UPDATE operators SET locked_until = :t, failed_logins = 5 WHERE id = :o"


def _cli(db: Database, *argv: str, passwords: tuple[str, ...] = ()) -> tuple[int, str]:
    answers = iter(passwords)
    out = io.StringIO()
    return main(list(argv), db=db, environ={}, out=out, ask=lambda prompt: next(answers)), out.getvalue()


def test_create_operator_asks_twice_with_the_users_rules_and_is_audited(db: Database) -> None:
    email = f"op-{uuid.uuid4().hex[:12]}@example.test"
    command = ("create-operator", "--email", email.upper(), "--name", "Ops")
    assert _cli(db, *command, passwords=(PASSWORD, PASSWORD + "x"))[0] == 2  # they do not match
    assert _cli(db, *command, passwords=("short", "short"))[0] == 2
    assert main(list(command), db=db, environ={"KAAGAZ_ADMIN_PASSWORD": PASSWORD}, ask=None) == 2  # no prompt
    assert _owner(db, "SELECT count(*) FROM operators WHERE email = :e", {"e": email}) == [(0,)]

    code, out = _cli(db, *command, passwords=(PASSWORD, PASSWORD))
    assert code == 0 and PASSWORD not in out
    operator_id = out.split()[1].rstrip(":")
    assert _cli(db, *command, passwords=(PASSWORD, PASSWORD))[0] == 2  # taken
    [(stored, enrolled)] = _owner(
        db, "SELECT password_hash, totp_enabled_at FROM operators WHERE id = :o", {"o": operator_id}
    )
    assert verify_password(PASSWORD, stored) and enrolled is None
    rows = _owner(
        db, "SELECT operator_id, action FROM platform_audit WHERE target_id = :o", {"o": operator_id}
    )
    assert rows == [(None, "operator.create")]


def test_reset_operator_2fa_ends_every_session_and_asks_to_set_it_up_again(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    client = api.client()
    assert operator.login(client).status_code == 200

    code, out = _cli(db, "reset-operator-2fa", "--email", operator.email)
    assert (code, out.startswith(f"operator {operator.id}: ")) == (0, True)
    assert client.get("/api/operator/me").status_code == 401
    assert operator.login(client, code=None).json()["state"] == "enrol_required"
    assert operator.actions()[-1] == (False, "operator.reset_totp")
    assert _cli(db, "reset-operator-2fa", "--email", "nobody@example.test")[0] == 2


def test_unlock_operator_ends_the_lock(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    operator.sql(_LOCK, t=api.clock() + timedelta(minutes=15))
    assert operator.login(api.client()).status_code == 401
    assert _cli(db, "unlock-operator", "--email", operator.email) == (
        0,
        f"operator {operator.id}: unlocked\n",
    )
    assert operator.login(api.client()).status_code == 200
    assert operator.actions() == [(False, "operator.unlock"), (True, "operator.login")]
    assert _cli(db, "unlock-operator", "--email", "nobody@example.test")[0] == 2


def test_create_customer_and_disable_customer_write_platform_audit(db: Database) -> None:
    out = io.StringIO()
    argv = ["create-customer", "--name", "Gupta Stores", "--admin-name", "Owner"]
    argv += ["--admin-email", f"owner-{uuid.uuid4().hex[:8]}@example.test"]
    assert main(argv, db=db, environ={"KAAGAZ_ADMIN_PASSWORD": PASSWORD}, out=out, ask=None) == 0
    customer = out.getvalue().split()[1]
    assert _cli(db, "disable-customer", "--customer-id", customer)[0] == 0
    rows = _owner(
        db,
        "SELECT operator_id, action, subject_customer_id::text, target_type, target_id FROM platform_audit"
        " WHERE subject_customer_id = :c ORDER BY id",
        {"c": customer},
    )
    assert rows == [
        (None, "customer.create", customer, "customer", customer),
        (None, "customer.disable", customer, "customer", customer),
    ]
