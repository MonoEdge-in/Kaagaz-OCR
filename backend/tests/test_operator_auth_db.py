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
from tests.test_api_support import (
    CSRF,
    EMAIL_ROUTES,
    OIDC_ROUTES,
    PASSWORD,
    Api,
    TestClient,
    password_hash_of,
    route_calls,
    wrong_code,
)
from tests.test_operator_activity import ACTIVITY_ROUTES
from tests.test_operator_insights import INSIGHT_ROUTES
from tests.test_operator_orgs import ORG_ROUTES

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
# Changed on purpose (issue 378, 27 Oct 2026): GET /auth/session answers anyone, signed in
# or not (test_api_auth.py checks it says signed_in false to an operator's cookie).
OPEN_TO_ALL = {
    ("GET", "/api/health"),
    ("GET", "/api/auth/session"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/admin/login"),
    ("POST", "/api/auth/admin/enrol"),
    ("POST", "/api/operator/login"),
    # Changed on purpose (#239, 27 Oct 2026): sign-up from the sign-in page (16 Oct, 8205163)
    # is for people with no session, as tests/test_api_sessions.py lists it.
    ("GET", "/api/auth/signup"),
    ("POST", "/api/auth/signup"),
}
# Changed on purpose (#457): 404 while email is off, whoever asks (tests/test_api_email.py).
# Changed on purpose (#457 phase 1): the provider sign-in's too, while no provider is on.
EMAIL_OFF = set(EMAIL_ROUTES) | set(OIDC_ROUTES)
WRONG = "not the password!!"
_INSERT = "INSERT INTO operators (email, display_name, password_hash) VALUES (:e, 'Ops', :h) RETURNING id"
# As the CLI issues a secret (pending) and as the first valid code leaves it (confirmed).
_ISSUED = (
    "UPDATE operators SET totp_secret_enc = :s, totp_enabled_at = CASE WHEN :c THEN now() END WHERE id = :o"
)
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
    """A new operator with a two-step secret: ``confirmed`` (a code was already accepted)
    or pending (as the CLI leaves it); ``secret=False``: none issued at all."""

    def __init__(self, api: Api, *, confirmed: bool = True, secret: bool = True) -> None:
        self.api, self.email, self.secret = api, f"op-{uuid.uuid4().hex[:12]}@example.test", totp.new_secret()
        self.id = str(_owner(api.db, _INSERT, {"e": self.email, "h": password_hash_of(PASSWORD)})[0][0])
        if secret:
            sealed = SecretBox(api.settings.totp_encryption_key).seal(self.secret, aad=self.id)
            self.sql(_ISSUED, s=sealed, c=confirmed)

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


def test_no_session_before_the_first_code_from_the_server_issued_secret(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api, confirmed=False)
    client = api.client()
    _refused(operator.login(client, code=None))  # the password alone never signs in or enrols
    _refused(operator.login(client, code=wrong_code(api, operator.secret)))
    assert client.get("/api/operator/me").status_code == 401

    signed = operator.login(client)  # the first valid code confirms the secret and signs in
    assert signed.status_code == 200, signed.text
    assert "Path=/api/operator" in signed.headers["set-cookie"]  # all the flags: test_operator_tokens.py
    assert client.get("/api/operator/me").json()["operator"]["id"] == operator.id
    _refused(operator.login(api.client()))  # the same code again
    api.clock.advance(totp.STEP_SECONDS)
    assert operator.login(api.client()).status_code == 200
    assert operator.actions() == [FAILED, FAILED, (True, "operator.enrolled"), FAILED, LOGIN]

    unissued = _Operator(api, secret=False)
    _refused(unissued.login(api.client()))  # no secret issued: no way in
    assert unissued.actions() == [FAILED]


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
    # Changed on purpose (A2, 7 Nov 2026): the lock is this address's, so another address signs
    # in; an unlock (the CLI's) does not reach the API's memory, so this one waits it out.
    assert operator.login(TestClient(api.app, client=("198.51.100.9", 50000))).status_code == 200
    operator.sql(_UNLOCK)
    api.clock.advance(api.settings.login_lock_minutes * 60)
    assert operator.login(api.client()).status_code == 200


def test_a_flood_from_one_address_never_locks_the_operator_out_of_their_own(db: Database) -> None:
    """A2: as an organisation's users (tests/test_api_lockout.py), with the same caps."""
    api = Api(db)
    operator = _Operator(api)
    attacker = TestClient(api.app, client=("203.0.113.7", 50000))
    for _ in range(105):
        _refused(operator.login(attacker, password=WRONG))
    _refused(operator.login(attacker))
    assert operator.actions() == [FAILED] * 4 + [(False, "operator.locked")]  # the fifth locks
    assert operator.login(TestClient(api.app, client=("198.51.100.9", 50000))).status_code == 200


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
    calls = [
        call
        for call in route_calls(api.app)
        if call not in OPEN_TO_ALL | EMAIL_OFF and "/operator" not in call[1]
    ]
    assert len(calls) > 20
    for method, path in calls:
        body: dict[str, str] | None = None if method in ("GET", "DELETE") else {}
        reply = api.client().request(method, path, headers=cookie, json=body)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), (method, path)


def test_a_customer_admin_session_with_two_step_opens_no_operator_route(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    calls = [call for call in route_calls(api.app, "/api/operator") if call not in OPEN_TO_ALL]
    # Every signed-in operator route, PR 3's organisation, PR 5's activity and the insights routes included.
    assert {
        ("GET", "/api/operator/me"),
        ("POST", "/api/operator/logout"),
    } | ORG_ROUTES | ACTIVITY_ROUTES | INSIGHT_ROUTES <= set(calls)
    for method, path in calls:
        reply = admin.client.request(method, path, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), (method, path)
    assert admin.totp_secret is not None
    api.clock.advance(totp.STEP_SECONDS)
    body = {"email": admin.email, "password": PASSWORD, "code": api.code(admin.totp_secret)}
    _refused(admin.client.post("/api/operator/login", json=body, headers=CSRF))  # not an operator


def test_moving_two_step_replaces_the_secret_once_and_signs_out_other_browsers(db: Database) -> None:
    """0031 against PostgreSQL: start uses up the current code, confirm swaps the secret."""
    api = Api(db)
    operator = _Operator(api)
    here, elsewhere = api.client(), api.client()
    assert operator.login(here).status_code == 200
    api.clock.advance(totp.STEP_SECONDS)
    assert operator.login(elsewhere).status_code == 200
    api.clock.advance(totp.STEP_SECONDS)
    start = {"password": PASSWORD, "code": api.code(operator.secret)}
    offer = here.post("/api/operator/2fa/move/start", json=start, headers=CSRF)
    assert offer.status_code == 200, offer.text
    assert here.post("/api/operator/2fa/move/start", json=start, headers=CSRF).status_code == 403  # used
    key = offer.json()["secret"].replace(" ", "")
    new = base64.b32decode(key + "=" * (-len(key) % 8))
    api.clock.advance(totp.STEP_SECONDS)
    confirm = {"move_token": offer.json()["move_token"], "code": api.code(new)}
    assert here.post("/api/operator/2fa/move/confirm", json=confirm, headers=CSRF).status_code == 200
    assert here.post("/api/operator/2fa/move/confirm", json=confirm, headers=CSRF).status_code == 403  # once
    assert (here.get("/api/operator/me").status_code, elsewhere.get("/api/operator/me").status_code) == (
        200,
        401,
    )
    api.clock.advance(totp.STEP_SECONDS)
    _refused(operator.login(api.client()))  # the old device
    operator.secret = new
    assert operator.login(api.client()).status_code == 200
    assert operator.actions() == [LOGIN, LOGIN, FAILED, (True, "operator.totp_moved"), FAILED, LOGIN]
