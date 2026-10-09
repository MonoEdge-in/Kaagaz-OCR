"""The "Needs attention" filter and its chips across customers (PRD 5 U2): each customer's
list, total and ``words_unsure`` come from its own pages only. A's flagged document never
shows in B's list, count or text, and B's never in A's; each sees its own (the control)."""

import pytest

from kaagaz.db.engine import Database
from tests.test_api_page_fixes import read_page
from tests.test_api_support import Api

pytestmark = pytest.mark.db


def test_needs_attention_and_its_chips_never_cross_customers(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed("reviewer"), api.new_signed("reviewer")
    mine = api.add_document(a.customer_id, name="mine.pdf")
    read_page(api, a.customer_id, mine, read_quality="uncertain", read_issues=["faint"], unsure_words=4)
    theirs = api.add_document(b.customer_id, name="sharma-flagged.pdf")
    read_page(api, b.customer_id, theirs, read_quality="unreadable", read_issues=["no_text"], unsure_words=7)

    for signed, own, words in ((a, mine, 4), (b, theirs, 7)):
        reply = signed.client.get("/api/documents", params={"attention": "true"})
        body = reply.json()
        assert [item["id"] for item in body["items"]] == [own]
        assert body["total"] == 1
        assert body["items"][0]["words_unsure"] == words
    assert "sharma" not in a.client.get("/api/documents", params={"attention": "true"}).text
