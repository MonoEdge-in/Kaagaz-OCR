"""The operator's organisation routes without a database (super admin PR 3): every route
needs the operator cookie, the step-up, each outcome's reply, the temporary password,
and what a reply may hold. The SQL itself: ``test_operator_orgs_db.py``."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest
from fastapi import FastAPI
from sqlalchemy import Connection, Engine

from kaagaz.api import totp
from kaagaz.api.auth import (
    OPERATOR_COOKIE,
    SESSION_COOKIE,
    OperatorClaims,
    SessionClaims,
    sign_operator,
    sign_session,
    verify_password,
)
from kaagaz.api.names import ORG_NAME_PROBLEM
from kaagaz.api.secret_box import SecretBox
from kaagaz.db import operator_orgs as orgs_sql
from kaagaz.db import operators as operators_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.operator_orgs import Outcome
from tests.test_api_auth import _offline_client
from tests.test_api_support import ANY_ID, CSRF, NOW, route_calls

OID, CID = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
UID = "44444444-4444-4444-8444-444444444444"  # a customer admin
AT = int(NOW.timestamp())
STEP = AT // totp.STEP_SECONDS
OWN = totp.new_secret()  # the operator's own authenticator
ORGS = "/api/operator/orgs"
ORG_ROUTES = {
    ("GET", ORGS),
    ("POST", ORGS),
    ("POST", f"{ORGS}/{ANY_ID}/disable"),
    ("POST", f"{ORGS}/{ANY_ID}/enable"),
    # PR 3b: one organisation's admins.
    ("GET", f"{ORGS}/{ANY_ID}/admins"),
    ("POST", f"{ORGS}/{ANY_ID}/admins"),
    *{("POST", f"{ORGS}/{ANY_ID}/admins/{ANY_ID}/{change}") for change in ("disable", "enable", "unlock")},
    ("POST", f"{ORGS}/{ANY_ID}/admins/{ANY_ID}/reset-2fa"),
    # PR 4: deleting an organisation.
    ("POST", f"{ORGS}/{ANY_ID}/schedule-delete"),
    ("POST", f"{ORGS}/{ANY_ID}/cancel-delete"),
    # One organisation's AI settings (routers/operator_models.py). Changed on purpose
    # (features 25-27, 15 Oct 2026): /models became /ai.
    ("GET", f"{ORGS}/{ANY_ID}/ai"),
    ("PUT", f"{ORGS}/{ANY_ID}/ai"),
    ("PUT", f"{ORGS}/{ANY_ID}/autofile"),  # feature 28: allow or stop auto-filing
    # Changed on purpose (0039): one organisation's feature switches.
    ("GET", f"{ORGS}/{ANY_ID}/feature-switches"),
    ("PUT", f"{ORGS}/{ANY_ID}/feature-switches/{ANY_ID}"),
    # Changed on purpose (0040): one organisation's sign-in idle timeout.
    ("GET", f"{ORGS}/{ANY_ID}/session-timeout"),
    ("PUT", f"{ORGS}/{ANY_ID}/session-timeout"),
    # Changed on purpose (#212, 21 Oct 2026): one organisation's model call rates.
    ("GET", f"{ORGS}/{ANY_ID}/model-rates"),
    ("PUT", f"{ORGS}/{ANY_ID}/model-rates"),
    ("PUT", f"{ORGS}/{ANY_ID}/users/{ANY_ID}/model-rate"),
    ("POST", f"{ORGS}/{ANY_ID}/approve"),  # Changed on purpose (#457): a pending sign-up
}
ORG_ROW = {
    "customer_id": CID,
    "name": "Sharma Traders",
    "status": "active",
    "disabled_at": None,
    "delete_after": None,
    "created_at": NOW,
    "admins": 1,
    "reviewers": 2,
    "members": 3,
    "documents": 4,
    "pages": 9,
    "size_bytes": 1234,
    "last_activity_at": NOW,
    "autofile_granted": True,
    "autofile_on": False,
    "admin_email_confirmed": False,
}


class _NoDatabase(OperatorDatabase):
    """Every call the routes make is replaced below; no connection is ever opened."""

    def __init__(self) -> None:
        super().__init__(cast(Engine, None))

    @contextmanager
    def operator(self) -> Iterator[Connection]:
        yield cast(Connection, None)


class _Offline:
    """An app with a signed-in operator; the database calls are recorded in ``calls``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.client = _offline_client()
        app = cast(FastAPI, self.client.app)
        app.state.kaagaz_operator = _NoDatabase()
        self.settings = app.state.kaagaz.settings
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.outcome = Outcome("done", CID)
        self.last_step: int | None = None
        self.failures, self.locked = 1, False
        monkeypatch.setattr(
            operators_sql, "session_operator", lambda conn, oid: ("ops@example.test", "Ops", 2)
        )
        monkeypatch.setattr(operators_sql, "begin_login", None)  # never the sign-in's count
        monkeypatch.setattr(orgs_sql, "begin_step_up", self._begin)
        monkeypatch.setattr(orgs_sql, "list_customers", lambda conn, oid: [ORG_ROW])
        for name in ("create_customer", "set_customer_status", "step_up_failed", "set_org_autofile"):
            monkeypatch.setattr(orgs_sql, name, self._recorder(name))
        token = sign_operator(
            OperatorClaims(operator_id=OID, session_version=2, expires_at=AT + 600),
            self.settings.session_secret,
        )
        self.headers = {**CSRF, "Cookie": f"{OPERATOR_COOKIE}={token}"}

    def _begin(self, conn: Connection, oid: str, **_: Any) -> tuple[str, int | None, int] | None:
        sealed = SecretBox(self.settings.totp_encryption_key).seal(OWN, aad=OID)
        return None if self.locked else (sealed, self.last_step, self.failures)

    def _recorder(self, name: str) -> Callable[..., Outcome]:
        def record(conn: Connection, *args: Any, **kwargs: Any) -> Outcome:
            self.calls.append((name, args, kwargs))
            return self.outcome

        return record

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.client.post(f"{ORGS}{path}", json=body, headers=self.headers)

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> _Offline:
    return _Offline(monkeypatch)


def test_every_org_route_needs_the_operator_cookie_not_a_customer_admin_one(offline: _Offline) -> None:
    calls = route_calls(cast(FastAPI, offline.client.app), ORGS)
    assert set(calls) == ORG_ROUTES
    admin = SessionClaims(
        user_id=UID, customer_id=CID, role="admin", scope="admin", session_version=2, expires_at=AT + 600
    )
    customer = {**CSRF, "Cookie": f"{SESSION_COOKIE}={sign_session(admin, offline.settings.session_secret)}"}
    for method, path in calls:
        for headers in (CSRF, customer):
            reply = offline.client.request(method, path, headers=headers, json={"code": "123456"})
            assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), (
                method,
                path,
            )
    assert offline.calls == []


def test_disabling_needs_a_fresh_code_from_the_operators_own_secret(offline: _Offline) -> None:
    path = f"/{CID}/disable"
    assert offline.post(path).status_code == 400  # no code at all
    accepted = {totp.code_at(OWN, STEP + offset) for offset in (-1, 0, 1)}
    wrong = next(code for code in ("000000", "111111", "222222") if code not in accepted)
    assert offline.post(path, {"code": wrong}).json()["error"]["code"] == "step_up_failed"
    assert offline.post(path, {"code": totp.code_at(totp.new_secret(), STEP)}).status_code == 403
    offline.failures = offline.settings.login_max_failures
    assert offline.post(path, {"code": "12 34 5x"}).status_code == 403
    assert [kwargs for name, _, kwargs in offline.calls] == [{"locked": False}] * 2 + [{"locked": True}]
    offline.calls.clear()
    offline.locked = True  # a lock refuses even the right code, with no new row
    assert offline.post(path, {"code": totp.code_at(OWN, STEP)}).status_code == 403
    offline.locked, offline.last_step = False, STEP  # the code's step was used already
    assert offline.post(path, {"code": totp.code_at(OWN, STEP)}).status_code == 403
    assert offline.names() == ["step_up_failed"]
    offline.calls.clear()
    offline.last_step = STEP - 1
    reply = offline.post(path, {"code": totp.code_at(OWN, STEP)[:3] + " " + totp.code_at(OWN, STEP)[3:]})
    assert reply.status_code == 200, reply.text
    [(name, _, kwargs)] = offline.calls
    assert (name, kwargs["step"]) == ("set_customer_status", STEP)
    offline.calls.clear()  # the attempt that reached the limit lost its step to a parallel one
    offline.failures, offline.outcome = offline.settings.login_max_failures, Outcome("step_up", CID)
    assert offline.post(path, {"code": totp.code_at(OWN, STEP)}).status_code == 403
    assert offline.calls[-1] == ("step_up_failed", (OID,), {"locked": True})


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("done", 200),
        ("unchanged", 200),
        ("missing", 404),
        ("deleting", 409),
        ("taken", 409),
        ("signed_out", 401),
        ("step_up", 403),
    ],
)
def test_each_outcome_gets_its_reply(offline: _Offline, code: str, status: int) -> None:
    offline.outcome = Outcome(code, CID)
    reply = offline.post(f"/{CID}/enable")
    assert reply.status_code == status, reply.text
    if status == 200:
        assert reply.json() == {"organisation": {"id": CID, "status": "active", "changed": code == "done"}}
    # step_up: the step was used meanwhile, by a request at the same time: its own row too
    assert offline.names() == ["set_customer_status"] + ["step_up_failed"] * (code == "step_up")


def test_the_first_admins_temporary_password_is_shown_once_and_only_its_hash_is_stored(
    offline: _Offline, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    path, body = "", {"name": " Mehta ", "admin_email": "Boss@Mehta.Example", "admin_name": "Boss"}
    reply = offline.post(path, body)
    assert reply.status_code == 201, reply.text
    temporary = reply.json()["temporary_password"]
    assert len(temporary) == 16
    [(name, _, kwargs)] = offline.calls
    assert name == "create_customer" and kwargs["name"] == "Mehta"
    assert verify_password(temporary, kwargs["password_hash"])
    assert temporary not in str(kwargs) and temporary not in caplog.text
    assert "boss@mehta.example" in reply.text and reply.headers["Cache-Control"] == "no-store"
    offline.outcome = Outcome("taken", None)  # audited in the function, no e-mail stored
    taken = offline.post(path, body)
    assert (taken.status_code, taken.json()["error"]["message"]) == (409, "That e-mail is already in use.")
    assert offline.post(path, {**body, "extra": "x"}).status_code == 400


def test_a_placeholder_organisation_name_is_refused_before_anything_is_made(offline: _Offline) -> None:
    reply = offline.post("", {"name": " Test ", "admin_email": "boss@mehta.example", "admin_name": "Boss"})
    assert reply.status_code == 400
    assert reply.json()["error"]["message"] == ORG_NAME_PROBLEM
    assert offline.calls == []


def test_the_organisation_list_holds_counts_only(offline: _Offline) -> None:
    [org] = offline.client.get(ORGS, headers=offline.headers).json()["items"]
    assert org["users"] == {"admins": 1, "reviewers": 2, "members": 3}
    assert set(org) == {
        "id",
        "name",
        "status",
        "disabled_at",
        "delete_after",
        "created_at",
        "users",
        "documents",
        "pages",
        "storage_bytes",
        "last_activity_at",
        "autofile_granted",  # feature 28
        "autofile_on",
        "admin_email_confirmed",  # Changed on purpose (#457): Approve needs it
    }
    assert (org["autofile_granted"], org["autofile_on"]) == (True, False)


@pytest.mark.parametrize(
    ("code", "status"), [("done", 200), ("unchanged", 200), ("missing", 404), ("deleting", 409)]
)
def test_auto_filing_is_allowed_or_stopped_without_a_code_and_each_outcome_gets_its_reply(
    offline: _Offline, code: str, status: int
) -> None:
    offline.outcome = Outcome(code, CID)
    path = f"{ORGS}/{CID}/autofile"
    reply = offline.client.put(path, json={"granted": False}, headers=offline.headers)
    assert reply.status_code == status, reply.text
    assert offline.calls == [("set_org_autofile", (OID, CID), {"granted": False})]
    if status == 200:
        changed = code == "done"
        assert reply.json() == {"organisation": {"id": CID, "autofile_granted": False, "changed": changed}}
    bad = offline.client.put(path, json={"granted": True, "on": True}, headers=offline.headers)
    assert bad.status_code == 400  # the operator never sets the organisation's own switch


def test_the_two_step_grant_route_is_gone(offline: _Offline) -> None:
    """0035: two-step sign-in is required for every admin, so there is nothing to allow."""
    reply = offline.client.put(f"{ORGS}/{CID}/two-step", json={"granted": True}, headers=offline.headers)
    assert reply.status_code in (404, 405)
    assert offline.calls == []
