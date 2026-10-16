"""Word review across customers: customer A cannot read or change customer B's flags,
history or changes (kaagaz/api/routers/word_review.py, 0030).

A's reviewer and admin, using B's document id, get 404 on every word review route, never
200, 403 or 409, and nothing of B's is written or audited. With A's own document, B's
change id is 404 too. The control: B's own reviewer then gets the real answer
for the same ids, so A's 404 comes from the customer (session, routes, SQL and row level
security), never from a broken route or id.
"""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api
from tests.test_api_word_review import flags, post, scan

pytestmark = pytest.mark.db

NOT_FOUND = {"code": "not_found", "message": "We couldn't find that."}


def snapshot(api: Api, customer_id: str, document_id: str) -> tuple[object, ...]:
    with api.db.customer(customer_id) as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT (SELECT count(*) FROM word_changes w WHERE w.document_id = d.id),"
                    " (SELECT count(*) FROM word_changes w WHERE w.document_id = d.id"
                    " AND w.undone_at IS NOT NULL),"
                    " (SELECT string_agg(v.value_text || v.label, ',') FROM extracted_values v"
                    " WHERE v.document_id = d.id),"
                    " (SELECT string_agg(p.text, ',' ORDER BY p.seq) FROM pieces p"
                    " WHERE p.document_id = d.id),"
                    " (SELECT count(*) FROM audit_log a WHERE a.customer_id = d.customer_id)"
                    " FROM documents d WHERE d.customer_id = :c AND d.id = :d"
                ),
                {"c": customer_id, "d": document_id},
            ).one()
        )


@pytest.mark.parametrize("role", ["reviewer", "admin"])
def test_a_cannot_reach_bs_word_review(db: Database, role: str) -> None:
    api = Api(db)
    b_reviewer = api.new_signed("reviewer")
    b_doc = scan(api, b_reviewer.customer_id)
    b_flag = flags(b_reviewer, b_doc)[0]
    b_key = b_flag["word_key"]
    made = post(
        b_reviewer, f"/api/documents/{b_doc}/flags/{b_key}/confirm", {"expected_text": b_flag["text"]}
    )
    assert made.status_code == 200, made.text
    b_change = b_reviewer.client.get(f"/api/documents/{b_doc}/changes").json()["items"][0]["id"]
    a_user = api.new_signed(role)
    a_doc = scan(api, a_user.customer_id)
    before = snapshot(api, b_reviewer.customer_id, b_doc)
    b_base = f"/api/documents/{b_doc}"

    calls = [
        a_user.client.get(f"{b_base}/review-status"),
        a_user.client.get(f"{b_base}/flags"),
        a_user.client.get(f"{b_base}/changes"),
        post(a_user, f"{b_base}/flags/{b_key}/confirm", {"expected_text": b_flag["text"]}),
        post(a_user, f"{b_base}/flags/{b_key}/edit", {"text": "x", "expected_text": b_flag["text"]}),
        post(a_user, f"{b_base}/changes/{b_change}/undo"),
        # A's own document with B's change id. (A word key is a hash of what was read, not
        # an id: the same words on A's own page have the same key, and only A's rows answer.)
        post(a_user, f"/api/documents/{a_doc}/changes/{b_change}/undo"),
        a_user.client.get(f"/api/documents/{a_doc}/changes?before={b_change}"),
    ]

    assert [(reply.status_code, reply.json()["error"]) for reply in calls] == [(404, NOT_FOUND)] * len(calls)
    assert snapshot(api, b_reviewer.customer_id, b_doc) == before
    assert not [
        action for action in api.audit_actions(a_user.customer_id) if action.startswith("word_review.")
    ]

    own = b_reviewer.client.post(f"{b_base}/changes/{b_change}/undo", headers=CSRF)
    assert own.status_code == 200, own.text
    assert snapshot(api, b_reviewer.customer_id, b_doc)[1] == 1
