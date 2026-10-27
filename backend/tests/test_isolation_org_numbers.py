"""Each organisation's numbers for the operator (#387, 0055's ``kaagaz_op_org_numbers``):
only a signed-in operator reaches them, never an organisation's admin, member or the app's
own database role; a disabled operator, or one whose two-step secret is not confirmed, gets
nothing; each organisation's row holds its own numbers, and no reply holds a person, a file
or any id but an organisation's. The numbers themselves: ``test_analytics_recount_db.py``."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import operator_insights as insights
from kaagaz.db.engine import Database
from kaagaz.db.sqlstate import sqlstate
from tests.test_analytics_recount_db import ZONE, _day, _document, expected_org, plant
from tests.test_api_support import CSRF, Api
from tests.test_operator_auth_db import _DISABLED, _Operator
from tests.test_operator_insights_db import read
from tests.test_operator_orgs_db import _signed_in

pytestmark = pytest.mark.db

ORG_NUMBERS = "/api/operator/org-numbers"


def test_each_organisation_gets_its_own_numbers_and_nothing_of_a_person(db: Database) -> None:
    api = Api(db)
    admin_a, admin_b = api.new_admin("Numbers A"), api.new_admin("Numbers B")
    a, b = plant(api, admin_a), plant(api, admin_b)
    _document(
        db, b, "m1", api.clock() - timedelta(days=1), [("ocr", "rapidocr")] * 7, display_name="secret-b.pdf"
    )
    _operator, client = _signed_in(api)
    reply = client.get(ORG_NUMBERS, params={"days": 30})
    assert reply.status_code == 200, reply.text
    rows = {row["organisation_id"]: row for row in reply.json()["organisations"]}
    today = api.clock().astimezone(ZONE).date()
    start = today - timedelta(days=29)
    window = (_day(start), _day(today + timedelta(days=1)))
    for org in (a, b):
        got = {key: value for key, value in rows[org.customer_id].items() if key != "organisation_id"}
        assert got == expected_org(db, org.customer_id, window), org.customer_id
    assert (
        rows[b.customer_id]["pages_by_route"]["local"] == rows[a.customer_id]["pages_by_route"]["local"] + 7
    )
    for org, admin in ((a, admin_a), (b, admin_b)):
        for hidden in (*org.people.values(), admin.email, "secret-b.pdf"):
            assert hidden not in reply.text, hidden
    for row in reply.json()["organisations"]:
        assert [key for key in row if key.endswith("_id") and key != "organisation_id"] == []


def test_no_organisation_session_reaches_the_numbers(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin("Numbers C")
    plant(api, admin)
    member = api.sign_in(admin.customer_id, "member")
    for client in (admin.client, member.client, api.client()):
        reply = client.get(ORG_NUMBERS, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")


def test_a_disabled_or_unconfirmed_operator_gets_nothing(db: Database) -> None:
    api = Api(db)
    plant(api, api.new_admin("Numbers D"))
    operator, client = _signed_in(api)
    today = api.clock().astimezone(ZONE).date()
    assert client.get(ORG_NUMBERS).status_code == 200
    assert read(db, operator.id, insights.org_numbers, today, today, "UTC")  # the control
    unconfirmed = _Operator(api, confirmed=False)
    assert read(db, unconfirmed.id, insights.org_numbers, today, today, "UTC") == []
    operator.sql(_DISABLED, d=True)
    assert read(db, operator.id, insights.org_numbers, today, today, "UTC") == []
    reply = client.get(ORG_NUMBERS)
    assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")


def test_the_app_role_cannot_run_the_function(db: Database) -> None:
    with pytest.raises(DBAPIError) as error, db.system() as conn:
        conn.execute(
            text("SELECT * FROM kaagaz_op_org_numbers(gen_random_uuid(), current_date, current_date, 'UTC')")
        )
    assert sqlstate(error.value) == "42501"  # insufficient privilege
