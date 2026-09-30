"""The fixed queries against a real PostgreSQL (kaagaz/answering/fixed_queries.py).

Documents and extracted values are inserted directly with SQL, the way the
pipeline store leaves them: one row per value, records keyed by
(document_id, record_key), each value with its page and region.
"""

import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering.dates import date_range, mentions_date
from kaagaz.answering.fixed_queries import MAX_CITED_VALUES, PgFixedQueries
from kaagaz.answering.hinglish import normalise
from kaagaz.answering.intent import FixedPlan, plan_fixed
from kaagaz.db.engine import Database
from kaagaz.extraction.labelled import values_from_lines
from kaagaz.extraction.parsing import parse_amount, parse_date
from kaagaz.extraction.spreadsheet import values_from_cells
from kaagaz.extraction.values import ExtractedValue
from kaagaz.reading.spans import BoxLocation, CellLocation, Span, location_to_json
from tests.db_support import new_customer

pytestmark = pytest.mark.db

AUGUST = (date(2026, 8, 1), date(2026, 9, 1))
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
    assert PgFixedQueries(db).available_fields(customer) == {"profit"}


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
    assert fixed.total(customer, "profit", (), None, None) == []
    assert fixed.count_documents(customer, None, None, date_fields=DATES) == []


def test_total_inside_a_month_and_overall(db: Database, register: Any) -> None:
    customer, _, values = register
    fixed = PgFixedQueries(db)
    [august] = fixed.total(customer, "profit", DATES, *AUGUST)
    assert (august.group, august.number, august.records, august.verified) == (None, Decimal(200000), 3, True)
    august_keys = {"sheet:1:row:2", "sheet:1:row:3", "sheet:1:row:4"}
    cited = {ref.value_id for ref in august.values}
    assert cited == {v["id"] for v in values if v["key"] in august_keys and v["field"] in {"profit", "date"}}
    [overall] = fixed.total(customer, "profit", (), None, None)
    assert (overall.number, overall.records) == (Decimal(320000), 5)
    assert {ref.field for ref in overall.values} == {"profit"}
    assert fixed.total(customer, "profit", DATES, date(2025, 1, 1), date(2026, 1, 1)) == []


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
    assert fixed.total(customer, hostile, (), None, None) == []
    assert fixed.top_group(customer, "profit", [hostile], (), None, None, True) == []


def test_start_and_end_go_together(db: Database) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(ValueError, match="together"):
        PgFixedQueries(db).total(customer, "profit", DATES, date(2026, 8, 1), None)
    with pytest.raises(ValueError, match="date fields"):
        PgFixedQueries(db).total(customer, "profit", (), *AUGUST)


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

    [august] = fixed.total(customer, "total_amount", DATES, *AUGUST)
    assert (august.number, august.records) == (Decimal(1180), 1)
    first_page = {v["id"] for v in values if v["key"] == "document" and v["field"] != "party_name"}
    assert {ref.value_id for ref in august.values} == first_page - ids(values, "invoice_number")
    [overall] = fixed.total(customer, "total_amount", (), None, None)
    assert (overall.number, overall.records) == (Decimal(1180), 1)


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
    assert fixed.total(customer, "total_amount", DATES, *AUGUST) == []
    [july] = fixed.count_documents(customer, date(2026, 7, 1), date(2026, 8, 1), date_fields=DATES)
    assert (july.number, july.records) == (Decimal(1), 1)
    assert [ref.value_text for ref in july.values] == ["12/07/2026"]


def test_a_register_column_named_total_amount_still_sums_every_row(db: Database) -> None:
    customer, _ = new_customer(db)
    add_invoice(db, customer)
    add_register(db, customer)
    fixed = PgFixedQueries(db)

    [august] = fixed.total(customer, "total_amount", DATES, *AUGUST)
    # 1,000 + 2,500 + 4,000 from the register, and the invoice's 1,180 once
    assert (august.number, august.records) == (Decimal(8680), 4)
    rows = fixed.top_group(customer, "total_amount", PARTIES, DATES, *AUGUST, True)
    assert [(r.group, r.number, r.records) for r in rows] == [
        ("Sharma Traders", Decimal(6180), 3),
        ("Mehta Textiles", Decimal(2500), 1),
    ]


def test_card_values_never_enter_a_fixed_query(db: Database, register: Any) -> None:
    """Council fix 9 (security amendment A1): the model chose a card value's field, so
    no total, group, extreme, count or list of fields ever uses one."""
    customer, document, _ = register
    fixed = PgFixedQueries(db)
    before = (
        fixed.available_fields(customer),
        fixed.total(customer, "profit", (), None, None),
        fixed.top_group(customer, "profit", PARTIES, DATES, *AUGUST, True),
        fixed.extreme_value(customer, "profit", (), None, None, True),
        fixed.count_documents(customer, None, None, date_fields=DATES),
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
        fixed.available_fields(customer),
        fixed.total(customer, "profit", (), None, None),
        fixed.top_group(customer, "profit", PARTIES, DATES, *AUGUST, True),
        fixed.extreme_value(customer, "profit", (), None, None, True),
        fixed.count_documents(customer, None, None, date_fields=DATES),
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

    available = fixed.available_fields(customer)
    assert available == {"party_name", "invoice_number", "invoice_date", "total_amount"}
    assert fixed.total(customer, "amount", (), None, None) == []
    assert fixed.extreme_value(customer, "amount", (), None, None, True) == []
    [august] = fixed.total(customer, "total_amount", DATES, *AUGUST)
    assert (august.number, august.records) == (Decimal(1180), 1)
    normalised = normalise("Total amount in August 2026?")
    plan = plan_fixed(
        normalised, date_range(normalised, date(2026, 9, 30)), mentions_date(normalised), available
    )
    assert plan == FixedPlan("total", "total_amount", (), ("invoice_date",), *AUGUST)
