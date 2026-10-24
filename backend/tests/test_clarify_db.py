"""PRD 6 B review fixes against a real database: the vocabulary narrowed to the chat's scope, a
property keyed as #245 keys it, and the statement served by value_keys' B-tree index."""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import clarify
from kaagaz.answering.clarify import Entity, Pending, PgVocabulary, after_reply
from kaagaz.db.engine import Database
from tests.db_support import insert_document, insert_key, new_customer

pytestmark = pytest.mark.db


def test_the_scope_narrows_the_vocabulary_and_a_property_pick_stays_live(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        mine, other = insert_document(conn, customer), insert_document(conn, customer)
        insert_key(conn, customer, mine, "person", "Nikhil Patel")
        insert_key(conn, customer, other, "person", "Nikhil Desai")
        insert_key(conn, customer, mine, "property", "Survey 45/2")
    everywhere, scoped = PgVocabulary(db), PgVocabulary(db).within([mine])
    asked = [Entity("person", "Nikhil")]

    assert sorted(e.label for e in everywhere.candidates(customer, asked)[0]) == [
        "Nikhil Desai",
        "Nikhil Patel",
    ]
    assert [e.label for e in scoped.candidates(customer, asked)[0]] == ["Nikhil Patel"]  # review 4
    pending = Pending("Unowned share of the property?", "property", None, (), (), 1, ("Survey 45/2",))
    assert after_reply(everywhere, customer, pending).question == "Unowned share of the property Survey 45/2"


def test_the_vocabulary_is_aggregated_once_and_read_through_indexes_under_rls(db: Database) -> None:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
        for n in range(200):
            insert_key(conn, customer, document, "person", f"Person {n}")
    with db.engine.begin() as conn:  # the owner: fresh statistics
        conn.execute(text("ANALYZE value_keys"))
    params: dict[str, Any] = {
        "c": customer,
        "n": [0],
        "kinds": ["person"],
        "latins": ["nikhil"],
        "sounds": ["nkl"],
    }
    params |= {"folds": ["nikhil"], "most": 10, "scoped": None}
    with db.customer(customer) as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(conn.execute(text("EXPLAIN " + clarify._CANDIDATES.text), params).scalars())
    assert "CTE vocabulary" in plan and "Seq Scan on value_keys" not in plan, plan  # review 5
    assert any("value_keys_" in line and "Index" in line for line in plan.splitlines()), plan
