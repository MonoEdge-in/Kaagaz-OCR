"""Customer B never finds customer A's pieces, not even with A's exact question
and embedding, and not even when the code names the wrong customer.

Both locks are tested: row level security (the transaction's customer) and the
explicit ``customer_id`` filter in every search query (docs/architecture.md).
"""

import pytest

from kaagaz.db.engine import Database
from kaagaz.search.hybrid import search
from kaagaz.search.indexing import embed_pieces
from tests.db_support import new_customer
from tests.test_indexing_db import FakeModel
from tests.test_search_db import add_document, add_pieces, axis, run

pytestmark = pytest.mark.db

SECRET = "Invoice INV-1042 from Sharma Traders, grand total 5,000"
QUESTIONS = [("INV-1042", None), ("invioce sharma", None), ("", axis(0)), ("grand total", axis(0))]


def test_b_never_gets_the_pieces_of_a_with_the_identical_question(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    (a_piece,) = add_pieces(db, a, add_document(db, a), [(SECRET, axis(0))])
    (b_piece,) = add_pieces(db, b, add_document(db, b), [("Rent paid for August", axis(1))])

    for query, embedding in QUESTIONS:
        assert a_piece in {hit.piece_id for hit in run(db, a, query, embedding)}, query
        b_hits = {hit.piece_id for hit in run(db, b, query, embedding)}
        assert a_piece not in b_hits, query
        assert b_hits <= {b_piece}, query


def test_a_customer_with_no_pieces_finds_nothing(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    add_pieces(db, a, add_document(db, a), [(SECRET, axis(0))])

    for query, embedding in QUESTIONS:
        assert run(db, b, query, embedding) == [], query


def test_naming_the_other_customer_in_the_call_finds_nothing(db: Database) -> None:
    """Row level security holds when the explicit filter names A, and the
    explicit filter holds when the transaction belongs to A."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    add_pieces(db, a, add_document(db, a), [(SECRET, axis(0))])

    for query, embedding in QUESTIONS:
        with db.customer(b) as conn:
            assert search(conn, customer_id=a, query_text=query, embedding=embedding, limit=10) == []
        with db.customer(a) as conn:
            assert search(conn, customer_id=b, query_text=query, embedding=embedding, limit=10) == []


def test_b_cannot_embed_a_document_of_a(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    document = add_document(db, a)
    add_pieces(db, a, document, [(SECRET, None)])
    model = FakeModel()

    assert embed_pieces(db, b, document, model) == 0
    assert model.calls == []
    assert embed_pieces(db, a, document, model) == 1
