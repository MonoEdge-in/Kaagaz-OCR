"""The PROVISIONAL header rule: values only under a header row that the rule finds."""

import unittest

from kaagaz.extraction.spreadsheet import values_from_cells
from kaagaz.reading.csv_reader import CsvReader
from kaagaz.reading.spans import BoxLocation, CellLocation, Span


def read(csv: bytes) -> list[Span]:
    return list(CsvReader(max_cells=1000).read(csv))


def triples(csv: bytes) -> list[tuple[str, str, str]]:
    return [(v.record_key, v.field, v.value_text) for v in values_from_cells(read(csv))]


class HeaderRuleTest(unittest.TestCase):
    def test_header_row_names_the_values_of_each_later_row(self) -> None:
        csv = b'Invoice No,Party Name,Amount\nINV-1,Sharma Traders,"1,200"\nINV-2,Mehta Textiles,300\n'
        self.assertEqual(
            triples(csv),
            [
                ("sheet:1:row:2", "invoice_no", "INV-1"),
                ("sheet:1:row:2", "party_name", "Sharma Traders"),
                ("sheet:1:row:2", "amount", "1,200"),
                ("sheet:1:row:3", "invoice_no", "INV-2"),
                ("sheet:1:row:3", "party_name", "Mehta Textiles"),
                ("sheet:1:row:3", "amount", "300"),
            ],
        )

    def test_each_value_points_at_its_cell_and_keeps_the_raw_text(self) -> None:
        (value,) = values_from_cells(read(b"Amount\n  250  \n"))
        self.assertEqual(value.location, CellLocation(1, 2, 1))
        self.assertEqual((value.value_text, value.value_raw), ("250", "  250  "))

    def test_a_title_row_above_the_header_is_skipped(self) -> None:
        csv = b"Sales Register 2026\nDate,Party,Amount\n12/08/2026,Sharma,500\n"
        self.assertEqual(
            triples(csv),
            [
                ("sheet:1:row:3", "date", "12/08/2026"),
                ("sheet:1:row:3", "party", "Sharma"),
                ("sheet:1:row:3", "amount", "500"),
            ],
        )

    def test_a_date_in_the_next_row_is_enough(self) -> None:
        self.assertEqual(
            triples(b"When,Who\nAug 2026,Asha\n"),
            [
                ("sheet:1:row:2", "when", "Aug 2026"),
                ("sheet:1:row:2", "who", "Asha"),
            ],
        )

    def test_no_header_means_no_values(self) -> None:
        for csv in (
            b"INV-1,100\nINV-2,200\n",  # the first row already holds a number
            b"name,city\nAsha,Pune\nRavi,Delhi\n",  # no row ever holds a number or date
            b"Amount\n\n500\n",  # the row right after the text row is empty
            b"",
        ):
            with self.subTest(csv=csv):
                self.assertEqual(values_from_cells(read(csv)), [])

    def test_cells_without_a_header_cell_give_no_value(self) -> None:
        self.assertEqual(triples(b"Amount\n100,extra\n"), [("sheet:1:row:2", "amount", "100")])

    def test_a_header_that_does_not_normalise_gives_no_value(self) -> None:
        self.assertEqual(triples(b"Amount,%\n100,18\n"), [("sheet:1:row:2", "amount", "100")])

    def test_a_repeated_header_gives_no_value_for_either_column(self) -> None:
        csv = b"Party,Amount,Amount\nSharma,100,200\n"
        self.assertEqual(triples(csv), [("sheet:1:row:2", "party", "Sharma")])

    def test_each_sheet_finds_its_own_header(self) -> None:
        spans = [
            Span("Item", "Item", CellLocation(1, 1, 1)),
            Span("Pen", "Pen", CellLocation(1, 2, 1)),  # sheet 1: no numbers, no header
            Span("Qty", "Qty", CellLocation(2, 4, 2)),
            Span("7", "7", CellLocation(2, 5, 2)),
        ]
        self.assertEqual(
            [(v.record_key, v.field, v.value_text) for v in values_from_cells(spans)],
            [("sheet:2:row:5", "qty", "7")],
        )

    def test_rows_above_the_header_give_no_values_and_order_does_not_matter(self) -> None:
        spans = [
            Span("9", "9", CellLocation(1, 3, 1)),
            Span("Amount", "Amount", CellLocation(1, 2, 1)),
            Span("5", "5", CellLocation(1, 1, 1)),
            Span("Note", "Note", CellLocation(1, 1, 2)),
        ]
        # Row 1 holds a number, so row 2 is the header; row 1 stays out.
        self.assertEqual(
            [(v.record_key, v.field, v.value_text) for v in values_from_cells(spans)],
            [("sheet:1:row:3", "amount", "9")],
        )

    def test_line_spans_are_ignored(self) -> None:
        box = Span("Total: 5", "Total: 5", BoxLocation(1, 0.1, 0.1, 0.5, 0.2))
        self.assertEqual(values_from_cells([box]), [])

    def test_new_values_start_as_needs_review_until_labelled(self) -> None:
        (value,) = values_from_cells(read(b"Amount\n100\n"))
        self.assertEqual((value.label, value.label_reason), ("needs_review", "single_reader"))


if __name__ == "__main__":
    unittest.main()
