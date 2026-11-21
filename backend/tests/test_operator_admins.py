"""The operator's routes for one organisation's admins without a database (super admin
PR 3b): the two-step reset's step-up, each outcome's reply, the temporary password, and
what an admin row may hold. Every route needs the operator cookie:
``test_operator_orgs.py`` walks them all. The SQL itself: ``test_operator_admins_db.py``."""

import logging
from typing import Any

import pytest

from kaagaz.api import totp
from kaagaz.api.auth import verify_password
from kaagaz.db import operator_orgs as orgs_sql
from kaagaz.db.operator_orgs import Outcome
from tests.test_api_support import NOW
from tests.test_operator_orgs import CID, ORGS, OWN, STEP, UID, _Offline

ADMIN_ROW = {
    "user_id": UID,
    "display_name": "Asha",
    "email": "asha@example.test",
    "totp_enabled": True,
    "locked_until": NOW,  # a lock that has just ended
    "disabled": False,
    "must_change_password": False,
    "last_login_at": NOW,
    "created_at": NOW,
}
ADMIN = f"/{CID}/admins/{UID}"
NEW = {"email": "Boss@Mehta.Example", "name": " Boss "}


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> _Offline:
    offline = _Offline(monkeypatch)
    offline.outcome = Outcome("done", UID)
    monkeypatch.setattr(orgs_sql, "list_admins", lambda conn, oid, cid: [ADMIN_ROW])
    for name in ("create_admin", "change_admin"):
        monkeypatch.setattr(orgs_sql, name, offline._recorder(name))
    return offline


@pytest.mark.parametrize(
    ("path", "body", "call"),
    [
        (f"{ADMIN}/reset-2fa", {}, ("change_admin", (CID, UID, "reset_totp"))),
        (f"/{CID}/admins", NEW, ("create_admin", (CID,))),  # its password signs in as an admin
    ],
    ids=["reset-2fa", "create-admin"],
)
def test_resetting_or_creating_an_admin_needs_a_fresh_code_from_the_operators_own_secret(
    offline: _Offline, path: str, body: dict[str, str], call: tuple[str, tuple[str, ...]]
) -> None:
    assert offline.post(path, body).status_code == 400  # no code at all
    assert offline.post(path, {**body, "code": totp.code_at(totp.new_secret(), STEP)}).status_code == 403
    offline.last_step = STEP  # the code's step was used already
    assert offline.post(path, {**body, "code": totp.code_at(OWN, STEP)}).status_code == 403
    assert offline.names() == ["step_up_failed", "step_up_failed"]  # nothing made or changed
    offline.calls.clear()
    offline.last_step = None
    reply = offline.post(path, {**body, "code": totp.code_at(OWN, STEP)})
    assert reply.status_code in (200, 201), reply.text
    [(name, args, kwargs)] = offline.calls
    assert ((name, args[1:]), kwargs["step"]) == (call, STEP)


@pytest.mark.parametrize("change", ["disable", "enable", "unlock"])
@pytest.mark.parametrize(
    ("code", "status"),
    [("done", 200), ("missing", 404), ("deleting", 409), ("signed_out", 401), ("step_up", 403)],
)
def test_each_change_names_its_organisation_and_admin_and_gets_its_reply(
    offline: _Offline, change: str, code: str, status: int
) -> None:
    offline.outcome = Outcome(code, UID)
    reply = offline.post(f"{ADMIN}/{change}")
    assert reply.status_code == status, reply.text
    name, args, kwargs = offline.calls[0]
    assert (name, args[1:], kwargs) == ("change_admin", (CID, UID, change), {"step": None})
    if status == 200:
        assert reply.json() == {
            "admin": {
                "id": UID,
                "name": "Asha",
                "email": "asha@example.test",
                "two_step": True,
                "locked": False,
                "disabled": False,
                "must_change_password": False,
                "last_sign_in_at": NOW.isoformat(),
                "created_at": NOW.isoformat(),
            }
        }


def test_a_new_admin_gets_a_company_sign_in_account_and_no_password_is_shown(
    offline: _Offline, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    body: dict[str, Any] = {**NEW, "code": totp.code_at(OWN, STEP)}
    reply = offline.post(f"/{CID}/admins", body)
    assert reply.status_code == 201, reply.text
    assert "temporary_password" not in reply.json() and reply.json()["setup_email_sent"] is True
    assert reply.json()["admin"] == {"id": UID, "email": "boss@mehta.example", "name": "Boss"}
    [(name, args, kwargs)] = offline.calls
    assert (name, args[1:], kwargs["email"]) == ("create_admin", (CID,), "boss@mehta.example")
    assert verify_password("", kwargs["password_hash"]) is False
    assert offline.kc_names() == ["create_user", "link", "send_setup_email"]
    assert "kc-new-1" not in caplog.text
    offline.kc.clear()
    offline.outcome = Outcome("missing", None)
    assert offline.post(f"/{CID}/admins", body).status_code == 404
    for code in ("taken", "deleting"):  # taken: audited in the function, no e-mail stored
        offline.outcome = Outcome(code, None)
        assert offline.post(f"/{CID}/admins", body).status_code == 409
    assert offline.kc == []  # nothing is made in Keycloak for a refused admin


def test_an_admin_whose_email_keycloak_already_has_is_refused_before_the_code_is_used(
    offline: _Offline,
) -> None:
    offline.existing.add("boss@mehta.example")
    reply = offline.post(f"/{CID}/admins", {**NEW, "code": totp.code_at(OWN, STEP)})
    assert reply.status_code == 409
    assert "already has a company sign-in account" in reply.json()["error"]["message"]
    assert offline.calls == [] and offline.kc == []


def test_resetting_an_admins_two_step_removes_their_authenticator_in_keycloak(offline: _Offline) -> None:
    reply = offline.post(f"{ADMIN}/reset-2fa", {"code": totp.code_at(OWN, STEP)})
    assert reply.status_code == 200, reply.text
    assert (
        reply.json()["message"] == "Done. Asha will set up their authenticator again at their next sign-in."
    )
    assert offline.kc == [("remove_otp", ("kc-subject-1",))]
    offline.kc.clear()
    offline.subjects.clear()  # an admin who is not on company sign-in has no authenticator to remove
    offline.last_step = None
    again = offline.post(f"{ADMIN}/reset-2fa", {"code": totp.code_at(OWN, STEP + 1)})
    assert (again.status_code, again.json()["error"]["message"]) == (
        409,
        "This person is not on company sign-in yet.",
    )


def test_unlocking_an_admin_clears_keycloaks_lock_too(offline: _Offline) -> None:
    assert offline.post(f"{ADMIN}/unlock").status_code == 200
    assert offline.kc == [("clear_lock", ("kc-subject-1",))]
    offline.kc.clear()
    offline.subjects.clear()  # not linked: the Kaagaz unlock still works, Keycloak has nothing to clear
    assert offline.post(f"{ADMIN}/unlock").status_code == 200
    assert offline.kc == []


def test_disabling_and_enabling_an_admin_mirrors_in_keycloak(offline: _Offline) -> None:
    assert offline.post(f"{ADMIN}/disable").status_code == 200
    assert offline.post(f"{ADMIN}/enable").status_code == 200
    assert offline.kc == [("set_enabled", ("kc-subject-1", False)), ("set_enabled", ("kc-subject-1", True))]


def test_an_organisation_with_no_admin_row_is_not_found(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert offline.client.get(f"{ORGS}/{CID}/admins", headers=offline.headers).status_code == 200
    monkeypatch.setattr(orgs_sql, "list_admins", lambda conn, oid, cid: [])
    assert offline.client.get(f"{ORGS}/{CID}/admins", headers=offline.headers).status_code == 404
