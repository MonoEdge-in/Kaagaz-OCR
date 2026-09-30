import ast
import pathlib
import unittest
from collections.abc import Callable, Iterator
from typing import ClassVar, cast

from PIL import Image

from kaagaz.ingestion import sniff
from kaagaz.reading.csv_reader import CsvReader
from kaagaz.reading.ocr import OcrLine
from kaagaz.reading.socket import NoReader, ReaderSocket
from kaagaz.reading.spans import BoxLocation, CellLocation, Location, ReadError, Span
from kaagaz.reading.wiring import build_reader_socket
from kaagaz.reading.xlsx_reader import XlsxLimits, XlsxReader
from tests.pdf_builder import Page, Text, make_pdf
from tests.settings_factory import make_settings
from tests.xlsx_builder import make_xlsx, sheet_xml

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "kaagaz"


class FixedReader:
    """A test double standing in for any reader: returns the spans it was given."""

    name = "fixed"
    version = "9"

    def __init__(self, *spans: Span) -> None:
        self._spans = list(spans)

    def read(self, data: bytes, *, on_page: Callable[[], None] | None = None) -> list[Span]:
        return self._spans


class TwoPageReader:
    """A test double for a reader of pages: calls ``on_page`` after each of two pages."""

    name = "two-pages"
    version = "1"

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def read(self, data: bytes, *, on_page: Callable[[], None] | None = None) -> list[Span]:
        spans = []
        for page in (1, 2):
            self._events.append(f"read page {page}")
            spans.append(Span("x", "x", BoxLocation(page, 0.1, 0.1, 0.5, 0.2)))
            if on_page is not None:
                on_page()
        return spans


class FakeOcr:
    name = "fake-ocr"
    version = "0"

    def read(self, image: Image.Image) -> list[OcrLine]:
        return []


def box(page: int = 1, x0: float = 0.1, y0: float = 0.1, x1: float = 0.5, y1: float = 0.2) -> BoxLocation:
    return BoxLocation(page, x0, y0, x1, y1)


class ReaderSocketTest(unittest.TestCase):
    def test_socket_returns_what_the_direct_reader_found(self) -> None:
        data = b"a,b\nc,d\n"
        socket = ReaderSocket({sniff.TEXT: CsvReader(max_cells=100)})
        self.assertEqual(socket.read(sniff.TEXT, data), list(CsvReader(max_cells=100).read(data)))

    def test_type_without_a_reader_needs_a_reader(self) -> None:
        socket = ReaderSocket({sniff.TEXT: CsvReader(max_cells=100)})
        with self.assertRaises(NoReader) as ctx:
            socket.read(sniff.PDF, b"%PDF-1.7")
        self.assertEqual(ctx.exception.detected_type, sniff.PDF)

    def test_a_second_reader_plugs_in_without_touching_any_other_module(self) -> None:
        class OneCellReader:  # a test double standing in for a future reader
            name = "one-cell"
            version = "1"

            def read(self, data: bytes, *, on_page: Callable[[], None] | None = None) -> Iterator[Span]:
                yield Span("hello", "hello", CellLocation(1, 1, 1))

        socket = ReaderSocket({sniff.TEXT: CsvReader(max_cells=100), "fake": OneCellReader()})
        self.assertEqual([s.text for s in socket.read("fake", b"")], ["hello"])

    def test_invalid_spans_are_refused_for_good(self) -> None:
        bad_spans = {
            "no location": Span("text", "text", cast(CellLocation, None)),
            "row zero": Span("text", "text", CellLocation(1, 0, 1)),
            "empty text": Span("", "", CellLocation(1, 1, 1)),
        }

        class BadReader:
            name = "bad"
            version = "1"

            def __init__(self, span: Span) -> None:
                self._span = span

            def read(self, data: bytes, *, on_page: Callable[[], None] | None = None) -> list[Span]:
                return [self._span]

        for name, bad in bad_spans.items():
            with self.subTest(name), self.assertRaises(ReadError) as ctx:
                ReaderSocket({"bad": BadReader(bad)}).read("bad", b"")
            self.assertEqual(ctx.exception.code, "invalid_reader_output")

    def test_later_changes_to_the_mapping_do_not_change_the_socket(self) -> None:
        readers = {sniff.TEXT: CsvReader(max_cells=100)}
        socket = ReaderSocket(readers)
        readers.clear()
        self.assertEqual(len(socket.read(sniff.TEXT, b"a")), 1)

    def test_on_page_reaches_the_reader_and_is_called_after_each_page(self) -> None:
        events: list[str] = []
        socket = ReaderSocket({sniff.PDF: TwoPageReader(events)})
        spans = socket.read(sniff.PDF, b"", on_page=lambda: events.append("on_page"))
        self.assertEqual(events, ["read page 1", "on_page", "read page 2", "on_page"])
        self.assertEqual(len(spans), 2)

    def test_every_wired_reader_accepts_on_page(self) -> None:
        # The spans are the same with it or without it. A reader with nothing to
        # beat for between pages (a spreadsheet, a typed PDF page) ignores it.
        socket = build_reader_socket(make_settings(), FakeOcr())
        workbook = make_xlsx([("S", sheet_xml('<row r="1"><c r="A1"><v>7</v></c></row>'))])
        pdf = make_pdf([Page(texts=[Text(72, 700, "typed")])])
        for kind, data in ((sniff.TEXT, b"a,b\n"), (sniff.ZIP, workbook), (sniff.PDF, pdf)):
            with self.subTest(kind):
                self.assertEqual(socket.read(kind, data, on_page=lambda: None), socket.read(kind, data))


class SecondReaderTest(unittest.TestCase):
    def test_second_reader_output_comes_back_checked(self) -> None:
        span = Span("INV-1", "INV-1", box())
        socket = ReaderSocket({sniff.PDF: FixedReader(span)}, {sniff.PDF: FixedReader(span)})
        self.assertEqual(socket.read_second(sniff.PDF, b""), [span])

    def test_type_without_a_second_reader_gives_none(self) -> None:
        socket = ReaderSocket({sniff.TEXT: CsvReader(max_cells=100)})
        self.assertIsNone(socket.read_second(sniff.TEXT, b"a"))
        self.assertIsNone(socket.read_second(sniff.PDF, b"%PDF-1.7"))

    def test_invalid_second_reader_output_is_refused_for_good(self) -> None:
        socket = ReaderSocket({}, {sniff.PDF: FixedReader(Span("x", "x", box(x0=0.6, x1=0.5)))})
        with self.assertRaises(ReadError) as ctx:
            socket.read_second(sniff.PDF, b"")
        self.assertEqual(ctx.exception.code, "invalid_reader_output")

    def test_later_changes_to_the_second_mapping_do_not_change_the_socket(self) -> None:
        seconds = {sniff.PDF: FixedReader()}
        socket = ReaderSocket({}, seconds)
        seconds.clear()
        self.assertEqual(socket.read_second(sniff.PDF, b""), [])


class ReaderIdTest(unittest.TestCase):
    def test_names_and_versions_of_first_and_second_readers(self) -> None:
        socket = ReaderSocket({sniff.TEXT: CsvReader(max_cells=10)}, {sniff.TEXT: FixedReader()})
        self.assertEqual(socket.reader_id(sniff.TEXT), ("csv", "1"))
        self.assertEqual(socket.reader_id(sniff.TEXT, second=True), ("fixed", "9"))

    def test_type_without_that_reader_needs_a_reader(self) -> None:
        socket = ReaderSocket({sniff.TEXT: CsvReader(max_cells=10)})
        with self.assertRaises(NoReader) as ctx:
            socket.reader_id(sniff.PDF)
        self.assertEqual(ctx.exception.detected_type, sniff.PDF)
        with self.assertRaises(NoReader) as ctx:
            socket.reader_id(sniff.TEXT, second=True)
        self.assertEqual(ctx.exception.detected_type, sniff.TEXT)

    def test_the_direct_readers_have_a_name_and_version(self) -> None:
        limits = XlsxLimits(
            max_entries=1, max_member_bytes=1, max_total_bytes=1, max_cells=1, max_text_chars=1
        )
        xlsx, csv = XlsxReader(limits), CsvReader(max_cells=1)
        self.assertEqual((xlsx.name, xlsx.version), ("xlsx", "1"))
        self.assertEqual((csv.name, csv.version), ("csv", "1"))


class SpanValidationTest(unittest.TestCase):
    def read_one(self, span: Span) -> list[Span]:
        return ReaderSocket({"t": FixedReader(span)}).read("t", b"")

    def test_valid_boxes_and_confidences_pass(self) -> None:
        good = [
            Span("a", "a", box()),
            Span("a", "a", box(x0=0.0, y0=0.0, x1=1.0, y1=1.0)),  # the whole page
            Span("a", "a", box(page=3), confidence=0.0),
            Span("a", "a", box(), confidence=1.0),
            Span("a", "a", CellLocation(1, 1, 1), confidence=0.42),
        ]
        for span in good:
            with self.subTest(span=span):
                self.assertEqual(self.read_one(span), [span])

    def test_boxes_that_cannot_be_pointed_at_are_refused(self) -> None:
        bad_locations: dict[str, Location] = {
            "page zero": box(page=0),
            "no width": box(x0=0.5, x1=0.5),
            "upside down": box(y0=0.3, y1=0.2),
            "off the page": box(x1=1.2),
            "negative": box(x0=-0.1),
            "not a number": box(y1=float("nan")),
            "page not whole": BoxLocation(cast(int, 1.5), 0.1, 0.1, 0.2, 0.2),
            "corner not a number": BoxLocation(1, cast(float, "0.1"), 0.1, 0.2, 0.2),
            "cell row as text": CellLocation(1, cast(int, "2"), 1),
        }
        for name, location in bad_locations.items():
            with self.subTest(name), self.assertRaises(ReadError) as ctx:
                self.read_one(Span("a", "a", location))
            self.assertEqual(ctx.exception.code, "invalid_reader_output")

    def test_kind_origin_and_table_fields_are_checked(self) -> None:
        cell = CellLocation(1, 1, 1)
        good = [
            Span("a", "a", box(), kind="heading", origin="ocr"),
            Span("a", "a", box(), None, "table_cell", "ocr", "t1", 0, 0, False),
            Span("a", "a", box(), None, "table_cell", "ocr", "T_2-b", 999, 3, True),
            Span("a", "a", box(), origin="text_layer"),
            Span("a", "a", cell, origin="cell"),
        ]
        for span in good:
            with self.subTest(span=span):
                self.assertEqual(self.read_one(span), [span])
        bad = {
            "a box that does not say how it was read": Span("a", "a", box()),  # fail closed (council)
            "unknown kind": Span("a", "a", box(), kind="banner", origin="ocr"),
            "unknown origin": Span("a", "a", box(), origin="scan"),
            "a cell with a kind": Span("a", "a", cell, kind="paragraph"),
            "a cell read by ocr": Span("a", "a", cell, origin="ocr"),
            "a cell with table fields": Span("a", "a", cell, None, None, "cell", "t1", 0, 0, False),
            "a table cell without its table": Span("a", "a", box(), kind="table_cell", origin="ocr"),
            "table fields on a heading": Span("a", "a", box(), None, "heading", "ocr", "t1", 0, 0, False),
            "only some table fields": Span("a", "a", box(), None, "table_cell", "ocr", table_id="t1"),
            "a table id with a space": Span("a", "a", box(), None, "table_cell", "ocr", "t 1", 0, 0, False),
            "a negative row": Span("a", "a", box(), None, "table_cell", "ocr", "t1", -1, 0, False),
            "a column as a bool": Span("a", "a", box(), None, "table_cell", "ocr", "t1", 0, True, False),
            "a header as a number": Span(
                "a", "a", box(), None, "table_cell", "ocr", "t1", 0, 0, cast(bool, 1)
            ),
        }
        for name, span in bad.items():
            with self.subTest(name), self.assertRaises(ReadError) as ctx:
                self.read_one(span)
            self.assertEqual(ctx.exception.code, "invalid_reader_output")

    def test_confidence_outside_zero_to_one_is_refused(self) -> None:
        for confidence in (-0.01, 1.01, float("nan"), float("inf"), cast(float, True), cast(float, "0.5")):
            with self.subTest(confidence=confidence), self.assertRaises(ReadError) as ctx:
                self.read_one(Span("a", "a", box(), confidence=confidence))
            self.assertEqual(ctx.exception.code, "invalid_reader_output")


class WiringTest(unittest.TestCase):
    def test_without_ocr_scans_and_photos_need_a_reader(self) -> None:
        socket = build_reader_socket(make_settings(), None)
        self.assertEqual(socket.reader_id(sniff.TEXT), ("csv", "1"))
        self.assertEqual(socket.reader_id(sniff.ZIP), ("xlsx", "1"))
        self.assertEqual(socket.reader_id(sniff.PDF)[0], "pdfplumber")
        self.assertEqual(socket.reader_id(sniff.PDF, second=True)[0], "pdfium")
        for kind in (sniff.PNG, sniff.JPEG, sniff.TIFF):
            with self.subTest(kind), self.assertRaises(NoReader):
                socket.read(kind, b"")

    def test_with_ocr_photos_are_read_and_the_pdf_version_names_the_engine(self) -> None:
        socket = build_reader_socket(make_settings(), FakeOcr())
        for kind in (sniff.PNG, sniff.JPEG, sniff.TIFF):
            with self.subTest(kind):
                self.assertEqual(socket.reader_id(kind), ("fake-ocr", "0"))
        self.assertTrue(socket.reader_id(sniff.PDF)[1].endswith("+fake-ocr-0"))

    def test_every_reader_says_how_its_spans_were_read(self) -> None:
        socket = build_reader_socket(make_settings(), None)
        pdf = make_pdf([Page(texts=[Text(72, 700, "typed page")])])
        workbook = make_xlsx([("S", sheet_xml('<row r="1"><c r="A1"><v>7</v></c></row>'))])
        self.assertEqual([s.origin for s in socket.read(sniff.TEXT, b"a,b\n")], ["cell", "cell"])
        self.assertEqual([s.origin for s in socket.read(sniff.ZIP, workbook)], ["cell"])
        self.assertEqual([s.origin for s in socket.read(sniff.PDF, pdf)], ["text_layer"])
        self.assertEqual([s.origin for s in socket.read_second(sniff.PDF, pdf) or []], ["text_layer"])

    def test_only_pdf_has_a_second_reader(self) -> None:
        socket = build_reader_socket(make_settings(), FakeOcr())
        for kind in (sniff.TEXT, sniff.ZIP, sniff.PNG, sniff.JPEG, sniff.TIFF):
            with self.subTest(kind):
                self.assertIsNone(socket.read_second(kind, b""))

    def test_the_readers_get_their_limits_from_the_settings(self) -> None:
        socket = build_reader_socket(make_settings(csv_max_cells=2), None)
        with self.assertRaises(ReadError) as ctx:
            socket.read(sniff.TEXT, b"a,b,c\n")
        self.assertEqual(ctx.exception.code, "too_many_cells")


class NoDirectReaderCallsTest(unittest.TestCase):
    """CLAUDE.md: application code never calls a reader directly."""

    # Every module in kaagaz/reading is a reader, except the socket and the
    # span types, which everyone may use, and the wiring and the OCR engine
    # interface, which the pipeline uses to build the socket. New readers are
    # covered automatically.
    ALLOWED: ClassVar[set[str]] = {"__init__", "socket", "spans", "wiring", "ocr"}

    def reader_modules(self) -> set[str]:
        return {
            f"kaagaz.reading.{path.stem}"
            for path in (PACKAGE_ROOT / "reading").glob("*.py")
            if path.stem not in self.ALLOWED
        }

    def test_the_rule_knows_every_reader(self) -> None:
        self.assertLessEqual(
            {
                "kaagaz.reading.csv_reader",
                "kaagaz.reading.xlsx_reader",
                "kaagaz.reading.pdf_reader",
                "kaagaz.reading.pdf_second_reader",
                "kaagaz.reading.image_reader",
            },
            self.reader_modules(),
        )

    def test_only_the_reading_package_imports_reader_modules(self) -> None:
        forbidden = self.reader_modules()
        offenders = []
        for path in PACKAGE_ROOT.rglob("*.py"):
            if "reading" in path.relative_to(PACKAGE_ROOT).parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    # Covers both "from kaagaz.reading.csv_reader import X"
                    # and "from kaagaz.reading import csv_reader".
                    names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
                if any(name in forbidden for name in names):
                    offenders.append(str(path.relative_to(PACKAGE_ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
