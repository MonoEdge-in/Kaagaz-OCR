"""Scanned text: GET /documents/{id}/pages/{n}/readings against a real database.

Each line is the stored ``raw``, in stored order; only the asked page's lines
come back, capped in SQL. Who may see it is who may see the page image, so the
parity test asks both routes the same questions. Customer isolation is in
tests/test_isolation_api_readings.py.
"""

import logging
from pathlib import Path
from typing import Any

import pytest

from kaagaz.db.engine import Database
from kaagaz.db.listing import MAX_READING_LINES, MAX_READING_TEXT
from kaagaz.db.pipeline_store import span_to_json
from kaagaz.reading.spans import BoxLocation, CellLocation, Span
from tests.test_api_support import NOW, Api, Signed

pytestmark = pytest.mark.db

KAAGAZ = Path(__file__).resolve().parents[1] / "kaagaz"


def _box(page: int, y: float, raw: str, **extra: Any) -> dict[str, Any]:
    return span_to_json(Span(raw.strip(), raw, BoxLocation(page, 0.1, y, 0.9, y + 0.04), **extra))


def _readings(signed: Signed, document_id: str, page_no: int = 1) -> dict[str, Any]:
    reply = signed.client.get(f"/api/documents/{document_id}/pages/{page_no}/readings")
    assert reply.status_code == 200, reply.text
    body: dict[str, Any] = reply.json()
    return body


def _scan(api: Api, signed: Signed, pages: int = 1) -> str:
    document_id = api.add_document(signed.customer_id, detected_type="png", name="scan.png")
    for page_no in range(1, pages + 1):
        api.add_page(signed.customer_id, document_id, page_no, unit="px")
    return document_id


def test_only_the_asked_page_comes_back_as_read_in_stored_order(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = _scan(api, signed, pages=2)
    # Stored bottom to top, with page 2 in between: the order is the stored one, never by y.
    spans = [
        _box(1, 0.8, " कुल ₹ 1,200 ", confidence=0.62, origin="ocr", kind="paragraph"),
        _box(2, 0.5, "other page", confidence=0.99, origin="ocr"),
        _box(1, 0.4, "Sharma\u200dTraders <b>", confidence=0.97, origin="ocr"),
        _box(1, 0.1, "Invoice", origin="ocr"),
    ]
    api.add_reader_output(signed.customer_id, document_id, spans, reader="paddleocr-vl", reader_version="1.5")

    body = _readings(signed, document_id)

    assert body == {
        "page_no": 1,
        "readers": [
            {
                "role": "first",
                "reader": "paddleocr-vl",
                "reader_version": "1.5",
                "truncated": False,
                "lines": [
                    {
                        "text": " कुल ₹ 1,200 ",  # the raw, spaces and all
                        "confidence": 0.62,
                        "origin": "ocr",
                        "kind": "paragraph",
                        "region": {"kind": "box", "page": 1, "box": [0.1, 0.8, 0.9, 0.84]},
                    },
                    {
                        "text": "Sharma\u200dTraders <b>",
                        "confidence": 0.97,
                        "origin": "ocr",
                        "kind": None,
                        "region": {"kind": "box", "page": 1, "box": [0.1, 0.4, 0.9, 0.44]},
                    },
                    {
                        "text": "Invoice",
                        "confidence": None,
                        "origin": "ocr",
                        "kind": None,
                        "region": {"kind": "box", "page": 1, "box": [0.1, 0.1, 0.9, 0.14]},
                    },
                ],
            }
        ],
    }
    assert [line["text"] for line in _readings(signed, document_id, 2)["readers"][0]["lines"]] == [
        "other page"
    ]


@pytest.mark.parametrize(
    ("case", "page_no", "expected"),
    [
        ("own", 1, 200),
        ("in_trash", 1, 200),  # PROVISIONAL, as the image
        ("older_version", 1, 200),  # PROVISIONAL, as the image
        ("rejected", 1, 404),
        ("own", 0, 404),
        ("own", 2, 404),  # past the last page
        ("another_customer", 1, 404),
    ],
)
def test_the_same_answer_as_the_page_image(db: Database, case: str, page_no: int, expected: int) -> None:
    api = Api(db)
    signed = api.new_signed()
    owner = api.new_signed() if case == "another_customer" else signed
    columns: dict[str, Any] = {}
    if case == "in_trash":
        columns["deleted_at"] = NOW
    if case == "rejected":
        columns["status"] = "rejected"
    if case == "older_version":
        columns["is_current"] = False
    document_id = api.add_document(owner.customer_id, **columns)
    if case == "older_version":
        api.add_document(owner.customer_id, family_id=document_id, version_no=2)
    api.add_page(owner.customer_id, document_id)
    api.storage.page_images[(owner.customer_id, document_id, 1)] = b"RIFF-webp"
    api.add_reader_output(owner.customer_id, document_id, [_box(1, 0.1, "Invoice", origin="text_layer")])

    image = signed.client.get(f"/api/documents/{document_id}/pages/{page_no}/image")
    readings = signed.client.get(f"/api/documents/{document_id}/pages/{page_no}/readings")

    assert (image.status_code, readings.status_code) == (expected, expected)
    if expected == 404:
        assert readings.json()["error"]["code"] == "not_found"
        assert "Invoice" not in readings.text
    else:
        assert readings.json()["readers"][0]["lines"][0]["text"] == "Invoice"


@pytest.mark.parametrize(("count", "truncated"), [(MAX_READING_LINES, False), (MAX_READING_LINES + 1, True)])
def test_at_most_2000_lines_per_reader(db: Database, count: int, truncated: bool) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = _scan(api, signed)
    spans = [_box(1, 0.5, f"line {n}", origin="ocr", confidence=0.5) for n in range(count)]
    api.add_reader_output(signed.customer_id, document_id, spans)

    reader = _readings(signed, document_id)["readers"][0]

    assert (len(reader["lines"]), reader["truncated"]) == (MAX_READING_LINES, truncated)
    assert reader["lines"][-1]["text"] == f"line {MAX_READING_LINES - 1}"


def test_at_most_100_000_characters_per_reader(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = _scan(api, signed, pages=2)
    big = "x" * 60_000
    api.add_reader_output(signed.customer_id, document_id, [_box(1, 0.1, big), _box(1, 0.5, big)])

    reader = _readings(signed, document_id)["readers"][0]

    assert ([line["text"] for line in reader["lines"]], reader["truncated"]) == ([big], True)


def test_a_line_longer_than_the_budget_is_cut_and_says_so(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = _scan(api, signed)
    api.add_reader_output(signed.customer_id, document_id, [_box(1, 0.1, "y" * 150_000), _box(1, 0.5, "")])

    reader = _readings(signed, document_id)["readers"][0]

    assert ([len(line["text"]) for line in reader["lines"]], reader["truncated"]) == (
        [MAX_READING_TEXT],
        True,
    )


def test_a_sheet_page_gives_its_cells_in_stored_order(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = api.add_document(signed.customer_id, detected_type="text", name="register.csv")
    api.add_page(signed.customer_id, document_id, 1, unit="cells", height=2)
    cells = [
        span_to_json(Span(value, value, CellLocation(1, row, column), origin="cell"))
        for value, row, column in (("party", 1, 1), ("amount", 1, 2), ("Sharma", 2, 1))
    ]
    api.add_reader_output(signed.customer_id, document_id, cells)

    lines = _readings(signed, document_id)["readers"][0]["lines"]

    assert [(line["text"], line["region"], line["origin"], line["confidence"]) for line in lines] == [
        ("party", {"kind": "cell", "page": 1, "row": 1, "column": 1}, "cell", None),
        ("amount", {"kind": "cell", "page": 1, "row": 1, "column": 2}, "cell", None),
        ("Sharma", {"kind": "cell", "page": 1, "row": 2, "column": 1}, "cell", None),
    ]


@pytest.mark.parametrize("second", ["present", "absent", "empty_on_this_page"])
def test_the_second_reader_comes_after_the_first(db: Database, second: str) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = _scan(api, signed, pages=2)
    if second != "absent":
        page = 1 if second == "present" else 2
        spans = [_box(page, 0.1, "1,250", origin="text_layer")]
        api.add_reader_output(signed.customer_id, document_id, spans, role="second", reader="pdfium")
    api.add_reader_output(signed.customer_id, document_id, [_box(1, 0.1, "1,200", origin="ocr")])

    readers = _readings(signed, document_id)["readers"]

    expected = {
        "present": [("first", ["1,200"]), ("second", ["1,250"])],
        "absent": [("first", ["1,200"])],
        "empty_on_this_page": [("first", ["1,200"]), ("second", [])],
    }[second]
    assert [(r["role"], [line["text"] for line in r["lines"]]) for r in readers] == expected
    assert not any(reader["truncated"] for reader in readers)


def test_a_page_not_read_yet_has_no_readers(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = _scan(api, signed)

    assert _readings(signed, document_id) == {"page_no": 1, "readers": []}


def test_the_text_is_never_logged(db: Database, caplog: pytest.LogCaptureFixture) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = _scan(api, signed)
    api.add_reader_output(signed.customer_id, document_id, [_box(1, 0.1, "PAN ABCDE1234F secret-line")])
    caplog.set_level(logging.DEBUG)

    assert _readings(signed, document_id)["readers"][0]["lines"][0]["text"] == "PAN ABCDE1234F secret-line"
    signed.client.get(f"/api/documents/{document_id}/pages/9/readings")

    assert caplog.records
    assert not [record for record in caplog.records if "secret-line" in record.getMessage()]


def test_answering_and_search_never_read_the_scanned_text() -> None:
    # This text goes to the screen only, never to the model (db/listing.py).
    for folder in ("answering", "search"):
        for source in (KAAGAZ / folder).rglob("*.py"):
            words = source.read_text(encoding="utf-8")
            assert "page_readings" not in words and "MAX_READING" not in words, source
