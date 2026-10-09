"""The "Needs attention" filter and the unsure words chip (PRD 5 U2) against PostgreSQL:
``attention=true`` lists the documents with a page unreadable or uncertain still on the
attention list, or a value to review, and the total counts the same; a page kept as is
has left it. ``words_unsure`` sums the unsure words of the pages still on the list.
Another customer's documents: ``test_isolation_listing_attention.py``."""

from datetime import timedelta

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_page_fixes import read_page
from tests.test_api_support import Api

pytestmark = pytest.mark.db


def test_needs_attention_lists_only_documents_with_a_problem_left(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed("reviewer")
    c = signed.customer_id
    clean = api.add_document(c, name="clean.pdf")
    read_page(api, c, clean)
    unsure = api.add_document(c, name="unsure.pdf", created_at=api.clock() + timedelta(minutes=1))
    read_page(api, c, unsure, read_quality="uncertain", read_issues=["handwriting_hard"], unsure_words=9)
    read_page(api, c, unsure, 2, read_quality="uncertain", read_issues=["faint"], unsure_words=5)
    kept = api.add_document(c, name="kept.pdf", created_at=api.clock() + timedelta(minutes=2))
    read_page(api, c, kept, read_quality="unreadable", read_issues=["blurry"], unsure_words=3)
    with api.db.customer(c) as conn:
        conn.execute(
            text(
                "UPDATE pages SET accepted_by = :u, accepted_at = now()"
                " WHERE customer_id = :c AND document_id = :d"
            ),
            {"u": signed.user_id, "c": c, "d": kept},
        )
    to_check = api.add_document(c, name="values.pdf", created_at=api.clock() + timedelta(minutes=3))
    read_page(api, c, to_check)
    api.add_value(c, to_check)

    everything = signed.client.get("/api/documents").json()
    attention = signed.client.get("/api/documents", params={"attention": "true"}).json()

    assert everything["total"] == 4
    assert [item["id"] for item in attention["items"]] == [to_check, unsure]  # newest first
    assert attention["total"] == 2
    by_id = {item["id"]: item for item in everything["items"]}
    assert {name: by_id[doc]["words_unsure"] for name, doc in (("unsure", unsure), ("kept", kept))} == {
        "unsure": 14,
        "kept": 0,  # kept as is: off the list, so no chip
    }
    assert signed.client.get("/api/documents", params={"attention": "maybe"}).status_code == 400
