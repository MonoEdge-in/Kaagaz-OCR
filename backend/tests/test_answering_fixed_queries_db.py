"""The fixed queries against a real PostgreSQL (kaagaz/answering/fixed_queries.py).

Documents and extracted values are inserted directly with SQL, the way the
pipeline store leaves them: one row per value, records keyed by
(document_id, record_key), each value with its page and region.
"""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import fixed_queries
from kaagaz.answering.dates import date_range, mentions_date
from kaagaz.answering.fixed_queries import MAX_CITED_VALUES, Figure, PgFixedQueries
from kaagaz.answering.format import indian_number
from kaagaz.answering.hinglish import normalise
from kaagaz.answering.intent import FIELDS, FixedPlan, plan_fixed
from kaagaz.answering.service import Answer, AnswerService
from kaagaz.answering.totals import PRINTED_TOTAL
from kaagaz.db.engine import Database
from kaagaz.extraction.labelled import values_from_lines
from kaagaz.extraction.parsing import parse_amount, parse_date
from kaagaz.extraction.spreadsheet import values_from_cells
from kaagaz.extraction.values import ExtractedValue
from kaagaz.reading.spans import BoxLocation, CellLocation, Span, location_to_json
from kaagaz.search.scope import EVERYTHING
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_answering_intent import EXAMPLE_3

pytestmark = pytest.mark.db

AUGUST = (date(2026, 8, 1), date(2026, 9, 1))
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
DATES = ("date", "invoice_date")
PARTIES = ("party_name", "name")

_DOCUMENT = text(
    "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes, detected_type, status,"
    " family_id) VALUES (:id, :c, :name, :sha, 10, :type, :status, :id)"
)
_VALUE = text(
    "INSERT INTO extracted_values (id, customer_id, document_id, record_key, field, value_text,"
    " value_raw, value_number, value_date, page_no, region, reader, label, label_reason)"
    " VALUES (:id, :c, :d, :key, :field, :text, :text, :number, :day, :page, CAST(:region AS jsonb),"
    " 'test', :label, :reason)"
)


def add_document(
    db: Database, customer: str, name: str, *, status: str = "ready", detected_type: str = "zip"
) -> str:
    document_id = str(uuid.uuid4())
    sha = hashlib.sha256(document_id.encode()).hexdigest()
    params = {"id": document_id, "c": customer, "name": name, "sha": sha, "type": detected_type}
    with db.customer(customer) as conn:
        conn.execute(_DOCUMENT, {**params, "status": status})
    return document_id


def cell(
    key: str,
    field: str,
    value_text: str,
    *,
    row: int = 2,
    column: int = 1,
    label: str = "verified",
    **parsed: Any,
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "key": key,
        "field": field,
        "text": value_text,
        "number": parsed.get("number"),
        "day": parsed.get("day"),
        "page": 1,
        "region": json.dumps({"kind": "cell", "page": 1, "row": row, "column": column}),
        "label": label,
        "reason": "direct_cell" if label == "verified" else "single_reader",
    }


def row(r: int, party: str, profit: int | None, day: date, **labels: str) -> list[dict[str, Any]]:
    key = f"sheet:1:row:{r}"
    return [
        cell(key, "party_name", party, row=r, column=1, label=labels.get("party_label", "verified")),
        cell(
            key,
            "profit",
            str(profit),
            row=r,
            column=2,
            number=profit,
            label=labels.get("profit_label", "verified"),
        ),
        cell(key, "date", day.isoformat(), row=r, column=3, day=day),
    ]


def add_values(db: Database, customer: str, document_id: str, values: list[dict[str, Any]]) -> None:
    with db.customer(customer) as conn:
        conn.execute(_VALUE, [{**v, "c": customer, "d": document_id} for v in values])


def ids(values: list[dict[str, Any]], field: str | None = None) -> set[str]:
    return {v["id"] for v in values if field is None or v["field"] == field}


@pytest.fixture
def register(db: Database) -> tuple[str, str, list[dict[str, Any]]]:
    customer, _ = new_customer(db)
    document = add_document(db, customer, "profit-register.xlsx")
    values = [
        *row(2, "Sharma Traders", 70000, date(2026, 8, 1)),
        *row(3, "Mehta Textiles", 80000, date(2026, 8, 15)),
        *row(4, "SHARMA   traders", 50000, date(2026, 8, 31)),
        *row(5, "Gupta Stores", 90000, date(2026, 9, 1)),
        *row(6, "Sharma Traders", 30000, date(2026, 7, 31)),
    ]
    add_values(db, customer, document, values)
    return customer, document, values


def test_available_fields_are_those_of_ready_documents(db: Database) -> None:
    customer, _ = new_customer(db)
    ready = add_document(db, customer, "a.xlsx")
    waiting = add_document(db, customer, "b.xlsx", status="clean")
    add_values(db, customer, ready, [cell("sheet:1:row:2", "profit", "5", number=5)])
    add_values(db, customer, waiting, [cell("sheet:1:row:2", "expense", "5", number=5)])
    assert PgFixedQueries(db).available_fields(customer, FIELDS) == {"profit"}


def test_top_group_sums_per_party_inside_the_month(db: Database, register: Any) -> None:
    customer, document, values = register
    rows = PgFixedQueries(db).top_group(customer, "profit", PARTIES, DATES, *AUGUST, True)

    assert [(r.group, r.number, r.records) for r in rows] == [
        ("Sharma Traders", Decimal(120000), 2),  # 70,000 on 1 Aug + 50,000 on 31 Aug
        ("Mehta Textiles", Decimal(80000), 1),
    ]
    top = rows[0]
    assert top.verified is True
    sharma_rows = {v["key"] for v in values if v["text"] in {"Sharma Traders", "SHARMA   traders"}}
    expected = {v["id"] for v in values if v["key"] in sharma_rows - {"sheet:1:row:6"}}
    assert {ref.value_id for ref in top.values} == expected
    profit = next(ref for ref in top.values if ref.field == "profit" and ref.value_text == "70000")
    assert profit.document_id == document
    assert profit.document_name == "profit-register.xlsx"
    assert profit.detected_type == "zip"
    assert profit.page_no == 1
    assert profit.region == {"kind": "cell", "page": 1, "row": 2, "column": 2}
    assert profit.label == "verified"


def test_top_group_ascending_and_without_a_date(db: Database, register: Any) -> None:
    customer, _, _ = register
    fixed = PgFixedQueries(db)
    lowest = fixed.top_group(customer, "profit", PARTIES, DATES, *AUGUST, False)
    assert [r.group for r in lowest] == ["Mehta Textiles", "Sharma Traders"]
    everything = fixed.top_group(customer, "profit", PARTIES, (), None, None, True)
    assert [(r.group, r.number) for r in everything] == [
        ("Sharma Traders", Decimal(150000)),
        ("Gupta Stores", Decimal(90000)),
        ("Mehta Textiles", Decimal(80000)),
    ]


def test_one_needs_review_value_makes_the_group_unverified(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer, "r.xlsx")
    add_values(
        db, customer, document, row(2, "Sharma Traders", 10, date(2026, 8, 3), party_label="needs_review")
    )
    rows = PgFixedQueries(db).top_group(customer, "profit", PARTIES, DATES, *AUGUST, True)
    assert rows[0].verified is False


def test_a_record_with_two_values_for_one_role_is_left_out(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer, "r.xlsx")
    two_profits = [
        *row(2, "Sharma Traders", 10, date(2026, 8, 3)),
        cell("sheet:1:row:2", "profit", "99", number=99),
    ]
    two_dates = [
        *row(3, "Mehta Textiles", 20, date(2026, 8, 4)),
        cell("sheet:1:row:3", "invoice_date", "2026-08-04", day=date(2026, 8, 4)),
    ]
    two_parties = [*row(4, "Gupta Stores", 30, date(2026, 8, 5)), cell("sheet:1:row:4", "name", "Gupta")]
    fine = row(5, "Bansal & Co", 5, date(2026, 8, 6))
    add_values(db, customer, document, [*two_profits, *two_dates, *two_parties, *fine])

    rows = PgFixedQueries(db).top_group(customer, "profit", PARTIES, DATES, *AUGUST, True)
    assert [(r.group, r.number) for r in rows] == [("Bansal & Co", Decimal(5))]


def test_a_metric_without_a_number_is_left_out(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer, "r.xlsx")
    add_values(
        db,
        customer,
        document,
        [*row(2, "Sharma Traders", None, date(2026, 8, 3)), *row(3, "Mehta", 7, date(2026, 8, 3))],
    )
    rows = PgFixedQueries(db).top_group(customer, "profit", PARTIES, DATES, *AUGUST, True)
    assert [r.group for r in rows] == ["Mehta"]


def test_documents_that_are_not_ready_are_left_out(db: Database) -> None:
    customer, _ = new_customer(db)
    failed = add_document(db, customer, "f.xlsx", status="failed")
    add_values(db, customer, failed, row(2, "Sharma Traders", 10, date(2026, 8, 3)))
    fixed = PgFixedQueries(db)
    assert fixed.top_group(customer, "profit", PARTIES, DATES, *AUGUST, True) == []
    assert fixed.figures(customer, "profit", (), None, None, invoices=False) == []
    assert fixed.count_documents(customer, None, None, date_fields=DATES) == []


def summed(figures: list[Figure]) -> tuple[Decimal, int]:
    """What the answer would add up (kaagaz/answering/totals.py): the figures' sum, and how many."""
    return sum((f.amount for f in figures if f.amount is not None), Decimal(0)), len(figures)


def test_total_inside_a_month_and_overall(db: Database, register: Any) -> None:
    # Changed on purpose (PRD 6 C, 24 Oct 2026): the query lists each matching record's figure,
    # and code adds them up; each cites its figure (it was one sum citing figures and dates).
    customer, _, values = register
    fixed = PgFixedQueries(db)
    august = fixed.figures(customer, "profit", DATES, *AUGUST, invoices=False)
    assert summed(august) == (Decimal(200000), 3)
    assert all(f.verified and f.dated and f.date_verified and f.printed == 1 for f in august)
    august_keys = {"sheet:1:row:2", "sheet:1:row:3", "sheet:1:row:4"}
    cited = {f.value.value_id for f in august if f.value is not None}
    assert cited == {v["id"] for v in values if v["key"] in august_keys and v["field"] == "profit"}
    assert [f.amount for f in august] == [Decimal(70000), Decimal(80000), Decimal(50000)]  # by date
    overall = fixed.figures(customer, "profit", (), None, None, invoices=False)
    assert summed(overall) == (Decimal(320000), 5)
    assert {f.value.field for f in overall if f.value is not None} == {"profit"}
    assert fixed.figures(customer, "profit", DATES, date(2025, 1, 1), date(2026, 1, 1), invoices=False) == []


def test_extreme_value(db: Database, register: Any) -> None:
    customer, _, _ = register
    fixed = PgFixedQueries(db)
    highest = fixed.extreme_value(customer, "profit", DATES, *AUGUST, True)
    assert [r.number for r in highest] == [Decimal(80000), Decimal(70000), Decimal(50000)]
    assert {ref.field for ref in highest[0].values} == {"profit", "date"}
    lowest = fixed.extreme_value(customer, "profit", (), None, None, False)
    assert lowest[0].number == Decimal(30000)


def test_count_documents_counts_each_document_once(db: Database) -> None:
    customer, _ = new_customer(db)
    sheet = add_document(db, customer, "r.xlsx")
    invoice = add_document(db, customer, "bill.pdf", detected_type="pdf")
    old_invoice = add_document(db, customer, "old.pdf", detected_type="pdf")
    add_values(db, customer, sheet, [*row(2, "A", 1, date(2026, 8, 3)), *row(3, "B", 2, date(2026, 8, 9))])
    invoice_date = cell("document", "invoice_date", "12/08/2026", day=date(2026, 8, 12), label="needs_review")
    add_values(db, customer, invoice, [invoice_date])
    add_values(
        db, customer, old_invoice, [cell("document", "invoice_date", "1/1/2025", day=date(2025, 1, 1))]
    )

    [count] = PgFixedQueries(db).count_documents(customer, *AUGUST, date_fields=DATES)
    assert (count.number, count.records, count.verified) == (Decimal(2), 2, False)
    assert {ref.document_id for ref in count.values} == {sheet, invoice}
    assert len(count.values) == 2
    [every] = PgFixedQueries(db).count_documents(customer, None, None, date_fields=DATES)
    assert every.number == Decimal(3)


def test_citations_are_capped(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer, "r.xlsx")
    add_values(
        db, customer, document, [v for r in range(2, 32) for v in row(r, "Sharma", r, date(2026, 8, 3))]
    )
    [top] = PgFixedQueries(db).top_group(customer, "profit", PARTIES, DATES, *AUGUST, True)
    assert top.records == 30
    assert len(top.values) == MAX_CITED_VALUES


def test_a_field_name_is_only_ever_a_value(db: Database, register: Any) -> None:
    customer, _, _ = register
    fixed = PgFixedQueries(db)
    hostile = "profit' OR '1'='1"
    assert fixed.figures(customer, hostile, (), None, None, invoices=False) == []
    assert fixed.top_group(customer, "profit", [hostile], (), None, None, True) == []


def test_start_and_end_go_together(db: Database) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(ValueError, match="together"):
        PgFixedQueries(db).figures(customer, "profit", DATES, date(2026, 8, 1), None, invoices=False)
    with pytest.raises(ValueError, match="date fields"):
        PgFixedQueries(db).figures(customer, "profit", (), *AUGUST, invoices=False)


# --- a labelled invoice's values count once per document ------------------------------

# A two-page invoice whose second page repeats the seller, the date and the total on
# one line, as continuation pages do. values_from_lines keys page 1's values
# "document" and page 2's repeats "page:2:line:1".
INVOICE_LINES = [
    (1, "Tax Invoice"),
    (1, "Seller: Sharma Traders"),
    (1, "Invoice No: INV-17"),
    (1, "Invoice Date: 12/08/2026"),
    (1, "Grand Total: Rs. 1,180.00"),
    (2, "Seller: Sharma Traders   Invoice Date: 12/08/2026   Grand Total: Rs. 1,180.00"),
]
# A July invoice whose page 2 prints another "Date:" (by the signature, say): that
# date is a "page:2:line:1" record, never the invoice's own date.
LATER_DATE_LINES = [
    (1, "Seller: Sharma Traders"),
    (1, "Invoice Date: 12/07/2026"),
    (1, "Grand Total: Rs. 1,180.00"),
    (2, "Date: 03/08/2026"),
]
REGISTER = [
    ("Party Name", "Total Amount", "Date"),
    ("Sharma Traders", "1,000", "03/08/2026"),
    ("Mehta Textiles", "2,500", "10/08/2026"),
    ("Sharma Traders", "4,000", "20/08/2026"),
]


def as_stored(values: list[ExtractedValue]) -> list[dict[str, Any]]:
    """The rows the pipeline saves: number and date parsed from the text
    (kaagaz/pipeline.py _with_parsed), the region from the location."""
    return [
        {
            "id": str(uuid.uuid4()),
            "key": value.record_key,
            "field": value.field,
            "text": value.value_text,
            "number": parse_amount(value.value_text),
            "day": parse_date(value.value_text),
            "page": value.location.page,
            "region": json.dumps(location_to_json(value.location)),
            "label": "verified",
            "reason": "direct_cell" if isinstance(value.location, CellLocation) else "readers_agree",
        }
        for value in values
    ]


def add_invoice(
    db: Database, customer: str, lines: list[tuple[int, str]] = INVOICE_LINES
) -> list[dict[str, Any]]:
    document = add_document(db, customer, "bill-17.pdf", detected_type="pdf")
    spans = [
        Span(text=line, raw=line, location=BoxLocation(page, 0.1, 0.1 * n, 0.9, 0.1 * n + 0.05))
        for n, (page, line) in enumerate(lines, start=1)
    ]
    values = as_stored(values_from_lines(spans))
    assert {v["key"] for v in values} == {"document", "page:2:line:1"}
    add_values(db, customer, document, values)
    return values


def add_register(db: Database, customer: str) -> None:
    document = add_document(db, customer, "sales-register.xlsx")
    spans = [
        Span(text=cell_text, raw=cell_text, location=CellLocation(1, r, c))
        for r, cells in enumerate(REGISTER, start=1)
        for c, cell_text in enumerate(cells, start=1)
    ]
    add_values(db, customer, document, as_stored(values_from_cells(spans)))


def test_an_invoice_total_printed_on_two_pages_is_counted_once(db: Database) -> None:
    customer, _ = new_customer(db)
    values = add_invoice(db, customer)
    fixed = PgFixedQueries(db)

    # Changed on purpose (PRD 6 C, 24 Oct 2026): one record, citing its figure (it was a sum
    # citing the figure and the date).
    [august] = fixed.figures(customer, "total_amount", DATES, *AUGUST, invoices=False)
    assert (august.amount, august.printed, august.dated) == (Decimal(1180), 1, True)
    first_page = {v["id"] for v in values if v["key"] == "document" and v["field"] == "total_amount"}
    assert august.value is not None and {august.value.value_id} == first_page
    overall = fixed.figures(customer, "total_amount", (), None, None, invoices=False)
    assert summed(overall) == (Decimal(1180), 1)


def test_top_group_counts_an_invoice_once(db: Database) -> None:
    customer, _ = new_customer(db)
    add_invoice(db, customer)
    fixed = PgFixedQueries(db)

    august = fixed.top_group(customer, "total_amount", PARTIES, DATES, *AUGUST, True)
    assert [(r.group, r.number, r.records) for r in august] == [("Sharma Traders", Decimal(1180), 1)]
    overall = fixed.top_group(customer, "total_amount", PARTIES, (), None, None, True)
    assert [(r.group, r.number, r.records) for r in overall] == [("Sharma Traders", Decimal(1180), 1)]


def test_extreme_value_lists_an_invoice_once(db: Database) -> None:
    customer, _ = new_customer(db)
    add_invoice(db, customer)
    rows = PgFixedQueries(db).extreme_value(customer, "total_amount", DATES, *AUGUST, True)
    assert [(r.number, r.records) for r in rows] == [(Decimal(1180), 1)]


def test_count_documents_takes_an_invoices_date_from_its_document_record(db: Database) -> None:
    customer, _ = new_customer(db)
    add_invoice(db, customer, LATER_DATE_LINES)
    fixed = PgFixedQueries(db)

    # The same answer as the total: the invoice is not in August at all.
    assert fixed.count_documents(customer, *AUGUST, date_fields=DATES) == []
    assert fixed.figures(customer, "total_amount", DATES, *AUGUST, invoices=False) == []
    [july] = fixed.count_documents(customer, date(2026, 7, 1), date(2026, 8, 1), date_fields=DATES)
    assert (july.number, july.records) == (Decimal(1), 1)
    assert [ref.value_text for ref in july.values] == ["12/07/2026"]


def test_a_register_column_named_total_amount_still_sums_every_row(db: Database) -> None:
    customer, _ = new_customer(db)
    add_invoice(db, customer)
    add_register(db, customer)
    fixed = PgFixedQueries(db)

    august = fixed.figures(customer, "total_amount", DATES, *AUGUST, invoices=False)
    # 1,000 + 2,500 + 4,000 from the register, and the invoice's 1,180 once
    assert summed(august) == (Decimal(8680), 4)
    rows = fixed.top_group(customer, "total_amount", PARTIES, DATES, *AUGUST, True)
    assert [(r.group, r.number, r.records) for r in rows] == [
        ("Sharma Traders", Decimal(6180), 3),
        ("Mehta Textiles", Decimal(2500), 1),
    ]


# A spreadsheet the card typed as an invoice: its rows are sheet records, and the invoice
# stage (PRD 2 E4) stores the invoice's own total and date in its document record.
SHEET_INVOICE = [
    ("Description", "Total Amount", "Invoice Date"),
    ("Cotton cloth", "600.00", "12/08/2026"),
    ("Silk", "400.00", "12/08/2026"),
    ("Grand Total", "1,000.00", ""),
]


def test_a_spreadsheet_invoices_total_is_counted_once_never_beside_its_cells(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer, "bill-9.xlsx")
    spans = [
        Span(text=cell_text, raw=cell_text, location=CellLocation(1, r, c))
        for r, cells in enumerate(SHEET_INVOICE, start=1)
        for c, cell_text in enumerate(cells, start=1)
        if cell_text
    ]
    e4 = [
        ExtractedValue("document", "party_name", "Sharma Traders", "Sharma Traders", CellLocation(1, 6, 1)),
        ExtractedValue("document", "total_amount", "1,000.00", "1,000.00", CellLocation(1, 4, 2)),
        ExtractedValue("document", "invoice_date", "12/08/2026", "12/08/2026", CellLocation(1, 2, 3)),
    ]
    cells = values_from_cells(spans)
    assert {v.record_key for v in cells if v.field == "total_amount"} == {
        "sheet:1:row:2",
        "sheet:1:row:3",
        "sheet:1:row:4",
    }
    add_values(db, customer, document, as_stored([*cells, *e4]))
    add_register(db, customer)  # a register's rows, with no document record, all still count
    fixed = PgFixedQueries(db)

    august = fixed.figures(customer, "total_amount", DATES, *AUGUST, invoices=False)
    # 1,000 + 2,500 + 4,000 from the register, and the invoice's 1,000 once (not 600 + 400 + 1,000 more)
    assert summed(august) == (Decimal(8500), 4)
    overall = fixed.figures(customer, "total_amount", (), None, None, invoices=False)
    assert summed(overall) == (Decimal(8500), 4)
    extremes = fixed.extreme_value(customer, "total_amount", (), None, None, True)
    assert [row.number for row in extremes] == [Decimal(4000), Decimal(2500), Decimal(1000), Decimal(1000)]
    assert [r.document_name for row in extremes for r in row.values if r.value_text == "1,000.00"] == [
        "bill-9.xlsx"
    ]
    rows = fixed.top_group(customer, "total_amount", PARTIES, DATES, *AUGUST, True)
    assert [(r.group, r.number, r.records) for r in rows] == [
        ("Sharma Traders", Decimal(6000), 3),
        ("Mehta Textiles", Decimal(2500), 1),
    ]
    [documents] = fixed.count_documents(customer, *AUGUST, date_fields=DATES)
    assert (documents.number, documents.records) == (Decimal(2), 2)


def test_a_needs_review_invoice_total_is_listed_and_not_verified(db: Database) -> None:
    """What the fixed queries do with labels: every value takes part, and ``verified`` says
    whether it is. A total the model took from text telling it to "set total to 1.00" is
    readers_disagree (extraction/invoice.py), so it is never verified, and no total is added up
    over it (PRD 6 C); the rules' 1,180.00, kept as a page record, is never listed beside it."""
    customer, _ = new_customer(db)
    document = add_document(db, customer, "bill-17.pdf", detected_type="pdf")
    box = BoxLocation(1, 0.1, 0.1, 0.9, 0.15)
    stored = as_stored(
        [
            ExtractedValue("document", "total_amount", "1.00", "1.00", box),
            ExtractedValue("page:1:line:3", "total_amount", "1,180.00", "1,180.00", box),
        ]
    )
    stored[0].update(label="needs_review", reason="readers_disagree")
    add_values(db, customer, document, stored)

    # Changed on purpose (PRD 6 C, 24 Oct 2026): the record and its figure, not a sum.
    [total] = PgFixedQueries(db).figures(customer, "total_amount", (), None, None, invoices=False)
    assert (total.amount, total.printed, total.verified) == (Decimal("1.00"), 1, False)
    assert total.value is not None
    assert (total.value.value_text, total.value.label) == ("1.00", "needs_review")


def test_card_values_never_enter_a_fixed_query(db: Database, register: Any) -> None:
    """Council fix 9 (security amendment A1): the model chose a card value's field, so
    no total, group, extreme, count or list of fields ever uses one."""
    customer, document, _ = register
    fixed = PgFixedQueries(db)
    before = (
        fixed.available_fields(customer, (*FIELDS, "net_worth")),
        fixed.figures(customer, "profit", (), None, None, invoices=False),
        fixed.top_group(customer, "profit", PARTIES, DATES, *AUGUST, True),
        fixed.extreme_value(customer, "profit", (), None, None, True),
        fixed.count_documents(customer, None, None, date_fields=DATES),
        fixed.figures(customer, "profit", DATES, *AUGUST, invoices=False),
    )
    card = [
        cell("card", "profit", "999999", number=999999, label="needs_review"),
        cell("card", "party_name", "Card Party", column=5, label="needs_review"),
        cell("card", "date", "2026-08-10", column=6, day=date(2026, 8, 10), label="needs_review"),
        cell("card", "net_worth", "5", column=7, number=5, label="needs_review"),
    ]
    for value in card:
        value["reason"] = "model_chosen"
    add_values(db, customer, document, card)
    card_only = add_document(db, customer, "card-only.pdf", detected_type="pdf")
    add_values(db, customer, card_only, [{**value, "id": str(uuid.uuid4())} for value in card])

    after = (
        fixed.available_fields(customer, (*FIELDS, "net_worth")),
        fixed.figures(customer, "profit", (), None, None, invoices=False),
        fixed.top_group(customer, "profit", PARTIES, DATES, *AUGUST, True),
        fixed.extreme_value(customer, "profit", (), None, None, True),
        fixed.count_documents(customer, None, None, date_fields=DATES),
        fixed.figures(customer, "profit", DATES, *AUGUST, invoices=False),
    )
    assert after == before
    assert "net_worth" not in after[0]


# --- invoice lines are never used by a fixed query (PRD 2 plan conflict 1) ----------------------------


def test_invoice_line_amounts_are_never_used_and_total_amount_still_plans(db: Database) -> None:
    """Plan conflict 1: a line's amount would make intent's "amount" (total_amount or amount)
    ambiguous for every customer with invoices, so line records stay out, like page records."""
    customer, _ = new_customer(db)
    document = add_document(db, customer, "bill-17.pdf", detected_type="pdf")
    spans = [
        Span(text=line, raw=line, location=BoxLocation(page, 0.1, 0.1 * n, 0.9, 0.1 * n + 0.05))
        for n, (page, line) in enumerate(INVOICE_LINES, start=1)
    ]
    box = BoxLocation(1, 0.1, 0.6, 0.9, 0.62)
    lines = [
        ExtractedValue("line:1", "description", "Cotton cloth", "Cotton cloth", box),
        ExtractedValue("line:1", "amount", "1,000.00", "1,000.00", box),
        ExtractedValue("line:2", "amount", "180.00", "180.00", box),
    ]
    add_values(db, customer, document, as_stored([*values_from_lines(spans), *lines]))
    fixed = PgFixedQueries(db)

    available = fixed.available_fields(customer, (*FIELDS, "invoice_number", "description"))
    assert available == {"party_name", "invoice_number", "invoice_date", "total_amount"}
    assert fixed.figures(customer, "amount", (), None, None, invoices=False) == []
    assert fixed.extreme_value(customer, "amount", (), None, None, True) == []
    august = fixed.figures(customer, "total_amount", DATES, *AUGUST, invoices=False)
    assert summed(august) == (Decimal(1180), 1)
    normalised = normalise("Total amount in August 2026?")
    plan = plan_fixed(
        normalised,
        date_range(normalised, date(2026, 9, 30)),
        mentions_date(normalised),
        lambda asked: fixed.available_fields(customer, asked),
    )
    assert plan == FixedPlan("total", "total_amount", (), ("invoice_date",), *AUGUST)


# --- the list of fields a question can name (A7a) ------------------------------------------------

# The fields query as it was before A7a: every field the customer has, read in full each time.
_ALL_FIELDS_BEFORE = text(
    "SELECT DISTINCT v.field FROM extracted_values v"
    " JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id"
    " WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND v.record_key <> 'card' AND v.record_key NOT LIKE 'line:%'"
)
_ASKED = ["profit", "net_profit", "party_name", "date", "invoice_date", "total_amount", "amount", "net_worth"]


def test_the_fields_query_finds_what_the_full_list_found_among_the_fields_asked(db: Database) -> None:
    """A7a: only the fields asked are looked for, one EXISTS each, and the answer is the
    old full list cut to them: the same card, line, trash, version and status rules."""
    customer, admin_user = new_customer(db)
    other, _ = new_customer(db)
    add_register(db, customer)
    add_invoice(db, customer)
    with_cards = add_document(db, customer, "card.pdf", detected_type="pdf")
    add_values(db, customer, with_cards, [cell("card", "net_worth", "5", number=5)])
    add_values(db, customer, with_cards, [cell("line:1", "amount", "5", number=5)])
    add_values(db, customer, add_document(db, customer, "w.xlsx", status="clean"), [cell("r", "profit", "1")])
    with db.customer(customer) as conn:
        binned = insert_document(conn, customer, detected_type="zip", deleted_at=NOW, deleted_by=admin_user)
        old = insert_document(conn, customer, detected_type="zip", is_current=False)
    add_values(db, customer, binned, [cell("r", "net_profit", "1")])
    add_values(db, customer, old, [cell("r", "net_worth", "1")])
    add_values(db, other, add_document(db, other, "o.xlsx"), [cell("r", "amount", "1")])

    found = {}
    for who in (customer, other):
        with db.customer(who) as conn:
            before = {str(row.field) for row in conn.execute(_ALL_FIELDS_BEFORE, {"customer_id": who})}
            params = {"customer_id": who, "candidates": _ASKED, **EVERYTHING.params()}
            found[who] = {str(row.field) for row in conn.execute(fixed_queries._AVAILABLE_FIELDS, params)}
        assert found[who] == before & set(_ASKED)
    # Left out: a card's net_worth, a line's amount, a waiting, a binned and an older document's.
    assert found[customer] == {"party_name", "date", "invoice_date", "total_amount"}
    assert found[other] == {"amount"}


# --- invoice counts: one per row of the invoices table (#228, restored 24 Oct 2026) ---------

_INVOICE_ROW = text(
    "INSERT INTO invoices (customer_id, document_id, invoice_number, invoice_date, label, check_status,"
    " check_reasons) VALUES (:c, :d, :number, :day, :label, :status, CAST(:reasons AS text[]))"
)


def add_invoice_row(
    db: Database,
    customer: str,
    day: date | None,
    *,
    label: str = "verified",
    check_status: str = "passed",
    document_status: str = "ready",
    extra: list[dict[str, Any]] | None = None,
) -> str:
    """A ready invoice document, its invoices row and its invoice number value (with a box, so it
    can be cited), and the ``extra`` values of its document record. Returns the value's id."""
    number = f"INV-{uuid.uuid4().hex[:6]}"
    document = add_document(db, customer, f"{number}.pdf", status=document_status, detected_type="pdf")
    value_id = str(uuid.uuid4())
    value = {
        "id": value_id,
        "key": "document",
        "field": "invoice_number",
        "text": number,
        "number": None,
        "day": None,
        "page": 1,
        "region": json.dumps({"kind": "box", "page": 1, "box": [0.1, 0.1, 0.5, 0.15]}),
        "label": label,
        "reason": "readers_agree" if label == "verified" else "single_reader",
    }
    add_values(db, customer, document, [value, *(extra or [])])
    reasons = "{total_mismatch}" if check_status == "failed" else "{}"
    with db.customer(customer) as conn:
        conn.execute(
            _INVOICE_ROW,
            {
                "c": customer,
                "d": document,
                "number": number,
                "day": day,
                "label": label,
                "status": check_status,
                "reasons": reasons,
            },
        )
    return value_id


def test_count_invoices_counts_each_invoice_inside_the_month(db: Database) -> None:
    customer, _ = new_customer(db)
    in_august = {
        add_invoice_row(db, customer, date(2026, 8, 5)),
        add_invoice_row(db, customer, date(2026, 8, 31)),
    }
    add_invoice_row(db, customer, date(2026, 7, 31))
    add_invoice_row(db, customer, None)  # no date read: counted overall, never inside a month
    fixed = PgFixedQueries(db)

    [august] = fixed.count_invoices(customer, *AUGUST)
    assert (august.number, august.records, august.verified) == (Decimal(2), 2, True)
    assert {ref.value_id for ref in august.values} == in_august
    [overall] = fixed.count_invoices(customer, None, None)
    assert (overall.number, overall.records) == (Decimal(4), 4)
    assert fixed.count_invoices(customer, date(2026, 6, 1), date(2026, 7, 1)) == []


def test_one_invoice_not_verified_or_not_passing_its_checks_makes_the_count_unverified(db: Database) -> None:
    for label, check_status in [
        ("needs_review", "passed"),
        ("verified", "failed"),
        ("verified", "not_checked"),
    ]:
        customer, _ = new_customer(db)
        add_invoice_row(db, customer, date(2026, 8, 5))
        add_invoice_row(db, customer, date(2026, 8, 6), label=label, check_status=check_status)
        [august] = PgFixedQueries(db).count_invoices(customer, *AUGUST)
        assert (august.number, august.verified) == (Decimal(2), False), (label, check_status)


def test_count_invoices_leaves_out_documents_that_are_not_ready(db: Database) -> None:
    customer, _ = new_customer(db)
    add_invoice_row(db, customer, date(2026, 8, 5))
    add_invoice_row(db, customer, date(2026, 8, 6), document_status="received")
    [august] = PgFixedQueries(db).count_invoices(customer, *AUGUST)
    assert august.number == Decimal(1)


def test_count_invoices_needs_start_and_end_together(db: Database) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(ValueError, match="start and end go together"):
        PgFixedQueries(db).count_invoices(customer, date(2026, 8, 1), None)


# --- the numbers rule over invoices (PRD 6 C) --------------------------------------------------------

SEPTEMBER = (date(2026, 9, 1), date(2026, 10, 1))
TOTAL_PROFIT = "Total profit of invoices in September 2026?"
IN_SEPTEMBER = "so I haven't added up the profit in September 2026."


def september_invoice(db: Database, customer: str, day: int | None, profit: int | None, **row: Any) -> str:
    """An invoice dated ``day`` September 2026 (None: no date read), printing ``profit`` (None:
    none) in its document record. Returns its invoice number value's id."""
    extra = []
    if day is not None:
        extra.append(cell("document", "invoice_date", f"{day}/09/2026", column=2, day=date(2026, 9, day)))
    if profit is not None:
        extra.append(cell("document", "profit", indian_number(profit), column=3, number=profit))
    return add_invoice_row(db, customer, None if day is None else date(2026, 9, day), extra=extra, **row)


def ask_total(db: Database, customer: str, question: str = TOTAL_PROFIT) -> Answer:
    service = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)
    return service.answer(customer, question)


def test_fourteen_september_invoices_three_printing_a_profit_give_no_total(db: Database) -> None:
    """PRD 6 Example 3: every September invoice is listed, the 3 figures as printed, and no total."""
    customer, _ = new_customer(db)
    for day in range(1, 12):
        september_invoice(db, customer, day, None)
    for day, profit in [(26, 31000), (12, 18400), (19, 9250)]:
        september_invoice(db, customer, day, profit)
    add_invoice_row(db, customer, date(2026, 8, 30), extra=[cell("document", "profit", "5,000", number=5000)])
    register = add_document(db, customer, "profit-register.xlsx")  # not an invoice: never matched here
    add_values(db, customer, register, row(2, "Sharma Traders", 70000, date(2026, 9, 3)))

    answer = ask_total(db, customer)

    assert (answer.status, answer.route, answer.label) == ("answered", "fixed_query", "needs_review")
    lines = [s.text for s in answer.sentences]
    assert lines[0] == f"11 of the 14 invoices have no profit I can add, {IN_SEPTEMBER}"
    assert [line.split(": ", 1)[1] for line in lines[1:4]] == ["18,400.", "9,250.", "31,000."]  # by date
    assert all(line.endswith(": no profit printed.") for line in lines[4:]) and len(lines) == 15
    assert [c.snippet for c in answer.citations[:3]] == ["profit: 18,400", "profit: 9,250", "profit: 31,000"]
    assert all(c.snippet.startswith("invoice_number: INV-") for c in answer.citations[3:])
    assert "profit-register.xlsx" not in str(answer.to_json()) and "5,000" not in str(answer.to_json())
    # PRD 6 C2: the example's own words reach the same rule, and the same answer.
    example = ask_total(db, customer, EXAMPLE_3)
    assert [s.text for s in example.sentences] == lines and example.route == "fixed_query"


def test_september_invoices_all_printing_a_verified_profit_give_the_total(db: Database) -> None:
    customer, _ = new_customer(db)
    for day, profit in [(26, 31000), (12, 18400), (19, 9250)]:
        september_invoice(db, customer, day, profit)

    answer = ask_total(db, customer)

    assert [s.text.split(": ", 1)[-1] for s in answer.sentences] == [
        "The total profit in September 2026 was 58,650, from 3 invoices.",
        "18,400.",
        "9,250.",
        "31,000.",
    ]
    assert answer.label == "verified"
    figures = PgFixedQueries(db).figures(customer, "profit", ("invoice_date",), *SEPTEMBER, invoices=True)
    assert summed(figures) == (Decimal(58650), 3)
    assert all(f.verified and f.dated and f.date_verified for f in figures)
    september_invoice(db, customer, 30, 1000, check_status="failed")  # its figure is never verified
    failed = ask_total(db, customer)
    assert failed.sentences[0].text == f"1 of the 4 profit figures is not verified yet, {IN_SEPTEMBER}"
    assert failed.sentences[4].text.endswith(": 1,000, not verified yet.")


def test_an_undated_invoice_in_a_dated_question_withholds_the_total(db: Database) -> None:
    customer, _ = new_customer(db)
    september_invoice(db, customer, 12, 18400)
    september_invoice(db, customer, 19, 9250)
    september_invoice(db, customer, None, 5000)

    answer = ask_total(db, customer)

    assert answer.sentences[0].text == f"1 of the 3 invoices has no date I can read, {IN_SEPTEMBER}"
    assert answer.sentences[3].text.endswith(": 5,000, no date I can read.")
    assert "32,650" not in str(answer.to_json())


def test_a_register_row_without_the_figure_is_a_record_that_prints_none(db: Database) -> None:
    """A row is a record: a register row that leaves the profit empty matches, with no figure."""
    customer, _ = new_customer(db)
    document = add_document(db, customer, "profit-register.xlsx")
    blank = [v for v in row(3, "Mehta Textiles", None, date(2026, 8, 15)) if v["field"] != "profit"]
    add_values(db, customer, document, [*row(2, "Sharma Traders", 70000, date(2026, 8, 1)), *blank])
    other = add_document(db, customer, "staff.xlsx")  # holds no profit: none of its rows match
    add_values(db, customer, other, [cell("sheet:1:row:2", "date", "2026-08-02", day=date(2026, 8, 2))])

    figures = PgFixedQueries(db).figures(customer, "profit", DATES, *AUGUST, invoices=False)

    assert [(f.amount, f.printed) for f in figures] == [(Decimal(70000), 1), (None, 0)]
    assert figures[1].value is not None and figures[1].value.document_id == document


# --- PRD 6 C2: a sheet's printed total, a party's sum, and what a highest rests on ----------------


def add_ledger(db: Database, customer: str, label: str = "Total") -> None:
    """Rows of 1,000 and 2,500, then a row ``label`` printing 3,500, read as values_from_cells reads it."""
    sheet = [*REGISTER[:3], (label, "3,500", "")]
    spans = [
        Span(text=text_, raw=text_, location=CellLocation(1, r, c))
        for r, cells in enumerate(sheet, start=1)
        for c, text_ in enumerate(cells, start=1)
        if text_
    ]
    add_values(db, customer, add_document(db, customer, "ledger.xlsx"), as_stored(values_from_cells(spans)))


@pytest.mark.parametrize(
    ("label", "printed"),
    [("Total", True), ("GRAND TOTAL", True), ("Sub-Total:", True), ("Net Total", True),
     ("एकूण", True), ("कुल", True), ("एकुण", True), ("Total Traders", False)],
)  # fmt: skip
def test_a_sheets_printed_total_row_is_never_a_record(db: Database, label: str, printed: bool) -> None:
    customer, _ = new_customer(db)
    add_ledger(db, customer, label)
    fixed = PgFixedQueries(db)
    figures = fixed.figures(customer, "total_amount", (), None, None, invoices=False)
    assert [(f.amount, f.printed_total) for f in figures] == [
        (Decimal(1000), False),
        (Decimal(2500), False),
        (Decimal(3500), printed),
    ]
    highest = fixed.extreme_value(customer, "total_amount", (), None, None, True)
    ranked = fixed.top_group(customer, "total_amount", PARTIES, (), None, None, True)
    if printed:
        assert [row.number for row in highest] == [2500, 1000]
        assert [(row.group, row.number) for row in ranked] == [
            ("Mehta Textiles", Decimal(2500)),
            ("Sharma Traders", Decimal(1000)),
        ]
    else:  # a party whose name starts with "Total" is a record like any other
        assert [row.number for row in highest] == [3500, 2500, 1000]


def test_a_ledger_with_a_total_row_never_gives_its_double(db: Database) -> None:
    """On a real database, rows of 1,000 and 2,500 and a Total row of 3,500 were summed to 7,000."""
    customer, _ = new_customer(db)
    add_ledger(db, customer)
    service = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)
    asked = ["Total amount?", "Total amount in August 2026?", "Who had the highest amount?"]
    answers = [service.answer(customer, q) for q in [*asked, "What was the highest amount?"]]

    lines = ["ledger.xlsx, its printed total: 3,500.", "ledger.xlsx: 1,000.", "ledger.xlsx: 2,500."]
    assert [s.text for s in answers[0].sentences] == [PRINTED_TOTAL, *lines]
    assert [s.text for s in answers[1].sentences] == [PRINTED_TOTAL, *lines]
    assert answers[0].label == "needs_review" and answers[0].citations[0].snippet == "total_amount: 3,500"
    assert answers[2].sentences[0].text == "Mehta Textiles had the highest total amount, 2,500."
    assert answers[3].sentences[0].text == "The highest total amount was 2,500."
    for answer in answers:
        said = json.dumps(answer.to_json())
        assert answer.route == "fixed_query" and "7,000" not in said and "7000" not in said


def test_a_partys_sum_needs_every_record_to_print_a_verified_figure(db: Database, register: Any) -> None:
    customer, document, _ = register
    service = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)
    question = "Kiska profit sabse jyada tha last month?"
    shown = service.answer(customer, question)
    assert [s.text for s in shown.sentences] == [
        "Sharma Traders had the highest profit, 1,20,000, in August 2026."
    ]
    assert shown.label == "verified"

    blank = [v for v in row(7, "Gupta Stores", None, date(2026, 8, 20)) if v["field"] != "profit"]
    add_values(db, customer, document, blank)
    withheld = service.answer(customer, question)

    assert withheld.sentences[0].text == (
        "1 of the 4 records has no profit I can add, so I haven't compared the parties' profit"
        " in August 2026."
    )
    assert [s.text for s in withheld.sentences[1:]] == [
        "profit-register.xlsx: 70000.",
        "profit-register.xlsx: 80000.",
        "profit-register.xlsx: 50000.",
        "profit-register.xlsx: no profit printed.",
    ]
    assert withheld.label == "needs_review" and "1,20,000" not in json.dumps(withheld.to_json())


def test_a_highest_or_a_ranking_is_verified_only_when_every_value_compared_is(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer, "profit-register.xlsx")
    rows = [
        *row(2, "Sharma Traders", 70000, date(2026, 8, 1)),
        *row(3, "Mehta Textiles", 80000, date(2026, 8, 15)),
        *row(
            4,
            "Gupta Stores",
            50000,
            date(2026, 8, 31),
            profit_label="needs_review",
            party_label="needs_review",
        ),
    ]
    add_values(db, customer, document, rows)
    fixed = PgFixedQueries(db)
    unsure = {v["id"] for v in rows if v["label"] != "verified"}

    [highest, *_] = fixed.extreme_value(customer, "profit", DATES, *AUGUST, True)
    assert (highest.number, highest.verified) == (Decimal(80000), True)
    assert {ref.value_id for ref in highest.unsure} == ids([v for v in rows if v["id"] in unsure], "profit")
    [top, *_] = fixed.top_group(customer, "profit", PARTIES, DATES, *AUGUST, True)
    assert (top.group, top.verified, {ref.value_id for ref in top.unsure}) == ("Mehta Textiles", True, unsure)

    service = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)
    answer = service.answer(customer, "What was the highest profit in August 2026?")
    assert [s.text for s in answer.sentences] == [
        "The highest profit was 80,000, in August 2026.",
        "Some of the values behind this are not verified yet.",
    ]
    assert answer.label == "needs_review"
    assert [answer.citations[n - 1].snippet for n in answer.sentences[1].citations] == ["profit: 50000"]
