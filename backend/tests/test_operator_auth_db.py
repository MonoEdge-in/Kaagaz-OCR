"""The platform operator's sign-in against PostgreSQL (super admin PR 2, 0015). Every
outcome writes exactly one ``platform_audit`` row (``actions``); sessions never cross."""

import base64
import uuid
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.api import totp
from kaagaz.api.auth import OPERATOR_COOKIE
from kaagaz.api.secret_box import SecretBox
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.db.sqlstate import sqlstate
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate
from tests.test_api_support import CSRF, PASSWORD, Api, TestClient, password_hash_of, route_calls, wrong_code

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
OPEN_TO_ALL = {
    ("GET", "/api/health"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/admin/login"),
    ("POST", "/api/auth/admin/enrol"),
    ("POST", "/api/operator/login"),
    ("POST", "/api/operator/enrol"),
}
WRONG = "not the password!!"
_INSERT = "INSERT INTO operators (email, display_name, password_hash) VALUES (:e, 'Ops', :h) RETURNING id"
_ENROLLED = "UPDATE operators SET totp_secret_enc = :s, totp_enabled_at = now() WHERE id = :o"
_DISABLED = "UPDATE operators SET disabled = :d WHERE id = :o"
_UNLOCK = "UPDATE operators SET locked_until = NULL, failed_logins = 0 WHERE id = :o"
_ACTIONS = "SELECT operator_id IS NOT NULL, action FROM platform_audit WHERE target_id = :o ORDER BY id"
FAILED, LOGIN = (False, "operator.login_failed"), (True, "operator.login")


def _owner(db: Database, statement: str, params: dict[str, Any]) -> list[Any]:
    """As the table owner (the tests' DATABASE_URL logs in as the owner, like the CLI)."""
    with db.engine.begin() as conn:
        result = conn.execute(text(statement), params)
        return list(result) if result.returns_rows else []


class _Operator:
    """A new operator; ``enrolled``: two-step set up by SQL, as enrolment leaves it."""

    def __init__(self, api: Api, *, enrolled: bool = True) -> None:
        self.api, self.email, self.secret = api, f"op-{uuid.uuid4().hex[:12]}@example.test", totp.new_secret()
        self.id = str(_owner(api.db, _INSERT, {"e": self.email, "h": password_hash_of(PASSWORD)})[0][0])
        if enrolled:
            sealed = SecretBox(api.settings.totp_encryption_key).seal(self.secret, aad=self.id)
            self.sql(_ENROLLED, s=sealed)

    def sql(self, statement: str, **params: Any) -> list[Any]:
        return _owner(self.api.db, statement, {"o": self.id, **params})

    def login(
        self, client: TestClient, *, password: str = PASSWORD, code: str | None = "now"
    ) -> httpx.Response:
        body = {"email": self.email.upper(), "password": password}
        if code is not None:
            body["code"] = self.api.code(self.secret) if code == "now" else code
        return client.post("/api/operator/login", json=body, headers=CSRF)

    def actions(self) -> list[tuple[bool, str]]:
        """This operator's platform_audit rows: (an operator acted, action)."""
        return [(bool(by), str(action)) for by, action in self.sql(_ACTIONS)]


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
        assert sqlstate(error.value) == "42501", statement  # insufficient privilege


def test_no_session_before_two_step_is_set_up_then_the_first_code_starts_it(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api, enrolled=False)
    client = api.client()

    offer = operator.login(client, code=None)
    assert offer.status_code == 200, offer.text
    assert set(offer.json()) == {"state", "enrol_token", "secret", "otpauth_uri"}
    assert offer.json()["state"] == "enrol_required" and "set-cookie" not in offer.headers
    assert client.get("/api/operator/me").status_code == 401
    secret = base64.b32decode(offer.json()["secret"].replace(" ", ""))

    def enrol(code: str, on: TestClient = client) -> httpx.Response:
        body = {"enrol_token": offer.json()["enrol_token"], "code": code}
        return on.post("/api/operator/enrol", json=body, headers=CSRF)

    _refused(enrol(wrong_code(api, secret)))
    signed = enrol(api.code(secret))
    assert signed.status_code == 200, signed.text
    assert "Path=/api/operator" in signed.headers["set-cookie"]  # all the flags: test_operator_tokens.py
    assert client.get("/api/operator/me").json()["operator"]["id"] == operator.id
    _refused(enrol(api.code(secret), api.client()))  # the token works once
    assert operator.actions() == [FAILED, (True, "operator.enrol"), FAILED]


def test_a_password_and_a_fresh_code_one_401_for_every_failure_and_the_lockout(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    client = api.client()
    failures = [
        operator.login(client, password=WRONG),
        operator.login(client, code=None),
        operator.login(client, code=wrong_code(api, operator.secret)),
    ]
    for reply in failures:
        _refused(reply)
    assert len({reply.text for reply in failures}) == 1
    assert operator.login(client).status_code == 200
    assert client.get("/api/operator/me").status_code == 200
    _refused(operator.login(api.client()))  # the same code again
    for _ in range(api.settings.login_max_failures - 1):  # with the reuse: the limit
        _refused(operator.login(api.client(), password=WRONG))
    api.clock.advance(totp.STEP_SECONDS)
    _refused(operator.login(api.client()))  # locked: even a right password and fresh code, and no row
    assert operator.actions() == [FAILED] * 3 + [LOGIN] + [FAILED] * 4 + [(False, "operator.locked")]
    operator.sql(_UNLOCK)
    assert operator.login(api.client()).status_code == 200


def test_disabling_signing_out_and_30_minutes_end_a_session(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    client = api.client()
    assert operator.login(client).status_code == 200
    copied = {"Cookie": f"{OPERATOR_COOKIE}={client.cookies.get(OPERATOR_COOKIE)}"}

    operator.sql(_DISABLED, d=True)
    assert client.get("/api/operator/me").status_code == 401
    operator.sql(_DISABLED, d=False)
    assert client.get("/api/operator/me").status_code == 200
    assert client.post("/api/operator/logout", headers=CSRF).status_code == 204
    assert api.client().get("/api/operator/me", headers=copied).status_code == 401  # the version went up
    api.clock.advance(totp.STEP_SECONDS)
    assert operator.login(client).status_code == 200
    api.clock.advance(30 * 60)
    assert client.get("/api/operator/me").status_code == 401
    assert operator.actions() == [LOGIN, (True, "operator.logout"), LOGIN]


def test_an_operator_session_opens_no_customer_route(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    client = api.client()
    assert operator.login(client).status_code == 200
    cookie = {**CSRF, "Cookie": f"{OPERATOR_COOKIE}={client.cookies.get(OPERATOR_COOKIE)}"}
    calls = [call for call in route_calls(api.app) if call not in OPEN_TO_ALL and "/operator" not in call[1]]
    assert len(calls) > 20
    for method, path in calls:
        body: dict[str, str] | None = None if method in ("GET", "DELETE") else {}
        reply = api.client().request(method, path, headers=cookie, json=body)
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
