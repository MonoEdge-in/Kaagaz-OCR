"""The chat's scope reaches every number (PRD 3 H2) through the real AnswerService on
PostgreSQL: a document outside the scope would change each one, and each case also
asks with no scope (the positive control)."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from kaagaz.search.scope import EVERYTHING, Scope, ScopeBody, resolve
from tests.db_support import insert_folder, new_customer
from tests.settings_factory import make_settings
from tests.test_answering_fixed_queries_db import add_document, add_values, row
from tests.test_answering_unchecked_db import _value as card_value

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 27, 6, 30, tzinfo=UTC)  # last month is August 2026
SETTINGS = make_settings()


def _world(db: Database) -> tuple[str, Scope]:
    """A register in the folder "Suppliers" and one outside it (with an unchecked sales
    card). Returns the customer and the folder's resolved scope."""
    customer, user = new_customer(db)
    inside = add_document(db, customer, "suppliers.xlsx")
    outside = add_document(db, customer, "others.xlsx")
    add_values(db, customer, inside, row(2, "Sharma Traders", 70000, date(2026, 8, 1)))
    add_values(db, customer, inside, row(3, "Mehta Textiles", 80000, date(2026, 8, 15)))
    add_values(db, customer, outside, row(2, "Gupta Stores", 90000, date(2026, 8, 20)))
    card_value(db, customer, outside, field="sales")
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "Suppliers")
        conn.execute(
            text("UPDATE documents SET folder_id = :f WHERE customer_id = :c AND id = :d"),
            {"f": folder, "c": customer, "d": inside},
        )
        scope = resolve(conn, customer, ScopeBody.model_validate({"folder_id": folder}), timezone="UTC")
    return customer, scope


def _ask(db: Database, customer: str, question: str, scope: Scope) -> dict[str, Any]:
    service = AnswerService(db=db, model=None, settings=SETTINGS, clock=lambda: NOW)
    return service.answer(customer, question, scope=scope).to_json()


@pytest.mark.parametrize(
    ("question", "scoped", "everything"),
    [
        (  # top_group
            "Kiska profit sabse jyada tha last month?",
            "Mehta Textiles had the highest profit, 80,000, in August 2026.",
            "Gupta Stores had the highest profit, 90,000, in August 2026.",
        ),
        (  # total
            "What was the total profit last month?",
            "The total profit in August 2026 was 1,50,000, from 2 records.",
            "The total profit in August 2026 was 2,40,000, from 3 records.",
        ),
        (  # extreme_value
            "What was the highest profit last month?",
            "The highest profit was 80,000, in August 2026.",
            "The highest profit was 90,000, in August 2026.",
        ),
        (  # count_documents
            "How many documents last month?",
            "1 document is dated in August 2026.",
            "2 documents are dated in August 2026.",
        ),
    ],
)
def test_a_fixed_query_counts_only_the_documents_in_scope(
    db: Database, question: str, scoped: str, everything: str
) -> None:
    customer, scope = _world(db)

    inside = _ask(db, customer, question, scope)
    unscoped = _ask(db, customer, question, EVERYTHING)

    assert (inside["route"], inside["sentences"][0]["text"]) == ("fixed_query", scoped)
    assert (unscoped["route"], unscoped["sentences"][0]["text"]) == ("fixed_query", everything)
    assert {c["document_name"] for c in inside["citations"]} == {"suppliers.xlsx"}


def test_an_unchecked_figure_outside_the_scope_is_not_pointed_to(db: Database) -> None:
    customer, scope = _world(db)
    question = "What were the total sales last month?"

    unscoped = _ask(db, customer, question, EVERYTHING)
    inside = _ask(db, customer, question, scope)

    assert unscoped["status"] == "unchecked"
    assert [doc["display_name"] for doc in unscoped["closest_documents"]] == ["others.xlsx"]
    assert inside["status"] == "not_found" and inside["closest_documents"] == []


def test_a_scope_that_matches_nothing_is_not_found_before_any_query(db: Database) -> None:
    """Edge case 1: never the whole customer's answer, and no SQL (the second database is unreachable)."""
    customer, _ = _world(db)
    unreachable = Database(create_engine("postgresql+psycopg://unused@localhost/unused"), "kaagaz_app")
    for database in (db, unreachable):
        service = AnswerService(db=database, model=None, settings=SETTINGS, clock=lambda: NOW)
        answer = service.answer(customer, "What was the total profit last month?", scope=Scope(folder_ids=()))
        assert answer.status == "not_found" and answer.closest_documents == []
    assert _ask(db, customer, "What was the total profit last month?", EVERYTHING)["status"] == "answered"
