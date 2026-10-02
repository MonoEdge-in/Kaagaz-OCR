"""Customer B never finds customer A's pieces, not even with A's exact question
and embedding, and not even when the code names the wrong customer.

Both locks are tested: row level security (the transaction's customer) and the
explicit ``customer_id`` filter in every search query (docs/architecture.md),
on the meaning index walk and on its exact fallback (PRD 3 V2), each with a
positive control: A's own search finds A's piece by meaning.
"""

from typing import Any

import pytest

from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.hybrid import search
from kaagaz.search.indexing import embed_pieces
from kaagaz.search.scope import EVERYTHING
from tests.db_support import insert_document, insert_folder, insert_tag, new_customer, tag_document
from tests.test_api_support import Api, Signed
from tests.test_indexing_db import FakeModel
from tests.test_search_db import EF_SEARCH, RECIPE, add_document, add_pieces, axis, run

pytestmark = pytest.mark.db

SECRET = "Invoice INV-1042 from Sharma Traders, grand total 5,000"
QUESTIONS = [("INV-1042", None), ("invioce sharma", None), ("", axis(0)), ("grand total", axis(0))]


def test_b_never_gets_the_pieces_of_a_with_the_identical_question(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    (a_piece,) = add_pieces(db, a, add_document(db, a), [(SECRET, axis(0))])
    (b_piece,) = add_pieces(db, b, add_document(db, b), [("Rent paid for August", axis(1))])

    for query, embedding in QUESTIONS:
        a_hits = {hit.piece_id: hit for hit in run(db, a, query, embedding)}
        assert a_piece in a_hits, query
        if embedding is not None:  # the positive control: the meaning list did run
            assert a_hits[a_piece].meaning_score is not None, query
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
        for transaction, named in ((b, a), (a, b)):
            with db.customer(transaction) as conn:
                hits = search(
                    conn,
                    customer_id=named,
                    query_text=query,
                    embedding=embedding,
                    limit=10,
                    recipe=RECIPE,
                    ef_search=EF_SEARCH,
                )
            assert hits == [], query


def test_neither_meaning_statement_returns_the_other_customers_vectors(db: Database) -> None:
    """The index walk and the exact fallback, each run on its own with A's exact
    vector: A finds its piece (the control), B only its own, and naming the
    other customer finds nothing."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    (a_piece,) = add_pieces(db, a, add_document(db, a), [(SECRET, axis(0))])
    (b_piece,) = add_pieces(db, b, add_document(db, b), [("Rent paid for August", axis(1))])
    params = {"vec": hybrid.vector_literal(axis(0)), "recipe": RECIPE, "n": 50}
    for statement in (hybrid._MEANING, hybrid._MEANING_EXACT):
        for transaction, named, found in ((a, a, [a_piece]), (b, b, [b_piece]), (b, a, []), (a, b, [])):
            with db.customer(transaction) as conn:
                rows = conn.execute(statement, {**params, "c": named}).all()
            assert [str(row[0]) for row in rows] == found, (transaction == a, named == a)


def test_b_cannot_embed_a_document_of_a(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    document = add_document(db, a)
    add_pieces(db, a, document, [(SECRET, None)])
    model = FakeModel()

    assert embed_pieces(db, b, document, model) == 0
    assert model.calls == []
    assert embed_pieces(db, a, document, model) == 1


def test_a_scope_of_the_other_customers_ids_finds_nothing(db: Database) -> None:
    """Edge case 9: A searches with B's folder, tag and document ids and finds nothing,
    neither A's documents nor B's; with the same ids B finds its own (the control)."""
    a, _ = new_customer(db)
    b, b_user = new_customer(db)
    with db.customer(b) as conn:
        b_folder = insert_folder(conn, b, b_user, "Bills")
        b_tag = insert_tag(conn, b, b_user, "gst")
        b_document = insert_document(conn, b, folder_id=b_folder)
        tag_document(conn, b, b_document, b_tag)
    add_pieces(db, b, b_document, [(SECRET, axis(0))])
    (a_piece,) = add_pieces(db, a, add_document(db, a), [(SECRET, axis(0))])

    scopes: list[dict[str, Any]] = [
        {"folder_id": b_folder},
        {"folder_id": b_folder, "include_subfolders": True},
        {"tag_ids": [b_tag]},
        {"document_ids": [b_document]},
    ]
    for query, embedding in QUESTIONS:
        assert a_piece in {hit.piece_id for hit in run(db, a, query, embedding)}, query  # A's own
    for body in scopes:
        assert scoped(db, b, body) == {b_document}, body
        assert scoped(db, a, body) == set(), body


def test_the_search_route_gives_a_nothing_for_the_ids_of_b(db: Database) -> None:
    """The same through GET /api/search, signed in as A."""
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    b_folder = api.add_folder(b.customer_id, b.user_id, "Bills")
    b_tag = api.add_tag(b.customer_id, b.user_id, "gst")
    b_document = api.add_document(b.customer_id, folder_id=b_folder)
    api.tag_document(b.customer_id, b_document, b_tag)
    add_pieces(db, b.customer_id, b_document, [(SECRET, None)])
    a_document = api.add_document(a.customer_id)
    add_pieces(db, a.customer_id, a_document, [(SECRET, None)])

    def ids(signed: Signed, **params: Any) -> set[str]:
        reply = signed.client.get("/api/search", params={"q": "INV-1042", **params})
        assert reply.status_code == 200, reply.text
        return {found["id"] for found in reply.json()["documents"]}

    assert ids(a) == {a_document}  # the control
    for params in ({"folder_id": b_folder}, {"tag_ids": [b_tag]}, {"document_ids": [b_document]}):
        assert ids(b, **params) == {b_document}, params
        assert ids(a, **params) == set(), params
