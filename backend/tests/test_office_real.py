"""Real conversions by LibreOffice (#522, PR 2, ``kaagaz/reading/office.py``), where ``soffice``
is installed: the backend image. Skipped elsewhere, with the reason. Run them in the image:

    docker run --rm -v "$PWD/backend/tests:/app/tests:ro" kaagaz-backend:<tag> \
        python -m unittest tests.test_office_real tests.test_office -v

The Word, PowerPoint and Excel files are made here by LibreOffice itself, from the flat
OpenDocument text below. The files in tests/fixtures/office were made by LibreOffice through
its UNO interface, which the image does not carry: ``locked.doc`` and ``locked.docx`` have the
password "secret"; ``macro.odt`` runs a Basic macro when it is opened that writes
``/tmp/kaagaz-macro-ran``.

Each safety test has a control: the same file converted by a profile that allows what ours
forbids (macros, link updates) does the forbidden thing, so the test can tell it apart."""

import contextlib
import http.server
import io
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
import zipfile
from pathlib import Path
from typing import ClassVar

import pypdfium2

from kaagaz.ingestion import resolve, sniff
from kaagaz.ingestion.resolve import resolve_kind
from kaagaz.reading import office
from kaagaz.reading.office import OfficeConverter
from kaagaz.reading.spans import CellLocation, ReadError
from kaagaz.reading.xlsx_reader import XlsxLimits, XlsxReader

SOFFICE = shutil.which("soffice")
FIXTURES = Path(__file__).with_name("fixtures") / "office"
LATIN = "Rent agreement 5000"
DEVANAGARI = "मराठी नमुना दस्तऐवज किंमत"
MACRO_MARK = Path("/tmp/kaagaz-macro-ran")  # noqa: S108 (where the fixture's macro writes)

_ODF = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" office:version="1.3"'
)
TEXT = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document {_ODF} office:mimetype="application/vnd.oasis.opendocument.text">
<office:body><office:text><text:p>{LATIN}</text:p><text:p>{DEVANAGARI}</text:p></office:text></office:body>
</office:document>
"""
SLIDES = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document {_ODF} office:mimetype="application/vnd.oasis.opendocument.presentation">
<office:body><office:presentation><draw:page draw:name="one">
<draw:frame svg:x="2cm" svg:y="2cm" svg:width="20cm" svg:height="3cm"><draw:text-box><text:p>{LATIN}</text:p>
</draw:text-box></draw:frame></draw:page></office:presentation></office:body>
</office:document>
"""
SHEET = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document {_ODF} office:mimetype="application/vnd.oasis.opendocument.spreadsheet">
<office:body><office:spreadsheet><table:table table:name="Sales"><table:table-row>
<table:table-cell office:value-type="string"><text:p>INV-7</text:p></table:table-cell>
<table:table-cell office:value-type="float" office:value="1180"><text:p>1180</text:p></table:table-cell>
</table:table-row></table:table></office:spreadsheet></office:body>
</office:document>
"""
# What each kind is made with, from which of the texts above.
MADE = {
    resolve.DOCX: ("text.fodt", TEXT, "docx:MS Word 2007 XML"),
    resolve.DOTX: ("text.fodt", TEXT, "dotx:MS Word 2007 XML Template"),
    resolve.DOC: ("text.fodt", TEXT, "doc:MS Word 97"),
    resolve.PPTX: ("slides.fodp", SLIDES, "pptx:Impress MS PowerPoint 2007 XML"),
    resolve.PPT: ("slides.fodp", SLIDES, "ppt:MS PowerPoint 97"),
    resolve.XLS: ("sheet.fods", SHEET, "xls:MS Excel 97"),
}
# A profile that allows what ours forbids: macros run, links update, nothing is blocked.
_ALLOWING = (
    ("Security/Scripting", "MacroSecurityLevel", "3", "0"),
    ("Security/Scripting", "DisableMacrosExecution", "true", "false"),
    ("Security/Scripting", "BlockUntrustedRefererLinks", "true", "false"),
    ("Writer/Content/Update", "Link", "0", "2"),  # always
    ("Calc/Content/Update", "Link", "1", "0"),  # always
)


def _allowing(profile: str) -> str:
    for path, name, ours, theirs in _ALLOWING:
        setting = f'{path}"><prop oor:name="{name}" oor:op="fuse"><value>{{}}</value>'
        assert setting.format(ours) in profile, name
        profile = profile.replace(setting.format(ours), setting.format(theirs))
    return profile


PERMISSIVE = _allowing(office.PROFILE)


def lo_convert(source: Path, target: str, out_dir: Path, profile: str = office.PROFILE) -> Path:
    """``source`` converted to ``target`` by soffice with ``profile``, as the converter runs it."""
    _soffice(["--convert-to", target, "--outdir", str(out_dir), str(source)], profile, 120)
    return out_dir / f"{source.stem}.{target.split(':')[0]}"


def lo_open(source: Path, profile: str, seconds: float = 15) -> None:
    """``source`` opened (not converted) by soffice with ``profile`` for ``seconds``, then killed:
    as a person opening it would, so its "when opened" macro runs if the profile lets it."""
    with contextlib.suppress(subprocess.TimeoutExpired):
        _soffice([str(source)], profile, seconds)


def _soffice(arguments: list[str], profile: str, seconds: float) -> None:
    assert SOFFICE is not None
    home = Path(tempfile.mkdtemp(prefix="lo-test-"))
    try:
        (home / "profile" / "user").mkdir(parents=True)
        (home / "profile" / "user" / "registrymodifications.xcu").write_text(profile, encoding="utf-8")
        command = [
            SOFFICE,
            f"-env:UserInstallation={(home / 'profile').as_uri()}",
            "--headless",
            "--norestore",
            "--nolockcheck",
            *arguments,
        ]
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8", "SAL_USE_VCLPLUGIN": "svp"}
        office.run_limited(command, env, seconds)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def pdf_text(pdf: bytes) -> str:
    document = pypdfium2.PdfDocument(pdf)
    try:
        return "\n".join(page.get_textpage().get_text_range() for page in document)
    finally:
        document.close()


def soffice_processes(wait: float = 5) -> list[int]:
    """The soffice processes still running (a zombie is gone: only its exit code is left),
    after waiting up to ``wait`` seconds for a killed one to go."""
    deadline = time.monotonic() + wait
    while (found := _soffice_processes()) and time.monotonic() < deadline:
        time.sleep(0.1)
    return found


def _soffice_processes() -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                command = (entry / "cmdline").read_bytes()
                state = (entry / "stat").read_text().rsplit(")", 1)[1].split()[0]
            except OSError:
                continue
            if b"soffice" in command and state != "Z":
                found.append(int(entry.name))
    return found


class _Requests(http.server.BaseHTTPRequestHandler):
    seen: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        type(self).seen.append(self.path)
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # quiet
        pass


def linked_docx(address: str) -> bytes:
    """A Word file with a picture linked to ``address``, not stored in it, and an
    INCLUDEPICTURE field that names it too: opening it with link updates on fetches it."""
    main = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    rels = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{main}" xmlns:r="{rels}"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>
<w:p><w:r><w:t>{LATIN}</w:t></w:r></w:p>
<w:p><w:r><w:drawing><wp:inline><wp:extent cx="914400" cy="914400"/><wp:docPr id="1" name="Picture 1"/>
<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>
<pic:nvPicPr><pic:cNvPr id="1" name="linked.png"/><pic:cNvPicPr/></pic:nvPicPr>
<pic:blipFill><a:blip r:link="rId9"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>
</wp:inline></w:drawing></w:r></w:p>
<w:p><w:fldSimple w:instr=' INCLUDEPICTURE "{address}/field.png" \\d '>
<w:r><w:t>picture</w:t></w:r></w:fldSimple></w:p>
</w:body></w:document>"""
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
        ),
        "_rels/.rels": (
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            f'package/2006/relationships"><Relationship Id="rId1" Type="{rels}/officeDocument" '
            'Target="word/document.xml"/></Relationships>'
        ),
        "word/_rels/document.xml.rels": (
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            f'package/2006/relationships"><Relationship Id="rId9" Type="{rels}/image" '
            f'Target="{address}/linked.png" TargetMode="External"/></Relationships>'
        ),
        "word/document.xml": document,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def as_word_file(odt: bytes) -> bytes:
    """An OpenDocument file with a ``word/document.xml`` part added: Kaagaz tells it a Word
    file (its parts), while LibreOffice opens it by its content, an OpenDocument with macros."""
    buffer = io.BytesIO(odt)
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.writestr("word/document.xml", "<w/>")
    return buffer.getvalue()


@unittest.skipUnless(
    SOFFICE and sys.platform.startswith("linux"), "needs LibreOffice: run in the backend image"
)
class RealConversionTest(unittest.TestCase):
    made: dict[str, bytes]
    folder: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="office-test-"))
        cls.made = {}
        for kind, (name, content, target) in MADE.items():
            source = cls.folder / name
            source.write_text(content, encoding="utf-8")
            cls.made[kind] = lo_convert(source, target, cls.folder / kind).read_bytes()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.folder, ignore_errors=True)

    def converter(self, timeout: float = office.TIMEOUT_SECONDS) -> OfficeConverter:
        assert SOFFICE is not None
        return OfficeConverter(SOFFICE, timeout=timeout)

    def test_each_word_and_powerpoint_file_becomes_a_pdf_with_its_text(self) -> None:
        for kind in sorted(office.PDF_KINDS):
            with self.subTest(kind):
                data = self.made[kind]
                self.assertEqual(resolve_kind(data, sniff.detect_type(data[:16]), "x"), kind)
                pdf = self.converter().convert(kind, data)
                self.assertTrue(pdf.startswith(b"%PDF-"))
                self.assertIn(LATIN, pdf_text(pdf))

    def test_devanagari_in_a_word_file_survives_into_the_pdfs_text_layer(self) -> None:
        for kind in (resolve.DOCX, resolve.DOC):
            with self.subTest(kind):
                text = pdf_text(self.converter().convert(kind, self.made[kind]))
                self.assertIn(DEVANAGARI, unicodedata.normalize("NFC", " ".join(text.split())))

    def test_an_old_excel_file_becomes_an_xlsx_the_xlsx_reader_reads(self) -> None:
        data = self.made[resolve.XLS]
        self.assertEqual(resolve_kind(data, sniff.OLE2, "x"), resolve.XLS)
        xlsx = self.converter().convert(resolve.XLS, data)
        self.assertEqual(resolve_kind(xlsx, sniff.ZIP, "x"), resolve.XLSX)
        limits = XlsxLimits(
            max_entries=100,
            max_member_bytes=10_000_000,
            max_total_bytes=10_000_000,
            max_cells=1000,
            max_text_chars=100_000,
        )
        spans = list(XlsxReader(limits).read(xlsx))
        cells = {
            (s.location.row, s.location.column): s.text for s in spans if isinstance(s.location, CellLocation)
        }
        self.assertEqual(cells, {(1, 1): "INV-7", (1, 2): "1180"})

    def test_a_password_protected_old_word_file_fails_cleanly_and_quickly(self) -> None:
        data = (FIXTURES / "locked.doc").read_bytes()
        self.assertEqual(resolve_kind(data, sniff.OLE2, "locked.doc"), resolve.DOC)
        started = time.monotonic()
        with self.assertRaises(ReadError) as caught:
            self.converter().convert(resolve.DOC, data)
        self.assertEqual(caught.exception.code, office.UNREADABLE)
        self.assertLess(time.monotonic() - started, 30)  # never waits for a password
        self.assertEqual(soffice_processes(), [])

    def test_a_password_protected_docx_is_told_before_any_conversion(self) -> None:
        data = (FIXTURES / "locked.docx").read_bytes()
        with self.assertRaises(ReadError) as caught:
            resolve_kind(data, sniff.detect_type(data[:16]), "locked.docx")
        self.assertEqual(caught.exception.code, "password_protected")

    def test_a_timeout_kills_libreoffice_and_fails_cleanly(self) -> None:
        started = time.monotonic()
        with self.assertRaises(ReadError) as caught:
            self.converter(timeout=0.5).convert(resolve.PPTX, self.made[resolve.PPTX])
        self.assertEqual(caught.exception.code, office.UNREADABLE)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(soffice_processes(), [])  # oosplash and soffice.bin both gone

    def test_a_macro_document_converts_without_running_its_macro(self) -> None:
        # The converter's profile (office.PROFILE) on a document whose macro runs when it opens.
        MACRO_MARK.unlink(missing_ok=True)
        source = self.folder / "macro.odt"
        source.write_bytes((FIXTURES / "macro.odt").read_bytes())
        pdf = lo_convert(source, "pdf", self.folder / "macro").read_bytes()
        self.assertIn("A document with a macro", pdf_text(pdf))
        self.assertFalse(MACRO_MARK.exists(), "the macro ran")
        # Converting never runs it, even with macros allowed; opening it does (the control), and
        # our profile alone stops that too, should a conversion ever run one.
        lo_convert(source, "pdf", self.folder / "converted-allowing-macros", PERMISSIVE)
        self.assertFalse(MACRO_MARK.exists(), "a conversion ran the macro")
        lo_open(source, office.PROFILE)
        self.assertFalse(MACRO_MARK.exists(), "our profile let the macro run")
        lo_open(source, PERMISSIVE)
        self.assertTrue(MACRO_MARK.exists(), "the control's macro did not run: the test proves nothing")
        MACRO_MARK.unlink()

    def test_an_opendocument_with_macros_dressed_as_a_word_file_is_never_opened(self) -> None:
        # Kaagaz tells it a Word file by its parts; LibreOffice opens a .docx only as one.
        disguised = as_word_file((FIXTURES / "macro.odt").read_bytes())
        self.assertEqual(resolve_kind(disguised, sniff.ZIP, "invoice.docx"), resolve.DOCX)
        MACRO_MARK.unlink(missing_ok=True)
        with self.assertRaises(ReadError):
            self.converter().convert(resolve.DOCX, disguised)
        self.assertFalse(MACRO_MARK.exists(), "the macro ran")

    def test_a_linked_picture_and_field_fetch_nothing(self) -> None:
        _Requests.seen = []
        server = http.server.HTTPServer(("127.0.0.1", 0), _Requests)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            address = f"http://127.0.0.1:{server.server_address[1]}"
            data = linked_docx(address)
            self.assertEqual(resolve_kind(data, sniff.ZIP, "linked.docx"), resolve.DOCX)
            pdf = self.converter().convert(resolve.DOCX, data)
            self.assertIn(LATIN, pdf_text(pdf))
            self.assertEqual(_Requests.seen, [])
            # The control: with links updated and nothing blocked, the same file fetches.
            source = self.folder / "control.docx"
            source.write_bytes(data)
            lo_convert(source, "pdf", self.folder / "control-links", PERMISSIVE)
            self.assertNotEqual(_Requests.seen, [], "the control fetched nothing: the test proves nothing")
        finally:
            server.shutdown()
            server.server_close()

    def test_nothing_is_left_in_the_temporary_folder(self) -> None:
        before = {p.name for p in Path(tempfile.gettempdir()).glob("lo-*")}
        self.converter().convert(resolve.DOC, self.made[resolve.DOC])
        after = {p.name for p in Path(tempfile.gettempdir()).glob("lo-*")}
        self.assertEqual(after - before, set())
