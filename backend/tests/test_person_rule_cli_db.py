"""``python -m kaagaz.cli person-rule`` (PRD 7, #606): the per-person database rule's switch, on the
owner's login, refused until the workers run as kaagaz_worker; and every database command runs as
the worker role."""

import io
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text

from kaagaz.cli import _cli_role, main
from kaagaz.db import person_rule
from kaagaz.db.engine import Database
from tests.db_support import new_customer, until_0092
from tests.settings_factory import make_settings

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("the_rule_on")]


def _run(db: Database, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(["person-rule", *args], db=db, out=out, settings=make_settings())
    return code, out.getvalue()


@pytest.fixture
def workers(db: Database) -> Iterator[None]:
    """reader_status as the test sets it (the owner's), put back after."""
    with db.engine.begin() as conn:
        kept = conn.execute(text("SELECT worker, state, since, seen_at, role FROM reader_status")).all()
        conn.execute(text("DELETE FROM reader_status"))
    yield
    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM reader_status"))
        for row in kept:
            conn.execute(
                text("INSERT INTO reader_status VALUES (:w, :s, :since, :seen, :r)"),
                dict(zip(("w", "s", "since", "seen", "r"), row, strict=True)),
            )


def _report(db: Database, worker: str, role: str) -> None:
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reader_status (worker, state, seen_at, role) VALUES (:w, 'working', now(), :r)"
            ),
            {"w": worker, "r": role},
        )


def _rows(db: Database, customer: str) -> int:
    with db.engine.begin() as conn:
        found = "SELECT count(*) FROM kaagaz_person_rule WHERE for_customer_id = :c"
        return int(conn.execute(text(found), {"c": customer}).scalar_one())


def test_on_is_refused_until_every_recent_worker_runs_as_kaagaz_worker_and_9b_is_in(
    db: Database, workers: None, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    customer, _ = new_customer(db)
    assert _run(db, "on", "--customer", customer)[0] == 2  # no worker reported
    assert "no worker reported" in capsys.readouterr().err
    _report(db, "w-1", "kaagaz_worker")
    _report(db, "w-2", "kaagaz_app")
    assert _run(db, "on", "--customer", customer)[0] == 2  # one still runs as the app role
    assert "another role than kaagaz_worker" in capsys.readouterr().err
    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM reader_status WHERE worker = 'w-2'"))
    assert _run(db, "on", "--customer", customer)[0] == 2  # 0091 is the head: PR 9b is not in
    assert "PR 9b (migration 0092) is not in yet" in capsys.readouterr().err
    assert _rows(db, customer) == 0
    monkeypatch.setattr(person_rule, "NEEDS_REVISION", "0091")  # as once 0092 is in
    code, said = _run(db, "on", "--customer", customer)
    assert (code, _rows(db, customer)) == (0, 1)
    assert said.startswith(f"person rule on for {customer}")
    assert f"on for {customer} since" in _run(db, "status")[1]
    # Off for one organisation while it is on for every one is no way back: refused, nothing changed.
    assert _run(db, "off", "--customer", customer)[0] == 2
    assert "still on for every organisation: use off --all" in capsys.readouterr().err
    assert _rows(db, customer) == 1
    with until_0092():  # the every-organisation row out of the way, put back after
        assert _run(db, "off", "--customer", customer)[0] == 0 and _rows(db, customer) == 0
        assert _run(db, "off", "--customer", customer)[0] == 2  # it was not on any more
        assert "it was not on for that organisation" in capsys.readouterr().err


def test_an_unknown_or_malformed_organisation_is_refused(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    for customer in ("not-a-uuid", str(uuid.uuid4())):
        assert _run(db, "off", "--customer", customer)[0] == 2
        assert "no such organisation" in capsys.readouterr().err


def test_off_needs_nothing_and_off_all_is_the_way_back(db: Database, workers: None) -> None:
    with until_0092():  # puts the session's every-organisation row back after
        _report(db, "w-1", "kaagaz_app")  # off is never refused
        code, said = _run(db, "off", "--all")
        assert (code, said.startswith("person rule off for every organisation")) == (0, True)
        assert _run(db, "status")[1] == "off\n"


def test_a_command_needs_a_customer_or_all(db: Database, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(db, "on")[0] == 2
    assert "--customer ID or --all" in capsys.readouterr().err


def test_the_cli_runs_as_the_worker_role_and_refuses_the_app_role_alone(db: Database) -> None:
    """DB_WORKER_ROLE unset (the app role): the owner's login may switch into kaagaz_worker, so the
    CLI takes it; a role set is kept."""
    assert _cli_role(db.engine, make_settings(db_worker_role="kaagaz_app")) == "kaagaz_worker"
    assert _cli_role(db.engine, make_settings(db_worker_role="kaagaz_worker")) == "kaagaz_worker"
