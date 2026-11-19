"""PRD 9 F-12 (Round 1, CC-01, CC-05): a fix saved in the Text tab did not show there. The tab showed
each line's raw text and laid the fixes over it in the browser, where a flag's text as read (its
whitespace squashed) was never found in a raw line holding a line break or a double space. The
readings' main reader lines now carry the text with fixes from the server, the same as the clean
copy (``views.clear_line``): its key, its text now, and the correction in force with its Undo.
"""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.chunking.piece import Piece
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.extraction.flags import word_key
from kaagaz.extraction.values import ExtractedValue
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
    second in Devanagari, with an unread word marked [?] inside it). Both spots are unconfirmed,
    so each line has a flag of its own."""
    document_id = api.add_document(customer_id, detected_type="jpeg")
    spans = (
        Span(ROAD, ROAD_RAW, ROAD_BOX, None, "paragraph", "ocr", quality="ok", box_source="model"),
        Span(CITY, CITY_RAW, CITY_BOX, 0.97, "paragraph", "ocr", quality="ok", box_source="model"),
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


def test_confirming_a_line_after_a_word_fixed_in_it_keeps_the_fix(db: Database) -> None:
    # PRD 9 review of #683: the line's box starts from its text now (the word fixed); Save unchanged
    # confirms the line flag as read, which writes nothing, so the word's fix stays in the line.
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan(api, reviewer.customer_id)
    mark, city = key("mark", MARK_BOX, "[?]"), key("line", CITY_BOX, CITY)
    fixed = reviewer.client.post(
        f"/api/documents/{document_id}/flags/{mark}/edit",
        json={"text": "दुनिया", "expected_text": "[?]"},
        headers=CSRF,
    )
    assert fixed.status_code == 200, fixed.text

    confirmed = reviewer.client.post(
        f"/api/documents/{document_id}/flags/{city}/confirm", json={"expected_text": CITY}, headers=CSRF
    )

    assert confirmed.status_code == 200, confirmed.text
    line = text_tab(reviewer, document_id)[1]
    assert (line["text"], line["correction"]["change_id"]) == ("नमस्ते दुनिया मुंबई", fixed.json()["change"]["id"])
    clean = reviewer.client.get(f"/api/documents/{document_id}/clear-copy").json()["pages"][0]["lines"][1]
    assert clean["text"] == "नमस्ते दुनिया मुंबई"


# #685: a word whose text is in its line more than once ("[?] Sharma [?]") is written in its own place:
# its rank among the line's flags with that text, left to right, stored with the change.
TWICE_BOX = BoxLocation(1, 0.1, 0.5, 0.9, 0.55)
FIRST_BOX = BoxLocation(1, 0.1, 0.5, 0.2, 0.55)
SECOND_BOX = BoxLocation(1, 0.7, 0.5, 0.8, 0.55)
TWICE = "[?] Sharma [?]"
VALUE_ROW = "SELECT value_text FROM extracted_values WHERE document_id = :d"
PIECE_ROW = "SELECT text FROM pieces WHERE document_id = :d ORDER BY seq"


def scan_twice(api: Api, customer_id: str) -> str:
    """A line holding [?] twice, each its own mark; a value that is the whole line; a piece holding
    the line and one more [?] on another line."""
    document_id = api.add_document(customer_id, detected_type="jpeg")
    spans = (
        Span(TWICE, TWICE, TWICE_BOX, 0.9, "paragraph", "ocr", quality="ok", box_source="found"),
        Span(CITY, CITY_RAW, CITY_BOX, 0.97, "paragraph", "ocr", quality="ok", box_source="found"),
        Span("[?]", "[?]", SECOND_BOX, 0.2, None, "ocr", quality="illegible"),
        Span("[?]", "[?]", FIRST_BOX, 0.2, None, "ocr", quality="illegible"),
    )
    pages = [PageRecord(PageInfo(1, 595.0, 842.0, "pt"), True, "ocr")]
    outputs = [ReaderOutput("first", "google/gemini-3.1-pro-preview", "1", spans)]
    pieces = [Piece(customer_id, document_id, f"{TWICE}\n{CITY}", (TWICE_BOX, CITY_BOX), "verified")]
    value = ExtractedValue(
        "document", "buyer_name", TWICE, TWICE, TWICE_BOX, 0.9, None, "needs_review", "low_confidence", None
    )
    content = DocumentContent(customer_id, document_id, pages, outputs, pieces, [value], reader="gemini")
    PgPipelineStore(api.db).save(content)
    return document_id


def stored(api: Api, customer_id: str, sql: str, document_id: str) -> list[str]:
    with api.db.customer(customer_id) as conn:
        return [str(row[0]) for row in conn.execute(text(sql), {"d": document_id}).all()]


def edit_mark(signed: Signed, document_id: str, location: BoxLocation, typed: str) -> Any:
    mark = key("mark", location, "[?]")
    body = {"text": typed, "expected_text": "[?]"}
    return signed.client.post(f"/api/documents/{document_id}/flags/{mark}/edit", json=body, headers=CSRF)


def test_a_word_twice_in_its_line_is_fixed_in_its_own_place_everywhere(db: Database) -> None:
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = scan_twice(api, reviewer.customer_id)

    second = edit_mark(reviewer, document_id, SECOND_BOX, "Traders")

    assert second.status_code == 200, second.text
    assert text_tab(reviewer, document_id)[0]["text"] == "[?] Sharma Traders"
    assert stored(api, reviewer.customer_id, PIECE_ROW, document_id) == [f"[?] Sharma Traders\n{CITY}"]
    assert stored(api, reviewer.customer_id, VALUE_ROW, document_id) == ["[?] Sharma Traders"]

    first = edit_mark(reviewer, document_id, FIRST_BOX, "Ravi")  # now once in the line: as before

    assert first.status_code == 200, first.text
    line = text_tab(reviewer, document_id)[0]
    assert (line["text"], line["correction"]["change_id"]) == (
        "Ravi Sharma Traders",
        first.json()["change"]["id"],
    )
    clean = reviewer.client.get(f"/api/documents/{document_id}/clear-copy").json()["pages"][0]["lines"][0]
    assert clean["text"] == "Ravi Sharma Traders"
    assert stored(api, reviewer.customer_id, PIECE_ROW, document_id) == [f"Ravi Sharma Traders\n{CITY}"]

    for change in (first, second):  # the latest first
        undone = reviewer.client.post(
            f"/api/documents/{document_id}/changes/{change.json()['change']['id']}/undo", headers=CSRF
        )
        assert undone.status_code == 200, undone.text
    assert text_tab(reviewer, document_id)[0]["text"] == TWICE
    assert stored(api, reviewer.customer_id, PIECE_ROW, document_id) == [f"{TWICE}\n{CITY}"]
    assert stored(api, reviewer.customer_id, VALUE_ROW, document_id) == [TWICE]


def test_a_word_twice_in_its_line_whose_place_is_not_known_is_refused(db: Database) -> None:
    # Only one of the two [?] is flagged: which one it is can't be told, so nothing is guessed.
    api = Api(db)
    reviewer = api.new_signed("reviewer")
    document_id = api.add_document(reviewer.customer_id, detected_type="jpeg")
    spans = (
        Span(TWICE, TWICE, TWICE_BOX, 0.9, "paragraph", "ocr", quality="ok", box_source="found"),
        Span("[?]", "[?]", SECOND_BOX, 0.2, None, "ocr", quality="illegible"),
    )
    pages = [PageRecord(PageInfo(1, 595.0, 842.0, "pt"), True, "ocr")]
    outputs = [ReaderOutput("first", "google/gemini-3.1-pro-preview", "1", spans)]
    PgPipelineStore(api.db).save(
        DocumentContent(reviewer.customer_id, document_id, pages, outputs, [], [], reader="gemini")
    )

    reply = edit_mark(reviewer, document_id, SECOND_BOX, "Traders")

    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "twice"), reply.text
    assert "Fix the whole line in Clean copy" in reply.json()["error"]["message"]
    assert text_tab(reviewer, document_id)[0]["text"] == TWICE
