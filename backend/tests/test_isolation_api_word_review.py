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
from tests.test_api_word_review import flags, post, scan, word

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


def kept_pages(api: Api, customer_id: str, document_id: str) -> list[int]:
    with api.db.customer(customer_id) as conn:
        found = conn.execute(
            text("SELECT page_no FROM pages WHERE document_id = :d AND accepted_at IS NOT NULL ORDER BY 1"),
            {"d": document_id},
        )
        return [int(page_no) for (page_no,) in found]


def test_a_page_kept_by_its_last_word_is_only_ever_the_customers_own(db: Database) -> None:
    # #443: settling a page's last review item keeps the page as is. A's change keeps A's page 2
    # only; B's same scan (same page, same word key) is untouched, and A's undo puts back A's alone.
    api = Api(db)
    a_user, b_user = api.new_signed("reviewer"), api.new_signed("reviewer")
    a_doc, b_doc = scan(api, a_user.customer_id), scan(api, b_user.customer_id)
    a_line = word(flags(a_user, a_doc), "Bill to Patil Stores")
    b_line = word(flags(b_user, b_doc), "Bill to Patil Stores")
    assert a_line["word_key"] == b_line["word_key"]
    confirm = "/api/documents/{}/flags/{}/confirm"

    refused = post(a_user, confirm.format(b_doc, b_line["word_key"]), {"expected_text": b_line["text"]})
    assert refused.status_code == 404
    assert kept_pages(api, b_user.customer_id, b_doc) == []

    made = post(a_user, confirm.format(a_doc, a_line["word_key"]), {"expected_text": a_line["text"]})
    assert made.status_code == 200, made.text
    assert kept_pages(api, a_user.customer_id, a_doc) == [2]
    assert kept_pages(api, b_user.customer_id, b_doc) == []

    own = post(b_user, confirm.format(b_doc, b_line["word_key"]), {"expected_text": b_line["text"]})
    assert own.status_code == 200, own.text
    change = a_user.client.get(f"/api/documents/{a_doc}/changes").json()["items"][0]["id"]
    undone = post(a_user, f"/api/documents/{a_doc}/changes/{change}/undo")
    assert undone.status_code == 200, undone.text
    assert kept_pages(api, a_user.customer_id, a_doc) == []
    assert kept_pages(api, b_user.customer_id, b_doc) == [2]
