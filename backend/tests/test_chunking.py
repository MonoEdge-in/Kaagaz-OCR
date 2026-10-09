import unittest

from kaagaz.chunking.piece import NEEDS_REVIEW, VERIFIED, Piece, piece_label
from kaagaz.chunking.rows import column_letter, pieces_from_rows
from kaagaz.reading.csv_reader import CsvReader
from kaagaz.reading.spans import BoxLocation, CellLocation, Span


def span(text: str, sheet: int, row: int, column: int, confidence: float | None = None) -> Span:
    return Span(text, text, CellLocation(sheet, row, column), confidence, origin="cell")


def cells(piece: Piece) -> list[CellLocation]:
    """The piece's cells; fails if any location is not a cell."""
    found = [location for location in piece.locations if isinstance(location, CellLocation)]
    assert len(found) == len(piece.locations)
    return found


def sheet_and_row(piece: Piece) -> tuple[int, int]:
    first = cells(piece)[0]
    return first.sheet, first.row


class PieceLabelTest(unittest.TestCase):
    def test_an_ocr_span_is_never_a_direct_read_even_with_no_confidence(self) -> None:
        box = BoxLocation(1, 0.1, 0.1, 0.5, 0.2)
        self.assertEqual(piece_label([Span("x", "x", box, origin="ocr")]), NEEDS_REVIEW)  # the main reader
        self.assertEqual(piece_label([Span("x", "x", box, origin="text_layer")]), VERIFIED)
        self.assertEqual(piece_label([Span("x", "x", CellLocation(1, 1, 1), origin="cell")]), VERIFIED)
        self.assertEqual(piece_label([Span("x", "x", box, 0.99, origin="ocr")]), NEEDS_REVIEW)  # RapidOCR

    def test_a_span_that_does_not_say_how_it_was_read_is_never_a_direct_read(self) -> None:
        # Fail closed (council): only origin "cell" or "text_layer" counts as a direct read.
        for location in (BoxLocation(1, 0.1, 0.1, 0.5, 0.2), CellLocation(1, 1, 1)):
            with self.subTest(location=location):
                self.assertEqual(piece_label([Span("x", "x", location)]), NEEDS_REVIEW)


class ColumnLetterTest(unittest.TestCase):
    def test_known_columns(self) -> None:
        # Literal table, not computed by the code under test.
        cases = {1: "A", 2: "B", 26: "Z", 27: "AA", 52: "AZ", 53: "BA", 702: "ZZ", 703: "AAA", 16384: "XFD"}
        for number, letters in cases.items():
            with self.subTest(number=number):
                self.assertEqual(column_letter(number), letters)

    def test_zero_or_negative_is_refused(self) -> None:
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                column_letter(bad)


class PiecesFromRowsTest(unittest.TestCase):
    def test_one_piece_per_row_labelled_by_column(self) -> None:
        spans = CsvReader(max_cells=100).read(b"invoice,amount\nINV-1,100\n")
        pieces = pieces_from_rows("cust-a", "doc-1", spans)
        self.assertEqual([p.text for p in pieces], ["A: invoice | B: amount", "A: INV-1 | B: 100"])
        self.assertEqual([sheet_and_row(p)[1] for p in pieces], [1, 2])

    def test_every_piece_traces_back_to_its_cells(self) -> None:
        spans = list(CsvReader(max_cells=100).read(b"a,,c\nd,e\n"))
        by_location = {s.location: s.text for s in spans}
        for piece in pieces_from_rows("cust-a", "doc-1", spans):
            rebuilt = " | ".join(f"{column_letter(c.column)}: {by_location[c]}" for c in cells(piece))
            self.assertEqual(rebuilt, piece.text)
            sheet, row = sheet_and_row(piece)
            self.assertTrue(all(c.row == row and c.sheet == sheet for c in cells(piece)))

    def test_long_cell_is_never_cut(self) -> None:
        long_text = "x" * 5000
        (piece,) = pieces_from_rows("cust-a", "doc-1", [span(long_text, 1, 1, 1)])
        self.assertEqual(piece.text, "A: " + long_text)

    def test_rows_are_grouped_and_ordered_even_if_spans_arrive_out_of_order(self) -> None:
        spans = [span("b2", 1, 2, 2), span("a1", 1, 1, 1), span("a2", 1, 2, 1), span("s2", 2, 1, 1)]
        pieces = pieces_from_rows("cust-a", "doc-1", spans)
        self.assertEqual(
            [(*sheet_and_row(p), p.text) for p in pieces],
            [
                (1, 1, "A: a1"),
                (1, 2, "A: a2 | B: b2"),
                (2, 1, "A: s2"),
            ],
        )

    def test_pieces_carry_their_customer_and_document(self) -> None:
        (piece,) = pieces_from_rows("cust-a", "doc-9", [span("x", 1, 1, 1)])
        self.assertEqual((piece.customer_id, piece.document_id), ("cust-a", "doc-9"))

    def test_no_spans_gives_no_pieces(self) -> None:
        self.assertEqual(pieces_from_rows("cust-a", "doc-1", []), [])

    def test_regions_pages_and_label_of_a_row(self) -> None:
        (piece,) = pieces_from_rows("cust-a", "doc-1", [span("x", 2, 5, 1), span("y", 2, 5, 3)])
        self.assertEqual(
            piece.regions(),
            [
                {"kind": "cell", "page": 2, "row": 5, "column": 1},
                {"kind": "cell", "page": 2, "row": 5, "column": 3},
            ],
        )
        self.assertEqual((piece.page_from, piece.page_to, piece.label), (2, 2, "verified"))

    def test_a_row_with_an_unsure_cell_needs_review(self) -> None:
        (piece,) = pieces_from_rows(
            "cust-a", "doc-1", [span("x", 1, 1, 1), span("y", 1, 1, 2, confidence=0.99)]
        )
        self.assertEqual(piece.label, "needs_review")

    def test_line_spans_are_left_to_the_line_chunker(self) -> None:
        box = Span("line", "line", BoxLocation(1, 0.1, 0.1, 0.5, 0.2))
        self.assertEqual(pieces_from_rows("cust-a", "doc-1", [box]), [])


if __name__ == "__main__":
    unittest.main()
