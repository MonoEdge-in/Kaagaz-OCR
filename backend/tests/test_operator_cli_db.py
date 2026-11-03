"""The platform operator's command line (super admin PR 2b).

create-operator (the password twice, at a hidden prompt, with the users' rules)
and reset-operator-2fa issue the two-step secret ON THE SERVER and print it once;
the first valid code at sign-in confirms it. unlock-operator. Neither secret
command runs without a terminal. Each writes one ``platform_audit`` row (no
operator), as do create-customer and disable-customer.
"""

import base64
import getpass
import io
import uuid
from datetime import timedelta

import pytest

from kaagaz.api.auth import verify_password
from kaagaz.cli import main
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PASSWORD, Api
from tests.test_operator_auth_db import _ACTIONS, FAILED, LOGIN, _Operator, _owner, _refused

_LOCK = "UPDATE operators SET locked_until = :t, failed_logins = 5 WHERE id = :o"
ENROLLED = (True, "operator.enrolled")


def _cli(db: Database, *argv: str, passwords: tuple[str, ...] = ()) -> tuple[int, str]:
    answers = iter(passwords)

    def ask(prompt: str) -> str:
        return next(answers)

    out = io.StringIO()
    return main(list(argv), db=db, environ={}, out=out, ask=ask, settings=make_settings()), out.getvalue()


def _shown_secret(out: str) -> bytes:
    """The secret from the printed key line, as an authenticator app reads it."""
    line = next(line for line in out.splitlines() if line.startswith("two-step key (shown once): "))
    return base64.b32decode(line.split(": ", 1)[1].replace(" ", ""))


@pytest.mark.parametrize("command", ["create-operator", "reset-operator-2fa"])
def test_the_secret_commands_refuse_to_run_without_a_terminal(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # a pipe, not a terminal
    argv = [command, "--email", "op@example.test"] + (
        ["--name", "Ops"] if command == "create-operator" else []
    )
    out = io.StringIO()
    assert main(argv, environ={}, out=out, ask=getpass.getpass) == 2  # before any database or prompt
    # Changed on purpose (#479, 3 Nov 2026): the owner's commands run in migrate, not api.
    assert out.getvalue() == "" and "run --rm migrate, WITHOUT -T" in capsys.readouterr().err


@pytest.mark.db
def test_create_operator_issues_a_pending_secret_and_its_first_code_signs_in(db: Database) -> None:
    email = f"op-{uuid.uuid4().hex[:12]}@example.test"
    command = ("create-operator", "--email", email.upper(), "--name", "Ops")
    assert _cli(db, *command, passwords=(PASSWORD, PASSWORD + "x"))[0] == 2  # they do not match
    assert _cli(db, *command, passwords=("short", "short"))[0] == 2
    assert main(list(command), db=db, environ={"KAAGAZ_ADMIN_PASSWORD": PASSWORD}, ask=None) == 2  # no prompt
    assert _owner(db, "SELECT count(*) FROM operators WHERE email = :e", {"e": email}) == [(0,)]

    code, out = _cli(db, *command, passwords=(PASSWORD, PASSWORD))
    assert code == 0 and PASSWORD not in out and "otpauth://totp/" in out
    operator_id = out.split()[1].rstrip(":")
    assert _cli(db, *command, passwords=(PASSWORD, PASSWORD))[0] == 2  # taken
    [(stored, sealed, confirmed)] = _owner(
        db,
        "SELECT password_hash, totp_secret_enc, totp_enabled_at FROM operators WHERE id = :o",
        {"o": operator_id},
    )
    assert verify_password(PASSWORD, stored) and sealed.startswith("v1:") and confirmed is None

    api = Api(db)
    body = {"email": email, "password": PASSWORD}
    _refused(api.client().post("/api/operator/login", json=body, headers=CSRF))  # the password alone
    body["code"] = api.code(_shown_secret(out))
    assert api.client().post("/api/operator/login", json=body, headers=CSRF).status_code == 200
    actions = [(bool(by), str(action)) for by, action in _owner(db, _ACTIONS, {"o": operator_id})]
    assert actions == [(False, "operator.create"), FAILED, ENROLLED]


@pytest.mark.db
def test_reset_operator_2fa_issues_a_new_secret_and_the_old_one_stops_working(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    client = api.client()
    assert operator.login(client).status_code == 200

    code, out = _cli(db, "reset-operator-2fa", "--email", operator.email)
    assert (code, out.startswith(f"operator {operator.id}: ")) == (0, True)
    assert client.get("/api/operator/me").status_code == 401  # every session ended
    api.clock.advance(30)
    _refused(operator.login(api.client()))  # the old authenticator
    operator.secret = _shown_secret(out)
    assert operator.login(api.client()).status_code == 200
    assert operator.actions() == [LOGIN, (False, "operator.reset_totp"), FAILED, ENROLLED]
    assert _cli(db, "reset-operator-2fa", "--email", "nobody@example.test")[0] == 2


@pytest.mark.db
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
    assert operator.actions() == [(False, "operator.unlock"), LOGIN]
    assert _cli(db, "unlock-operator", "--email", "nobody@example.test")[0] == 2


@pytest.mark.db
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
