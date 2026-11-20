"""#718, #719: an answer's folder paths and the files it searched show only what the asker can see.

PR 6's walled organisation (test_isolation_access_views.py): Finance is a department only its
people see, Salaries a private folder inside it with ``named`` on it, and ``plain`` is given one
Finance file. Every statement runs as the person, under row level security (``_app``)."""

import json

import pytest

from kaagaz.answering.service import AnswerService
from kaagaz.answering.sources import PgPaths
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_answering_golden import NOW
from tests.test_api_support import Api
from tests.test_isolation_access_views import _app, _walled
from tests.test_isolation_ask_views import HIDDEN, AskOrg, _ask_org
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db


def test_a_path_stops_below_the_first_folder_the_asker_cannot_see(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    other, _ = api.customer()
    elsewhere = api.add_document(other, name="elsewhere.pdf")
    files = {name: org.files[name] for name in ("fin", "sal", "sales", "top", "given")}
    ids = [*files.values(), elsewhere]
    paths = PgPaths(_app(db))

    def seen(person: str) -> dict[str, list[str] | None]:
        found = paths.paths(org.c, ids, user_id=org.people[person].user_id)
        return {name: found[d] for name, d in files.items() if d in found}

    whole = {
        "fin": ["Finance"],
        "sal": ["Finance", "Salaries"],
        "sales": ["Sales"],
        "top": [],
        "given": ["Finance"],
    }
    assert seen("management") == whole
    # An Admin sees every folder's name (0086): given a Salaries file, its whole path.
    _owner(
        db,
        "INSERT INTO document_shares (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :by)",
        {
            "c": org.c,
            "d": files["sal"],
            "u": org.people["admin"].user_id,
            "by": org.people["management"].user_id,
        },
    )
    admin = seen("admin")
    assert admin["sal"] == ["Finance", "Salaries"]
    assert all(path == whole[name] for name, path in admin.items())
    # On Salaries but not in Finance: Salaries is named, Finance above it never is.
    named = seen("named")
    assert named["sal"] == ["Salaries"]
    assert "Finance" not in json.dumps(named)
    # Given one Finance file: its folder is hidden, so it has no path ("Shared with you").
    plain = seen("plain")
    assert plain["given"] is None and plain["top"] == [] and plain["sales"] == ["Sales"]
    assert {"fin", "sal"}.isdisjoint(plain) and "Finance" not in json.dumps(plain)
    # Another organisation's document is never found, by anyone.
    for person in org.people:
        assert elsewhere not in paths.paths(org.c, ids, user_id=org.people[person].user_id)


@pytest.fixture
def ask(db: Database) -> AskOrg:
    return _ask_org(Api(db))


def test_the_files_searched_and_their_paths_are_only_ones_the_asker_can_open(
    db: Database, ask: AskOrg
) -> None:
    service = AnswerService(
        db=_app(db), model=None, settings=make_settings(model_embed="test-embed"), clock=lambda: NOW
    )
    for person in ("plain", "member"):
        answer = service.answer(ask.org.c, "ledger", user_id=ask.user(person)).to_json()
        searched = {f["document_id"]: f for f in answer["searched_files"]}
        hidden = {ask.id(name) for name in HIDDEN[person]}
        assert searched and not set(searched) & hidden, person
        assert {d["document_id"] for d in answer["documents"]}.isdisjoint(hidden), person
        assert searched[ask.id("sales")]["path"] == ["Sales"], person
        if person == "plain":  # given, in Finance, which the plain reviewer can't see
            assert searched[ask.id("given")]["path"] is None
            assert "Finance" not in json.dumps(answer)
        else:
            assert searched[ask.id("fin")]["path"] == ["Finance"]
