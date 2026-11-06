"""The amount safety layer (#524) on PostgreSQL: search finds a page's Devanagari digits by
their ASCII number, Ask adds up the numbers read from Indic digits and keeps an amount in review
out of its total (listed, marked "not verified yet", as before), the review queue lists each
amount reason with the text as read. Migration 0077c: tests/test_migration_0077c_db.py; the rules
themselves: tests/test_amount_guard.py."""

import json
import uuid
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering.service import AnswerService
from kaagaz.db import review
from kaagaz.db.engine import Database
from kaagaz.extraction.labelled import values_from_lines
from kaagaz.extraction.labels import label_amounts
from kaagaz.extraction.land import value_number
from kaagaz.extraction.parsing import parse_date
from kaagaz.extraction.values import ExtractedValue
from kaagaz.reading.spans import BoxLocation, Span, location_to_json
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_answering_fixed_queries_db import NOW, add_document, add_values
from tests.test_review_queue_db import _with_pages
from tests.test_search_db import add_document as add_search_document
from tests.test_search_db import add_pieces, run

pytestmark = pytest.mark.db
AMOUNT_REASONS = ("mixed_digits", "amount_commas", "amount_mark", "amount_outlier")
TOTAL = "What is the total amount?"


def scanned(*lines: str) -> list[ExtractedValue]:
    """A scanned bill's values as the pipeline saves them: labelled.py's values of its lines,
    verified as if the checker agreed, their numbers parsed (pipeline._with_parsed), then
    ``label_amounts``."""
    spans = [
        Span(line, line, BoxLocation(1, 0.1, 0.1 * k, 0.9, 0.1 * k + 0.05), origin="ocr")
        for k, line in enumerate(lines, start=1)
    ]
    values = [
        replace(
            v,
            label="verified",
            label_reason="readers_agree",
            value_number=value_number(v.field, v.value_text),
            value_date=parse_date(v.value_text),
        )
        for v in values_from_lines(spans)
    ]
    return label_amounts(values)


def stored(values: list[ExtractedValue]) -> list[dict[str, Any]]:
    """The rows test_answering_fixed_queries_db.add_values inserts, from the values."""
    return [
        {
            "id": str(uuid.uuid4()),
            "key": v.record_key,
            "field": v.field,
            "text": v.value_text,
            "number": v.value_number,
            "day": v.value_date,
            "page": v.location.page,
            "region": json.dumps(location_to_json(v.location)),
            "label": v.label,
            "reason": v.label_reason,
        }
        for v in values
    ]


def add_bill(db: Database, customer: str, name: str, *lines: str) -> None:
    add_values(db, customer, add_document(db, customer, name, detected_type="pdf"), stored(scanned(*lines)))


def test_search_finds_940000_on_a_page_printing_it_in_devanagari_digits(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_search_document(db, customer)
    printed = ["Grand Total ९४००००/-", "Sub Total ९४0000/-", "Rent paid 12,000"]
    devanagari, mixed, other = add_pieces(db, customer, document, [(line, None) for line in printed])

    hits = run(db, customer, "940000")

    # Found by the exact words (its Latin spelling, text_latin), first; "12,000" only by its look.
    by_words = [hit.piece_id for hit in hits if hit.word_score]
    assert sorted(by_words) == sorted([devanagari, mixed])
    assert [hit.piece_id for hit in hits][:2] == by_words and other not in by_words
    with db.customer(customer) as conn:
        shown = conn.execute(
            text("SELECT text FROM pieces WHERE customer_id = :c ORDER BY seq"), {"c": customer}
        ).scalars()
        assert list(shown) == printed  # the text shown is as read


def test_an_ask_total_adds_the_numbers_read_from_indic_digits(db: Database) -> None:
    customer, _ = new_customer(db)
    add_bill(db, customer, "bill-a.pdf", "Invoice No: A-1", "Grand Total: ९४००००/-")
    add_bill(db, customer, "bill-b.pdf", "Invoice No: B-1", "Grand Total: ३४०००० ")
    service = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)

    answer = service.answer(customer, TOTAL)

    assert (answer.route, answer.label) == ("fixed_query", "verified")
    assert "12,80,000" in answer.sentences[0].text  # 940000 + 340000
    assert {s.text.rsplit(": ", 1)[-1] for s in answer.sentences[1:]} == {"९४००००/-.", "३४००००."}


def test_an_amount_in_review_is_listed_and_marked_and_withholds_the_total(db: Database) -> None:
    """Ask's rule for a figure not verified (PRD 6 C, decision 10), kept: the amount is listed
    as read and marked, and no total is added up."""
    customer, _ = new_customer(db)
    add_bill(db, customer, "bill-a.pdf", "Invoice No: A-1", "Grand Total: ९४००००/-")
    add_bill(db, customer, "bill-c.pdf", "Invoice No: C-1", "Grand Total: ९४0000/-")  # amount-2.png
    service = AnswerService(db=db, model=None, settings=make_settings(), clock=lambda: NOW)

    answer = service.answer(customer, TOTAL)

    assert (answer.route, answer.label) == ("fixed_query", "needs_review")
    assert answer.sentences[0].text.startswith("1 of the 2 amount figures is not verified yet")
    assert "18,80,000" not in str(answer.to_json())
    assert any(s.text.endswith(": ९४0000/-, not verified yet.") for s in answer.sentences[1:])


def test_the_review_queue_lists_each_amount_reason_with_the_text_as_read(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
    _with_pages(db, customer, document, 1)
    values = scanned(
        "Grand Total: ९४0000/-",
        "Sub Total: 1,15,0000",
        "Total Amount: 115000001-",
    ) + scanned("Sub Total: 1,000", "Grand Total: 11,500")
    add_values(db, customer, document, stored(values))

    with db.customer(customer) as conn:
        listed = {
            reason: [
                row["value_text"]
                for row in review.list_queue(
                    conn, customer, limit=50, offset=0, document_id=None, reasons=[reason]
                )[0]
            ]
            for reason in AMOUNT_REASONS
        }

    assert listed == {
        "mixed_digits": ["९४0000/-"],
        "amount_commas": ["1,15,0000"],
        "amount_mark": ["115000001-"],
        "amount_outlier": ["11,500"],
    }
