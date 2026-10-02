"""The viewer panels and the failed jobs tab across customers (PRD 3 Y2, Y5, Z1).

Each test has its control: the same request by the owning customer shows the
row, so the other customer's empty list or 404 comes from the customer, never
from an empty fixture or a broken route.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api
from tests.test_api_viewer_panels import _LINK, link, links_of

pytestmark = pytest.mark.db


def test_b_never_sees_the_failed_documents_of_a(db: Database) -> None:
    api = Api(db)
    a_admin, b_admin = api.new_admin("A"), api.new_admin("B")
    a_doc = api.add_document(a_admin.customer_id, status="failed", failure_code="ocr_failed")
    b_doc = api.add_document(b_admin.customer_id, status="needs_reader")

    a_ids = [item["id"] for item in a_admin.client.get("/api/admin/failed").json()["items"]]
    b_body = b_admin.client.get("/api/admin/failed").json()

    assert a_ids == [a_doc]  # the control
    assert ([item["id"] for item in b_body["items"]], b_body["total"]) == ([b_doc], 1)


def test_retry_on_a_document_of_another_customer_is_404_and_changes_nothing(db: Database) -> None:
    api = Api(db)
    a_admin, b_admin = api.new_admin("A"), api.new_admin("B")
    a_doc = api.add_document(a_admin.customer_id, status="failed", failure_code="ocr_failed")

    reply = b_admin.client.post(f"/api/documents/{a_doc}/reprocess", headers=CSRF)

    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert api.queue.jobs == []
    assert [item["id"] for item in a_admin.client.get("/api/admin/failed").json()["items"]] == [a_doc]
    own = a_admin.client.post(f"/api/documents/{a_doc}/reprocess", headers=CSRF)
    assert own.status_code == 200, own.text  # the control


def test_a_link_can_never_point_at_another_customers_document(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    a_bill, a_order = api.add_document(a.customer_id), api.add_document(a.customer_id)
    b_bill = api.add_document(b.customer_id)
    link(db, a.customer_id, a_bill, a_order)

    with pytest.raises(IntegrityError), db.customer(a.customer_id) as conn:
        params = {"c": a.customer_id, "a": a_bill, "b": b_bill, "k": "invoice_number", "v": "inv-1"}
        conn.execute(_LINK, params)

    assert [each["id"] for each in links_of(a, a_bill)] == [a_order]  # the control
    assert b.client.get(f"/api/documents/{a_bill}").status_code == 404


def test_the_reason_filter_shows_only_this_customers_values(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed("reviewer"), api.new_signed("reviewer")
    ids = {}
    for signed in (a, b):
        doc = api.add_document(signed.customer_id)
        api.add_page(signed.customer_id, doc)
        ids[signed.customer_id] = api.add_value(signed.customer_id, doc, reason="check_failed")

    for signed in (a, b):
        body = signed.client.get("/api/review", params={"reason": "check_failed"}).json()
        assert ([item["value"]["id"] for item in body["items"]], body["total"]) == (
            [ids[signed.customer_id]],
            1,
        )
    with db.customer(a.customer_id) as conn:  # RLS too: B's value is not even there
        assert conn.execute(text("SELECT count(*) FROM extracted_values")).scalar_one() == 1
