import unittest
from datetime import date

from kaagaz.chunking.context import context_header, with_context
from kaagaz.chunking.piece import Piece
from kaagaz.reading.spans import BoxLocation, CellLocation, Location


def piece(*pages: int, section: tuple[str, ...] = ()) -> Piece:
    locations: tuple[Location, ...] = tuple(BoxLocation(page, 0.1, 0.1, 0.9, 0.12) for page in pages)
    return Piece("cust-a", "doc-1", "text", locations, "verified", section=section)


def header(
    item: Piece,
    *,
    name: str = "Sept report.pdf",
    doc_type: str | None = None,
    day: date | None = None,
    before: str | None = None,
    sheet: int | None = None,
) -> str:
    return context_header(
        item, document_name=name, doc_type=doc_type, document_date=day, before=before, sheet=sheet
    )


class ContextHeaderTest(unittest.TestCase):
    def test_the_full_format(self) -> None:
        item = piece(15, 16, section=("Monthly results", "Profit"))
        self.assertEqual(
            header(
                item,
                doc_type="other",
                day=date(2026, 9, 30),
                before="The total expenses for September were $48,500.",
            ),
            "Document: Sept report.pdf | Type: other | Date: 2026-09-30 | Section: Monthly results > Profit"
            " | Pages 15-16 | Before: The total expenses for September were $48,500.",
        )

    def test_parts_with_no_value_are_left_out(self) -> None:
        # Before the document card (PR 5) there is no type or date.
        self.assertEqual(header(piece(3)), "Document: Sept report.pdf | Page 3")
        self.assertEqual(header(piece(3), before=""), "Document: Sept report.pdf | Page 3")

    def test_one_page_or_a_range(self) -> None:
        self.assertTrue(header(piece(4, 4)).endswith(" | Page 4"))
        self.assertTrue(header(piece(4, 6)).endswith(" | Pages 4-6"))

    def test_before_over_120_characters_keeps_its_last_120_after_an_ellipsis(self) -> None:
        end = "b" * 119 + "."
        self.assertTrue(header(piece(1), before="a" * 5 + end).endswith(" | Before: \u2026" + end))
        self.assertTrue(header(piece(1), before=end).endswith(" | Before: " + end))  # exactly 120: kept

    def test_the_section_part_is_at_most_120_characters(self) -> None:
        # Council A7: a heading can be long (a layout reader may call a paragraph a heading).
        text = header(piece(1, section=("Title", "h" * 300)))
        (part,) = [p for p in text.split(" | ") if p.startswith("Section: ")]
        self.assertEqual(len(part), 120)
        self.assertEqual(part, "Section: Title > " + "h" * 102 + "\u2026")

    def test_document_text_can_never_forge_a_part_or_hide_a_control_character(self) -> None:
        # Council: the header holds document text (A9), so a "|" or a bidi control in it
        # must not make a new part.
        forged = piece(1, section=("Profit | Type: invoice | Date: 2019-01-01 | Pages 1-99",))
        text = header(forged, name="bill\u202efdp.pdf", before="a | b\u200d\x07")
        self.assertEqual(
            text,
            "Document: billfdp.pdf | Section: Profit \u00a6 Type: invoice \u00a6 Date: 2019-01-01"
            " \u00a6 Pages 1-99 | Page 1 | Before: a \u00a6 b",
        )
        self.assertEqual(text.count(" | "), 3)  # 4 parts
        self.assertNotIn("\u202e", text)

    def test_a_sheet_piece_names_its_sheet_in_place_of_the_section_and_pages(self) -> None:
        row = Piece("cust-a", "doc-1", "A: x", (CellLocation(2, 5, 1),), "verified")
        self.assertEqual(
            header(row, name="sales.xlsx", sheet=2, before="A: y"),
            "Document: sales.xlsx | Sheet 2 | Before: A: y",
        )

    def test_line_breaks_and_runs_of_spaces_become_one_space(self) -> None:
        text = header(piece(1, section=("Monthly\nresults",)), name="Sept  report.pdf", before="a\n b")
        self.assertEqual(text, "Document: Sept report.pdf | Section: Monthly results | Page 1 | Before: a b")


class WithContextTest(unittest.TestCase):
    def test_each_piece_gets_its_header_with_the_line_before_it(self) -> None:
        first = Piece(
            "cust-a", "doc-1", "Sales were steady.\nCosts were checked.", (BOX_1, BOX_1), "verified"
        )
        second = Piece("cust-a", "doc-1", "Profit rose.", (BOX_1, BOX_2), "verified", section=("Profit",))
        done = with_context(
            [first, second], document_name="Sept report.pdf", doc_type=None, document_date=None
        )
        self.assertEqual(
            [p.context_header for p in done],
            [
                "Document: Sept report.pdf | Page 1",
                "Document: Sept report.pdf | Section: Profit | Pages 1-2 | Before: Costs were checked.",
            ],
        )
        self.assertEqual([p.text for p in done], [first.text, second.text])  # nothing else changes

    def test_the_type_and_date_are_in_the_header_when_known(self) -> None:
        (done,) = with_context(
            [piece(3)], document_name="bill.pdf", doc_type="invoice", document_date=date(2026, 8, 12)
        )
        self.assertEqual(
            done.context_header, "Document: bill.pdf | Type: invoice | Date: 2026-08-12 | Page 3"
        )

    def test_a_sheet_row_names_its_sheet(self) -> None:
        rows = [Piece("cust-a", "doc-1", f"A: {n}", (CellLocation(2, n, 1),), "verified") for n in (1, 2)]
        done = with_context(rows, document_name="sales.xlsx", doc_type=None, document_date=None)
        self.assertEqual(
            [p.context_header for p in done],
            ["Document: sales.xlsx | Sheet 2", "Document: sales.xlsx | Sheet 2 | Before: A: 1"],
        )

    def test_only_devanagari_pieces_get_a_latin_spelling(self) -> None:
        texts = ["पाटील\nशिंदे", "Sharma Traders", "पाटील शिंदे INV", "Total पाटील Invoice Sharma"]
        pieces = [Piece("cust-a", "doc-1", t, (BOX_1,), "verified") for t in texts]
        done = with_context(pieces, document_name="d.pdf", doc_type=None, document_date=None)
        # The last piece is mostly Latin letters, so it is not Devanagari (L2) and gets none.
        self.assertEqual([p.text_latin for p in done], ["patil\nshinde", None, "patil shinde inv", None])


BOX_1, BOX_2 = BoxLocation(1, 0.1, 0.1, 0.9, 0.12), BoxLocation(2, 0.1, 0.1, 0.9, 0.12)


if __name__ == "__main__":
    unittest.main()
