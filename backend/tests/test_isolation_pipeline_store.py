"""Customer B's save, clear or load never touches customer A's rows (real PostgreSQL).

B uses A's exact document id. Marked ``isolation`` by its file name.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from tests.db_support import new_customer
from tests.test_pipeline_store_db import content, counts, new_document

pytestmark = pytest.mark.db

FULL = {"pages": 2, "reader_outputs": 2, "pieces": 2, "extracted_values": 2}
EMPTY = {"pages": 0, "reader_outputs": 0, "pieces": 0, "extracted_values": 0}


def test_saving_for_another_customers_document_is_refused_and_changes_nothing(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    doc = new_document(db, a)
    store = PgPipelineStore(db)
    store.save(content(a, doc))

    # B has no document with this id: the (customer, document) foreign key refuses it.
    with pytest.raises(IntegrityError):
        store.save(content(b, doc, total="1.00"))

    assert counts(db, a, doc) == FULL
    assert counts(db, b, doc) == EMPTY
    with db.customer(a) as conn:
        texts = conn.execute(text("SELECT text FROM pieces WHERE document_id = :d ORDER BY seq"), {"d": doc})
        assert [row[0] for row in texts] == ["Sharma Traders", "Total: 1,180.00"]


def test_clearing_with_another_customers_document_id_deletes_nothing(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    doc = new_document(db, a)
    store = PgPipelineStore(db)
    store.save(content(a, doc))

    store.clear(b, doc)

    assert counts(db, a, doc) == FULL


def test_other_customers_rows_are_invisible(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    doc = new_document(db, a)
    PgPipelineStore(db).save(content(a, doc))
    assert counts(db, b, doc) == EMPTY


def test_learned_corrections_are_loaded_for_their_own_customer_only(db: Database) -> None:
    a, user_a = new_customer(db)
    b, _ = new_customer(db)
    with db.customer(a) as conn:
        conn.execute(
            text(
                "INSERT INTO learned_corrections (customer_id, field, raw_value, corrected_value, created_by)"
                " VALUES (:c, 'total_amount', '1,13O', '1,130', :u)"
            ),
            {"c": a, "u": user_a},
        )
    store = PgPipelineStore(db)
    assert [item.corrected_value for item in store.load_learned(a, {"total_amount"})] == ["1,130"]
    assert store.load_learned(b, {"total_amount"}) == []
