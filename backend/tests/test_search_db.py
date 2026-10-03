"""The three searches and their merge, against a real PostgreSQL.

Documents and pieces are inserted with plain SQL inside ``db.customer``, with
the columns from migration 0001 (the vector is halfvec(1024) since 0006, tagged
``RECIPE``; a Devanagari piece gets production's ``latin_spelling``, as the
pipeline gives it). Embeddings are hand-made 1024 number vectors:
``axis(i)`` points one way, so two pieces on different axes mean different
things. ``add_document`` and ``add_pieces`` are shared with the isolation and
indexing tests.
"""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.chunking.script import is_devanagari, latin_spelling
from kaagaz.db.engine import Database
from kaagaz.search.hybrid import SearchHit, search, vector_literal
from kaagaz.search.indexing import recipe_tag
from tests.db_support import new_customer

pytestmark = pytest.mark.db

DIM = 1024
REGION = {"kind": "box", "page": 1, "box": [0.1, 0.2, 0.6, 0.25]}
RECIPE = recipe_tag("test-embed")  # the tag add_pieces writes and run searches for
EF_SEARCH = 100  # SEARCH_EF in .env.example


def axis(index: int, weight: float = 1.0, other: int | None = None, other_weight: float = 0.0) -> list[float]:
    vector = [0.0] * DIM
    vector[index] = weight
    if other is not None:
        vector[other] = other_weight
    return vector


def add_document(
    db: Database,
    customer_id: str,
    *,
    name: str = "bill.pdf",
    status: str = "ready",
    detected_type: str = "pdf",
) -> str:
    document_id = str(uuid.uuid4())
    with db.customer(customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO documents"
                " (id, customer_id, display_name, sha256, size_bytes, detected_type, status, family_id)"
                " VALUES (:d, :c, :name, :h, 10, :type, :status, :d)"
            ),
            {
                "d": document_id,
                "c": customer_id,
                "name": name,
                "h": uuid.uuid4().hex * 2,
                "type": detected_type,
                "status": status,
            },
        )
    return document_id


def add_pieces(
    db: Database,
    customer_id: str,
    document_id: str,
    pieces: list[tuple[str, list[float] | None]],
    *,
    label: str = "verified",
    model: str = RECIPE,
) -> list[str]:
    """Insert (text, embedding or None) pieces in seq order; returns their ids."""
    ids = [str(uuid.uuid4()) for _ in pieces]
    rows: list[dict[str, Any]] = [
        {
            "id": piece_id,
            "c": customer_id,
            "d": document_id,
            "seq": seq,
            "text": piece_text,
            "regions": json.dumps([REGION]),
            "label": label,
            "latin": latin_spelling(piece_text) if is_devanagari(piece_text) else None,
            "v": None if embedding is None else vector_literal(embedding),
            "m": None if embedding is None else model,
        }
        for seq, (piece_id, (piece_text, embedding)) in enumerate(zip(ids, pieces, strict=True))
    ]
    with db.customer(customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO pieces (id, customer_id, document_id, seq, page_from, page_to, text, regions,"
                " label, text_latin, embedding, embedding_model)"
                " VALUES (:id, :c, :d, :seq, 1, 1, :text, CAST(:regions AS jsonb), :label, :latin,"
                " CAST(:v AS halfvec(1024)), :m)"
            ),
            rows,
        )
    return ids


def run(
    db: Database,
    customer_id: str,
    query: str,
    embedding: list[float] | None = None,
    limit: int = 10,
    recipe: str = RECIPE,
) -> list[SearchHit]:
    with db.customer(customer_id) as conn:
        return search(
            conn,
            customer_id=customer_id,
            query_text=query,
            embedding=embedding,
            limit=limit,
            recipe=recipe,
            ef_search=EF_SEARCH,
        )


def test_an_exact_word_finds_its_piece_first(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer, name="sharma-bill.pdf")
    wanted, other = add_pieces(
        db,
        customer,
        document,
        [("Invoice INV-1042 from Sharma Traders", None), ("Invoice INV-2077 from Mehta Textiles", None)],
        label="needs_review",
    )

    hits = run(db, customer, "INV-1042")

    assert hits[0].piece_id == wanted
    assert hits[0].word_score is not None and hits[0].word_score > 0
    assert all(hit.word_score is None for hit in hits if hit.piece_id == other)
    top = hits[0]
    assert (top.document_id, top.document_name, top.detected_type) == (document, "sharma-bill.pdf", "pdf")
    assert (top.page_from, top.page_to, top.label) == (1, 1, "needs_review")
    assert top.regions == [REGION]
    assert top.text == "Invoice INV-1042 from Sharma Traders"


def test_a_typo_still_finds_the_word(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    wanted, _ = add_pieces(
        db, customer, document, [("Tax invoice No 42", None), ("Rent paid for August", None)]
    )

    hits = run(db, customer, "invioce")

    assert [hit.piece_id for hit in hits] == [wanted]
    assert hits[0].word_score is None  # the words list does not know the typo
    assert hits[0].fuzzy_score is not None and hits[0].fuzzy_score >= 0.3


def test_a_meaning_match_needs_no_shared_word(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    wanted, _ = add_pieces(
        db, customer, document, [("Net income was largest in August", axis(0)), ("Rent paid", axis(1))]
    )

    # Mostly axis 0, a little axis 1: closest to the first piece.
    hits = run(db, customer, "highest profit", embedding=axis(0, 0.9, other=1, other_weight=0.1))

    assert hits[0].piece_id == wanted
    assert hits[0].word_score is None
    assert hits[0].meaning_score is not None and hits[0].meaning_score > 0.9
    assert hits[1].meaning_score is not None and hits[1].meaning_score < hits[0].meaning_score


def test_pieces_without_an_embedding_are_left_out_of_the_meaning_list(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    with_vector, _ = add_pieces(db, customer, document, [("alpha", axis(3)), ("beta", None)])

    hits = run(db, customer, "", embedding=axis(3))

    assert [hit.piece_id for hit in hits] == [with_vector]


def test_only_ready_documents_are_searched(db: Database) -> None:
    customer, _ = new_customer(db)
    ready = add_document(db, customer, status="ready")
    (wanted,) = add_pieces(db, customer, ready, [("Invoice INV-5005 total", axis(2))])
    for status in ("received", "clean", "needs_reader", "failed", "rejected"):
        other = add_document(db, customer, status=status)
        add_pieces(db, customer, other, [("Invoice INV-5005 total", axis(2))])

    for query, embedding in (("INV-5005", None), ("invioce", None), ("", axis(2))):
        hits = run(db, customer, query, embedding)
        assert [hit.piece_id for hit in hits] == [wanted], query


def test_a_piece_found_several_ways_ranks_first(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    all_ways, words_only, meaning_only = add_pieces(
        db,
        customer,
        document,
        [
            ("Grand total of the invoice 5,000", axis(0)),
            ("Invoice copy for the grand total register, filed", axis(7)),
            ("Amount payable this month", axis(0, 0.95, other=5, other_weight=0.3)),
        ],
    )

    hits = run(db, customer, "invoice grand total", embedding=axis(0))

    assert hits[0].piece_id == all_ways
    assert None not in (hits[0].word_score, hits[0].fuzzy_score, hits[0].meaning_score)
    assert {hit.piece_id for hit in hits} == {all_ways, words_only, meaning_only}
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("query", ["", "   ", "the and of", "-", '"'])
def test_empty_or_stop_word_questions_find_nothing_and_do_not_fail(db: Database, query: str) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [("Invoice total", None), ("Rent paid in August", None)])

    assert run(db, customer, query) == []


def test_a_stop_word_question_with_an_embedding_uses_meaning_only(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    (wanted,) = add_pieces(db, customer, document, [("Invoice total", axis(4))])

    hits = run(db, customer, "the", embedding=axis(4))

    assert [hit.piece_id for hit in hits] == [wanted]
    assert (hits[0].word_score, hits[0].fuzzy_score) == (None, None)


def test_limit_is_kept_and_checked(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    add_pieces(db, customer, document, [(f"Invoice number {n}", None) for n in range(5)])

    assert len(run(db, customer, "invoice", limit=2)) == 2
    for bad in (0, 101):
        with pytest.raises(ValueError, match="limit"):
            run(db, customer, "invoice", limit=bad)


def _ranks(db: Database, customer_id: str, piece_id: str, english: str, simple: str) -> tuple[float, float]:
    """ts_rank_cd of one piece on tsv (english) and on tsv_simple, worked out on their own."""
    with db.customer(customer_id) as conn:
        row = conn.execute(
            text(
                "SELECT ts_rank_cd(tsv, websearch_to_tsquery('english', :e)),"
                " ts_rank_cd(tsv_simple, websearch_to_tsquery('simple', :s))"
                " FROM pieces WHERE customer_id = :c AND id = :id"
            ),
            {"c": customer_id, "id": piece_id, "e": english, "s": simple},
        ).one()
    return float(row[0]), float(row[1])


def test_the_word_score_is_the_better_of_the_english_and_simple_ranks(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    english_only, simple_only = add_pieces(
        db, customer, document, [("Invoices from the Pune office", None), ("पाटील शिंदे", None)]
    )

    # "invoice" matches "Invoices" only through English stemming; "patil" matches
    # the Devanagari piece only through its Latin spelling in tsv_simple (PRD 3 W1).
    for query, piece_id, better in (("invoice", english_only, 0), ("patil", simple_only, 1)):
        (hit,) = run(db, customer, query)
        ranks = _ranks(db, customer, piece_id, query, query)
        assert hit.piece_id == piece_id, query
        assert ranks[better] > 0 and ranks[1 - better] == 0, query
        assert hit.word_score == pytest.approx(max(ranks)), query


def test_simple_words_hold_devanagari_and_its_latin_spelling(db: Database) -> None:
    customer, _ = new_customer(db)
    (piece,) = add_pieces(db, customer, add_document(db, customer), [("पाटील शिंदे", None)])
    with db.customer(customer) as conn:
        words = conn.execute(
            text("SELECT tsv_simple::text FROM pieces WHERE customer_id = :c AND id = :id"),
            {"c": customer, "id": piece},
        ).scalar_one()

    assert words == "'patil':3 'shinde':4 'पाटील':1 'शिंदे':2"
    hits = run(db, customer, "Patil Shinde")
    assert [hit.piece_id for hit in hits] == [piece]
    assert hits[0].word_score is not None and hits[0].word_score > 0


def test_a_latin_typo_finds_devanagari_through_its_latin_spelling(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    wanted, _ = add_pieces(db, customer, document, [("पाटील शिंदे", None), ("Rent paid for August", None)])

    hits = run(db, customer, "shindey")  # one letter too many: no word matches (W2)

    assert [hit.piece_id for hit in hits] == [wanted]
    assert hits[0].word_score is None
    assert hits[0].fuzzy_score is not None and hits[0].fuzzy_score >= 0.3


def test_vectors_with_an_old_tag_are_left_out_of_meaning_but_words_find_them(db: Database) -> None:
    """Edge case 7: after a MODEL_EMBED or recipe change, until reembed."""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    (old,) = add_pieces(db, customer, document, [("Invoice INV-7007", axis(6))], model="old-model")
    (current,) = add_pieces(db, customer, add_document(db, customer), [("Rent", axis(6, 0.5, 7, 0.5))])

    assert [hit.piece_id for hit in run(db, customer, "", embedding=axis(6))] == [current]
    hits = run(db, customer, "INV-7007", embedding=axis(6))
    assert {hit.piece_id for hit in hits} == {old, current}
    by_id = {hit.piece_id: hit for hit in hits}
    assert by_id[old].meaning_score is None and by_id[old].word_score is not None
