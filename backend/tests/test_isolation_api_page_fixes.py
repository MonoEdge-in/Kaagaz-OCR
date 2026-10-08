"""The fixes for one page across customers (PRD 5 F5): customer A cannot read again, or
keep as is, a page of customer B's document.

A's reviewer and admin, using B's document id and a page B has, always get 404, never
409 or 429, and nothing of B's is changed, audited, counted or queued: no page mark, no
acceptance, B's document still ready. The control: B's own reviewer then gets the real
answer for the same page, so A's 404 comes from the customer (session, routes, SQL and
row level security), never from a broken route or id.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_page_fixes import read_page
from tests.test_api_support import CSRF, Api

pytestmark = pytest.mark.db


def snapshot(api: Api, customer_id: str, document_id: str) -> tuple[object, ...]:
    with api.db.customer(customer_id) as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT d.status, d.attempts, p.read_again_requested_at, p.accepted_by, p.accepted_at,"
                    " (SELECT count(*) FROM audit_log a WHERE a.customer_id = d.customer_id"
                    " AND a.target_id = CAST(d.id AS text))"
                    " FROM documents d JOIN pages p ON p.customer_id = d.customer_id AND p.document_id = d.id"
                    " WHERE d.customer_id = :c AND d.id = :d AND p.page_no = 1"
                ),
                {"c": customer_id, "d": document_id},
            ).one()
        )


@pytest.mark.parametrize("action", ["read-again", "accept"])
@pytest.mark.parametrize("role", ["reviewer", "admin"])
def test_a_cannot_fix_a_page_of_bs_document(db: Database, action: str, role: str) -> None:
    api = Api(db)
    b_reviewer = api.new_signed("reviewer")
    a_user = api.new_signed(role)
    b_doc = api.add_document(b_reviewer.customer_id)
    read_page(api, b_reviewer.customer_id, b_doc, 1, read_quality="unreadable", read_issues=["blurry"])
    before = snapshot(api, b_reviewer.customer_id, b_doc)

    reply = a_user.client.post(f"/api/documents/{b_doc}/pages/1/{action}", headers=CSRF)

    assert (reply.status_code, reply.json()["error"]) == (
        404,
        {"code": "not_found", "message": "We couldn't find that."},
    )
    assert snapshot(api, b_reviewer.customer_id, b_doc) == before
    assert api.queue.jobs == []
    assert not [action for action in api.audit_actions(a_user.customer_id) if action.startswith("document.")]
    assert a_user.client.get(f"/api/documents/{b_doc}").status_code == 404

    own = b_reviewer.client.post(f"/api/documents/{b_doc}/pages/1/{action}", headers=CSRF)
    assert own.status_code == 200, own.text
    after = snapshot(api, b_reviewer.customer_id, b_doc)
    if action == "read-again":
        assert after[0] == "received" and after[2] is not None
        assert [(job.customer_id, job.document_id) for job in api.queue.jobs] == [
            (b_reviewer.customer_id, b_doc)
        ]
    else:
        assert after[3] is not None and str(after[3]) == b_reviewer.user_id
        assert api.queue.jobs == []
