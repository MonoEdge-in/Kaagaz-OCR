"""Deleting an organisation, without a database (super admin PR 4): scheduling and
cancelling need a fresh code from the operator's own secret, the typed name reaches the
database exactly as typed, and each outcome gets its reply. Every route needs the
operator cookie: ``test_operator_orgs.py`` walks them all. The SQL and the purge:
``test_isolation_customer_purge.py``."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest
from sqlalchemy.exc import SQLAlchemyError

from kaagaz.api import totp
from kaagaz.db import customer_purge
from kaagaz.db import operator_orgs as orgs_sql
from kaagaz.db.engine import Database
from kaagaz.db.operator_orgs import Outcome
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.jobs import purge
from tests.test_api_support import NOW
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


class _System:
    @contextmanager
    def system(self) -> Iterator[None]:
        yield None


@pytest.mark.parametrize(
    ("failure", "purged"),
    [
        (BackendUnavailable("storage"), ["d"]),
        (SQLAlchemyError("bug"), ["d"]),
        (BackendUnavailable("database"), []),
    ],
)
def test_one_organisation_failing_never_stops_the_next_but_the_database_down_does(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, purged: list[str]
) -> None:
    done: list[str] = []

    def purge_customer(db: Any, storage: Any, customer_id: str, **kwargs: Any) -> tuple[int, bool]:
        if customer_id == "c":
            raise failure
        assert kwargs["keep_going"]()  # within the time
        done.append(customer_id)
        return 2, True

    monkeypatch.setattr(customer_purge, "ids_to_purge", lambda conn, now: ["c", "d"])
    monkeypatch.setattr(purge, "purge_customer", purge_customer)
    db, storage = cast(Database, _System()), cast(Any, None)
    if purged:
        assert purge.purge_deleted_customers(db, storage, lambda: NOW) == 2
    else:
        with pytest.raises(BackendUnavailable):
            purge.purge_deleted_customers(db, storage, lambda: NOW)
    assert done == purged
    done.clear()
    assert purge.purge_deleted_customers(db, storage, lambda: NOW, seconds=0) == 0
    assert done == []  # no time left: nothing starts
