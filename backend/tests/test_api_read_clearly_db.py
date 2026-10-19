"""Read clearly (19 Oct 2026) against PostgreSQL: the clean copy has every page's lines
with their table place and marks; customer B gets 404 and none of A's text from either
route; translation follows Ask (403 ``feature_off`` with the switch off or the level at
None), and a second view of a page makes no model call. The translator itself:
test_page_translate.py (no database)."""

from typing import Any

import pytest

from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import span_to_json
from kaagaz.reading.spans import BoxLocation, Span
from tests.test_ai_settings_db import GEMINI, put, switched_off
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


def _translate(signed: Signed, document_id: str, page: int = 1, language: str = "en") -> Any:
    path = f"/api/documents/{document_id}/pages/{page}/translate"
    return signed.client.post(path, json={"language": language}, headers=CSRF)


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
    assert (pages[0]["truncated"], pages[0]["marks"]) == (False, [])


def test_b_gets_404_and_none_of_a_s_text_from_either_route(db: Database) -> None:
    model = FakeModel(lambda system, user: {"lines": ["Sale deed", "Item"]})
    api = Api(db, model=model)
    a, b = api.new_signed(), api.new_signed()
    document_id = _document(api, a)
    assert _translate(a, document_id).status_code == 200  # the control, and A's page is cached
    calls = len(model.prompts)
    for reply in (b.client.get(f"/api/documents/{document_id}/clear-copy"), _translate(b, document_id)):
        assert (reply.status_code, reply.json()["error"]["code"]) == (404, "not_found")
        assert "sharma" not in reply.text and "Sale deed" not in reply.text
    assert len(model.prompts) == calls  # B's request never reached the model or the cache


def test_translation_follows_ask_and_a_second_view_makes_no_model_call(db: Database) -> None:
    model = FakeModel(lambda system, user: {"lines": ["बिक्री विलेख", "वस्तु"]})
    api = Api(db, settings=GEMINI, model=model)
    member = api.new_signed()
    document_id = _document(api, member)

    first = _translate(member, document_id, language="hi")
    assert first.status_code == 200, first.text
    assert first.json() == {
        "page_no": 1,
        "language": "hi",
        "lines": ["बिक्री विलेख", "वस्तु"],
        "truncated": False,
        "cached": False,
    }
    again = _translate(member, document_id, language="hi")
    assert again.json()["cached"] is True
    assert len(model.prompts) == 1

    with switched_off(db, "ask"):
        reply = _translate(member, document_id, page=2)
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "feature_off")
    put(member, "me", {"ask": "none"})
    reply = _translate(member, document_id, page=2)
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "feature_off")
    assert len(model.prompts) == 1
    assert member.client.get(f"/api/documents/{document_id}/clear-copy").status_code == 200  # no AI needed
