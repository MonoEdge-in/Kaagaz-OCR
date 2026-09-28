"""Values from spreadsheet cells, under a header row found by one written rule.

PROVISIONAL header rule (Vrushit's to confirm): in each sheet, the header is
the first row whose non-empty cells are all text (no cell reads as an amount
or a date, kaagaz.extraction.parsing), where the very next row has at least
one amount or date. Each later row is one record, ``sheet:{s}:row:{r}``, and
each of its cells under a header cell is one value, the field being the
normalised header text.

Never a guess (CLAUDE.md never-do 6):

* no row fits the rule: the sheet gives no values at all;
* a header cell that does not normalise to a field name gives no values;
* two header cells with the same field name give no values for either,
  because which column is "the" amount would be a guess.

The rows still become searchable pieces either way (kaagaz/chunking/rows.py).

Time O(n log n) for n cells (one sort; each cell is parsed at most twice);
memory O(n).
"""

from collections import Counter
from collections.abc import Iterable
from itertools import groupby

from kaagaz.extraction.parsing import normalise_field, parse_amount, parse_date
from kaagaz.extraction.values import ExtractedValue
from kaagaz.reading.spans import CellLocation, Span

_Row = list[tuple[Span, CellLocation]]


def values_from_cells(spans: Iterable[Span]) -> list[ExtractedValue]:
    """Values of every sheet that has a header row; spans of other kinds are ignored."""
    cells = [(span, span.location) for span in spans if isinstance(span.location, CellLocation)]
    cells.sort(key=lambda pair: pair[1])  # (sheet, row, column)
    values: list[ExtractedValue] = []
    for sheet, sheet_cells in groupby(cells, key=lambda pair: pair[1].sheet):
        rows = {row: list(group) for row, group in groupby(sheet_cells, key=lambda pair: pair[1].row)}
        header_row = _header_row(rows)
        if header_row is None:
            continue
        fields = _fields(rows[header_row])
        for row_number, row in rows.items():
            if row_number <= header_row:
                continue
            for span, cell in row:
                field = fields.get(cell.column)
                if field is not None:
                    values.append(
                        ExtractedValue(
                            record_key=f"sheet:{sheet}:row:{row_number}",
                            field=field,
                            value_text=span.text,
                            value_raw=span.raw,
                            location=cell,
                            confidence=span.confidence,
                        )
                    )
    return values


def _is_number_or_date(text: str) -> bool:
    return parse_amount(text) is not None or parse_date(text) is not None


def _header_row(rows: dict[int, _Row]) -> int | None:
    """The first row (in row order) that fits the header rule, or None."""
    for row_number in sorted(rows):
        all_text = not any(_is_number_or_date(span.text) for span, _ in rows[row_number])
        next_row = rows.get(row_number + 1, [])
        if all_text and any(_is_number_or_date(span.text) for span, _ in next_row):
            return row_number
    return None


def _fields(header: _Row) -> dict[int, str]:
    """Column -> field name, leaving out headers that do not normalise or repeat."""
    named = {cell.column: normalise_field(span.text) for span, cell in header}
    counts = Counter(field for field in named.values() if field is not None)
    return {column: field for column, field in named.items() if field is not None and counts[field] == 1}
