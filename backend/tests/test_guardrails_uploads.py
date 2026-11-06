"""#520 guard rails, uploads: attacks on the type, the scan, the limits, the names and the PDF
content limits, through the pieces production runs.

6 and 8. A file is judged by its bytes and goes through the scan gate with the production
   layering (the content check in its isolated child, then the virus scanner): a renamed
   program, HTML, SVG with script, a PDF with JavaScript or an attached file, and the EICAR test
   string are each rejected and quarantined; a scanner that is down or never answers lets
   nothing through. The content rules themselves: test_content_inspection.
7. The size can't be bypassed by a resumable upload's declared size or its parts, nor the
   folder limits by a re-plan.
9. Hidden, bidi and line-separator characters and path tricks in a file or a typed name.
10. The PDF content limits of #505: the dark-glyph cap (fixed), the read budget and the
    check's timeout (OPEN, xfail).
"""

import io
import subprocess
import threading
import unicodedata
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
from sqlalchemy import text

from kaagaz.adapters.scanner import EICAR, ClamdScanner, EicarDevScanner, LayeredScanner
from kaagaz.api.names import clean_name
from kaagaz.api.routers.documents import display_name
from kaagaz.db.engine import Database
from kaagaz.ingestion.policy import ZIP_MAX_BYTES
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.worker import JobFailed
from kaagaz.reading import pdf_signals
from kaagaz.scanning import content
from kaagaz.scanning.gate import DocumentStatus, ScanGate, Scanner, ScannerError, require_clean
from tests.fakes import MemoryStatuses, MemoryStorage
from tests.test_adapter_scanner import FakeClamd
from tests.test_api_folder_upload_files import ResumableStorage, _fingerprint
from tests.test_api_folder_uploads import item, plan, register
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed
from tests.test_content_inspection import PAGE, PAGES, WINDOWS_PROGRAM, pdf_with
from tests.test_operator_auth_db import _owner

CUSTOMER, DOCUMENT = "cust-a", "doc-1"
CATALOG = b"<< /Type /Catalog /Pages 2 0 R"
SHAPE = (
    b"<< /Type /XObject /Subtype /Form /BBox [0 0 10 10] /Length 15 >>\nstream\n0 0 m 10 10 l S\nendstream"
)
SESSIONS = "/api/documents/upload-sessions"
MIB = 1024 * 1024

# --- 6 and 8. the type and the scan, through the gate ------------------------------------------

ATTACKS = {
    "a program renamed invoice.pdf": (WINDOWS_PROGRAM, "blocked_content"),
    "a program behind a PDF header": (b"%PDF-1.7\n" + WINDOWS_PROGRAM, "blocked_content"),
    "an HTML page": (b"<html><body><script>alert(1)</script></body></html>", "blocked_content"),
    "an SVG with script": (
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>',
        "blocked_content",
    ),
    "a PDF that runs JavaScript": (
        pdf_with([CATALOG + b" /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>", PAGES, PAGE]),
        "blocked_content",
    ),
    "a PDF with an attached file": (
        pdf_with(
            [
                CATALOG + b" /Names << /EmbeddedFiles 4 0 R >> >>",
                PAGES,
                PAGE,
                b"<< /Names [(a.exe) 5 0 R] >>",
                b"<< /Type /Filespec >>",
            ]
        ),
        "blocked_content",
    ),
    "the EICAR test string": (EICAR, "virus_found"),
}


def _gate(scanner: Scanner, data: bytes) -> tuple[ScanGate, MemoryStorage, MemoryStatuses]:
    storage, statuses = MemoryStorage(), MemoryStatuses()
    storage.put(CUSTOMER, DOCUMENT, io.BytesIO(data))
    statuses.set_status(CUSTOMER, DOCUMENT, DocumentStatus.RECEIVED)
    return ScanGate(storage, scanner, storage, statuses), storage, statuses


def _refused_for_good(scanner: Scanner, data: bytes, code: str) -> None:
    gate, storage, statuses = _gate(scanner, data)
    job = Job(CUSTOMER, DOCUMENT)
    with pytest.raises(JobFailed) as failed:
        gate.check(job)
    assert (failed.value.code, failed.value.retryable) == (code, False)
    assert statuses.get_status(CUSTOMER, DOCUMENT) is DocumentStatus.REJECTED
    assert storage.quarantined == {(CUSTOMER, DOCUMENT): data}  # out of normal reads
    with pytest.raises(JobFailed):
        require_clean(statuses, job)  # no later step may open it


@pytest.mark.parametrize("name", list(ATTACKS))
def test_each_attack_file_is_rejected_and_quarantined_by_the_production_layers(name: str) -> None:
    data, code = ATTACKS[name]
    _refused_for_good(LayeredScanner(EicarDevScanner()), data, code)


@pytest.fixture
def clamd() -> Iterator[FakeClamd]:
    server = FakeClamd()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    yield server
    server.release.set()
    server.shutdown()
    server.server_close()
    thread.join(5)


def _clamd_scanner(server: FakeClamd, timeout: float = 2.0) -> LayeredScanner:
    host, port = server.server_address[:2]
    return LayeredScanner(ClamdScanner(str(host), int(port), timeout=timeout))


def test_clamd_finding_eicar_rejects_it(clamd: FakeClamd) -> None:
    _refused_for_good(_clamd_scanner(clamd), b"Name,Note\nA," + EICAR + b"\n", "virus_found")
    gate, _, statuses = _gate(_clamd_scanner(clamd), b"Name,Amount\nA,10\n")  # the control
    gate.check(Job(CUSTOMER, DOCUMENT))
    assert statuses.get_status(CUSTOMER, DOCUMENT) is DocumentStatus.CLEAN


def _never_clean(scanner: Scanner) -> None:
    gate, storage, statuses = _gate(scanner, b"Name,Amount\nA,10\n")
    job = Job(CUSTOMER, DOCUMENT)
    for _ in range(3):  # every retry the same: the file is never let through
        with pytest.raises(JobFailed) as failed:
            gate.check(job)
        assert (failed.value.code, failed.value.retryable) == ("scanner_unavailable", True)
    assert statuses.get_status(CUSTOMER, DOCUMENT) is DocumentStatus.RECEIVED
    assert storage.quarantined == {}
    with pytest.raises(JobFailed):
        require_clean(statuses, job)


def test_a_clamd_that_never_answers_fails_closed_and_is_retried(clamd: FakeClamd) -> None:
    clamd.never_answer = True
    _never_clean(_clamd_scanner(clamd, timeout=0.3))


def test_a_clamd_that_is_down_fails_closed_and_is_retried(clamd: FakeClamd) -> None:
    scanner = _clamd_scanner(clamd)
    clamd.shutdown()
    clamd.server_close()  # nothing listens on its port any more
    _never_clean(scanner)


@pytest.mark.db
def test_the_type_comes_from_the_bytes_never_the_name(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    program = {"file": ("invoice.pdf", WINDOWS_PROGRAM, "application/pdf")}
    refused = signed.client.post("/api/documents", files=program, headers=CSRF)
    assert (refused.status_code, refused.json()["error"]["code"]) == (400, "unsupported_type")
    page = {"file": ("bill.pdf", b"<html><script>alert(1)</script></html>", "application/pdf")}
    taken = signed.client.post("/api/documents", files=page, headers=CSRF)
    assert taken.status_code == 201, taken.text
    assert taken.json()["document"]["detected_type"] == "text"  # never a PDF; the scan refuses it


# --- 7. the limits ------------------------------------------------------------------------------


def _start(signed: Signed, size: int, data: bytes = PDF_BYTES) -> Any:
    body = {
        "file_name": "big.pdf",
        "file_size": size,
        "fingerprint": _fingerprint(data),
        "user_id": signed.user_id,
    }
    return signed.client.post(SESSIONS, json=body, headers=CSRF)


def _error(reply: Any) -> tuple[int, str]:
    return reply.status_code, reply.json()["error"]["code"]


@pytest.mark.db
def test_a_resumable_upload_never_takes_more_than_it_declared_or_than_the_limit(db: Database) -> None:
    storage = ResumableStorage()
    api = Api(db, storage=storage)
    signed = api.new_signed()
    # Changed on purpose (#522): a zip may be up to ZIP_MAX_BYTES, so that is now the most a start
    # may declare; the complete still holds each file to its own type's limit (the next test).
    largest = ZIP_MAX_BYTES
    for size in (largest + 1, 0, -1):
        assert _error(_start(signed, size)) == (413, "too_large"), size

    session = _start(signed, len(PDF_BYTES)).json()["id"]
    part = f"{SESSIONS}/{session}"
    assert _error(signed.client.put(f"{part}/parts/1", content=PDF_BYTES + b"x", headers=CSRF)) == (
        413,
        "too_large",
    )
    assert _error(signed.client.put(f"{part}/parts/1", content=PDF_BYTES[:-1], headers=CSRF)) == (
        400,
        "bad_request",
    )
    for number in (0, 2):
        reply = signed.client.put(f"{part}/parts/{number}", content=PDF_BYTES, headers=CSRF)
        assert _error(reply) == (400, "bad_request"), number
    assert storage.parts[f"{signed.customer_id}/{signed.user_id}/{session}"] == {}  # no byte stored
    assert _error(signed.client.post(f"{part}/complete", headers=CSRF)) == (409, "upload_incomplete")
    assert signed.client.put(f"{part}/parts/1", content=PDF_BYTES, headers=CSRF).status_code == 204
    assert signed.client.post(f"{part}/complete", headers=CSRF).status_code == 201  # the control


@pytest.mark.db
def test_a_resumable_upload_is_held_to_its_types_own_limit_at_the_complete(db: Database) -> None:
    """Declared under the largest limit (a PDF's), the bytes are a photo over the photo limit."""
    storage = ResumableStorage()
    api = Api(db, storage=storage)
    signed = api.new_signed()
    photo = b"\x89PNG\r\n\x1a\n" + b"\0" * (3 * MIB)  # the photo limit is 2 MB
    session = _start(signed, len(photo), photo).json()["id"]
    assert signed.client.put(f"{SESSIONS}/{session}/parts/1", content=photo, headers=CSRF).status_code == 204
    refused = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)
    assert _error(refused) == (413, "too_large")
    assert (storage.parts, storage.done, api.storage.count(), len(api.queue.jobs)) == ({}, {}, 0, 0)


_LIMITS = (
    "INSERT INTO org_upload_limits (customer_id, max_files, max_gb, max_folders) VALUES (:c, :f, :g, :d)"
)


@pytest.mark.db
def test_a_re_plan_never_lets_more_files_in_than_the_organisations_limit(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _owner(db, _LIMITS, {"c": signed.customer_id, "f": 2, "g": None, "d": None})
    first = plan(signed, file_count=2).json()
    upload, folder = first["id"], first["folder_ids"][0]
    sent = register(signed, upload, [item(f"A/{n}.pdf", folder, 100) for n in range(2)])
    assert sent.status_code == 200, sent.text

    again = {"id": upload, "file_count": 5}
    assert _error(plan(signed, **again)) == (409, "too_many_files")  # a re-plan past the limit
    wider = plan(signed, **again, only_new=True)  # "Add new files only": the ceilings, not the limit
    assert wider.status_code == 200, wider.text
    more = register(signed, upload, [item(f"A/new{n}.pdf", folder, 100) for n in range(3)])
    assert _error(more) == (409, "too_many_files")  # every file still to send counts against 2
    items = signed.client.get(f"/api/folder-uploads/{upload}/items").json()["items"]
    assert len(items) == 2


# --- 9. names ---------------------------------------------------------------------------------

HIDDEN = {
    "\u202e": "right-to-left override",
    "\u202d": "left-to-right override",
    "\u2066": "left-to-right isolate",
    "\u200f": "right-to-left mark",
    "\u200b": "zero width space",
    "\u2060": "word joiner",
    "\ufeff": "byte order mark",
    "\u00ad": "soft hyphen",
    "\u2028": "line separator",
    "\u2029": "paragraph separator",
}


@pytest.mark.parametrize("hidden", list(HIDDEN), ids=list(HIDDEN.values()))
def test_an_uploaded_files_name_never_keeps_a_hidden_or_bidi_character(hidden: str) -> None:
    """ "invoice<RLO>fdp.exe" shows as "invoiceexe.pdf": the trick that hides a program's type."""
    shown = display_name(f"invoice{hidden}fdp.exe")
    assert shown == "invoicefdp.exe"
    assert not any(unicodedata.category(char) in ("Cf", "Zl", "Zp") for char in shown)


@pytest.mark.parametrize("hidden", list(HIDDEN), ids=list(HIDDEN.values()))
def test_a_typed_folder_tag_or_document_name_never_keeps_a_hidden_or_bidi_character(hidden: str) -> None:
    assert clean_name(f"GST{hidden} 2026/27", max_length=100) == "GST 2026/27"
    assert clean_name(hidden, max_length=100) is None  # nothing left: refused, as an empty name


def test_devanagari_joiners_stay_in_both_names() -> None:
    marathi = "मराठी\u200cफाइल\u200d"
    assert display_name(f"{marathi}.pdf") == f"{marathi}.pdf"
    assert clean_name(marathi, max_length=100) == marathi


# Review of #520: the name rule's gaps.
SCOTLAND = "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f"  # a flag emoji: tags


@pytest.mark.parametrize("c1", ["\x85", "\x80", "\x9b", "\x9f"], ids=["NEL", "PAD", "CSI", "APC"])
def test_a_c1_control_character_goes_from_both_names(c1: str) -> None:
    assert clean_name(f"GST{c1} 2026/27", max_length=100) == "GST 2026/27"
    assert clean_name(c1, max_length=100) is None
    assert display_name(f"invoice{c1}.pdf") == "invoice.pdf"


@pytest.mark.parametrize("joiners", ["\u200c", "\u200d", "\u200d\u200c", " \u200c \u200d ", "\u200b\u200d"])
def test_a_name_of_joiners_alone_is_refused_as_an_empty_name(joiners: str) -> None:
    assert clean_name(joiners, max_length=100) is None


def test_a_flag_emojis_tags_stay_and_a_lone_tag_goes() -> None:
    assert clean_name(f"Scotland {SCOTLAND}", max_length=100) == f"Scotland {SCOTLAND}"
    assert display_name(f"Trip {SCOTLAND}.pdf") == f"Trip {SCOTLAND}.pdf"
    hidden = "\U000e0065\U000e0078\U000e0065"  # "exe" in tag characters, which draw nothing
    assert clean_name(f"invoice{hidden}.pdf", max_length=100) == "invoice.pdf"
    unended = f"invoice\U0001f3f4{hidden}.pdf"  # no cancel tag: no flag, the tags go
    assert clean_name(unended, max_length=100) == "invoice\U0001f3f4.pdf"
    assert clean_name(f"{SCOTLAND}{hidden}\U000e007f", max_length=100) == SCOTLAND  # a flag, then lone tags
    assert clean_name("\U000e007f", max_length=100) is None


@pytest.mark.parametrize(
    "name",
    [
        "\u092e\u0930\u093e\u0920\u0940\u200c\u092b\u093e\u0907\u0932",  # Devanagari with ZWNJ
        "\u0915\u094d\u200d\u0937",  # Devanagari with ZWJ
        "\u0641\u0627\u062a\u0648\u0631\u0629 \u0662\u0660\u0662\u0666",  # Arabic
        "\u0631\u0633\u06cc\u062f\u200c\u06c1\u0627",  # Urdu with ZWNJ
        "Team \U0001f469\u200d\U0001f4bb",  # a ZWJ emoji sequence
        "\U0001f468\u200d\U0001f469\u200d\U0001f467",
    ],
    ids=["devanagari zwnj", "devanagari zwj", "arabic", "urdu zwnj", "zwj emoji", "family emoji"],
)
def test_other_scripts_and_joined_emoji_stay_as_typed(name: str) -> None:
    assert clean_name(name, max_length=100) == name
    assert display_name(f"{name}.pdf") == f"{name}.pdf"


@pytest.mark.parametrize(
    ("given", "shown"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\Windows\\System32\\cmd.exe", "cmd.exe"),
        ("/var/lib/kaagaz/bill.pdf", "bill.pdf"),
        ("C:\\Users\\ravi\\bill.pdf", "bill.pdf"),
        ("..", "document"),
        (".", "document"),
        ("bill.pdf/..", "document"),
        ("x" * 1000 + ".pdf", "x" * 255),
    ],
)
def test_a_path_trick_or_a_very_long_name_leaves_only_a_plain_name(given: str, shown: str) -> None:
    assert display_name(given) == shown


@pytest.mark.db
def test_hidden_characters_never_reach_a_stored_name_through_the_api(db: Database) -> None:
    api = Api(db)
    signed = api.new_admin()
    files = {"file": ("invoice\u202efdp.exe", PDF_BYTES, "application/pdf")}
    uploaded = signed.client.post("/api/documents", files=files, headers=CSRF).json()["document"]
    assert uploaded["display_name"] == "invoicefdp.exe"
    other = {"file": ("second\u202efdp.exe", b"%PDF-1.7\n" + b"2" * 200, "application/pdf")}
    second = signed.client.post("/api/documents", files=other, headers=CSRF).json()["document"]["id"]
    renamed = signed.client.patch(
        f"/api/documents/{second}", json={"display_name": "bill\u202efdp.exe"}, headers=CSRF
    )
    assert renamed.status_code == 200, renamed.text
    folder = signed.client.post("/api/folders", json={"name": "Accounts\u2066\u202e"}, headers=CSRF)
    tag = signed.client.post("/api/tags", json={"name": "GST\u200b"}, headers=CSRF)
    assert (folder.status_code, tag.status_code) == (201, 201), (folder.text, tag.text)
    with db.customer(signed.customer_id) as conn:
        names = {
            table: sorted(conn.execute(text(f"SELECT {column} FROM {table}")).scalars())  # noqa: S608
            for table, column in (("documents", "display_name"), ("folders", "name"), ("tags", "name"))
        }
    assert names == {"documents": ["billfdp.exe", "invoicefdp.exe"], "folders": ["Accounts"], "tags": ["GST"]}


# --- 10. the PDF content limits (#505) ----------------------------------------------------------


class _NeverDrawn:
    def get_size(self) -> tuple[float, float]:
        raise AssertionError("the page was drawn")


def test_the_dark_glyph_cap_still_holds_when_the_light_glyph_cap_fires() -> None:
    """#505 item 3: with the light ones dropped, the dark count was "all checked minus the light
    ones", so 4 light and 5 dark passed a cap of 3 dark and every dark glyph was drawn and
    checked (a crafted page of a million of each stalled the read)."""
    light = [(n, (250, 250, 250), True) for n in range(4)]
    dark = [(n, (0, 0, 0), False) for n in range(4, 9)]
    with (
        mock.patch.object(pdf_signals, "MAX_LIGHT_GLYPHS", 2),
        mock.patch.object(pdf_signals, "MAX_DARK_GLYPHS", 3),
    ):
        assert pdf_signals._unseen(_NeverDrawn(), None, light + dark) == {0, 1, 2, 3}


def _busy_pdf(pages: int, per_page: int) -> bytes:
    """A plain PDF whose pages each draw ``per_page`` small shapes kept as their own objects (as
    many statement and report generators write a logo, a stamp or table rules); no object
    stream, no active content."""
    objects = [CATALOG + b" >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for page in range(pages):
        shapes = []
        for _ in range(per_page):
            objects.append(SHAPE)
            shapes.append(len(objects))
        draw = b"".join(b"q 1 0 0 1 %d 40 cm /X%d Do Q\n" % (40 + k, k) for k in range(per_page))
        draw += b"BT /F1 9 Tf 40 800 Td (Page %d) Tj ET\n" % page
        objects.append(b"<< /Length %d >>\nstream\n" % len(draw) + draw + b"\nendstream")
        names = b" ".join(b"/X%d %d 0 R" % (k, number) for k, number in enumerate(shapes))
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >>"
            b" /XObject << " + names + b" >> >> /Contents %d 0 R >>" % (len(objects))
        )
        kids.append(b"%d 0 R" % len(objects))
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % pages
    return pdf_with(objects)


def test_a_plain_pdf_of_a_few_hundred_objects_passes_the_content_check() -> None:
    data = _busy_pdf(50, 6)  # 403 objects
    assert len(data) < 200_000
    assert content.inspect(data) is None


@pytest.mark.xfail(
    strict=True,
    reason="OPEN #505 item 1: the read budget's 16 MiB object grants never refill, so a plain"
    " 50-page PDF of 603 objects is refused as unscannable; the bound is Vrushit's to retune",
)
def test_a_plain_50_page_pdf_of_600_objects_passes_the_content_check() -> None:
    assert content.inspect(_busy_pdf(50, 10)) is None


@pytest.mark.xfail(
    strict=True,
    reason="OPEN #505 item 2: a content check that runs out of time is a final rejection;"
    " whether to retry it like clamd's time limit is Vrushit's call",
)
def test_a_content_check_that_runs_out_of_time_is_retried_not_rejected() -> None:
    timeout = subprocess.TimeoutExpired(cmd="check", timeout=45)
    with (
        mock.patch("kaagaz.scanning.isolated.subprocess.run", side_effect=timeout),
        pytest.raises(ScannerError),
    ):
        LayeredScanner(EicarDevScanner()).scan(PDF_BYTES)
