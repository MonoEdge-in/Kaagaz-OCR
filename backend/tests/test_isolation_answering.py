"""Customer B's questions never reach customer A's values or pieces.

Runs the real AnswerService (PgFixedQueries and PgRetriever over
kaagaz.search.hybrid) against PostgreSQL, with no model. Every check that B
sees nothing is paired with A getting the answer, so the test can fail.
"""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering.fixed_queries import PgFixedQueries
from kaagaz.answering.intent import FIELDS
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from tests.db_support import new_customer
from tests.settings_factory import make_settings
from tests.test_answering_neighbours_db import add, near

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 27, 6, 30, tzinfo=UTC)
HINGLISH = "Kiska profit sabse jyada tha last month?"
SEARCH = "What is the grand total of INV-1042?"
INVOICE_TEXT = "Tax Invoice\nSeller: Sharma Traders\nInvoice No: INV-1042\nGrand Total: Rs. 1,20,000"


def _seed(db: Database, customer: str) -> dict[str, str]:
    """A register row and an invoice piece for ``customer``. Returns their ids."""
    sheet, invoice = str(uuid.uuid4()), str(uuid.uuid4())
    piece_id = str(uuid.uuid4())
    value_ids = [str(uuid.uuid4()) for _ in range(3)]
    key = "sheet:1:row:2"
    values = [
        (value_ids[0], "party_name", "Sharma Traders", None, None, 1),
        (value_ids[1], "profit", "120000", 120000, None, 2),
        (value_ids[2], "date", "2026-08-10", None, date(2026, 8, 10), 3),
    ]
    with db.customer(customer) as conn:
        conn.execute(
            text(
                "INSERT INTO documents (id, customer_id, display_name, sha256, size_bytes, detected_type,"
                " status, family_id) VALUES (:id, :c, :name, :sha, 10, :type, 'ready', :id)"
            ),
            [
                {"id": sheet, "c": customer, "name": "register.xlsx", "type": "zip", "sha": _sha(sheet)},
                {"id": invoice, "c": customer, "name": "bill.pdf", "type": "pdf", "sha": _sha(invoice)},
            ],
        )
        conn.execute(
            text(
                "INSERT INTO extracted_values (id, customer_id, document_id, record_key, field, value_text,"
                " value_raw, value_number, value_date, page_no, region, reader, label, label_reason)"
                " VALUES (:id, :c, :d, :key, :field, :text, :text, :number, :day, 1,"
                " CAST(:region AS jsonb), 'test', 'verified', 'direct_cell')"
            ),
            [
                {
                    "id": value_id,
                    "c": customer,
                    "d": sheet,
                    "key": key,
                    "field": field,
                    "text": value_text,
                    "number": number,
                    "day": day,
                    "region": json.dumps({"kind": "cell", "page": 1, "row": 2, "column": column}),
                }
                for value_id, field, value_text, number, day, column in values
            ],
        )
        conn.execute(
            text(
                "INSERT INTO pieces (id, customer_id, document_id, seq, page_from, page_to, text, regions,"
                " label) VALUES (:id, :c, :d, 0, 1, 1, :text, CAST(:regions AS jsonb), 'verified')"
            ),
            {
                "id": piece_id,
                "c": customer,
                "d": invoice,
                "text": INVOICE_TEXT,
                "regions": json.dumps([{"kind": "box", "page": 1, "box": [0.1, 0.1, 0.9, 0.5]}]),
            },
        )
    return {"sheet": sheet, "invoice": invoice, "piece": piece_id, **dict(zip("abc", value_ids, strict=True))}


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _service(db: Database) -> AnswerService:
    return AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)


def _mentions(answer: dict[str, Any], ids: dict[str, str]) -> bool:
    dumped = json.dumps(answer)
    return any(value in dumped for value in ids.values())


def test_b_never_gets_a_fixed_answer_from_as_values(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    a_ids = _seed(db, a)
    answers = _service(db)

    for_a = answers.answer(a, HINGLISH).to_json()
    assert for_a["status"] == "answered" and for_a["route"] == "fixed_query"
    assert {c["ref_id"] for c in for_a["citations"]} == {a_ids["a"], a_ids["b"], a_ids["c"]}

    for_b = answers.answer(b, HINGLISH).to_json()
    assert for_b["status"] == "not_found"
    assert not _mentions(for_b, a_ids)
    assert "Sharma" not in json.dumps(for_b)


def test_b_never_gets_a_search_answer_from_as_pieces(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    a_ids = _seed(db, a)
    answers = _service(db)

    for_a = answers.answer(a, SEARCH).to_json()
    assert for_a["status"] == "answered" and for_a["route"] == "search"
    assert [c["ref_id"] for c in for_a["citations"]] == [a_ids["piece"]]

    for_b = answers.answer(b, SEARCH).to_json()
    assert for_b["status"] == "not_found"
    assert for_b["closest_documents"] == []
    assert not _mentions(for_b, a_ids)


def test_fixed_queries_for_b_find_nothing_even_with_as_field_names(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    _seed(db, a)
    fixed = PgFixedQueries(db)
    august = (date(2026, 8, 1), date(2026, 9, 1))
    assert fixed.available_fields(a, FIELDS) == {"party_name", "profit", "date"}
    assert fixed.available_fields(b, FIELDS) == set()
    assert fixed.top_group(b, "profit", ["party_name"], ["date"], *august, True) == []
    assert fixed.total(b, "profit", [], None, None) == []
    assert fixed.extreme_value(b, "profit", [], None, None, True) == []
    assert fixed.count_documents(b, None, None, date_fields=["date"]) == []


def test_b_never_gets_as_pieces_as_neighbours(db: Database) -> None:
    """Q4: the neighbour fetch filters on the customer too (with RLS), even given A's ids."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    before, the_hit, after = add(db, a, ["before", "the hit", "after"])

    assert near(db, a, [the_hit]) == [before.piece_id, after.piece_id]
    assert near(db, b, [the_hit]) == []
