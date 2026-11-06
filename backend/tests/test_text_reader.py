"""The text reader (#522): plain text, Markdown, JSON and HTML read as lines on pages of about
3,000 characters, each line a cell of a one-column sheet, with no AI."""

import json

import pytest

from kaagaz.ingestion import resolve
from kaagaz.reading.spans import CellLocation, ReadError, Span
from kaagaz.reading.text_reader import LINE_CHARS, PAGE_CHARS, TextReader, html_text, pages


def read(kind: str, data: bytes) -> list[Span]:
    return list(TextReader(kind).read(data))


def lines(kind: str, data: bytes) -> list[str]:
    return [span.text for span in read(kind, data)]


def test_each_line_is_a_cell_on_its_page_and_a_blank_line_is_a_row_with_none() -> None:
    spans = read(resolve.TXT, b"Invoice INV-1\r\n\r\n  Total: 1,180.50  \nPaid\n")
    assert [(s.text, s.raw, s.location, s.origin, s.confidence) for s in spans] == [
        ("Invoice INV-1", "Invoice INV-1", CellLocation(1, 1, 1), "cell", None),
        ("Total: 1,180.50", "  Total: 1,180.50  ", CellLocation(1, 3, 1), "cell", None),
        ("Paid", "Paid", CellLocation(1, 4, 1), "cell", None),
    ]


def test_markdown_keeps_its_text_and_its_marks_as_typed() -> None:
    data = "# Rent\n\n| Month | ₹ |\n|---|---|\n| April | 12,000 |\n\n*paid* [receipt](http://x.test/r)\n"
    assert lines(resolve.MD, data.encode()) == [
        "# Rent",
        "| Month | ₹ |",
        "|---|---|",
        "| April | 12,000 |",
        "*paid* [receipt](http://x.test/r)",
    ]


def test_devanagari_and_a_byte_order_mark_are_read_as_they_are() -> None:
    assert lines(resolve.TXT, "﻿एकूण रक्कम: १,२००\n".encode()) == ["एकूण रक्कम: १,२००"]


def test_pages_are_about_3000_characters_cut_at_line_breaks() -> None:
    line = "x" * 99  # 100 with its line break
    spans = read(resolve.TXT, ((line + "\n") * 75).encode())
    by_page: dict[int, list[int]] = {}
    for span in spans:
        assert isinstance(span.location, CellLocation)
        by_page.setdefault(span.location.sheet, []).append(span.location.row)
    assert {page: len(rows) for page, rows in by_page.items()} == {1: 30, 2: 30, 3: 15}
    assert by_page[2][0] == 1  # each page counts its own lines from 1
    assert all(len(page) * 100 <= PAGE_CHARS for page in pages((line + "\n") * 75))


def test_a_page_never_starts_with_blank_lines_and_none_is_all_blank() -> None:
    assert pages("\n\n  \nfirst\n\nsecond\n\n") == [["first", "", "second", ""]]
    full = pages(("a" * 99 + "\n") * 29 + "b" * 95 + "\n\n\nnext")
    assert [len(page) for page in full] == [32, 1]  # 30 lines and 2 blank ones: 2,998 characters
    assert full[0][-3:] == ["b" * 95, "", ""]
    assert full[1] == ["next"]  # a blank line adds no text
    assert pages("\n\n  \n") == []


def test_a_long_line_is_cut_at_a_space_or_at_the_limit() -> None:
    words = " ".join(["word"] * 100)  # 499 characters
    cut = [line for page in pages(words) for line in page]
    assert all(len(line) <= LINE_CHARS for line in cut)
    assert "".join(cut) == words  # nothing lost
    assert all(line.endswith(" ") for line in cut[:-1])  # cut after a space, never inside a word
    solid = [line for page in pages("y" * 450) for line in page]
    assert solid == ["y" * 200, "y" * 200, "y" * 50]


def test_one_line_of_a_megabyte_is_read_in_pieces() -> None:
    spans = read(resolve.TXT, b"z" * 1_000_000)
    assert [len(s.text) for s in spans] == [LINE_CHARS] * 5000
    assert max(s.location.page for s in spans) == 358  # 14 lines of 200 to a page


def test_json_is_written_out_with_two_spaces_one_value_to_a_line() -> None:
    data = json.dumps({"invoice": "INV-1", "items": [{"name": "चहा", "amount": 20}], "paid": True}).encode()
    assert lines(resolve.JSON, data) == [
        "{",
        '"invoice": "INV-1",',
        '"items": [',
        "{",
        '"name": "चहा",',
        '"amount": 20',
        "}",
        "],",
        '"paid": true',
        "}",
    ]
    raw = [s.raw for s in read(resolve.JSON, data)]
    assert raw[1] == '  "invoice": "INV-1",'  # the indent stays in the raw line


def test_json_nested_deeper_than_100_is_refused_before_it_is_parsed() -> None:
    at_cap = b"[" * 100 + b"1" + b"]" * 100
    assert len(read(resolve.JSON, at_cap)) == 201
    for deep in (b"[" * 101 + b"]" * 101, b'{"a":' * 5000 + b"1" + b"}" * 5000, b"[" * 1_000_000):
        with pytest.raises(ReadError) as refused:
            read(resolve.JSON, deep)
        assert refused.value.code == "too_deep"


def test_json_that_is_not_json_is_refused() -> None:
    with pytest.raises(ReadError) as refused:
        read(resolve.JSON, b'{"a": ')
    assert refused.value.code == "malformed_json"


def test_html_keeps_its_title_first_and_its_words_never_its_scripts_or_styles() -> None:
    page = b"""<!DOCTYPE html><html><head><title>Bill  of
    sale</title><style>p { color: red }</style><script>steal(document.cookie)</script>
    <meta name="x" content="hidden words"></head><body>
    <!-- a comment with secret words -->
    <h1>Invoice &amp; receipt</h1><p>Total: <b>&#8377;1,180</b> due</p>
    <noscript>Turn on scripts</noscript><template><p>template words</p></template>
    <script type="text/javascript">var x = "</p><p>not text";</script>
    <a href="http://evil.test/track">Pay here</a><img src="http://evil.test/pixel.png" alt="">
    <table><tr><th>Item</th><th>Qty</th></tr><tr><td>Tea</td><td>2</td></tr></table>
    <ul><li>one</li><li>two</li></ul>line<br>break<pre>a  b
  c</pre></body></html>"""
    assert lines(resolve.HTML, page) == [
        "Bill of sale",
        "Invoice & receipt",
        "Total: ₹1,180 due",
        "Pay here",
        "Item | Qty",
        "Tea | 2",
        "one",
        "two",
        "line",
        "break",
        "a b",
        "c",
    ]


def test_html_whose_head_is_never_closed_still_gives_its_body() -> None:
    assert html_text("<html><head><title>T</title><p>Body words</p>") == "T\nBody words"
    assert html_text("<p>No head at all</p>") == "No head at all"
    assert html_text("<head><title>First</title><title>Second</title></head><p>x</p>") == "First\nx"


def test_html_left_inside_an_unclosed_script_is_dropped() -> None:
    assert html_text("<p>kept</p><script>never closed <p>dropped</p>") == "kept"


def test_not_utf8_control_characters_and_no_text_are_refused() -> None:
    for kind in sorted(resolve.TEXT_KINDS - {resolve.CSV}):
        blank = "malformed_json" if kind == resolve.JSON else "no_text"  # blank is no JSON
        for data, code in ((b"caf\xe9", "not_utf8"), (b"a\x00b", "not_text"), (b" \n\t\n", blank)):
            with pytest.raises(ReadError) as refused:
                read(kind, data)
            assert refused.value.code == code, (kind, data)


def test_html_with_no_words_has_no_text() -> None:
    with pytest.raises(ReadError) as refused:
        read(resolve.HTML, b"<html><head><script>x()</script></head><body> </body></html>")
    assert refused.value.code == "no_text"


def test_only_the_text_kinds_have_a_text_reader_each_named_for_its_kind() -> None:
    assert {TextReader(kind).name for kind in ("txt", "md", "json", "html")} == {
        "text-txt",
        "text-md",
        "text-json",
        "text-html",
    }
    for kind in (resolve.CSV, resolve.PDF, "text"):
        with pytest.raises(ValueError):
            TextReader(kind)
