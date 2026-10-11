"""Customer B never reads, writes, changes or deletes customer A's document sections or
topics (PRD 5 CX2, CX3, migration 0021; real PostgreSQL).

B uses A's exact document id. The ``customer_only`` policy hides A's rows from B
whatever B's statement says, refuses a row B writes in A's name, and the
(customer_id, document_id) foreign key refuses a row of B's under A's document.
Marked ``isolation`` by its file name.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from tests.db_support import count_rows, insert_document, new_customer, violation
from tests.test_pipeline_store_db import content

pytestmark = pytest.mark.db

SECTION = (
    "INSERT INTO document_sections (customer_id, document_id, seq, path, page_from, page_to,"
    " piece_seq_from, piece_seq_to, summary) VALUES (:c, :d, :n, '{Leave}', 1, 2, 0, 1, 'Leave rules.')"
)
TOPIC = (
    "INSERT INTO document_topics (customer_id, document_id, topic, aliases, piece_seqs)"
    " VALUES (:c, :d, :topic, '{holidays}', '{0, 1}')"
)
TABLES = ("document_sections", "document_topics")
COUNT = {
    "document_sections": "SELECT count(*) FROM document_sections WHERE document_id = :d",
    "document_topics": "SELECT count(*) FROM document_topics WHERE document_id = :d",
}
DELETE = {
    "document_sections": "DELETE FROM document_sections WHERE document_id = :d",
    "document_topics": "DELETE FROM document_topics WHERE document_id = :d",
}


def _a_with_a_section_and_a_topic(db: Database) -> tuple[str, str, str]:
    """(A, B, A's document with section 0 and the topic leave)."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    with db.customer(a) as conn:
        doc = insert_document(conn, a)
        conn.execute(text(SECTION), {"c": a, "d": doc, "n": 0})
        conn.execute(text(TOPIC), {"c": a, "d": doc, "topic": "leave"})
    return a, b, doc


def _seen_by(db: Database, customer_id: str, document_id: str) -> dict[str, int]:
    with db.customer(customer_id) as conn:
        return {table: count_rows(conn, COUNT[table], {"d": document_id}) for table in TABLES}


def test_another_customer_sees_no_section_or_topic(db: Database) -> None:
    a, b, doc = _a_with_a_section_and_a_topic(db)
    assert _seen_by(db, b, doc) == dict.fromkeys(TABLES, 0)
    assert _seen_by(db, a, doc) == dict.fromkeys(TABLES, 1)


@pytest.mark.parametrize(("statement", "params"), [(SECTION, {"n": 1}), (TOPIC, {"topic": "salary"})])
def test_the_composite_foreign_key_refuses_another_customers_document(
    db: Database, statement: str, params: dict[str, object]
) -> None:
    _, b, doc = _a_with_a_section_and_a_topic(db)

    with pytest.raises(IntegrityError) as refused, db.customer(b) as conn:
        conn.execute(text(statement), {"c": b, "d": doc, **params})
    assert violation(refused.value)[0] == "23503"


@pytest.mark.parametrize(("statement", "params"), [(SECTION, {"n": 1}), (TOPIC, {"topic": "salary"})])
def test_a_row_in_another_customers_name_is_refused_by_the_policy(
    db: Database, statement: str, params: dict[str, object]
) -> None:
    a, b, doc = _a_with_a_section_and_a_topic(db)

    with pytest.raises(DBAPIError) as refused, db.customer(b) as conn:
        conn.execute(text(statement), {"c": a, "d": doc, **params})
    assert violation(refused.value)[0] == "42501"
    assert _seen_by(db, a, doc) == dict.fromkeys(TABLES, 1)


def test_another_customer_can_neither_change_nor_delete_them(db: Database) -> None:
    a, b, doc = _a_with_a_section_and_a_topic(db)

    with db.customer(b) as conn:
        changed = conn.execute(
            text("UPDATE document_sections SET embedding = NULL, embed_recipe = NULL WHERE document_id = :d"),
            {"d": doc},
        )
        deleted = [conn.execute(text(DELETE[table]), {"d": doc}).rowcount for table in TABLES]
        assert (changed.rowcount, deleted) == (0, [0, 0])

    assert _seen_by(db, a, doc) == dict.fromkeys(TABLES, 1)


def test_another_customers_save_with_the_same_document_id_leaves_them(db: Database) -> None:
    """The replacing save deletes sections and topics: B's save with A's id deletes none
    of A's (it is refused, and its deletes see no row of A's)."""
    a, b, doc = _a_with_a_section_and_a_topic(db)

    with pytest.raises(IntegrityError):
        PgPipelineStore(db).save(content(b, doc))
    PgPipelineStore(db).clear(b, doc)

    assert _seen_by(db, a, doc) == dict.fromkeys(TABLES, 1)
