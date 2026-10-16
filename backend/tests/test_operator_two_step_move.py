"""Moving the operator's two-step to a new device (0031) without a database: the routes
against an in-memory operator that does what the 0015 and 0031 functions do. Start needs
the password AND the current code (counted like a sign-in); confirm needs a code from the
new secret; the old device works until then and never after; every other browser is
signed out; a bad token changes nothing; no route turns two-step off."""

import base64
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
from fastapi import FastAPI
from sqlalchemy import Connection

from kaagaz.api import auth, totp
from kaagaz.api.secret_box import SecretBox
from kaagaz.db import operators as operators_sql
from kaagaz.db.operators import OperatorAttempt
from tests.test_api_auth import _offline_client
from tests.test_api_support import CSRF, NOW, PASSWORD, password_hash_of, route_calls
from tests.test_operator_orgs import _NoDatabase

OID, CID = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
UID = "44444444-4444-4444-8444-444444444444"  # a customer admin
EMAIL = "ops@example.test"
START, CONFIRM = "/api/operator/2fa/move/start", "/api/operator/2fa/move/confirm"


class _Operator:
    """One operator as the database functions keep it, and the app in front of it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = NOW
        self.client = _offline_client()
        app = cast(FastAPI, self.client.app)
        app.state.kaagaz = replace(app.state.kaagaz, clock=lambda: self.now)  # a clock that moves
        app.state.kaagaz_operator = _NoDatabase()
        self.settings = app.state.kaagaz.settings
        self.box = SecretBox(self.settings.totp_encryption_key)
        self.old = totp.new_secret()  # the device in the operator's hand today
        self.sealed = self.box.seal(self.old, aad=OID)
        self.last_step: int | None = None
        self.failures, self.locked_until, self.version = 0, cast(datetime | None, None), 2
        self.audit: list[str] = []
        self.moves: list[dict[str, Any]] = []
        for name in ("begin_login", "login_failed", "login_succeeded", "session_operator", "move_check"):
            monkeypatch.setattr(operators_sql, name, getattr(self, name))
        monkeypatch.setattr(operators_sql, "move_totp", self.move_totp)

    # --- what the functions do (0015, 0031) -----------------------------------

    def begin_login(
        self, conn: Connection, email: str, now: datetime, max_failures: int, lock_end: datetime
    ) -> OperatorAttempt | None:
        if email.lower() != EMAIL or (self.locked_until is not None and self.locked_until > now):
            return None
        self.failures = 1 if self.locked_until is not None else self.failures + 1
        self.locked_until = lock_end if self.failures >= max_failures else None
        return OperatorAttempt(
            operator_id=OID,
            email=EMAIL,
            display_name="Ops",
            password_hash=password_hash_of(PASSWORD),
            disabled=False,
            totp_secret_enc=self.sealed,
            totp_last_step=self.last_step,
            failed_logins=self.failures,
        )

    def login_failed(self, conn: Connection, operator_id: str, *, locked: bool) -> None:
        self.audit.append("operator.locked" if locked else "operator.login_failed")

    def _use(self, step: int) -> bool:
        if self.last_step is not None and step <= self.last_step:
            return False
        self.last_step, self.failures, self.locked_until = step, 0, None
        return True

    def login_succeeded(self, conn: Connection, operator_id: str, step: int, now: datetime) -> int | None:
        if not self._use(step):
            return None
        self.audit.append("operator.login")
        return self.version

    def session_operator(self, conn: Connection, operator_id: str) -> tuple[str, str, int] | None:
        return (EMAIL, "Ops", self.version) if operator_id == OID else None

    def move_check(self, conn: Connection, operator_id: str, step: int) -> bool:
        return self._use(step)

    def move_totp(
        self, conn: Connection, operator_id: str, *, version: int, sealed: str, step: int, now: datetime
    ) -> int | None:
        self.moves.append({"sealed": sealed, "step": step})
        if version != self.version or not sealed:
            return None
        self.sealed, self.last_step, self.version = sealed, step, self.version + 1
        self.audit.append("operator.totp_moved")
        return self.version

    # --- the browser's side -----------------------------------------------------

    def browser(self) -> str:
        """A cookie for a session started now (another browser, or this one)."""
        claims = auth.OperatorClaims(
            operator_id=OID, session_version=self.version, expires_at=self.at() + 1800
        )
        return auth.sign_operator(claims, self.settings.session_secret)

    def at(self) -> int:
        return int(self.now.timestamp())

    def code(self, secret: bytes) -> str:
        return totp.code_at(secret, self.at() // totp.STEP_SECONDS)

    def tick(self) -> None:
        """The next 30-second code."""
        self.now += timedelta(seconds=totp.STEP_SECONDS)

    def post(self, path: str, body: dict[str, Any], cookie: str | None = None) -> Any:
        self.client.cookies.clear()
        headers = {**CSRF, "Cookie": f"{auth.OPERATOR_COOKIE}={cookie}"} if cookie else CSRF
        return self.client.post(path, json=body, headers=headers)

    def me(self, cookie: str) -> int:
        self.client.cookies.clear()
        cookies = {"Cookie": f"{auth.OPERATOR_COOKIE}={cookie}"}
        return self.client.get("/api/operator/me", headers=cookies).status_code

    def login(self, secret: bytes) -> int:
        body = {"email": EMAIL, "password": PASSWORD, "code": self.code(secret)}
        return int(self.post("/api/operator/login", body).status_code)

    def start(self, cookie: str, *, password: str = PASSWORD, code: str | None = None) -> Any:
        return self.post(START, {"password": password, "code": code or self.code(self.old)}, cookie)


@pytest.fixture
def op(monkeypatch: pytest.MonkeyPatch) -> _Operator:
    return _Operator(monkeypatch)


def _refused(reply: Any) -> None:
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "move_refused"), reply.text
    assert "set-cookie" not in reply.headers


def _b32(offer: dict[str, str]) -> bytes:
    """The new secret, as the operator types the shown key into the new device."""
    text = offer["secret"].replace(" ", "")
    return base64.b32decode(text + "=" * (-len(text) % 8))


def test_start_needs_the_right_password_and_the_current_code_each_refusal_counted(op: _Operator) -> None:
    cookie = op.browser()
    wrong = next(c for c in ("000000", "111111", "222222") if c != op.code(op.old))
    _refused(op.start(cookie, password="not the password!!"))
    _refused(op.start(cookie, code=wrong))
    _refused(op.start(cookie, code=op.code(totp.new_secret())))  # a code from some other secret
    assert op.audit == ["operator.login_failed"] * 3 and op.failures == 3
    assert op.post(START, {"password": PASSWORD}, cookie).status_code == 400  # no code: nothing counted
    reply = op.start(cookie)
    assert reply.status_code == 200, reply.text
    offer = reply.json()
    assert set(offer) == {"move_token", "secret", "otpauth_uri"}
    assert offer["otpauth_uri"].startswith("otpauth://totp/Kaagaz:ops%40example.test?secret=")
    assert offer["otpauth_uri"].split("secret=")[1].split("&")[0] == offer["secret"].replace(" ", "")
    assert op.failures == 0  # the right password and code clear the count
    assert op.box.open(op.sealed, aad=OID) == op.old and op.version == 2 and op.moves == []  # nothing yet
    _refused(op.start(cookie))  # the same code works once
    assert op.me(cookie) == 200  # the console stays open after a refusal


def test_start_respects_the_sign_in_lockout(op: _Operator) -> None:
    cookie = op.browser()
    for _ in range(op.settings.login_max_failures):
        _refused(op.start(cookie, password="not the password!!"))
    assert op.audit[-1] == "operator.locked"
    count = len(op.audit)
    _refused(op.start(cookie))  # locked: the right password and code are refused too, no new row
    assert len(op.audit) == count
    assert op.login(op.old) == 401  # and the sign-in is locked as well


def test_the_old_device_works_until_the_new_code_confirms_then_only_the_new_one(op: _Operator) -> None:
    here, elsewhere = op.browser(), op.browser()
    offer = op.start(here).json()
    new = _b32(offer)
    op.tick()
    assert op.login(op.old) == 200  # not confirmed yet: the old device still signs in
    op.tick()
    _refused(op.post(CONFIRM, {"move_token": offer["move_token"], "code": op.code(op.old)}, here))
    assert op.box.open(op.sealed, aad=OID) == op.old and op.version == 2
    reply = op.post(CONFIRM, {"move_token": offer["move_token"], "code": op.code(new)}, here)
    assert reply.status_code == 200, reply.text
    assert reply.json()["operator"] == {"id": OID, "email": EMAIL, "name": "Ops"}
    resigned = reply.cookies.get(auth.OPERATOR_COOKIE)
    claims = auth.read_operator(resigned, op.settings.session_secret, op.at())
    assert claims is not None and claims.session_version == op.version == 3
    assert op.audit[-1] == "operator.totp_moved"
    assert op.box.open(op.sealed, aad=OID) == new
    assert (op.me(here), op.me(elsewhere), op.me(resigned)) == (401, 401, 200)  # other browsers signed out
    op.tick()
    assert op.login(op.old) == 401  # the old device stops working
    assert op.login(new) == 200


def test_a_tampered_expired_foreign_or_used_token_changes_nothing(op: _Operator) -> None:
    cookie = op.browser()
    token = op.start(cookie).json()["move_token"]
    claims = auth.read_move(token, op.settings.session_secret, op.at())
    assert claims is not None
    new = op.box.open(claims.sealed_secret, aad=OID)
    secret_key = op.settings.session_secret

    def confirm(move_token: str, cookie: str = cookie) -> Any:
        return op.post(CONFIRM, {"move_token": move_token, "code": op.code(new)}, cookie)

    body, mac = token.split(".")
    _refused(confirm(body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + mac))  # tampered
    _refused(confirm(cookie))  # a session token is not a move token
    _refused(confirm(auth.sign_move(replace(claims, operator_id=CID), secret_key)))  # someone else's
    _refused(confirm(auth.sign_move(replace(claims, sealed_secret="v1:x"), secret_key)))  # not sealed by us
    _refused(confirm(auth.sign_move(claims, secret_key + "x")))  # not signed with our key
    op.now += timedelta(minutes=10)
    _refused(confirm(token, op.browser()))  # expired after 10 minutes
    op.now -= timedelta(minutes=10)
    assert op.box.open(op.sealed, aad=OID) == op.old and op.version == 2 and op.moves == []
    assert confirm(token).status_code == 200
    _refused(confirm(token, op.browser()))  # used: the session version moved on
    assert op.box.open(op.sealed, aad=OID) == new and op.version == 3


def test_no_route_turns_two_step_off(op: _Operator) -> None:
    app = cast(FastAPI, op.client.app)
    own = [
        call for call in route_calls(app, "/api/operator") if not call[1].startswith("/api/operator/orgs/")
    ]
    # The operator's own two-step: only the move (/orgs/... is an organisation's admins).
    assert [call for call in own if any(word in call[1] for word in ("2fa", "totp", "two-step"))] == [
        ("POST", CONFIRM),
        ("POST", START),
    ]
    cookie = op.browser()
    token = op.start(cookie).json()["move_token"]
    for body in (
        {"move_token": token},
        {"move_token": token, "code": ""},
        {"move_token": token, "code": None},
    ):
        assert op.post(CONFIRM, body, cookie).status_code == 400
    assert op.post(CONFIRM, {"move_token": token, "code": "000000", "off": True}, cookie).status_code == 400
    assert op.moves == [] and op.box.open(op.sealed, aad=OID) == op.old


def test_a_customer_admin_session_or_none_gets_401_on_the_move_routes(op: _Operator) -> None:
    admin = auth.SessionClaims(
        user_id=UID, customer_id=CID, role="admin", scope="admin", session_version=2, expires_at=op.at() + 600
    )
    customer = f"{auth.SESSION_COOKIE}={auth.sign_session(admin, op.settings.session_secret)}"
    body = {"password": PASSWORD, "code": op.code(op.old), "move_token": "x"}
    for path in (START, CONFIRM):
        for headers in (CSRF, {**CSRF, "Cookie": customer}):
            op.client.cookies.clear()
            reply = op.client.post(path, json=body, headers=headers)
            assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), path
    assert op.audit == [] and op.failures == 0 and op.moves == []


def test_a_move_token_and_a_session_token_never_read_as_each_other(op: _Operator) -> None:
    key, at = op.settings.session_secret, op.at()
    move = auth.MoveClaims(operator_id=OID, session_version=2, sealed_secret="v1:x", expires_at=at + 60)
    assert auth.read_move(auth.sign_move(move, key), key, at) == move
    assert auth.read_move(auth.sign_move(move, key), key, at + 60) is None  # the end is exclusive
    assert auth.read_move(auth.sign_move(move, key), key + "x", at) is None
    assert auth.read_operator(auth.sign_move(move, key), key, at) is None
    assert auth.read_move(op.browser(), key, at) is None
    assert auth.read_move(auth.sign_move(replace(move, sealed_secret=""), key), key, at) is None
