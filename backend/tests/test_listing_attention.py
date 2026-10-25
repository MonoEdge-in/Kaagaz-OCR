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


def test_values_to_review_counts_every_value_still_waiting(db: Database) -> None:
    """The chip sums unsure and illegible words. Changed on purpose (QA issue #366, 25 Oct
    2026): a card value the model chose still waits to be verified, so it is counted now (it
    was left out under KZ-004, and the tray said "All values verified")."""
    api = Api(db)
    signed = api.new_signed("reviewer")
    c = signed.customer_id
    card = api.add_document(c, name="card.pdf")
    words = {"unsure_words": 2, "illegible_words": 3}
    read_page(api, c, card, read_quality="uncertain", read_issues=["faint"], **words)
    api.add_value(c, card, reason="model_chosen")
    api.add_value(c, card, field_name="gstin", reason="readers_disagree")
    only_card = api.add_document(c, name="only-card.pdf", created_at=api.clock() + timedelta(minutes=1))
    read_page(api, c, only_card)
    api.add_value(c, only_card, reason="model_chosen")

    by_id = {item["id"]: item for item in signed.client.get("/api/documents").json()["items"]}
    attention = signed.client.get("/api/documents", params={"attention": "true"}).json()

    assert (by_id[card]["values_to_review"], by_id[card]["words_unsure"]) == (2, 5)
    assert by_id[only_card]["values_to_review"] == 1
    assert {item["id"] for item in attention["items"]} == {only_card, card}


def test_values_to_review_is_what_the_review_queue_lists_for_the_document(db: Database) -> None:
    """QA #366: one definition of a value still waiting to be verified. A value read once
    (single_reader) is in the document's review queue, so the file list and the upload tray
    count it too, and the file is never "All values verified" while it waits."""
    api = Api(db)
    signed = api.new_signed("reviewer")
    c = signed.customer_id
    doc = api.add_document(c, name="invoice.pdf")
    read_page(api, c, doc)
    api.add_value(c, doc, field_name="invoice_date", reason="single_reader")
    api.add_value(c, doc, field_name="gstin", reason="check_failed")
    api.add_value(c, doc, field_name="total_amount", label="verified", reason="readers_agree")

    listed = {item["id"]: item for item in signed.client.get("/api/documents").json()["items"]}
    queue = signed.client.get("/api/review", params={"document_id": doc}).json()
    attention = signed.client.get("/api/documents", params={"attention": "true"}).json()

    assert queue["total"] == 2
    assert listed[doc]["values_to_review"] == queue["total"]
    assert signed.client.get(f"/api/documents/{doc}").json()["values_to_review"] == 2
    assert [item["id"] for item in attention["items"]] == [doc]
