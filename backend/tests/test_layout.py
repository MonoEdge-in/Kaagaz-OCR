"""Layout-aware reading (#383): two columns or a side note are never one line or one block.

Synthetic word boxes go through ``reading.lines.line_spans`` (the PDF text layer's and the
local reader's line rule), and spans through ``chunking.layout.page_layout``. No model call.
"""

from kaagaz.chunking.layout import Block, page_layout
from kaagaz.reading.lines import PageFrame, WordBox, line_spans
from kaagaz.reading.spans import BoxLocation, Span

FRAME = PageFrame(0.0, 0.0, 600.0, 800.0)  # points; a line is 10 high


def word(text: str, x0: float, top: float, x1: float) -> WordBox:
    return WordBox(text, x0, top, x1, top + 10.0)


def two_columns() -> list[WordBox]:
    """A title over two columns of four lines. The gutter (280 to 298) is 1.8 line heights:
    narrower than the old rule's 3, so the old rule read each row as one line."""
    words = [word("Annual", 220.0, 50.0, 290.0), word("Report", 296.0, 50.0, 380.0)]
    for i in range(4):
        top = 100.0 + 14.0 * i
        words += [
            word(f"left{i}a", 50.0, top, 150.0),
            word(f"left{i}b", 156.0, top, 280.0),
            word(f"right{i}a", 298.0, top, 400.0),
            word(f"right{i}b", 406.0, top, 550.0),
        ]
    return words


def test_a_two_column_page_is_never_read_as_one_line_and_is_read_column_by_column() -> None:
    spans = line_spans(two_columns(), 1, FRAME, origin="text_layer")
    texts = [span.text for span in spans]
    assert texts == [
        "Annual Report",
        *(f"left{i}a left{i}b" for i in range(4)),
        *(f"right{i}a right{i}b" for i in range(4)),
    ]
    assert not any("left" in text and "right" in text for text in texts)


def test_a_two_column_page_has_a_block_per_column_with_its_box() -> None:
    layout = page_layout(line_spans(two_columns(), 1, FRAME, origin="text_layer"))
    assert [block.kind for block in layout.blocks] == ["text", "text", "text"]
    title, left, right = (block.box for block in layout.blocks)
    assert (title.y1, left.x1, right.x0) == (60 / 800, 280 / 600, 298 / 600)
    assert left.x1 < right.x0  # the columns' blocks never meet
    assert (left.y0, left.y1) == (right.y0, right.y1) == (100 / 800, 152 / 800)
    assert layout.cells == ()


def test_a_totals_box_is_no_gutter_so_its_labels_keep_their_amounts() -> None:
    """Narrow text either side of a gap (a label and its amount, lined up in 4 rows) is never
    a column: each row stays one line, so "Grand Total 1,180.00" is still one value."""
    words: list[WordBox] = []
    for i, label in enumerate(("Sub", "CGST", "SGST", "Grand")):
        top = 600.0 + 14.0 * i
        words += [
            word(label, 350.0, top, 400.0),
            word("Total", 404.0, top, 430.0),
            word("1,180.00", 445.0, top, 490.0),
        ]
    spans = line_spans(words, 1, FRAME, origin="text_layer")
    assert [span.text for span in spans] == [
        "Sub Total 1,180.00",
        "CGST Total 1,180.00",
        "SGST Total 1,180.00",
        "Grand Total 1,180.00",
    ]


def test_a_table_row_under_two_columns_keeps_its_row_order() -> None:
    """A row with more spans than columns (a table) ends the columns' run and stays in place."""
    words = two_columns() + [
        word(text, x, 200.0, x + 40.0)
        for text, x in (("Item", 50.0), ("Qty", 200.0), ("Rate", 350.0), ("Amt", 500.0))
    ]
    texts = [span.text for span in line_spans(words, 1, FRAME, origin="text_layer")]
    assert texts[-4:] == ["Item", "Qty", "Rate", "Amt"]
    assert texts[1] == "left0a left0b" and texts[5] == "right0a right0b"


def line(
    text: str,
    box: tuple[float, float, float, float],
    *,
    kind: str | None = None,
    block: int | None = None,
    table_id: str | None = None,
    row: int | None = None,
    col: int | None = None,
    header: bool | None = None,
) -> Span:
    """A line read by OCR on page 1; ``box`` is (x0, y0, x1, y1) in page fractions."""
    location = BoxLocation(1, *box)
    return Span(text, text, location, None, kind, "ocr", table_id, row, col, header, block=block)


def test_the_readers_blocks_keep_a_side_note_apart_with_their_own_boxes() -> None:
    """The Gemini reader's block ids: a paragraph, a note at its side given between its lines,
    a table and a stamp are four blocks, each the union of its own lines' boxes."""
    spans = [
        line("The goods were received", (0.1, 0.1, 0.6, 0.12), kind="paragraph", block=0),
        line("Checked, RK", (0.8, 0.105, 0.95, 0.125), kind="note", block=1),
        line("in good order.", (0.1, 0.13, 0.4, 0.15), kind="paragraph", block=0),
        line(
            "Item",
            (0.1, 0.3, 0.3, 0.32),
            kind="table_cell",
            table_id="t1",
            row=0,
            col=0,
            header=True,
            block=2,
        ),
        line(
            "Rice",
            (0.1, 0.33, 0.3, 0.35),
            kind="table_cell",
            table_id="t1",
            row=1,
            col=0,
            header=False,
            block=2,
        ),
        line("PAID", (0.6, 0.8, 0.8, 0.85), kind="stamp", block=3),
    ]
    layout = page_layout(spans)
    assert layout.blocks == (
        Block(1, "text", BoxLocation(1, 0.1, 0.1, 0.6, 0.15)),
        Block(1, "note", BoxLocation(1, 0.8, 0.105, 0.95, 0.125)),
        Block(1, "table", BoxLocation(1, 0.1, 0.3, 0.3, 0.35), "t1"),
        Block(1, "stamp", BoxLocation(1, 0.6, 0.8, 0.8, 0.85)),
    )
    assert [cell.text for cell in layout.cells] == ["Item", "Rice"]


def test_lines_with_no_block_are_grouped_by_where_they_sit() -> None:
    """A heading is its own block; the lines under it join while close and lined up; a gap
    wider than a line height starts a new block; another page starts its own."""
    spans = [
        line("Terms", (0.1, 0.1, 0.3, 0.12), kind="heading"),
        line("Payment is due in", (0.1, 0.125, 0.7, 0.145)),
        line("thirty days.", (0.1, 0.15, 0.3, 0.17)),
        line("Goods once sold", (0.1, 0.4, 0.6, 0.42)),
        Span("Page two", "Page two", BoxLocation(2, 0.1, 0.1, 0.5, 0.12), origin="ocr"),
    ]
    assert [(block.page, block.kind, block.box.y0, block.box.y1) for block in page_layout(spans).blocks] == [
        (1, "title", 0.1, 0.12),
        (1, "text", 0.125, 0.17),
        (1, "text", 0.4, 0.42),
        (2, "text", 0.1, 0.12),
    ]
