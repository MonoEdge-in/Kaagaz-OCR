"""The xlsx reader reads each part as a stream of XML events (perf A6a).

Memory: the reader holds each part's bytes and the spans it gives; nothing
else may grow with the sheet. The guards stay: strict UTF-8 whatever the part
declares, and no DOCTYPE anywhere in the part's bytes.

The differential test checks the reader against what its own generator wrote,
never against the reader's earlier output.
"""

import gc
import io
import random
import tracemalloc
import unittest
import zipfile

from kaagaz.reading.spans import CellLocation, ReadError
from kaagaz.reading.xlsx_reader import XlsxLimits, XlsxReader
from tests.xlsx_builder import MAIN_NS, make_xlsx, make_zip, sheet_xml

BIG = XlsxLimits(
    max_entries=50,
    max_member_bytes=50_000_000,
    max_total_bytes=100_000_000,
    max_cells=1_000_000,
    max_text_chars=100_000_000,
)
SHEET_PART = "xl/worksheets/sheet99.xml"  # make_xlsx's name for the first sheet
HEAD = '<?xml version="1.0" encoding="UTF-8"?>'


def texts(data: bytes, limits: XlsxLimits = BIG) -> dict[tuple[int, int, int], str]:
    found = {}
    for span in XlsxReader(limits).read(data):
        assert isinstance(span.location, CellLocation)
        found[(span.location.sheet, span.location.row, span.location.column)] = span.text
    return found


def refused(test: unittest.TestCase, data: bytes, code: str) -> None:
    with test.assertRaises(ReadError) as ctx:
        XlsxReader(BIG).read(data)  # the call itself raises: no span is handed out first
    test.assertEqual(ctx.exception.code, code)
    test.assertIsNone(ctx.exception.__context__)


def replace_part(data: bytes, name: str, content: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = {n: archive.read(n) for n in archive.namelist()}
    parts[name] = content
    return make_zip(parts)


def measure(data: bytes) -> tuple[int, int]:
    """(peak, kept): traced bytes at the peak of one read, and still held by its spans after."""
    gc.collect()
    tracemalloc.start()
    try:
        start = tracemalloc.get_traced_memory()[0]
        _spans = list(XlsxReader(BIG).read(data))  # alive until "kept" is taken
        kept, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak - start, kept - start


def letters(column: int) -> str:
    name = ""
    while column:
        column, rest = divmod(column - 1, 26)
        name = chr(ord("A") + rest) + name
    return name


def number_sheet(rows: int, columns: int = 10) -> str:
    body = "".join(
        f'<row r="{r}">'
        + "".join(f'<c r="{letters(c)}{r}"><v>{r * c}</v></c>' for c in range(1, columns + 1))
        + "</row>"
        for r in range(1, rows + 1)
    )
    return sheet_xml(body)


class EncodingGuardTest(unittest.TestCase):
    def test_a_declared_latin1_encoding_is_ignored_and_utf8_is_read(self) -> None:
        declared = '<?xml version="1.0" encoding="ISO-8859-1"?>'
        sheet = (
            f'{declared}<worksheet xmlns="{MAIN_NS}"><sheetData><row r="1">'
            '<c r="A1" t="inlineStr"><is><t>café ₹</t></is></c><c r="B1" t="s"><v>0</v></c>'
            "</row></sheetData></worksheet>"
        )
        shared = f'{declared}<sst xmlns="{MAIN_NS}"><si><t>naïve</t></si></sst>'.encode()
        data = make_xlsx([("S", sheet)], extra_parts={"xl/sharedStrings.xml": shared})
        self.assertEqual(texts(data), {(1, 1, 1): "café ₹", (1, 1, 2): "naïve"})

    def test_real_latin1_bytes_are_corrupt(self) -> None:
        sheet = (
            f'<?xml version="1.0" encoding="ISO-8859-1"?><worksheet xmlns="{MAIN_NS}"><sheetData>'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>café</t></is></c></row></sheetData></worksheet>'
        )
        data = replace_part(make_xlsx([("S", sheet_xml(""))]), SHEET_PART, sheet.encode("latin-1"))
        refused(self, data, "corrupt_file")

    def test_a_utf8_byte_order_mark_is_read(self) -> None:
        data = make_xlsx([("S", "﻿" + sheet_xml('<row r="1"><c r="A1"><v>7</v></c></row>'))])
        self.assertEqual(texts(data), {(1, 1, 1): "7"})


class DoctypeGuardTest(unittest.TestCase):
    def test_a_doctype_anywhere_in_the_bytes_is_refused(self) -> None:
        root = f'<worksheet xmlns="{MAIN_NS}"><sheetData/></worksheet>'
        padding = "<!--" + "p" * (64 * 1024 - 4 - 4 - 3) + "-->"  # the DOCTYPE starts 4 bytes before 64 KiB
        assert len(padding) == 64 * 1024 - 4
        cases = {
            "after the root": HEAD + root + "<!DOCTYPE x>",
            "inside a comment": HEAD + "<!-- <!DOCTYPE x> -->" + root,
            "after a byte order mark": "﻿<!DOCTYPE x [<!ENTITY a 'b'>]>" + root,
            "lower case": "<!doctype x>" + root,
            "across 64 KiB": padding + '<!DOCTYPE x [<!ENTITY a "b">]>' + root,
        }
        for name, xml in cases.items():
            with self.subTest(name):
                refused(self, make_xlsx([("S", xml)]), "doctype_not_allowed")

    def test_a_doctype_after_the_root_of_the_shared_strings_is_refused(self) -> None:
        shared = f'{HEAD}<sst xmlns="{MAIN_NS}"><si><t>a</t></si></sst><!DOCTYPE x>'.encode()
        data = make_xlsx([("S", sheet_xml(""))], extra_parts={"xl/sharedStrings.xml": shared})
        refused(self, data, "doctype_not_allowed")


class StreamShapeTest(unittest.TestCase):
    def test_a_refusal_in_the_last_sheet_comes_before_the_first_span(self) -> None:
        good = sheet_xml('<row r="1"><c r="A1"><v>1</v></c></row>')
        cases = {
            "corrupt_file": '<row r="1"><c r="A1"><v>1</v></c><c r="A1"><v>2</v></c></row>',
            "doctype_not_allowed": None,
        }
        for code, cells_xml in cases.items():
            last = "<!DOCTYPE x>" + good if cells_xml is None else sheet_xml(cells_xml)
            with self.subTest(code):
                refused(self, make_xlsx([("A", good), ("B", good), ("C", last)]), code)

    def test_deep_nesting_is_ignored_and_only_direct_rows_and_cells_count(self) -> None:
        depth = 50_000
        nest = "<x>" * depth + '<row r="9"><c r="A9"><v>9</v></c></row>' + "</x>" * depth
        body = (
            '<row r="1"><c r="A1"><v>1</v></c><row r="5"><c r="A5"><v>5</v></c></row></row>'
            + nest
            + '<row r="2"><c r="B2"><x><v>8</v></x></c><c r="C2"><v>2</v></c></row>'
        )
        shared = f'<sst xmlns="{MAIN_NS}"><si>{"<y>" * depth}<t>z</t>{"</y>" * depth}</si></sst>'
        data = make_xlsx(
            [("S", sheet_xml(body))],
            extra_parts={"xl/sharedStrings.xml": shared.encode()},
        )
        self.assertEqual(texts(data), {(1, 1, 1): "1", (1, 2, 3): "2"})

    def test_only_the_first_sheet_data_is_read(self) -> None:
        sheet = (
            f'<worksheet xmlns="{MAIN_NS}"><cols><col min="1" max="2"/></cols>'
            '<sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData>'
            '<sheetData><row r="2"><c r="A2"><v>2</v></c></row></sheetData></worksheet>'
        )
        self.assertEqual(texts(make_xlsx([("S", sheet)])), {(1, 1, 1): "1"})


class MemoryTest(unittest.TestCase):
    def test_working_memory_does_not_grow_with_the_sheet(self) -> None:
        """Beyond the part's own bytes and the spans it gives, a 200k-cell sheet needs at most
        1.5 times what a 20k-cell sheet needs. A whole parse tree needs about 10 times."""
        working = {}
        for rows in (2_000, 20_000):
            xml = number_sheet(rows)
            peak, kept = measure(make_xlsx([("S", xml)]))
            working[rows] = peak - kept - len(xml.encode())
        self.assertLessEqual(working[20_000], 1.5 * working[2_000], working)

    def test_an_empty_shared_string_bomb_costs_a_list_slot_not_a_tree(self) -> None:
        """500,000 "<si/>" (2.5 MB): the part plus one list slot per string is about 2.8 times
        the part; a whole tree is more than 15 times."""
        shared = f'<sst xmlns="{MAIN_NS}">{"<si/>" * 500_000}</sst>'.encode()
        data = make_xlsx(
            [("S", sheet_xml('<row r="1"><c r="A1" t="s"><v>499999</v></c></row>'))],
            extra_parts={"xl/sharedStrings.xml": shared},
        )
        peak, _ = measure(data)
        self.assertLessEqual(peak, 4 * len(shared), (peak, len(shared)))


# -- differential: 200 seeded workbooks ------------------------------------

_WORDS = ("invoice", "GSTIN", "₹1,200.50", "café", "हिन्दी", "📄", "a&b", "<x>", '"q"', "  pad  ", "\tt")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _random_workbook(rng: random.Random) -> tuple[bytes, list[tuple[tuple[int, int, int], str, str]]]:
    """A workbook, and the (cell, text, raw) the reader must give, in reading order."""
    shared: list[str] = []
    expected = []
    sheets = []
    for sheet in range(1, rng.randint(1, 3) + 1):
        rows_xml, row = [], 0
        for _ in range(rng.randint(0, 12)):
            row_ref = rng.random() < 0.8
            row += rng.randint(1, 4) if row_ref else 1
            cells_xml, column = [], 0
            for _ in range(rng.randint(0, 8)):
                cell_ref = rng.random() < 0.8
                column += rng.randint(1, 5) if cell_ref else 1
                ref = f' r="{letters(column)}{row}"' if cell_ref else ""
                text = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 3)))
                kind = rng.randrange(9)
                if kind == 0:
                    value: str | None = rng.choice(("0", "1.10", "-3", "1E-3", "45000", "9" * 20))
                    xml = f"<c{ref}><v>{value}</v></c>"
                elif kind == 1:
                    shared.append(text)
                    value, xml = text, f'<c{ref} t="s"><v>{len(shared) - 1}</v></c>'
                elif kind == 2:
                    value, xml = text, f'<c{ref} t="inlineStr"><is><t>{_escape(text)}</t></is></c>'
                elif kind == 3:
                    first, second = text, rng.choice(_WORDS)
                    value = first + second
                    xml = (
                        f'<c{ref} t="inlineStr"><is><r><t>{_escape(first)}</t></r><r><t>{_escape(second)}'
                        "</t></r><rPh><t>hint</t></rPh></is></c>"
                    )
                elif kind == 4:
                    stored = rng.choice("01")
                    value, xml = ("TRUE" if stored == "1" else "FALSE"), f'<c{ref} t="b"><v>{stored}</v></c>'
                elif kind == 5:
                    value = rng.choice(("#N/A", "#DIV/0!"))
                    xml = f'<c{ref} t="e"><v>{value}</v></c>'
                elif kind == 6:
                    cached = rng.random() < 0.5
                    value = "42" if cached else None
                    xml = f"<c{ref}><f>40+2</f>{'<v>42</v>' if cached else ''}</c>"
                elif kind == 7:
                    value, xml = "2024-03-01T00:00:00", f'<c{ref} t="d"><v>2024-03-01T00:00:00</v></c>'
                else:
                    value, xml = None, rng.choice((f"<c{ref}/>", f"<c{ref}><v>   </v></c>"))
                cells_xml.append(xml)
                if value is not None and value.strip():
                    expected.append(((sheet, row, column), value.strip(), value))
            row_attr = f' r="{row}"' if row_ref else ""
            rows_xml.append(f"<row{row_attr}>{''.join(cells_xml)}</row>")
        sheets.append((f"S{sheet}", sheet_xml("".join(rows_xml))))
    return make_xlsx(sheets, [_escape(text) for text in shared]), expected


class DifferentialTest(unittest.TestCase):
    def test_two_hundred_seeded_workbooks_read_as_generated(self) -> None:
        for seed in range(200):
            data, expected = _random_workbook(random.Random(seed))  # noqa: S311 - a fixed seed makes test workbooks, not a secret
            with self.subTest(seed=seed):
                got = []
                for span in XlsxReader(BIG).read(data):
                    assert isinstance(span.location, CellLocation)
                    location = span.location
                    got.append(((location.sheet, location.row, location.column), span.text, span.raw))
                self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()
