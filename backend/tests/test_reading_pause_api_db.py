"""The reading pause through the API (#478, Tushar, 3 Nov 2026): the super admin sets the daily
page limit with a fresh code on the organisation's AI settings (and as a platform default), it is
audited in the operator's log only, the console shows the pages read today; the org admin's Usage
screen says the pages today against the limit and never money; Ask keeps working while reading is
paused."""

import re
from typing import Any

import pytest

from kaagaz.db import reading_days
from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api
from tests.test_api_usage import _gets, add_spend, set_budget
from tests.test_migration_0023_db import as_owner
from tests.test_operator_models_db import FOLLOWS_DEFAULTS, _save, _signed_in
from tests.test_reading_pause_db import set_limit

pytestmark = pytest.mark.db

MONEY = re.compile(r"usd|price|budget|cost|spend|\$|₹", re.IGNORECASE)  # never to an organisation
BODY: dict[str, Any] = {**FOLLOWS_DEFAULTS, "ensemble": False, "ai_budget_usd": None}


def _count_today(api: Api, customer: str, pages: int, usd: float = 0.0) -> None:
    with api.db.customer(customer) as conn:
        reading_days.count(conn, customer, reading_days.india_day(api.clock()), pages=pages, cost_usd=usd)


def test_the_super_admin_sets_the_limit_with_a_fresh_code_and_only_the_operators_log_holds_it(
    db: Database,
) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    customer, _ = api.customer("Org Pause")
    path = f"/api/operator/orgs/{customer}/ai"
    _count_today(api, customer, 1240, 0.42)

    no_code = client.put(path, json={**BODY, "daily_page_limit": 2000, "code": "000000"}, headers=CSRF)
    assert (no_code.status_code, no_code.json()["error"]["code"]) == (403, "step_up_failed")
    for bad in (0, 1_000_001):  # refused before a code is used up
        refused = client.put(path, json={**BODY, "daily_page_limit": bad, "code": "000000"}, headers=CSRF)
        assert (refused.status_code, refused.json()["error"]["code"]) == (400, "pages_out_of_range")
    assert client.get(path).json()["own"]["daily_page_limit"] is None

    saved = _save(api, operator, client, path, {**BODY, "daily_page_limit": 2000})
    assert saved.status_code == 200, saved.text
    got = client.get(path).json()
    assert got["own"]["daily_page_limit"] == 2000 and got["defaults"]["daily_page_limit"] is None
    assert got["reading_today"] == {"pages": 1240, "cost_usd": 0.42}  # the operator sees money

    [(platform,)] = as_owner(
        db,
        "SELECT details FROM platform_audit WHERE subject_customer_id = :c"
        " AND action = 'org.ai_settings_set'",
        {"c": customer},
    )
    [(own,)] = as_owner(
        db,
        "SELECT details FROM audit_log WHERE customer_id = :c AND action = 'org.ai_settings_set'",
        {"c": customer},
    )
    assert platform["daily_page_limit"] == 2000 and "daily_page_limit" not in own


def test_a_platform_default_limit_is_inherited_and_counted(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    customer, _ = api.customer("Org Inherits")
    before = client.get("/api/operator/ai-defaults").json()["defaults"]
    try:
        body = {**before, "daily_page_limit": 500}
        saved = _save(api, operator, client, "/api/operator/ai-defaults", body)
        assert saved.status_code == 200, saved.text
        reply = saved.json()
        assert reply["defaults"]["daily_page_limit"] == 500
        assert reply["orgs_inheriting"]["daily_page_limit"] >= 1
        with db.customer(customer) as conn:
            org = reading_days.state(conn, customer, reading_days.india_day(api.clock()))
        assert org.page_limit == 500  # its own is empty: the platform's
        set_limit(db, customer, 50)
        with db.customer(customer) as conn:
            assert reading_days.state(conn, customer, reading_days.india_day(api.clock())).page_limit == 50
    finally:  # the platform defaults as they were, for the other tests
        assert _save(api, operator, client, "/api/operator/ai-defaults", before).status_code == 200


def test_the_org_admin_sees_pages_today_against_the_limit_and_never_money(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    customer = admin.customer_id
    set_budget(db, customer, 10.0)
    _count_today(api, customer, 1240, 3.5)
    _, report = _gets(admin, "/api/admin/usage")
    assert report["reading_today"] == {"pages": 1240, "limit": None}
    set_limit(db, customer, 2000)
    status, report = _gets(admin, "/api/admin/usage")
    assert status == 200 and report["reading_today"] == {"pages": 1240, "limit": 2000}
    assert not MONEY.search(str(report["reading_today"]))
    member = api.sign_in(customer, "member")
    assert _gets(member, "/api/admin/usage")[0] == 403


def test_ask_keeps_working_while_reading_is_paused(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    customer = signed.customer_id
    set_budget(db, customer, 1.0)
    add_spend(db, customer, at=api.clock(), cost=5.0, kind="chat")  # the budget is reached
    set_limit(db, customer, 1)
    _count_today(api, customer, 1)  # and the daily page limit
    reply = signed.client.post("/api/chat", json={"question": "total for March?"}, headers=CSRF)
    assert reply.status_code == 200, reply.text
    assert reply.json()["answer"]["text"] == "The total is 1,200."
