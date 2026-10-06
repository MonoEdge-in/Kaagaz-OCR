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


def test_a_new_admins_temporary_password_is_shown_once_and_only_its_hash_is_stored(
    offline: _Offline, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    body: dict[str, Any] = {**NEW, "code": totp.code_at(OWN, STEP)}
    reply = offline.post(f"/{CID}/admins", body)
    assert reply.status_code == 201, reply.text
    temporary = reply.json()["temporary_password"]
    assert reply.json()["admin"] == {"id": UID, "email": "boss@mehta.example", "name": "Boss"}
    [(name, args, kwargs)] = offline.calls
    assert (name, args[1:], kwargs["email"]) == ("create_admin", (CID,), "boss@mehta.example")
    assert verify_password(temporary, kwargs["password_hash"]) and temporary not in caplog.text
    offline.outcome = Outcome("missing", None)
    assert offline.post(f"/{CID}/admins", body).status_code == 404
    for code in ("taken", "deleting"):  # taken: audited in the function, no e-mail stored
        offline.outcome = Outcome(code, None)
        assert offline.post(f"/{CID}/admins", body).status_code == 409


def test_an_organisation_with_no_admin_row_is_not_found(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert offline.client.get(f"{ORGS}/{CID}/admins", headers=offline.headers).status_code == 200
    monkeypatch.setattr(orgs_sql, "list_admins", lambda conn, oid, cid: [])
    assert offline.client.get(f"{ORGS}/{CID}/admins", headers=offline.headers).status_code == 404
