import unittest

from kaagaz.chunking.lines import pieces_from_lines
from kaagaz.reading.spans import BoxLocation, CellLocation, Span


def line(
    text: str, top: float, *, page: int = 1, height: float = 0.02, confidence: float | None = None
) -> Span:
    origin = "text_layer" if confidence is None else "ocr"  # as the readers set it
    return Span(text, text, BoxLocation(page, 0.1, top, 0.9, top + height), confidence, origin=origin)


def texts(spans: list[Span], **kwargs: int) -> list[str]:
    return [p.text for p in pieces_from_lines("cust-a", "doc-1", spans, **kwargs)]


class PiecesFromLinesTest(unittest.TestCase):
    def test_close_lines_on_one_page_make_one_piece(self) -> None:
        spans = [line("one", 0.10), line("two", 0.125), line("three", 0.15)]
        self.assertEqual(texts(spans), ["one\ntwo\nthree"])

    def test_a_blank_line_gap_starts_a_new_piece(self) -> None:
        # Line height 0.02, so the limit is a gap of 0.03. 0.12 -> 0.155 is 0.035.
        spans = [line("one", 0.10), line("two", 0.155), line("three", 0.18)]
        self.assertEqual(texts(spans), ["one", "two\nthree"])

    def test_a_gap_just_under_the_limit_does_not_split(self) -> None:
        spans = [line("one", 0.10), line("two", 0.149)]  # gap 0.029 < 0.03
        self.assertEqual(texts(spans), ["one\ntwo"])

    def test_a_page_change_starts_a_new_piece(self) -> None:
        spans = [line("end of one", 0.9, page=1), line("start of two", 0.02, page=2)]
        pieces = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual(
            [(p.text, p.page_from, p.page_to) for p in pieces],
            [
                ("end of one", 1, 1),
                ("start of two", 2, 2),
            ],
        )

    def test_pieces_stay_under_the_character_limit(self) -> None:
        spans = [line("x" * 30, 0.1 + i * 0.021) for i in range(10)]
        pieces = texts(spans, max_chars=100)
        self.assertEqual([len(p) for p in pieces], [92, 92, 92, 30])  # 3 lines + 2 newlines each
        self.assertEqual("\n".join(pieces), "\n".join(s.text for s in spans))

    def test_one_line_over_the_limit_is_never_cut(self) -> None:
        spans = [line("short", 0.1), line("y" * 900, 0.125), line("after", 0.15)]
        self.assertEqual(texts(spans), ["short", "y" * 900, "after"])

    def test_every_line_keeps_its_box_in_text_order(self) -> None:
        spans = [line("one", 0.10), line("two", 0.125)]
        (piece,) = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual(piece.locations, (spans[0].location, spans[1].location))
        self.assertEqual(piece.regions()[1], {"kind": "box", "page": 1, "box": [0.1, 0.125, 0.9, 0.145]})

    def test_reading_order_within_a_page_is_kept_and_pages_are_ordered(self) -> None:
        spans = [line("p2", 0.1, page=2), line("right column", 0.5), line("left column", 0.1)]
        # Page 1 lines keep the reader's order even though "left" sits higher.
        self.assertEqual(texts(spans), ["right column\nleft column", "p2"])

    def test_a_scanned_line_makes_the_piece_need_review(self) -> None:
        spans = [line("sure", 0.10), line("unsure", 0.125, confidence=0.95), line("far", 0.6)]
        pieces = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual([p.label for p in pieces], ["needs_review", "verified"])

    def test_cells_are_left_to_the_row_chunker(self) -> None:
        cell = Span("x", "x", CellLocation(1, 1, 1))
        self.assertEqual(pieces_from_lines("cust-a", "doc-1", [cell]), [])

    def test_no_lines_gives_no_pieces(self) -> None:
        self.assertEqual(pieces_from_lines("cust-a", "doc-1", []), [])


if __name__ == "__main__":
    unittest.main()
