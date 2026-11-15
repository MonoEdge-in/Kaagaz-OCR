"""#617 part 1: the owner-rights candidate function (0094) and the word counts never cross
customers, and a hidden piece (Trash, an older version, still reading) never reaches a search
through them. Run as the app role, with row level security on."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.search import hybrid, word_counts
from kaagaz.search.scope import Scope
from tests.db_support import APP_ROLE, insert_document, new_customer
from tests.land_support import add_land
from tests.test_search_bounded_db import NOW, candidates, counted, word_list
from tests.test_search_db import add_document, add_pieces, run

pytestmark = pytest.mark.db


def test_the_candidates_are_only_the_current_customers(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    a_doc, b_doc = add_document(db, a), add_document(db, b)
    (a_piece,) = add_pieces(db, a, a_doc, [("Jindal bhogwatdar", None)])
    (b_piece,) = add_pieces(db, b, b_doc, [("Jindal bhogwatdar", None)])

    for column, query in (("tsv", "jindal"), ("tsv_simple", "jindal"), ("tsv_sound", "jindl")):
        assert candidates(db, a, query, column=column) == [a_piece], column
        assert candidates(db, b, query, column=column) == [b_piece], column
    assert candidates(db, a, "jindal", docs=[b_doc]) == []  # B's document named: nothing
    assert candidates(db, a, "jindal", docs=[a_doc, b_doc]) == [a_piece]


def test_with_no_customer_set_the_candidates_are_none(db: Database) -> None:
    a, _ = new_customer(db)
    add_pieces(db, a, add_document(db, a), [("Jindal", None)])
    with db.engine.begin() as conn:
        conn.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))  # the app role, no kaagaz.customer_id
        found = conn.execute(text("SELECT kaagaz_word_candidates('tsv_simple', 'jindal', NULL, 10)")).all()
    assert found == []


def test_a_search_never_returns_another_customers_or_a_hidden_piece(db: Database) -> None:
    a, a_user = new_customer(db)
    b, _ = new_customer(db)
    with db.customer(a) as conn:
        old = insert_document(conn, a, is_current=False)
        current = insert_document(conn, a, family_id=old, version_no=2)
        binned = insert_document(conn, a, deleted_at=NOW, deleted_by=a_user)
        reading = insert_document(conn, a, status="clean")
    hidden = [add_pieces(db, a, d, [("Jindal Krishna", None)])[0] for d in (old, binned, reading)]
    (shown,) = add_pieces(db, a, current, [("Jindal Krishna", None)])
    (other,) = add_pieces(db, b, add_document(db, b), [("Jindal Krishna", None)])

    for question in ("jindal", "krishna jindal", "jindaal"):  # exact, and by sound
        found = word_list(db, a, question) + [hit.piece_id for hit in run(db, a, question)]
        assert shown in found and not {*hidden, other} & set(found), question
    assert other in word_list(db, b, "jindal") and shown not in word_list(db, b, "jindal")


def test_another_customers_stored_identifier_never_finds_its_pieces(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    b_land = add_land(db, b, "7-12.pdf", survey="61")
    (b_piece,) = add_pieces(db, b, b_land, [("गट नंबर ६१", None)])
    a_land = add_land(db, a, "7-12.pdf", survey="61")
    (a_piece,) = add_pieces(db, a, a_land, [("गट नंबर ६१", None)])

    assert word_list(db, a, "gat 61") == [a_piece]
    assert word_list(db, b, "gat 61") == [b_piece]
    assert word_list(db, a, "gat 61", Scope(document_ids=(b_land,))) == []


def test_word_counts_are_one_customers_only(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    add_pieces(db, a, add_document(db, a), [("Jindal", None), ("Jindal", None)])
    add_pieces(db, b, add_document(db, b), [("Jindal", None), ("Patil", None), ("Rent", None)])

    counted(db, a)
    counted(db, b)

    with db.customer(a) as conn:
        seen = conn.execute(text("SELECT DISTINCT customer_id::text FROM term_df")).scalars().all()
        stats = conn.execute(text("SELECT DISTINCT customer_id::text FROM customer_stats")).scalars().all()
        df = conn.execute(
            text("SELECT df FROM term_df WHERE field = 'tsv_simple' AND term = 'jindal'")
        ).scalar_one()
        assert word_counts.shares(conn, a, "tsv_simple", [["patil"]], 0) == [0.0]  # B's Patil is not A's
        with pytest.raises(Exception, match="row-level security"):
            conn.execute(
                text("INSERT INTO term_df (customer_id, field, term, df) VALUES (:b, 'tsv', 'x', 1)"),
                {"b": b},
            )
    assert (seen, stats, df) == ([a], [a], 2)


def test_the_word_statement_naming_another_customer_finds_nothing(db: Database) -> None:
    """Defence in depth: the candidates are the transaction's customer's, and the statement's own
    filter is :c, so naming B inside A's transaction (or A inside B's) finds nothing."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    add_pieces(db, a, add_document(db, a), [("Jindal", None)])
    words = {
        "q": "jindal",
        "k": "jindal",
        "g": "",
        "gc": "",
        "gk": "",
        "sk": "",
        "n": 50,
        "docs": None,
        "cap": 2000,
    }
    for transaction, named in ((a, b), (b, a)):
        with db.customer(transaction) as conn:
            params = words | {"c": named} | Scope().params()
            assert conn.execute(hybrid._WORD, params).all() == []
