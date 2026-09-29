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

from kaagaz.answering.fixed_queries import MAX_CITED_VALUES, PgFixedQueries
from kaagaz.db.engine import Database
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
