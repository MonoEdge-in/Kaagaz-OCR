import unittest
from datetime import date

from kaagaz.chunking.context import context_header
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
        self.assertTrue(header(piece(1), before="a" * 5 + end).endswith(" | Before: …" + end))
        self.assertTrue(header(piece(1), before=end).endswith(" | Before: " + end))  # exactly 120: kept

    def test_the_section_part_is_at_most_120_characters(self) -> None:
        # Council A7: a heading can be long (a layout reader may call a paragraph a heading).
        text = header(piece(1, section=("Title", "h" * 300)))
        (part,) = [p for p in text.split(" | ") if p.startswith("Section: ")]
        self.assertEqual(len(part), 120)
        self.assertEqual(part, "Section: Title > " + "h" * 102 + "…")

    def test_a_sheet_piece_names_its_sheet_in_place_of_the_section_and_pages(self) -> None:
        row = Piece("cust-a", "doc-1", "A: x", (CellLocation(2, 5, 1),), "verified")
        self.assertEqual(
            header(row, name="sales.xlsx", sheet=2, before="A: y"),
            "Document: sales.xlsx | Sheet 2 | Before: A: y",
        )

    def test_line_breaks_and_runs_of_spaces_become_one_space(self) -> None:
        text = header(piece(1, section=("Monthly\nresults",)), name="Sept  report.pdf", before="a\n b")
        self.assertEqual(text, "Document: Sept report.pdf | Section: Monthly results | Page 1 | Before: a b")


if __name__ == "__main__":
    unittest.main()
