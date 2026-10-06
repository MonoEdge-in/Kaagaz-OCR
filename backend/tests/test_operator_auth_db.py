"""The platform operator's sign-in against PostgreSQL (super admin PR 2, 0015).

* 0015 up, down (0014's schema exactly) and up again; neither the app role nor
  ``kaagaz_operator`` can read the operator tables or run the other's functions.
* Two-step sign-in is mandatory: no session before it is set up; the password
  and a fresh code; one 401 for every failure; a code works once; the lockout.
* A disabled operator, sign-out (it raises the session version) and the 30
  minutes end a session. Every outcome writes exactly one ``platform_audit`` row.
* Sessions never cross: an operator cookie opens no customer route, and a
  customer admin session (with two-step) opens no operator route.
* The CLI: create-operator (password twice, privately), reset-operator-2fa,
  unlock-operator, and create-customer's ``platform_audit`` row.
"""

import base64
import io
import uuid
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.api import totp
from kaagaz.api.auth import OPERATOR_COOKIE, verify_password
from kaagaz.api.secret_box import SecretBox
from kaagaz.cli import main
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.db.sqlstate import sqlstate
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate
from tests.test_api_support import CSRF, PASSWORD, Api, TestClient, route_calls, wrong_code

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
INSUFFICIENT_PRIVILEGE = "42501"
OPEN_TO_ALL = {
    ("GET", "/api/health"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/admin/login"),
    ("POST", "/api/auth/admin/enrol"),
    ("POST", "/api/operator/login"),
    ("POST", "/api/operator/enrol"),
}


def _owner(db: Database, statement: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    """As the table owner, like the CLI (the tests' DATABASE_URL logs in as the owner)."""
    with db.engine.begin() as conn:
        result = conn.execute(text(statement), params or {})
        return [tuple(row) for row in result] if result.returns_rows else []


def _cli(db: Database, *argv: str, passwords: tuple[str, ...] = ()) -> tuple[int, str]:
    answers = iter(passwords)
    out = io.StringIO()
    return main(list(argv), db=db, environ={}, out=out, ask=lambda prompt: next(answers)), out.getvalue()


class _Operator:
    """An operator made by the CLI; ``enrolled``: two-step set up by SQL, as enrolment leaves it."""

    def __init__(self, api: Api, *, enrolled: bool = True) -> None:
        self.api, self.email = api, f"op-{uuid.uuid4().hex[:12]}@example.test"
        code, out = _cli(
            api.db, "create-operator", "--email", self.email, "--name", "Ops", passwords=(PASSWORD,) * 2
        )
        assert code == 0 and PASSWORD not in out
        self.id = out.split()[1].rstrip(":")
        self.secret = totp.new_secret()
        if enrolled:
            sealed = SecretBox(api.settings.totp_encryption_key).seal(self.secret, aad=self.id)
            _owner(
                api.db,
                "UPDATE operators SET totp_secret_enc = :s, totp_enabled_at = now() WHERE id = :o",
                {"s": sealed, "o": self.id},
            )

    def login(
        self, client: TestClient, *, password: str = PASSWORD, code: str | None = "now"
    ) -> httpx.Response:
        body = {"email": self.email.upper(), "password": password}
        if code is not None:
            body["code"] = self.api.code(self.secret) if code == "now" else code
        return client.post("/api/operator/login", json=body, headers=CSRF)

    def actions(self) -> list[Any]:
        """This operator's platform_audit rows: (whether an operator acted, action)."""
        rows = _owner(
            self.api.db,
            "SELECT operator_id IS NOT NULL, action FROM platform_audit WHERE target_id = :o ORDER BY id",
            {"o": self.id},
        )
        return [row[1] if not row[0] else ("by_operator", row[1]) for row in rows]


def _refused(reply: httpx.Response) -> None:
    assert (reply.status_code, reply.json()["error"]["code"]) == (401, "bad_credentials"), reply.text
    assert "set-cookie" not in reply.headers


def test_0015_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0014")
    at_0014 = m0002._schema(database)
    migrate(url, "head")
    at_head = m0002._schema(database)
    assert {"operators", "platform_audit"} <= m0002._names(at_head, "relations")
    m0002._downgrade(url, "0014")
    assert m0002._schema(database) == at_0014
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_neither_role_reaches_the_operator_tables_or_the_other_roles_functions(db: Database) -> None:
    as_operator = OperatorDatabase(db.engine)
    refused = [
        (db.system, "SELECT count(*) FROM operators"),
        (db.system, "SELECT count(*) FROM platform_audit"),
        (db.system, "SELECT * FROM kaagaz_op_session(gen_random_uuid())"),
        (as_operator.operator, "SELECT count(*) FROM operators"),
        (as_operator.operator, "INSERT INTO platform_audit (action) VALUES ('operator.login')"),
        (as_operator.operator, "SELECT count(*) FROM customers"),
        (as_operator.operator, "SELECT count(*) FROM documents"),
        (as_operator.operator, "SELECT * FROM kaagaz_login_lookup('someone')"),
        (as_operator.operator, "SELECT * FROM kaagaz_customer_ids()"),
    ]
    for begin, statement in refused:
        with pytest.raises(DBAPIError) as error, begin() as conn:
            conn.execute(text(statement))
        assert sqlstate(error.value) == INSUFFICIENT_PRIVILEGE, statement


def test_no_session_before_two_step_is_set_up_then_the_first_code_starts_it(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api, enrolled=False)
    client = api.client()

    offer = operator.login(client, code=None)
    assert offer.status_code == 200, offer.text
    assert set(offer.json()) == {"state", "enrol_token", "secret", "otpauth_uri"}
    assert offer.json()["state"] == "enrol_required"
    assert "set-cookie" not in offer.headers
    assert client.get("/api/operator/me").status_code == 401
    secret = base64.b32decode(offer.json()["secret"].replace(" ", ""))
    token = offer.json()["enrol_token"]

    def enrol(code: str, on: TestClient = client) -> httpx.Response:
        return on.post("/api/operator/enrol", json={"enrol_token": token, "code": code}, headers=CSRF)

    _refused(enrol(wrong_code(api, secret)))
    signed = enrol(api.code(secret))
    assert signed.status_code == 200, signed.text
    assert signed.json()["operator"]["id"] == operator.id
    cookie = signed.headers["set-cookie"]
    for flag in ("kaagaz_operator=", "HttpOnly", "Max-Age=1800", "Path=/api/operator", "SameSite=strict"):
        assert flag in cookie
    assert client.get("/api/operator/me").json()["operator"]["email"] == operator.email
    _refused(enrol(api.code(secret), api.client()))  # the token works once
    assert operator.actions() == [
        "operator.create",
        "operator.login_failed",
        ("by_operator", "operator.enrol"),
        "operator.login_failed",
    ]


def test_sign_in_needs_the_password_and_a_fresh_code_and_every_failure_looks_the_same(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    client = api.client()

    failures = [
        operator.login(client, password="not the password!!"),
        operator.login(client, code=None),
        operator.login(client, code=wrong_code(api, operator.secret)),
    ]
    for reply in failures:
        _refused(reply)
    assert len({reply.text for reply in failures}) == 1
    signed = operator.login(client)
    assert signed.status_code == 200, signed.text
    assert client.get("/api/operator/me").status_code == 200
    _refused(operator.login(api.client()))  # the same code again
    assert operator.actions() == [
        "operator.create",
        *["operator.login_failed"] * 3,
        ("by_operator", "operator.login"),
        "operator.login_failed",
    ]


def test_the_lockout_and_unlock_operator(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    for _ in range(api.settings.login_max_failures):
        _refused(operator.login(api.client(), password="not the password!!"))
    _refused(operator.login(api.client()))  # locked: even the right password and code
    assert _cli(db, "unlock-operator", "--email", operator.email) == (
        0,
        f"operator {operator.id}: unlocked\n",
    )
    assert operator.login(api.client()).status_code == 200
    assert operator.actions() == [
        "operator.create",
        *["operator.login_failed"] * (api.settings.login_max_failures - 1),
        "operator.locked",
        "operator.unlock",
        ("by_operator", "operator.login"),
    ]


def test_disabling_signing_out_and_30_minutes_end_a_session(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    client = api.client()
    assert operator.login(client).status_code == 200
    token = client.cookies.get(OPERATOR_COOKIE)
    copied = {"Cookie": f"{OPERATOR_COOKIE}={token}"}

    _owner(db, "UPDATE operators SET disabled = true WHERE id = :o", {"o": operator.id})
    assert client.get("/api/operator/me").status_code == 401
    _owner(db, "UPDATE operators SET disabled = false WHERE id = :o", {"o": operator.id})
    assert client.get("/api/operator/me").status_code == 200

    assert client.post("/api/operator/logout", headers=CSRF).status_code == 204
    assert api.client().get("/api/operator/me", headers=copied).status_code == 401  # the version went up
    api.clock.advance(totp.STEP_SECONDS)
    assert operator.login(client).status_code == 200
    api.clock.advance(30 * 60)
    assert client.get("/api/operator/me").status_code == 401
    assert operator.actions()[-3:] == [
        ("by_operator", "operator.login"),
        ("by_operator", "operator.logout"),
        ("by_operator", "operator.login"),
    ]


def test_an_operator_session_opens_no_customer_route(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    client = api.client()
    assert operator.login(client).status_code == 200
    cookie = {**CSRF, "Cookie": f"{OPERATOR_COOKIE}={client.cookies.get(OPERATOR_COOKIE)}"}
    calls = [
        call
        for call in route_calls(api.app)
        if call not in OPEN_TO_ALL and not call[1].startswith("/api/operator")
    ]
    assert len(calls) > 20
    other = api.client()
    for method, path in calls:
        body: dict[str, str] | None = None if method in ("GET", "DELETE") else {}
        reply = other.request(method, path, headers=cookie, json=body)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), (method, path)


def test_a_customer_admin_session_with_two_step_opens_no_operator_route(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    calls = [call for call in route_calls(api.app, "/api/operator") if call not in OPEN_TO_ALL]
    assert {("GET", "/api/operator/me"), ("POST", "/api/operator/logout")} <= set(calls)
    for method, path in calls:
        reply = admin.client.request(method, path, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), (method, path)
    assert admin.totp_secret is not None
    api.clock.advance(totp.STEP_SECONDS)
    body = {"email": admin.email, "password": PASSWORD, "code": api.code(admin.totp_secret)}
    _refused(admin.client.post("/api/operator/login", json=body, headers=CSRF))  # not an operator


def test_reset_operator_2fa_ends_every_session_and_asks_to_set_it_up_again(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    client = api.client()
    assert operator.login(client).status_code == 200

    code, out = _cli(db, "reset-operator-2fa", "--email", operator.email)
    assert (code, out.startswith(f"operator {operator.id}: ")) == (0, True)
    assert client.get("/api/operator/me").status_code == 401
    assert operator.login(client, code=None).json()["state"] == "enrol_required"
    assert operator.actions()[-1] == "operator.reset_totp"
    assert _cli(db, "reset-operator-2fa", "--email", "nobody@example.test")[0] == 2
    assert _cli(db, "unlock-operator", "--email", "nobody@example.test")[0] == 2


def test_create_operator_asks_twice_with_the_users_rules(db: Database) -> None:
    email = f"op-{uuid.uuid4().hex[:12]}@example.test"
    command = ("create-operator", "--email", email, "--name", "Ops")
    assert _cli(db, *command, passwords=(PASSWORD, PASSWORD + "x"))[0] == 2  # do not match
    assert _cli(db, *command, passwords=("short", "short"))[0] == 2
    assert _owner(db, "SELECT count(*) FROM operators WHERE email = :e", {"e": email}) == [(0,)]
    assert _cli(db, *command, passwords=(PASSWORD, PASSWORD))[0] == 0
    assert _cli(db, *command, passwords=(PASSWORD, PASSWORD))[0] == 2  # taken
    [(stored,)] = _owner(db, "SELECT password_hash FROM operators WHERE email = :e", {"e": email})
    assert verify_password(PASSWORD, stored)


def test_create_customer_and_disable_customer_write_platform_audit(db: Database) -> None:
    email = f"owner-{uuid.uuid4().hex[:8]}@example.test"
    out = io.StringIO()
    argv = ["create-customer", "--name", "Gupta Stores", "--admin-email", email, "--admin-name", "Owner"]
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
