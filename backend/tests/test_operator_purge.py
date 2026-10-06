"""Deleting an organisation, without a database (super admin PR 4): scheduling and
cancelling need a fresh code from the operator's own secret, the typed name reaches the
database exactly as typed, and each outcome gets its reply. Every route needs the
operator cookie: ``test_operator_orgs.py`` walks them all. The SQL and the purge:
``test_isolation_customer_purge.py``."""

import pytest

from kaagaz.api import totp
from kaagaz.cli import main
from kaagaz.db import operator_orgs as orgs_sql
from kaagaz.db.operator_orgs import Outcome
from tests.test_operator_orgs import CID, OWN, STEP, _Offline

NAME = " Sharma Traders "  # spaces and all: compared exactly, never trimmed
ROUTES = {
    "schedule-delete": ("schedule_delete", {"name": NAME}, {"name": NAME, "step": STEP}, "deleting"),
    "cancel-delete": ("cancel_delete", {}, {"step": STEP}, "disabled"),
}


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> _Offline:
    offline = _Offline(monkeypatch)
    for name in ("schedule_delete", "cancel_delete"):
        monkeypatch.setattr(orgs_sql, name, offline._recorder(name))
    return offline


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_scheduling_and_cancelling_need_a_fresh_code_from_the_operators_own_secret(
    offline: _Offline, route: str
) -> None:
    call, body, passed, status = ROUTES[route]
    path = f"/{CID}/{route}"
    assert offline.post(path, body).status_code == 400  # no code at all
    assert offline.post(path, {**body, "code": totp.code_at(totp.new_secret(), STEP)}).status_code == 403
    offline.last_step = STEP  # the code's step was used already
    assert offline.post(path, {**body, "code": totp.code_at(OWN, STEP)}).status_code == 403
    assert offline.names() == ["step_up_failed", "step_up_failed"]  # nothing scheduled or cancelled
    offline.calls.clear()
    offline.last_step = None
    reply = offline.post(path, {**body, "code": totp.code_at(OWN, STEP)})
    assert reply.json() == {"organisation": {"id": CID, "status": status, "changed": True}}
    [(name, args, kwargs)] = offline.calls
    assert (name, args[1:], kwargs) == (call, (CID,), passed)
    smuggled = {**body, "code": totp.code_at(OWN, STEP), "customer_id": CID}
    assert offline.post(path, smuggled).status_code == 400


@pytest.mark.parametrize(
    ("route", "code", "status", "error"),
    [
        ("schedule-delete", "missing", 404, "not_found"),
        ("schedule-delete", "not_disabled", 409, "conflict"),
        ("schedule-delete", "name_mismatch", 409, "name_mismatch"),  # audited in the function
        ("schedule-delete", "deleting", 409, "conflict"),
        ("schedule-delete", "signed_out", 401, "not_signed_in"),
        ("schedule-delete", "step_up", 403, "step_up_failed"),
        ("cancel-delete", "missing", 404, "not_found"),
        ("cancel-delete", "not_deleting", 409, "conflict"),
        ("cancel-delete", "purging", 409, "conflict"),  # the grace period is over
        ("cancel-delete", "step_up", 403, "step_up_failed"),
    ],
)
def test_each_outcome_gets_its_reply(
    offline: _Offline, route: str, code: str, status: int, error: str
) -> None:
    offline.outcome = Outcome(code, CID)
    _, body, _, _ = ROUTES[route]
    reply = offline.post(f"/{CID}/{route}", {**body, "code": totp.code_at(OWN, STEP)})
    assert (reply.status_code, reply.json()["error"]["code"]) == (status, error)


def test_the_cli_purge_needs_now_and_stops_before_any_database() -> None:
    """``purge-customer`` (PR 4b) without ``--now`` is a usage error: nothing is built."""
    with pytest.raises(SystemExit) as stopped:
        main(["purge-customer", "--customer-id", CID], db=None, environ={})
    assert stopped.value.code == 2
