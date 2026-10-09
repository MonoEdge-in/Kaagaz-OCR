"""The reader status against PostgreSQL (PRD 5 U9, 0022): only an enabled operator reads
it, only through ``kaagaz_op_reader``, and only the reports of the last 10 minutes; the
app and a customer's admin cannot run the function; Health shows each report with exactly
its four keys, and no customer id."""

import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import reader_status
from kaagaz.db.engine import Database, OperatorDatabase
from tests.db_support import violation
from tests.test_api_support import Api
from tests.test_operator_activity import HEALTH
from tests.test_operator_auth_db import _DISABLED, _Operator, _owner
from tests.test_operator_health_reader import READER_KEYS
from tests.test_operator_orgs_db import _signed_in

pytestmark = pytest.mark.db

_STALE = "UPDATE reader_status SET seen_at = now() - interval '11 minutes' WHERE worker = :w"


def _workers(rows: Sequence[RowMapping]) -> list[str]:
    return [str(row["worker"]) for row in rows]


def test_only_an_enabled_operator_reads_the_reports_of_the_last_10_minutes(db: Database) -> None:
    api = Api(db)
    fresh, stale = f"w-{uuid.uuid4().hex[:12]}", f"w-{uuid.uuid4().hex[:12]}"
    reader_status.report(db, fresh, "key_problem", None)
    reader_status.report(db, stale, "working", None)
    _owner(db, _STALE, {"w": stale})
    operator = _Operator(api)
    disabled, unconfirmed = _Operator(api), _Operator(api, confirmed=False)
    disabled.sql(_DISABLED, d=True)

    with OperatorDatabase(db.engine).operator() as conn:
        shown = _workers(reader_status.read(conn, operator.id))
        assert fresh in shown
        assert stale not in shown
        for refused in (str(uuid.uuid4()), disabled.id, unconfirmed.id):
            assert reader_status.read(conn, refused) == [], refused


def test_the_app_role_cannot_run_the_function(db: Database) -> None:
    api = Api(db)
    operator = _Operator(api)
    customer, _ = api.customer()
    for begin in (db.system, lambda: db.customer(customer)):
        with pytest.raises(DBAPIError) as refused, begin() as conn:
            conn.execute(text("SELECT * FROM kaagaz_op_reader(:o)"), {"o": operator.id})
        assert violation(refused.value)[0] == "42501", begin  # insufficient_privilege


def test_health_shows_the_reports_with_their_keys_only_and_no_customer(db: Database) -> None:
    api = Api(db)
    worker = f"w-{uuid.uuid4().hex[:12]}"
    reader_status.report(db, worker, "failing", None)
    _, client = _signed_in(api)
    admin = api.new_admin()

    reply = client.get(HEALTH)
    assert reply.status_code == 200, reply.text
    reports = [report for report in reply.json()["reader"] if report["worker"] == worker]
    assert [set(report) for report in reports] == [READER_KEYS]
    assert (reports[0]["state"], reports[0]["since"]) == ("failing", None)
    assert admin.customer_id not in str(reply.json()["reader"])
    assert reply.json()["queues"] == {"express": 0, "main": 0}

    refused = admin.client.get(HEALTH)
    assert (refused.status_code, refused.json()["error"]["code"]) == (401, "not_signed_in")
