"""The missing-vector pass never reads one customer's pieces as another.

``_MISSING``, the statement that picks the documents to embed, runs inside
``Database.customer(c)`` and names ``c`` again. Both locks are tested: B's
transaction naming A finds nothing (row level security), and so does a
transaction with no customer at all (``Database.system()``, which the pass
uses only for the customer list). A's own transaction finds A's document, the
positive control. A pass over both customers fills each one's pieces only.
"""

import pytest
from sqlalchemy import Connection

from kaagaz.db.engine import Database
from kaagaz.search import indexing
from kaagaz.search.indexing import embed_missing
from tests.db_support import new_customer
from tests.test_embed_missing_db import CURRENT, SameVector, drain, tags
from tests.test_search_db import add_document, add_pieces

pytestmark = pytest.mark.db


def _missing(conn: Connection, customer_id: str) -> list[str]:
    params = {"c": customer_id, "after": "00000000-0000-0000-0000-000000000000", "n": 64}
    return [str(row[0]) for row in conn.execute(indexing._MISSING, params)]


def test_b_naming_a_and_no_customer_at_all_find_none_of_as_documents(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    a_document = add_document(db, a)
    add_pieces(db, a, a_document, [("Invoice INV-1042 from Sharma Traders", None)])
    add_pieces(db, b, add_document(db, b), [("Rent paid for August", None)])

    with db.customer(a) as conn:
        assert _missing(conn, a) == [a_document]  # the positive control
    with db.customer(b) as conn:
        assert _missing(conn, a) == []
    with db.system() as conn:
        assert _missing(conn, a) == []


def test_a_pass_over_two_customers_fills_each_ones_own_pieces(db: Database) -> None:
    drain(db)
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    a_document = add_document(db, a)
    b_document = add_document(db, b)
    add_pieces(db, a, a_document, [("A's piece", None)])
    add_pieces(db, b, b_document, [("B's piece", None)])
    model = SameVector()

    assert embed_missing(db, model, None).written == 2

    assert sorted(item for call in model.calls for item in call) == ["A's piece", "B's piece"]
    assert len(model.calls) == 2  # one call per document: never one batch across customers
    assert tags(db, a, a_document) == tags(db, b, b_document) == [CURRENT]
