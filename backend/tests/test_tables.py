"""Tables as rows and cells (#382): which number belongs to which column, for the model and
for Ask, with every cell keeping its page and spot. No model call.

The table, as the Gemini reader gives it (g3): a merged header "Amount" over CGST and SGST,
a row with no quantity, and that row's cells given out of column order.

    Item   | Qty | Amount
           |     | CGST | SGST
    Widget |     | 90   | 45
    Bolt   | 2   | 18   | 9
"""

from kaagaz.answering import draft, service
from kaagaz.answering.evidence import number_evidence
from kaagaz.chunking.layout import header_names, page_layout
from kaagaz.chunking.lines import pieces_from_lines
from kaagaz.extraction.invoice import _Merge
from kaagaz.extraction.quotes import find_quote, reader_text
from kaagaz.reading.spans import BoxLocation, Span, location_to_json
from kaagaz.search.hybrid import SearchHit

CUSTOMER = "11111111-1111-1111-1111-111111111111"
DOCUMENT = "22222222-2222-2222-2222-222222222222"
COLUMNS = ((0.1, 0.3), (0.3, 0.4), (0.4, 0.55), (0.55, 0.7))


def cell(text: str, row: int, col: int, *, header: bool = False, col_span: int | None = None) -> Span:
    x0 = COLUMNS[col][0]
    x1 = COLUMNS[col + (col_span or 1) - 1][1]
    box = BoxLocation(1, x0, 0.1 + 0.03 * row, x1, 0.12 + 0.03 * row)
    return Span(
        text, text, box, None, "table_cell", "ocr", "t1", row, col, header, block=1, col_span=col_span
    )


HEADERS = [
    cell("Item", 0, 0, header=True),
    cell("Qty", 0, 1, header=True),
    cell("Amount", 0, 2, header=True, col_span=2),
    cell("CGST", 1, 2, header=True),
    cell("SGST", 1, 3, header=True),
]
WIDGET = [cell("Widget", 2, 0), cell("45", 2, 3), cell("90", 2, 2)]  # out of column order
BOLT = [cell("Bolt", 3, 0), cell("2", 3, 1), cell("18", 3, 2), cell("9", 3, 3)]
TABLE = [*HEADERS, *WIDGET, *BOLT]
SGST_45 = WIDGET[1]


def test_a_merged_header_names_every_column_it_covers() -> None:
    assert header_names(HEADERS) == {0: "Item", 1: "Qty", 2: "Amount CGST", 3: "Amount SGST"}


def test_the_model_sees_each_row_with_its_columns_kept_and_each_cell_is_still_found() -> None:
    """The invoice and land windows' text: every row in column order, an empty cell for a
    missing quantity and for the columns a merged header covers."""
    reader = reader_text(TABLE)
    assert reader.text == "Item | Qty | Amount\n |  | CGST | SGST\nWidget |  | 90 | 45\nBolt | 2 | 18 | 9"
    found = find_quote(reader, "45")
    assert found is not None and (found.location, found.whole) == (SGST_45.location, True)
    # The invoice's rows (council 2): a row's cells are one row across its empty cells.
    merge = _Merge(reader)
    widget = reader.spans.index(WIDGET[0])
    assert [merge.row_first[reader.spans.index(span)] for span in WIDGET] == [widget] * 3


def test_a_table_piece_names_each_cell_by_its_column_merged_headers_included() -> None:
    (piece,) = pieces_from_lines(CUSTOMER, DOCUMENT, TABLE)
    assert piece.text == (
        "Item: Widget | Amount CGST: 90 | Amount SGST: 45\n"
        "Item: Bolt | Qty: 2 | Amount CGST: 18 | Amount SGST: 9"
    )
    texts = ["Widget", "90", "45", "Bolt", "2", "18", "9"]
    by_text = {span.text: span.location for span in TABLE}
    assert list(piece.locations) == [by_text[text] for text in texts]  # every cell keeps its spot


def test_the_table_is_stored_as_one_table_block_and_its_cells_in_order() -> None:
    layout = page_layout(TABLE)
    assert [(block.kind, block.table_id) for block in layout.blocks] == [("table", "t1")]
    assert [(span.row, span.col, span.col_span) for span in layout.cells][:3] == [
        (0, 0, None),
        (0, 1, None),
        (0, 2, 2),
    ]
    assert [span.text for span in layout.cells][5:8] == ["Widget", "90", "45"]


def test_asks_evidence_for_a_table_keeps_its_labels_and_cites_the_cell_it_used() -> None:
    (piece,) = pieces_from_lines(CUSTOMER, DOCUMENT, TABLE)
    hit = SearchHit(
        "p1", DOCUMENT, "bill.pdf", "pdf", "invoice", 1, 1, piece.text, piece.regions(), "needs_review",
        1.0, None, None, 0.9, 0, "",
    )  # fmt: skip
    (evidence,) = number_evidence([hit])
    prompt = draft.user_prompt("What is the SGST on the widget?", [evidence])
    assert "Item: Widget | Amount CGST: 90 | Amount SGST: 45" in prompt

    citation = service._piece_citation(1, evidence, ["The SGST on the widget is 45."])
    assert citation.regions == [location_to_json(SGST_45.location)]
    assert citation.page_no == 1
