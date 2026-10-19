"""Read clearly's clean copy (19 Oct 2026, CodeRabbit): ``views.clear_copy`` puts each line
and mark on its page, every page listed even with no lines, and stops at the document-wide
cap (CLEAR_COPY_LINES lines, CLEAR_COPY_TEXT characters) with a top-level ``truncated``.
No database: rows are dicts shaped as ``listing.clear_copy`` returns them."""

from typing import Any

from kaagaz.api.views import clear_copy
from kaagaz.db.listing import CLEAR_COPY_LINES, CLEAR_COPY_TEXT

REGION = {"kind": "box", "page": 1, "box": [0.1, 0.1, 0.9, 0.14]}


def row(page_no: int, text: str, **extra: Any) -> dict[str, Any]:
    return {
        "page_no": page_no,
        "text": text,
        "cut": False,
        "confidence": None,
        "origin": "ocr",
        "kind": "paragraph",
        "region": REGION,
        "table_id": None,
        "table_row": None,
        "table_col": None,
        "table_header": None,
        **extra,
    }


def test_lines_and_marks_go_to_their_pages_in_order() -> None:
    mark = {"page_no": 2, "quality": "unsure", "region": REGION, "text": "1180"}
    rows = [row(1, "a"), row(2, "b", table_id="t1", table_row=0, table_col=1, table_header=True)]
    body = clear_copy([1, 2, 3], rows, [mark])  # type: ignore[arg-type]
    assert body["truncated"] is False
    assert [[line["text"] for line in page["lines"]] for page in body["pages"]] == [["a"], ["b"], []]
    assert body["pages"][1]["lines"][0]["table"] == {"id": "t1", "row": 0, "col": 1, "header": True}
    assert body["pages"][1]["marks"] == [{"quality": "unsure", "region": REGION, "text": "1180"}]


def test_the_whole_document_stops_at_the_line_cap() -> None:
    rows = [row(1 + n // 1000, "x") for n in range(CLEAR_COPY_LINES + 1)]
    body = clear_copy(list(range(1, 22)), rows, [])  # type: ignore[arg-type]
    assert body["truncated"] is True
    assert sum(len(page["lines"]) for page in body["pages"]) == CLEAR_COPY_LINES


def test_the_whole_document_stops_at_the_character_cap() -> None:
    half = "y" * (CLEAR_COPY_TEXT // 2)
    body = clear_copy([1, 2], [row(1, half), row(2, half), row(2, "one more")], [])  # type: ignore[arg-type]
    assert body["truncated"] is True
    assert [len(page["lines"]) for page in body["pages"]] == [1, 1]


def test_a_line_cut_in_sql_says_truncated() -> None:
    assert clear_copy([1], [row(1, "long", cut=True)], [])["truncated"] is True  # type: ignore[arg-type]
