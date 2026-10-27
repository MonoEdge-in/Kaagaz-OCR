"""Read clearly (19 Oct 2026) against PostgreSQL: the clean copy has every page's lines
with their table place and marks; customer B gets 404 and none of A's text. The page
translation route is gone (issue 358): a POST to it is 404 and calls no model."""

# Changed on purpose (issue 358, 27 Oct 2026): the page translation route had no caller since
# #328 and was removed; its tests went with it, and one checks the route stays gone.

from typing import Any

import pytest

from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import span_to_json
from kaagaz.reading.spans import BoxLocation, Span
from tests.test_answering_golden import FakeModel
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db


def _span(page: int, y: float, raw: str, **extra: Any) -> dict[str, Any]:
    return span_to_json(Span(raw, raw, BoxLocation(page, 0.1, y, 0.9, y + 0.04), 0.95, origin="ocr", **extra))


def _document(api: Api, signed: Signed) -> str:
    document_id = api.add_document(signed.customer_id, detected_type="png", name="sharma.png")
    for page_no in (1, 2):
        api.add_page(signed.customer_id, document_id, page_no, unit="px")
    spans = [
        _span(1, 0.1, "Sale deed", kind="heading"),
        _span(1, 0.2, "Item", kind="table_cell", table_id="t1", row=0, col=0, header=True),
        _span(2, 0.1, "sharma line two", kind="paragraph"),
    ]
    api.add_reader_output(signed.customer_id, document_id, spans)
    return document_id


def test_the_clear_copy_has_every_page_in_order_with_table_places(db: Database) -> None:
    api = Api(db)
    a = api.new_signed()
    document_id = _document(api, a)
    reply = a.client.get(f"/api/documents/{document_id}/clear-copy")
    assert reply.status_code == 200, reply.text
    pages = reply.json()["pages"]
    assert [page["page_no"] for page in pages] == [1, 2]
    assert [(line["text"], line["kind"], line["table"]) for line in pages[0]["lines"]] == [
        ("Sale deed", "heading", None),
        ("Item", "table_cell", {"id": "t1", "row": 0, "col": 0, "header": True}),
    ]
    assert pages[1]["lines"][0]["text"] == "sharma line two"
    assert (reply.json()["truncated"], pages[0]["marks"]) == (False, [])


def test_b_gets_404_and_none_of_a_s_text(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    document_id = _document(api, a)
    assert a.client.get(f"/api/documents/{document_id}/clear-copy").status_code == 200  # the control
    reply = b.client.get(f"/api/documents/{document_id}/clear-copy")
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
    assert "sharma" not in reply.text and "Sale deed" not in reply.text


def test_the_page_translation_route_is_gone(db: Database) -> None:
    model = FakeModel(lambda system, user: {"lines": ["Sale deed", "Item"]})
    api = Api(db, model=model)
    member = api.new_signed()
    document_id = _document(api, member)
    path = f"/api/documents/{document_id}/pages/1/translate"
    reply = member.client.post(path, json={"language": "en"}, headers=CSRF)
    assert reply.status_code == 404
    assert "Sale deed" not in reply.text
    assert model.prompts == []
