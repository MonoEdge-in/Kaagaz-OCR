"""PRD 9 F-12 (Round 1, CC-01, CC-05): a fix saved in the Text tab did not show there. The tab showed
each line's raw text and laid the fixes over it in the browser, where a flag's text as read (its
whitespace squashed) was never found in a raw line holding a line break or a double space. The
readings' main reader lines now carry the text with fixes from the server, the same as the clean
copy (``views.clear_line``): its key, its text now, and the correction in force with its Undo.
"""

from typing import Any

import pytest

from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.extraction.flags import word_key
from kaagaz.pipeline import DocumentContent, PageRecord, ReaderOutput
from kaagaz.reading.spans import BoxLocation, PageInfo, Span, location_to_json
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

ROAD_BOX = BoxLocation(1, 0.1, 0.1, 0.9, 0.2)
CITY_BOX = BoxLocation(1, 0.1, 0.3, 0.9, 0.4)
MARK_BOX = BoxLocation(1, 0.4, 0.31, 0.6, 0.39)  # the [?] inside the city's line
ROAD_RAW, ROAD = "Shop 4,  DAREGAO\nWest", "Shop 4, DAREGAO West"
CITY_RAW, CITY = "नमस्ते  [?]\nमुंबई", "नमस्ते [?] मुंबई"


def scan(api: Api, customer_id: str) -> str:
    """A one-page scan: two lines whose raw text holds a line break and a double space (the
    second in Devanagari, with an unread word marked [?] inside it)."""
    document_id = api.add_document(customer_id, detected_type="jpeg")
    spans = (
        Span(ROAD, ROAD_RAW, ROAD_BOX, None, "paragraph", "ocr", quality="ok", box_source="model"),
        Span(CITY, CITY_RAW, CITY_BOX, 0.97, "paragraph", "ocr", quality="ok", box_source="found"),
        Span("[?]", "[?]", MARK_BOX, 0.2, None, "ocr", quality="illegible"),
    )
    pages = [PageRecord(PageInfo(1, 595.0, 842.0, "pt"), True, "ocr")]
    outputs = [ReaderOutput("first", "mistral/mistral-ocr-4-1", "1", spans)]
    PgPipelineStore(api.db).save(
        DocumentContent(customer_id, document_id, pages, outputs, [], [], reader="gemini")
    )
    return document_id


def key(kind: str, location: BoxLocation, as_read: str) -> str:
    return word_key(kind, location.page, location_to_json(location), as_read)


def text_tab(signed: Signed, document_id: str) -> list[dict[str, Any]]:
    reply = signed.client.get(f"/api/documents/{document_id}/pages/1/readings")
    assert reply.status_code == 200, reply.text
    lines: list[dict[str, Any]] = next(r for r in reply.json()["readers"] if r["role"] == "first")["lines"]
    return lines


def test_a_line_fixed_through_its_own_flag_shows_fixed_and_marked_with_its_undo(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan(api, reviewer.customer_id)
    road = key("line", ROAD_BOX, ROAD)
    before = text_tab(reviewer, document_id)
    assert [(line["text"], line["key"], line["correction"]) for line in before] == [
        (ROAD_RAW, road, None),  # untouched: as read, line break and double space kept
        (CITY_RAW, key("line", CITY_BOX, CITY), None),
    ]

    fixed = "Shop 4, Goregaon West"
    saved = reviewer.client.post(
        f"/api/documents/{document_id}/lines/{road}/edit",
        json={"text": fixed, "expected_text": ROAD},
        headers=CSRF,
    )

    assert saved.status_code == 200, saved.text
    line = text_tab(reviewer, document_id)[0]
    assert (line["text"], line["key"]) == (fixed, road)
    assert line["correction"]["before_text"] == ROAD_RAW
    assert line["correction"]["change_id"] == saved.json()["change_id"]
    # The same source as the clean copy.
    clean = reviewer.client.get(f"/api/documents/{document_id}/clear-copy").json()["pages"][0]["lines"][0]
    assert clean == {name: value for name, value in line.items() if name != "box_source"}  # F-16's, Text only


def test_a_devanagari_word_fixed_in_a_line_shows_in_it_marked_with_its_undo(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan(api, reviewer.customer_id)
    mark = key("mark", MARK_BOX, "[?]")

    saved = reviewer.client.post(
        f"/api/documents/{document_id}/flags/{mark}/edit",
        json={"text": "दुनिया", "expected_text": "[?]"},
        headers=CSRF,
    )

    assert saved.status_code == 200, saved.text
    change_id = saved.json()["change"]["id"]
    road, city = text_tab(reviewer, document_id)
    assert (road["text"], road["correction"]) == (ROAD_RAW, None)  # another line: untouched
    assert city["text"] == "नमस्ते दुनिया मुंबई"
    assert (city["correction"]["before_text"], city["correction"]["change_id"]) == (CITY_RAW, change_id)

    undone = reviewer.client.post(f"/api/documents/{document_id}/changes/{change_id}/undo", headers=CSRF)

    assert undone.status_code == 200, undone.text
    city = text_tab(reviewer, document_id)[1]
    assert (city["text"], city["correction"]) == (CITY_RAW, None)
