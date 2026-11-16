"""PRD 7 PR 9a (#606): an Admin's names and numbers are the whole organisation's (decision 20), also
the files the database rule hides from them: 0091's kaagaz_admin_* functions (F12, F13). Each
checks its caller (an Admin, or the worker role with no person) and reads only the current
organisation. The rule is on for every organisation in the test session (conftest)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.db_support import violation
from tests.test_api_support import Api
from tests.test_isolation_access_views import Org, _app, _seen, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

CALLS = {
    "stats": "SELECT * FROM kaagaz_admin_stats()",
    "members": "SELECT * FROM kaagaz_admin_member_numbers(NULL, NULL)",
    "failed": "SELECT * FROM kaagaz_admin_failed(100, 0)",
    "reader_pages": "SELECT * FROM kaagaz_admin_reader_pages('-infinity', 'infinity')",
    "usage": "SELECT * FROM kaagaz_admin_usage('-infinity', 'infinity', 'UTC', NULL)",
    "usage_rows": "SELECT * FROM kaagaz_admin_usage_rows('-infinity', 'infinity', 'UTC', NULL)",
    "folder_summary": "SELECT * FROM kaagaz_admin_folder_summary(CAST(:finance AS uuid))",
    "names": "SELECT * FROM kaagaz_admin_document_names(CAST(:hidden AS uuid[]))",
    "given_to": "SELECT * FROM kaagaz_admin_given_to(CAST(:plain AS uuid))",
    "department_files": "SELECT kaagaz_admin_files(CAST(:finance AS uuid), NULL) AS files",
    "folder_files": "SELECT kaagaz_admin_files(NULL, CAST(:folders AS uuid[])) AS files",
}


def _org(api: Api) -> Org:
    """The walled organisation, with a failed file, a run and a model call, all in Finance (which
    its ``admin`` can't see)."""
    org = _walled(api)
    fin = org.files["fin"]
    first = _owner(api.db, "SELECT uploaded_by FROM documents WHERE id = :d", {"d": fin})[0][0]
    failed = "UPDATE documents SET status = 'failed', failure_code = 'unreadable' WHERE id = :d"
    _owner(api.db, failed, {"d": fin})
    _owner(
        api.db,
        "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status, pages_ocr)"
        " VALUES (:c, :d, now(), now(), 'failed', 3)",
        {"c": org.c, "d": fin},
    )
    _owner(
        api.db,
        "INSERT INTO model_usage (customer_id, user_id, document_id, kind, model, calls, input_tokens,"
        " output_tokens, reasoning_tokens, cost_usd)"
        " VALUES (:c, :u, :d, 'scan', 'google/test-model', 1, 1000, 100, 0, 1)",
        {"c": org.c, "u": first, "d": fin},
    )
    return org


def _read(database: Database, customer: str, user: str | None, name: str, org: Org) -> list[dict[str, Any]]:
    params = {
        "finance": org.folders["finance"],
        "hidden": [org.files["fin"], org.files["sal"]],
        "plain": org.people["plain"].user_id,
        "folders": [org.folders["finance"], org.folders["salaries"]],
    }
    with database.customer(customer) if user is None else database.user(customer, user) as conn:
        return [dict(row) for row in conn.execute(text(CALLS[name]), params).mappings()]


def test_an_admin_who_cant_see_finance_gets_its_names_and_numbers(db: Database) -> None:
    org = _org(Api(db))
    app, c, admin = _app(db), org.c, org.people["admin"].user_id
    assert org.files["fin"] not in _seen(app, c, admin)  # the rule and the view hide it from them
    got = {name: _read(app, c, admin, name, org) for name in CALLS}
    (stats,) = got["stats"]
    live = "SELECT count(*) FROM documents WHERE customer_id = :c AND deleted_at IS NULL AND is_current"
    by_status = stats["documents_by_status"]
    assert (sum(by_status.values()), by_status["failed"]) == (_owner(db, live, {"c": c})[0][0], 1)
    uploaded = {row["id"]: row["documents_uploaded"] for row in got["members"]}
    assert max(uploaded.values()) == len(org.files)  # the first admin uploaded every file
    assert [(row["id"], row["total"]) for row in got["failed"]] == [(uuid.UUID(org.files["fin"]), 1)]
    assert got["reader_pages"][0]["pages_ocr"] == 3
    documents = [row for row in got["usage"] if row["dimension"] == "document"]
    assert [row["document_name"] for row in documents] == ["fin.pdf"]
    assert [row["calls"] for row in got["usage_rows"]] == [1]
    # Finance and Salaries; fin, given and sal (the trashed file is not live).
    assert [(row["folders"], row["files"]) for row in got["folder_summary"]] == [(2, 3)]
    assert sorted(row["display_name"] for row in got["names"]) == ["fin.pdf", "sal.pdf"]
    assert [str(row["id"]) for row in got["given_to"]] == [org.files["given"]]
    # Finance's open files (fin and given), and every live file in Finance and Salaries (sal too).
    assert (got["department_files"], got["folder_files"]) == ([{"files": 2}], [{"files": 3}])
    # The worker role with no person reads the same (it sees the whole organisation anyway).
    assert _read(db, c, None, "stats", org) == got["stats"]


def test_the_api_role_with_no_person_is_refused(db: Database) -> None:
    """Only the worker role passes with no person named: the API role with none gets 42501, so an
    API transaction that forgot the person never reads an Admin's names."""
    org = _walled(Api(db))
    for name in CALLS:
        with pytest.raises(DBAPIError) as refused:
            _read(_app(db), org.c, None, name, org)
        assert violation(refused.value)[0] == "42501", name


@pytest.mark.parametrize("person", ["plain", "member", "management", "head", "off"])
def test_a_person_who_is_not_an_admin_is_refused(db: Database, person: str) -> None:
    """Management and a Head too; a person switched off is no Admin either."""
    org = _walled(Api(db))
    for name in CALLS:
        with pytest.raises(DBAPIError) as refused:
            _read(_app(db), org.c, org.people[person].user_id, name, org)
        assert violation(refused.value)[0] == "42501", name


def test_another_organisations_admin_reads_none_of_it(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    other, _ = api.customer()
    theirs = api.admin_sign_in(other)
    got = {name: _read(_app(db), other, theirs.user_id, name, org) for name in CALLS}
    assert got["stats"][0]["documents_by_status"] == {}
    assert {str(row["id"]) for row in got["members"]} <= {
        str(row[0]) for row in _owner(db, "SELECT id FROM users WHERE customer_id = :c", {"c": other})
    }
    assert [(row["id"], row["total"]) for row in got["failed"]] == [(None, 0)]
    assert set(got["reader_pages"][0].values()) == {0}
    assert [row["dimension"] for row in got["usage"]] == ["total"] and got["usage"][0]["calls"] is None
    assert got["usage_rows"] == [] and got["names"] == [] and got["given_to"] == []
    assert [row["folders"] for row in got["folder_summary"]] == [0]
    assert (got["department_files"], got["folder_files"]) == ([{"files": 0}], [{"files": 0}])
    assert not any(name in str(got) for name in ("fin.pdf", "sal.pdf", org.files["fin"]))
