"""Every format #522 takes, through the real scanner (security review, 6 Nov 2026): the worker's
own steps with ``LayeredScanner`` (our content check, in its child process, then ClamAV), with
only clamd faked, at its socket (tests/test_adapter_scanner.py's ``FakeClamd``). Before, every
pipeline test used a fake scanner, so the content check refused HEIC photos and every old
Office file, and every HTML file, unseen."""

import io
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from kaagaz.adapters.scanner import ClamdScanner, LayeredScanner
from kaagaz.db.engine import Database
from kaagaz.ingestion import resolve
from kaagaz.reading.wiring import build_reader_socket
from kaagaz.scanning.gate import DocumentStatus, Verdict
from tests.db_support import new_customer
from tests.pdf_builder import Page, Text, make_pdf
from tests.settings_factory import make_settings
from tests.support import PDF_BYTES
from tests.test_adapter_scanner import FakeClamd
from tests.test_pipeline import CUSTOMER, build, stored_type
from tests.test_resolve import compound
from tests.unpack_support import Unpacking
from tests.xlsx_builder import make_xlsx, make_zip, sheet_xml

HEIC = (Path(__file__).with_name("fixtures") / "heic" / "one.heic").read_bytes()
HTML = (
    b"<!doctype html><html><head><title>Rent</title><script>track()</script></head>"
    b"<body>Rent 5000</body></html>"
)
MARKDOWN = b"<!-- a note -->\n# Rent\n\nPaid on the fifth. <iframe src='x'></iframe>\n"
JSON = json.dumps({"rent": 5000, "note": "<script>alert(1)</script>", "link": "javascript:void(0)"}).encode()

# name, bytes, the kind told after the scan.
FORMATS = [
    ("IMG_0042.HEIC", HEIC, resolve.HEIC),
    ("letter.doc", compound(["WordDocument", "1Table"]), resolve.DOC),
    ("book.xls", compound(["Workbook"]), resolve.XLS),
    ("slides.ppt", compound(["PowerPoint Document", "Current User"]), resolve.PPT),
    (
        "letter.docx",
        make_zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"}),
        resolve.DOCX,
    ),
    ("slides.pptx", make_zip({"ppt/presentation.xml": b"<p/>"}), resolve.PPTX),
    ("book.xlsx", make_xlsx([("S", sheet_xml("<row/>"))]), resolve.XLSX),
    ("page.html", HTML, resolve.HTML),
    ("notes.md", MARKDOWN, resolve.MD),
    ("data.json", JSON, resolve.JSON),
    ("notes.txt", b"Rent 5000, paid on the fifth.\n", resolve.TXT),
    ("rent.csv", b"Name,Amount\nRent,5000\n", resolve.CSV),
    (
        "bills.zip",
        make_zip({"a/rent.csv": b"Name,Amount\nRent,5000\n", "b/bill.pdf": PDF_BYTES}),
        resolve.ZIP,
    ),
]


RENDITION = make_pdf(
    [Page(texts=[Text(72, 700, "Rent agreement for the flat at Pune, 5000 rupees a month")])]
)
WORKBOOK = make_xlsx([("S", sheet_xml('<row r="1"><c r="A1"><v>5</v></c></row>'))])


def converted(kind: str, data: bytes) -> bytes:
    """LibreOffice faked: an old Excel file's XLSX, any other file's PDF."""
    return WORKBOOK if kind == resolve.XLS else RENDITION


@pytest.fixture
def clamd() -> Iterator[ClamdScanner]:
    server = FakeClamd()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield ClamdScanner(str(host), int(port), timeout=5.0)
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize(("name", "data", "kind"), FORMATS, ids=[name for name, _, _ in FORMATS])
def test_each_format_passes_the_real_scanner_and_is_read(
    clamd: ClamdScanner, name: str, data: bytes, kind: str
) -> None:
    system = build(
        scanner=LayeredScanner(clamd),  # type: ignore[arg-type]
        socket=build_reader_socket(make_settings(), None),
        office=converted,
    )
    doc = system.upload.accept(CUSTOMER, io.BytesIO(data), display_name=name).document_id
    system.worker.run_once()
    status = system.statuses.get_status(CUSTOMER, doc)
    assert status in (DocumentStatus.READY, DocumentStatus.NEEDS_READER), system.repo.failure_codes
    assert stored_type(system, doc) == kind


def test_markup_is_refused_in_a_text_or_csv_file_or_with_no_name(clamd: ClamdScanner) -> None:
    # The text reader never renders HTML, Markdown or JSON, so markup in them is only text; in a
    # .txt or .csv file (which a spreadsheet may open) or a file with no name, it is refused.
    scanner = LayeredScanner(clamd)
    for name in ("page.html", "notes.md", "data.json"):
        assert scanner.scan(HTML if name == "page.html" else JSON, name=name) is Verdict.CLEAN
    for other in ("page.txt", "page.csv", "page", None):
        assert scanner.scan(HTML, name=other) is Verdict.BLOCKED
    assert scanner.scan(b"[1, 2", name="broken.json") is Verdict.CLEAN  # read as .txt: no markup
    assert scanner.scan(b"<x/>", name="broken.json") is Verdict.BLOCKED  # not JSON, so a .txt


@pytest.mark.db
def test_each_file_in_a_zip_passes_the_real_scanner_by_its_own_name(
    db: Database, clamd: ClamdScanner
) -> None:
    # The unpacker scans each file with its own name: an HTML page's markup is text, the same
    # markup in a .txt file is refused, and the rest pass, one by one. (The test settings allow
    # no HEIC or old Office upload; the pipeline test above takes those through the scanner.)
    customer, user = new_customer(db)
    unpacking = Unpacking(db, customer, user)
    unpacking.scanner = LayeredScanner(clamd)  # type: ignore[assignment]
    data = make_zip(
        {
            "site/page.html": HTML,
            "site/notes.txt": HTML,
            "data/rent.csv": b"Name,Amount\nRent,5000\n",
            "data/rent.json": JSON,
        }
    )
    record = unpacking.upload(data)
    assert unpacking.unpack(record, data) is True
    found = unpacking.result(record)
    assert found is not None
    assert (found.stored, found.failed, found.skipped) == (3, 1, [("notes.txt", "blocked_content")])
