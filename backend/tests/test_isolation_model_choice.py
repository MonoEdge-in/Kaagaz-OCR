"""Model choice and AI spend across customers (council 11 Oct 2026, MS5, SP2): A's admin
never sees B's spend, in the report, the CSV or the month's figure; naming one of B's users
gives A an empty report; a model the operator allowed B is 403 on A's upload and Read
again, with nothing stored. Each customer sees its own (the controls). Through the whole
API (session, routes, SQL and row level security).
"""

import csv
import io

import pytest

from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_model_choice import add_model, allow, documents_of, reading, upload
from tests.test_api_support import CSRF, Api, MovingClock
from tests.test_api_usage import NOW, THIS_MONTH, add_spend

pytestmark = pytest.mark.db


def test_as_admin_never_sees_bs_spend(db: Database) -> None:
    api = Api(db, clock=MovingClock(NOW))
    a, b = api.new_admin("Sharma Traders"), api.new_admin("Mehta Textiles")
    b_member, _ = api.add_user(b.customer_id, "member")
    add_spend(db, b.customer_id, at=THIS_MONTH, cost=3.0, user_id=b_member)
    add_spend(db, a.customer_id, at=THIS_MONTH, cost=1.0)

    a_report = a.client.get("/api/admin/usage").json()
    b_report = b.client.get("/api/admin/usage").json()
    a_csv = list(csv.reader(io.StringIO(a.client.get("/api/admin/usage.csv").text)))
    a_models = a.client.get("/api/admin/models").json()

    assert (a_report["totals"]["calls"], a_report["by_user"][0]["user_id"]) == (1, None)
    assert a_report["budget"]["spent_this_month_usd"] == pytest.approx(1.0)
    assert len(a_csv) == 2  # the columns and A's one line
    assert a_models["spent_this_month_usd"] == pytest.approx(1.0)
    assert b_report["totals"]["calls"] == 1  # the control
    assert [item["user_id"] for item in b_report["by_user"]] == [b_member]


def test_naming_bs_user_gives_a_an_empty_report(db: Database) -> None:
    api = Api(db, clock=MovingClock(NOW))
    a, b = api.new_admin("Sharma Traders"), api.new_admin("Mehta Textiles")
    b_member, _ = api.add_user(b.customer_id, "member")
    add_spend(db, b.customer_id, at=THIS_MONTH, cost=3.0, user_id=b_member)

    report = a.client.get(f"/api/admin/usage?user_id={b_member}").json()
    lines = list(csv.reader(io.StringIO(a.client.get(f"/api/admin/usage.csv?user_id={b_member}").text)))

    assert report["totals"]["calls"] == 0
    assert [report[part] for part in ("by_user", "by_model", "by_kind", "by_day", "top_documents")] == [
        []
    ] * 5
    assert lines == [
        ["day", "user", "model", "kind", "calls", "tokens", "cost", "estimated", "part_not_known"]
    ]


def test_a_model_allowed_b_is_403_on_as_upload_and_read_again(db: Database) -> None:
    api = Api(db, settings=make_settings(ocr_engine="gemini"))
    a_customer, _ = api.customer("Sharma Traders")
    b_customer, _ = api.customer("Mehta Textiles")
    a = api.sign_in(a_customer, "reviewer")
    a_model, b_model = add_model(db, name="A's model"), add_model(db, name="B's model")
    allow(db, a_customer, [a_model], default=a_model)
    allow(db, b_customer, [b_model], default=b_model)
    document = api.add_document(a_customer, status="failed", failure_code="corrupt_file")

    refused = upload(a, read_model=b_model)
    again = a.client.post(f"/api/documents/{document}/reprocess", json={"read_model": b_model}, headers=CSRF)

    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "model_not_allowed")
    assert (again.status_code, again.json()["error"]["code"]) == (403, "model_not_allowed")
    assert documents_of(db, a_customer) == 1  # only the one added above
    assert reading(db, a_customer, document) == (None, None)
    assert api.queue.jobs == []
    assert [item["id"] for item in a.client.get("/api/upload-limits").json()["readers"]] == [a_model]
    assert upload(a, read_model=a_model).status_code == 201  # the control
