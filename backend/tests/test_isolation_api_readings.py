"""Scanned text across customers: B, signed in, asks for A's page with A's exact
ids and gets "not found" with none of A's text. The control: A asks the same and
gets the lines, so B's 404 comes from the customer, never from an empty fixture.
"""

from typing import Any

import pytest

from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import span_to_json
from kaagaz.reading.spans import BoxLocation, Span
from tests.test_api_page_fixes import read_page
from tests.test_api_support import Api

pytestmark = pytest.mark.db


def test_b_gets_404_and_no_text_for_a_page_of_a(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    document_id = api.add_document(a.customer_id, detected_type="png", name="sharma.png")
    api.add_page(a.customer_id, document_id, unit="px")
    span = span_to_json(Span("sharma line", "sharma line", BoxLocation(1, 0.1, 0.1, 0.9, 0.2), 0.95))
    api.add_reader_output(a.customer_id, document_id, [span])
    path = f"/api/documents/{document_id}/pages/1/readings"

    own = a.client.get(path)
    assert own.status_code == 200  # the control
    assert own.json()["readers"][0]["lines"][0]["text"] == "sharma line"

    other = b.client.get(path)
    assert other.status_code == 404
    assert other.json()["error"] == {"code": "not_found", "message": "We couldn't find that."}
    assert "sharma" not in other.text

    signed_out = api.client().get(path)
    assert signed_out.status_code == 401
    assert "sharma" not in signed_out.text


def test_b_cannot_see_a_reading_counts_or_review_page_fields(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed("reviewer"), api.new_signed("reviewer")
    a_document = api.add_document(a.customer_id, name="a-private.pdf", page_count=2)
    read_page(api, a.customer_id, a_document, 1, read_quality="unreadable", read_issues=["blurry"])
    read_page(
        api,
        a.customer_id,
        a_document,
        2,
        read_quality="uncertain",
        read_issues=["faint"],
        unsure_words=2,
        unpinned_lines=1,
    )
    a_value = api.add_value(a.customer_id, a_document, page_no=2, reason="low_confidence")

    b_document = api.add_document(b.customer_id, name="b-private.pdf")
    read_page(api, b.customer_id, b_document)
    b_value = api.add_value(b.customer_id, b_document, reason="ocr_script")

    a_listing = {item["id"]: item for item in a.client.get("/api/documents").json()["items"]}
    b_listing = {item["id"]: item for item in b.client.get("/api/documents").json()["items"]}
    assert set(a_listing) == {a_document}
    assert (
        a_listing[a_document]["pages_unreadable"],
        a_listing[a_document]["pages_uncertain"],
        a_listing[a_document]["values_to_review"],
    ) == (1, 1, 1)
    assert set(b_listing) == {b_document}
    assert (
        b_listing[b_document]["pages_unreadable"],
        b_listing[b_document]["pages_uncertain"],
        b_listing[b_document]["values_to_review"],
    ) == (0, 0, 1)

    a_review = a.client.get("/api/review").json()["items"]
    b_review = b.client.get("/api/review").json()["items"]
    assert [item["value"]["id"] for item in a_review] == [a_value]
    assert (a_review[0]["document_id"], a_review[0]["page"]["read_quality"]) == (
        a_document,
        "uncertain",
    )
    assert a_review[0]["page"]["read_issues"] == ["faint"]
    assert (a_review[0]["page"]["unsure_words"], a_review[0]["page"]["unpinned_lines"]) == (2, 1)
    assert [item["value"]["id"] for item in b_review] == [b_value]
    assert (b_review[0]["document_id"], b_review[0]["page"]["read_quality"]) == (b_document, "good")


def test_the_reading_marks_are_a_s_own_and_b_gets_none_of_them(db: Database) -> None:
    """PRD 5 U3: an unsure and an illegible word mark, and an unpinned line, on A's page 1;
    a pinned line, a mark of page 2 and the line text itself are not marks."""
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    document_id = api.add_document(a.customer_id, detected_type="png", name="sharma.png")
    api.add_page(a.customer_id, document_id, unit="px")

    def ocr(
        text: str,
        page: int = 1,
        *,
        kind: str | None = None,
        quality: str | None = None,
        box_source: str = "found",
    ) -> dict[str, Any]:
        location = BoxLocation(page, 0.1, 0.1, 0.9, 0.2)
        span = Span(
            text, text, location, 0.9, kind=kind, origin="ocr", quality=quality, box_source=box_source
        )
        return span_to_json(span)

    spans = [
        ocr("sharma total 1,180 [?]", kind="paragraph"),
        ocr("1,180", quality="unsure"),
        ocr("[?]", quality="illegible"),
        ocr("sharma stamp", kind="stamp", box_source="model"),
        ocr("page two", page=2, quality="unsure"),
    ]
    api.add_reader_output(a.customer_id, document_id, spans, reader="gemini")
    path = f"/api/documents/{document_id}/pages/1/readings"

    own = a.client.get(path)
    assert own.status_code == 200  # the control
    box = {"kind": "box", "page": 1, "box": [0.1, 0.1, 0.9, 0.2]}
    assert own.json()["marks"] == [
        {"quality": "unsure", "region": box, "text": "1,180"},
        {"quality": "illegible", "region": box, "text": "[?]"},
        {"quality": "unpinned", "region": box, "text": "sharma stamp"},
    ]
    other = b.client.get(path)
    assert other.status_code == 404
    assert "sharma" not in other.text and "marks" not in other.text
