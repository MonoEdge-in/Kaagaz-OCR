import unittest

from kaagaz.chunking.lines import pieces_from_lines, running_spans
from kaagaz.reading.spans import BoxLocation, CellLocation, Span


def line(
    text: str,
    top: float,
    *,
    page: int = 1,
    height: float = 0.02,
    confidence: float | None = None,
    kind: str | None = None,
) -> Span:
    origin = "text_layer" if confidence is None else "ocr"  # as the readers set it
    box = BoxLocation(page, 0.1, top, 0.9, top + height)
    return Span(text, text, box, confidence, kind=kind, origin=origin)


def cell(text: str, page: int, row: int, col: int, *, table: str = "t1", header: bool = False) -> Span:
    """A table cell read directly, with its box, and its table, row and column from 0."""
    top, left = 0.05 + row * 0.03, 0.1 + col * 0.2
    box = BoxLocation(page, left, top, left + 0.15, top + 0.02)
    return Span(
        text,
        text,
        box,
        kind="table_cell",
        origin="text_layer",
        table_id=table,
        row=row,
        col=col,
        header=header,
    )


def table(page: int, rows: list[list[str]], *, header: bool, table: str = "t1") -> list[Span]:
    """The cells of one table, row by row; the first row is a header row when ``header``."""
    return [
        cell(text, page, r, c, table=table, header=header and r == 0)
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]


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

    def test_a_page_change_does_not_start_a_new_piece(self) -> None:
        # PRD 2 C1 replaces the old rule (a page change started a new piece).
        spans = [line("end of one", 0.9, page=1), line("start of two", 0.02, page=2)]
        (piece,) = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual((piece.text, piece.page_from, piece.page_to), ("end of one\nstart of two", 1, 2))
        self.assertEqual([r["page"] for r in piece.regions()], [1, 2])

    def test_the_gap_rule_is_never_measured_across_a_page_break(self) -> None:
        # Page 2's first line sits far below page 1's last one: that is no blank line.
        spans = [line("end of one", 0.1, page=1), line("start of two", 0.8, page=2)]
        self.assertEqual(texts(spans), ["end of one\nstart of two"])

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
        # Page 1 lines keep the reader's order even though "left" sits higher,
        # and page 2 follows page 1 in the same piece (C1: no split at the page).
        self.assertEqual(texts(spans), ["right column\nleft column\np2"])

    def test_a_scanned_line_makes_the_piece_need_review(self) -> None:
        spans = [line("sure", 0.10), line("unsure", 0.125, confidence=0.95), line("far", 0.6)]
        pieces = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual([p.label for p in pieces], ["needs_review", "verified"])

    def test_cells_are_left_to_the_row_chunker(self) -> None:
        cell = Span("x", "x", CellLocation(1, 1, 1))
        self.assertEqual(pieces_from_lines("cust-a", "doc-1", [cell]), [])

    def test_no_lines_gives_no_pieces(self) -> None:
        self.assertEqual(pieces_from_lines("cust-a", "doc-1", []), [])

    def test_seq_is_document_wide(self) -> None:
        # C6: the list order is pieces.seq. Spans may arrive out of page order.
        spans = []
        for page, name in ((3, "Three"), (2, "Two"), (1, "One")):
            spans += [line(name, 0.1, page=page, kind="heading"), line(f"{name} body", 0.13, page=page)]
        pieces = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual(
            [(p.text, p.page_from) for p in pieces],
            [("One\nOne body", 1), ("Two\nTwo body", 2), ("Three\nThree body", 3)],
        )


class SectionTest(unittest.TestCase):
    """C1 and C3: titles and headings start pieces, and each piece knows its section."""

    def test_heading_starts_a_piece_and_sets_the_section(self) -> None:
        spans = [
            line("Monthly results", 0.05, kind="title"),
            line("Sales were steady.", 0.08),
            line("Profit", 0.11, kind="heading"),  # no gap above it: the heading alone splits
            line("Profit rose.", 0.14),
        ]
        pieces = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual(
            [(p.text, p.section) for p in pieces],
            [
                ("Monthly results\nSales were steady.", ("Monthly results",)),
                ("Profit\nProfit rose.", ("Monthly results", "Profit")),
            ],
        )

    def test_heading_at_the_bottom_of_page_15_with_its_paragraph_on_16_is_one_piece(self) -> None:
        spans = [
            line("Costs were checked.", 0.1, page=15, kind="paragraph"),
            line("Profit", 0.9, page=15, kind="heading"),
            line("Profit of the month was $12,000.", 0.05, page=16, kind="paragraph"),
        ]
        pieces = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual(
            [(p.text, p.page_from, p.page_to, p.section) for p in pieces],
            [
                ("Costs were checked.", 15, 15, ()),
                ("Profit\nProfit of the month was $12,000.", 15, 16, ("Profit",)),
            ],
        )

    def test_a_heading_leads_the_lines_after_it_even_across_a_gap(self) -> None:
        # PROVISIONAL: a heading is never left alone in a piece. A title and the
        # heading under it lead one piece; a gap after them does not split it.
        spans = [
            line("Annual report", 0.05, kind="title"),
            line("Sales", 0.20, kind="heading"),
            line("Sales grew.", 0.40),
            line("Far below.", 0.80),
        ]
        pieces = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual(
            [(p.text, p.section) for p in pieces],
            [
                ("Annual report\nSales\nSales grew.", ("Annual report", "Sales")),
                ("Far below.", ("Annual report", "Sales")),
            ],
        )

    def test_the_section_carries_across_pages_until_a_new_title_replaces_it(self) -> None:
        spans = [
            line("Accounts", 0.05, page=1, kind="title"),
            line("Bank", 0.08, page=1, kind="heading"),
            line("Balance checked.", 0.11, page=1),
            line("Cash counted.", 0.1, page=2),
            line("Cheques listed.", 0.5, page=2),  # a gap on page 2: a new piece, same section
            line("Stock", 0.8, page=2, kind="title"),
            line("Bales counted.", 0.83, page=2),
        ]
        pieces = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual(
            [(p.text, p.section) for p in pieces],
            [
                ("Accounts\nBank\nBalance checked.\nCash counted.", ("Accounts", "Bank")),
                ("Cheques listed.", ("Accounts", "Bank")),
                ("Stock\nBales counted.", ("Stock",)),
            ],
        )


class RunningLinesTest(unittest.TestCase):
    """C2: running headers and footers are left out of pieces. PROVISIONAL (council):
    a repeated line keeps its first copy; a page counter is left out on every page."""

    def test_header_and_footer_kinds_are_left_out_when_they_repeat(self) -> None:
        one_page = [line("Sharma Traders", 0.02, kind="header"), line("Body text.", 0.3)]
        self.assertEqual(running_spans(one_page), set())  # a letterhead on one page is kept
        spans = []
        for page in (1, 2):
            spans += [
                line("Sharma Traders", 0.02, page=page, kind="header"),
                line(f"Body {page}.", 0.3, page=page),
            ]
            spans.append(line(f"Page {page}", 0.5, page=page, kind="footer"))  # wherever it sits
        self.assertEqual(running_spans(spans), {2, 3, 5})  # page 2's header, and both counters
        self.assertEqual(texts(spans), ["Sharma Traders", "Body 1.\nBody 2."])

    def test_a_line_repeated_in_the_band_on_3_pages_keeps_only_its_first_copy(self) -> None:
        def pages(count: int) -> list[Span]:
            spans = []
            for page in range(1, count + 1):
                spans += [
                    line("Sharma Traders Pvt Ltd", 0.02, page=page),  # the top 8%
                    line(f"Body {page}.", 0.3, page=page),
                    line("Total", 0.33, page=page),  # repeated, but not in a band
                    line(f"Page {page} of {count}", 0.95, page=page),  # the bottom 8%: a page counter
                ]
            return spans

        three = texts(pages(3))
        self.assertEqual(three, ["Sharma Traders Pvt Ltd", "Body 1.\nTotal\nBody 2.\nTotal\nBody 3.\nTotal"])
        two = texts(pages(2))  # a line on 2 pages is kept; a page counter is not
        self.assertEqual(
            two, ["Sharma Traders Pvt Ltd", "Body 1.\nTotal\nSharma Traders Pvt Ltd", "Body 2.\nTotal"]
        )

    def test_page_counters_are_left_out_on_every_page(self) -> None:
        for counter in ("Page {n} of 20", "- {n} -", "{n}/20", "Page {printed}"):  # printed: an offset of 4
            with self.subTest(counter=counter):
                spans = []
                for n in (1, 2, 3):
                    spans.append(line(counter.format(n=n, printed=n + 4), 0.95, page=n))
                    spans.append(line(f"Body {n}.", 0.3, page=n))
                self.assertEqual(texts(spans), ["Body 1.\nBody 2.\nBody 3."])

    def test_lines_in_the_band_that_differ_per_page_are_kept(self) -> None:
        spans = []
        for n, (invoice, carried) in enumerate((("101", "12,000"), ("205", "24,500"), ("318", "31,750")), 1):
            spans.append(line(f"Invoice No: {invoice}", 0.02, page=n))
            spans.append(line(f"Carried forward {carried}", 0.95, page=n))
        kept = "\n".join(texts(spans))
        for text in ("101", "205", "318", "12,000", "24,500", "31,750"):
            self.assertIn(text, kept)

    def test_an_invoice_number_rising_one_a_page_is_kept_but_a_small_page_offset_is_not(self) -> None:
        # PROVISIONAL (lead): a page counter's offset from the page number is at most 10,
        # so invoices 101 to 103 on pages 1 to 3 (offset 100) are kept.
        spans = [line(f"Invoice No: {100 + n}", 0.02, page=n) for n in (1, 2, 3)]
        spans += [line(f"Page {n + 2}", 0.95, page=n) for n in (1, 2, 3)]  # offset 2: a counter
        kept = "\n".join(texts(spans))
        for n in (101, 102, 103):
            self.assertIn(f"Invoice No: {n}", kept)
        self.assertNotIn("Page", kept)

    def test_a_carried_amount_at_the_top_of_each_page_stays_in_its_sentence(self) -> None:
        spans = [line("Profit of the month Sept is", 0.9, page=15)]
        for page, amount in ((16, "$12,000"), (17, "$48,500"), (18, "$9,250")):
            spans += [line(amount, 0.03, page=page), line(f"Page {page} of 20", 0.95, page=page)]
        (piece,) = [p for p in pieces_from_lines("cust-a", "doc-1", spans) if "Profit" in p.text]
        self.assertTrue(piece.text.startswith("Profit of the month Sept is\n$12,000"))

    def test_a_triplicate_invoice_keeps_one_copy_of_its_repeated_lines(self) -> None:
        spans = []
        for page, copy in enumerate(("Original", "Duplicate", "Triplicate"), 1):
            spans += [line("Sharma Traders", 0.02, page=page), line(copy, 0.2, page=page)]
            spans += [line("Invoice INV-7", 0.3, page=page), line("Grand Total: 1,180.00", 0.95, page=page)]
        pieces = texts(spans)
        self.assertEqual(sum("Sharma Traders" in p for p in pieces), 1)
        self.assertEqual(sum("Grand Total" in p for p in pieces), 1)

    def test_the_band_is_the_top_and_bottom_8_percent(self) -> None:
        spans = []
        for page in (1, 2, 3):
            spans += [line("Top edge", 0.06, page=page), line("Just below the band", 0.07, page=page)]
            spans += [line("Bottom edge", 0.92, page=page), line("Just above the band", 0.91, page=page)]
        # "Top edge" ends at 0.08 and "Bottom edge" starts at 0.92: both in a band.
        running = running_spans(spans)
        self.assertEqual({spans[i].text for i in running}, {"Top edge", "Bottom edge"})

    def test_a_table_header_row_repeated_at_the_top_of_pages_is_not_running(self) -> None:
        spans = [cell("Item", page, 0, 0, table=f"t{page}", header=True) for page in (1, 2, 3)]
        # Only the repeats of the line "x" (pages 2 and 3) are running; no cell is.
        self.assertEqual(running_spans([*spans, *(line("x", 0.02, page=p) for p in (1, 2, 3))]), {4, 5})


class TableTest(unittest.TestCase):
    """C5: tables from a layout reader make their own pieces with header names."""

    def test_table_piece_has_at_most_20_rows_and_header_names(self) -> None:
        rows = [["Item", "Amount"]] + [[f"item {n}", f"{n}00"] for n in range(1, 26)]
        pieces = pieces_from_lines("cust-a", "doc-1", table(1, rows, header=True))
        self.assertEqual([p.text.count("\n") + 1 for p in pieces], [20, 5])
        self.assertEqual(pieces[0].text.split("\n")[0], "Item: item 1 | Amount: 100")
        self.assertEqual(pieces[1].text.split("\n")[-1], "Item: item 25 | Amount: 2500")
        # Only the data cells are the piece's parts, in text order; the header names are labels.
        self.assertEqual(pieces[1].locations, tuple(s.location for s in table(1, rows, header=True)[42:]))

    def test_a_table_piece_also_stays_under_the_character_limit(self) -> None:
        rows = [["Item"]] + [["x" * 30] for _ in range(6)]
        lengths = [len(t) for t in texts(table(1, rows, header=True), max_chars=100)]
        self.assertEqual(lengths, [73, 73, 73])  # "Item: " + 30 = 36 per row, 2 rows + 1 newline

    def test_a_table_without_a_header_row_uses_column_letters(self) -> None:
        self.assertEqual(texts(table(1, [["INV-1", "100"]], header=False)), ["A: INV-1 | B: 100"])

    def test_a_long_header_name_is_cut_to_60_characters(self) -> None:
        (text,) = texts(table(1, [["h" * 500, "Qty"], ["x", "2"]], header=True))
        self.assertEqual(text, "h" * 59 + "\u2026: x | Qty: 2")  # council: a header cell can be a paragraph

    def test_a_table_of_only_a_header_row_keeps_its_text(self) -> None:
        self.assertEqual(texts(table(1, [["Item", "Qty"]], header=True)), ["A: Item | B: Qty"])

    def test_a_continued_table_inherits_headers_only_when_the_column_count_matches(self) -> None:
        first = table(1, [["Item", "Qty", "Amount"], ["Cotton", "2", "500"]], header=True)
        same = table(2, [["Silk", "1", "900"]], header=False, table="t2")
        other = table(3, [["Wool", "4"]], header=False, table="t3")
        self.assertEqual(
            texts(first + same + other),
            [
                "Item: Cotton | Qty: 2 | Amount: 500",
                "Item: Silk | Qty: 1 | Amount: 900",  # page 2 inherits page 1's headers
                "A: Wool | B: 4",  # 2 columns, not 3: no headers to inherit
            ],
        )
        # Body text between the two tables means page 2's table is a new one.
        between = [line("Notes", 0.9, page=1), *same]
        self.assertEqual(texts(first + between)[-1], "A: Silk | B: 1 | C: 900")

    def test_a_page_that_is_all_table_is_never_joined_across(self) -> None:
        spans = [line("Before the table", 0.9, page=1)]
        spans += table(2, [["Item", "Qty"], ["Cotton", "2"]], header=True)
        spans += [line("After the table", 0.05, page=3)]
        pieces = pieces_from_lines("cust-a", "doc-1", spans)
        self.assertEqual(
            [(p.text, p.page_from, p.page_to) for p in pieces],
            [("Before the table", 1, 1), ("Item: Cotton | Qty: 2", 2, 2), ("After the table", 3, 3)],
        )

    def test_a_table_piece_keeps_the_section_and_an_unsure_cell_needs_review(self) -> None:
        spans = [line("Stock", 0.02, kind="heading"), *table(1, [["Item"], ["Cotton"]], header=True)]
        box = BoxLocation(1, 0.1, 0.5, 0.3, 0.52)
        unsure = Span("Silk", "Silk", box, 0.8, origin="ocr", table_id="t1", row=2, col=0)
        pieces = pieces_from_lines("cust-a", "doc-1", [*spans, unsure])
        self.assertEqual(
            [(p.text, p.section, p.label) for p in pieces],
            [("Stock", ("Stock",), "verified"), ("Item: Cotton\nItem: Silk", ("Stock",), "needs_review")],
        )


if __name__ == "__main__":
    unittest.main()
