"""The operator's organisation routes without a database (super admin PR 3): every route
needs the operator cookie, the step-up, each outcome's reply, the temporary password,
and what a reply may hold. The SQL itself: ``test_operator_orgs_db.py``."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from typing import Any, cast

import pytest
from fastapi import FastAPI
from sqlalchemy import Connection, Engine

from kaagaz.api import keycloak_accounts as accounts
from kaagaz.api import keycloak_admin as kc_admin
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
from kaagaz.api.routers import operator_orgs as orgs_router
from kaagaz.api.secret_box import SecretBox
from kaagaz.db import keycloak as keycloak_sql
from kaagaz.db import operator_orgs as orgs_sql
from kaagaz.db import operators as operators_sql
from kaagaz.db import users as users_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.keycloak import Person
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
    # Changed on purpose (#737): admin-two-step and google-signin are gone (Keycloak always asks for
    # the code, Google is dropped); moving people to company sign-in is new.
    ("GET", f"{ORGS}/{ANY_ID}/sign-in"),
    ("POST", f"{ORGS}/{ANY_ID}/move-everyone"),
    ("POST", f"{ORGS}/{ANY_ID}/people/{ANY_ID}/resend-setup"),
    ("GET", f"{ORGS}/{ANY_ID}/folder-upload-limits"),  # #484
    ("PUT", f"{ORGS}/{ANY_ID}/folder-upload-limits"),
    ("GET", f"{ORGS}/{ANY_ID}/storage-limit"),  # #516
    ("PUT", f"{ORGS}/{ANY_ID}/storage-limit"),
    # Changed on purpose (#212, 21 Oct 2026): one organisation's model call rates.
    ("GET", f"{ORGS}/{ANY_ID}/model-rates"),
    ("PUT", f"{ORGS}/{ANY_ID}/model-rates"),
    ("PUT", f"{ORGS}/{ANY_ID}/users/{ANY_ID}/model-rate"),
    ("POST", f"{ORGS}/{ANY_ID}/approve"),  # Changed on purpose (#457): a pending sign-up
    # Changed on purpose (#463, 31 Oct 2026): one organisation's accuracy check against max settings.
    ("POST", f"{ORGS}/{ANY_ID}/accuracy-check"),
    ("GET", f"{ORGS}/{ANY_ID}/accuracy-check/latest"),
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
        # Changed on purpose (#457 review): a new organisation first frees a stale sign-up's
        # e-mail through the app's database (tests/test_api_email.py proves it).
        monkeypatch.setattr(app.state.kaagaz.db, "system", lambda: nullcontext(None))
        monkeypatch.setattr(users_sql, "release_stale_signup", lambda conn, email: False)
        # #737: Keycloak is on, and every call to it (and the link in the database) is recorded in
        # ``kc``. ``existing`` are emails Keycloak already has; ``email_fails`` breaks the setup email.
        self.kc: list[tuple[str, tuple[Any, ...]]] = []
        self.existing: set[str] = set()
        self.email_fails = False
        self.subjects = {UID: "kc-subject-1"}
        self.people = [Person(UID, "asha@example.test", "Asha", False, True)]
        db = app.state.kaagaz.db
        monkeypatch.setattr(accounts, "enabled", lambda context: True)
        monkeypatch.setattr(kc_admin, "find_user_by_email", self._find)
        monkeypatch.setattr(kc_admin, "create_user", self._create)
        monkeypatch.setattr(kc_admin, "send_setup_email", self._send)
        for name in ("remove_otp", "set_enabled", "delete_user", "clear_lock"):
            monkeypatch.setattr(kc_admin, name, self._kc_recorder(name))
        monkeypatch.setattr(db, "customer", lambda customer_id: nullcontext(None))
        monkeypatch.setattr(db, "user", lambda customer_id, user_id: nullcontext(None))
        monkeypatch.setattr(keycloak_sql, "subjects", lambda conn, issuer: dict(self.subjects))
        monkeypatch.setattr(keycloak_sql, "people", lambda conn, customer_id, issuer: self.people)
        monkeypatch.setattr(keycloak_sql, "link_account", self._link)
        monkeypatch.setattr(orgs_router, "_people", lambda context, customer_id: ("active", self.people))
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

    def _find(self, settings: Any, email: str) -> str | None:
        return "someone-else" if email in self.existing else None

    def _create(self, settings: Any, *, email: str, name: str) -> str:
        self.kc.append(("create_user", (email, name)))
        return f"kc-new-{len([1 for c, _ in self.kc if c == 'create_user'])}"

    def _send(self, settings: Any, subject: str) -> None:
        self.kc.append(("send_setup_email", (subject,)))
        if self.email_fails:
            raise kc_admin.KeycloakAdminError()

    def _kc_recorder(self, name: str) -> Callable[..., None]:
        def record(settings: Any, subject: str, *args: Any) -> None:
            self.kc.append((name, (subject, *args)))

        return record

    def _link(self, conn: Any, issuer: str, subject: str, *, now: Any, as_user: str | None = None) -> int:
        self.kc.append(("link", (subject,)))
        return 2

    def kc_names(self) -> list[str]:
        return [name for name, _ in self.kc]

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


def _first_admin_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orgs_sql, "list_admins", lambda conn, oid, cid: [{"user_id": UID}])


NEW_ORG = {"name": " Mehta ", "admin_email": "Boss@Mehta.Example", "admin_name": "Boss"}


def test_creating_an_organisation_makes_the_admin_account_links_it_and_sends_the_setup_email(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    _first_admin_row(monkeypatch)
    reply = offline.post("", NEW_ORG)
    assert reply.status_code == 201, reply.text
    body = reply.json()
    assert "temporary_password" not in body and body["setup_email_sent"] is True
    admin = {"email": "boss@mehta.example", "name": "Boss"}
    assert body["organisation"] == {"id": CID, "name": "Mehta", "admin": admin}
    assert body["message"] == (
        "Created. We emailed boss@mehta.example a link to set their password and authenticator."
        " It works for 12 hours."
    )
    [(name, _, kwargs)] = offline.calls
    assert name == "create_customer" and kwargs["name"] == "Mehta"
    assert verify_password("", kwargs["password_hash"]) is False  # a password nobody knows
    assert offline.kc == [
        ("create_user", ("boss@mehta.example", "Boss")),
        ("link", ("kc-new-1",)),
        ("send_setup_email", ("kc-new-1",)),
    ]
    assert "kc-new-1" not in caplog.text
    offline.outcome = Outcome("taken", None)  # audited in the function, no e-mail stored
    taken = offline.post("", NEW_ORG)
    assert (taken.status_code, taken.json()["error"]["message"]) == (409, "That e-mail is already in use.")
    assert offline.post("", {**NEW_ORG, "extra": "x"}).status_code == 400


def test_an_email_keycloak_already_has_stops_a_new_organisation_before_anything_is_made(
    offline: _Offline,
) -> None:
    offline.existing.add("boss@mehta.example")
    reply = offline.post("", NEW_ORG)
    assert reply.status_code == 409
    assert reply.json()["error"]["message"] == (
        "This email already has a company sign-in account. Use a different email."
    )
    assert offline.calls == [] and offline.kc == []  # not linked, not created, no organisation


def test_an_organisation_whose_setup_email_failed_is_made_and_linked_and_says_to_resend(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    _first_admin_row(monkeypatch)
    offline.email_fails = True
    reply = offline.post("", NEW_ORG)
    assert reply.status_code == 201
    assert reply.json()["setup_email_sent"] is False
    assert (
        reply.json()["message"] == "Created, but the setup email could not be sent. Use Resend setup email."
    )
    assert offline.kc_names() == ["create_user", "link", "send_setup_email"]  # linked: Resend finishes it


def test_when_linking_fails_the_new_keycloak_account_is_removed_again(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    _first_admin_row(monkeypatch)

    def refuse(*args: Any, **kwargs: Any) -> int:
        raise keycloak_sql.IdentityTaken

    monkeypatch.setattr(keycloak_sql, "link_account", refuse)
    reply = offline.post("", NEW_ORG)
    assert reply.status_code == 201
    assert reply.json()["message"] == (
        "Created, but the sign-in account could not be made. Use Move everyone to try again."
    )
    assert offline.kc_names() == ["create_user", "delete_user"]  # no half-made account is left


def test_the_summary_counts_active_people_on_company_sign_in(offline: _Offline) -> None:
    offline.people = [
        Person(UID, "a@x.test", "A", False, True),
        Person(CID, "b@x.test", "B", False, False),
        Person(ANY_ID, "c@x.test", "C", True, False),  # switched off: not counted
    ]
    reply = offline.client.get(f"{ORGS}/{CID}/sign-in", headers=offline.headers)
    assert reply.json() == {"status": "active", "available": True, "connected": 1, "to_move": 1}


def test_move_everyone_counts_moved_connected_and_those_needing_attention(offline: _Offline) -> None:
    offline.people = [
        Person(UID, "done@x.test", "Done", False, True),
        Person("00000000-0000-4000-8000-000000000001", "new@x.test", "New One", False, False),
        Person("00000000-0000-4000-8000-000000000002", "taken@x.test", "Taken", False, False),
        Person("00000000-0000-4000-8000-000000000003", "off@x.test", "Off", True, False),
    ]
    offline.existing.add("taken@x.test")
    reply = offline.post(f"/{CID}/move-everyone")
    assert reply.status_code == 200, reply.text
    assert reply.json() == {
        "moved": 1,
        "connected": 1,
        "attention": [
            {"email": "taken@x.test", "reason": "This email already has a company sign-in account."}
        ],
        "email_not_sent": [],
        "message": "1 moved, 1 already connected, 1 need attention.",
    }
    # the taken email was neither created nor linked; the switched-off person was left alone
    assert offline.kc == [
        ("create_user", ("new@x.test", "New One")),
        ("link", ("kc-new-1",)),
        ("send_setup_email", ("kc-new-1",)),
    ]


def test_move_everyone_reports_emails_not_sent_and_refuses_an_organisation_that_is_off(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    offline.people = [Person(CID, "new@x.test", "New", False, False)]
    offline.email_fails = True
    reply = offline.post(f"/{CID}/move-everyone").json()
    assert (reply["moved"], reply["email_not_sent"]) == (1, [{"user_id": CID, "email": "new@x.test"}])
    assert reply["message"].endswith("1 setup email(s) were not sent: use Resend setup email.")
    monkeypatch.setattr(orgs_router, "_people", lambda context, customer_id: ("disabled", offline.people))
    refused = offline.post(f"/{CID}/move-everyone")
    assert (refused.status_code, refused.json()["error"]["message"]) == (
        409,
        "Enable the organisation first.",
    )


def test_move_everyone_stops_when_keycloak_is_down(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    offline.people = [Person(UID, "a@x.test", "A", False, False), Person(CID, "b@x.test", "B", False, False)]

    def down(settings: Any, email: str) -> None:
        offline.kc.append(("find", (email,)))
        raise kc_admin.KeycloakAdminError()

    monkeypatch.setattr(kc_admin, "find_user_by_email", down)
    reply = offline.post(f"/{CID}/move-everyone").json()
    assert reply["moved"] == 0 and [a["email"] for a in reply["attention"]] == ["a@x.test", "b@x.test"]
    assert offline.kc == [("find", ("a@x.test",))]  # the second person was not tried


def test_resend_setup_email_goes_to_the_linked_account_only(offline: _Offline) -> None:
    reply = offline.post(f"/{CID}/people/{UID}/resend-setup")
    assert reply.status_code == 200
    assert reply.json()["message"] == "Done. We emailed a new setup link. It works for 12 hours."
    assert offline.kc == [("send_setup_email", ("kc-subject-1",))]
    offline.subjects.clear()  # not linked
    assert offline.post(f"/{CID}/people/{UID}/resend-setup").status_code == 409


def test_disabling_and_enabling_an_organisation_follows_its_linked_people(offline: _Offline) -> None:
    offline.people = [
        Person(UID, "a@x.test", "A", False, True),
        Person(CID, "off@x.test", "Off", True, True),  # switched off by an admin
        Person(ANY_ID, "n@x.test", "N", False, False),  # not on company sign-in
    ]
    offline.subjects = {UID: "sub-a", CID: "sub-off"}
    assert offline.post(f"/{CID}/enable").status_code == 200
    assert offline.kc == [("set_enabled", ("sub-a", True))]  # the person an admin switched off stays off
    offline.kc.clear()
    assert offline.post(f"/{CID}/disable", {"code": totp.code_at(OWN, STEP)}).status_code == 200
    assert offline.kc == [("set_enabled", ("sub-a", False)), ("set_enabled", ("sub-off", False))]


def test_a_keycloak_failure_after_the_change_is_reported_not_hidden(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(settings: Any, subject: str, enabled: bool) -> None:
        raise kc_admin.KeycloakAdminError()

    monkeypatch.setattr(kc_admin, "set_enabled", down)
    reply = offline.post(f"/{CID}/enable")
    assert reply.status_code == 502
    assert reply.json()["error"]["message"] == (
        "Saved here, but 1 sign-in account(s) could not be updated. Try the same action again."
    )


def test_the_two_switches_that_keycloak_replaces_are_gone(offline: _Offline) -> None:
    for name in ("admin-two-step", "google-signin"):
        for method in ("get", "put"):
            reply = getattr(offline.client, method)(f"{ORGS}/{CID}/{name}", headers=offline.headers)
            assert reply.status_code in (404, 405), (name, method)
    assert offline.calls == []


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
