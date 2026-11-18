"""The three searches and their merge, against a real PostgreSQL.

Documents and pieces are inserted with plain SQL inside ``db.customer``, with
the columns from migration 0001 (the vector is halfvec(1024) since 0006, tagged
``RECIPE``; a piece gets production's ``piece_latin``, as the pipeline gives it:
a Devanagari piece, or one with an Indic digit, its Latin spelling). Embeddings are
hand-made 1024 number vectors: ``axis(i)`` points one way, so two pieces on different axes mean different
things. ``add_document`` and ``add_pieces`` are shared with the isolation and
indexing tests.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import event, text

from kaagaz.answering.expansion import NO_EXPANSION
from kaagaz.answering.hinglish import normalise
from kaagaz.chunking.script import piece_latin
from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.hybrid import SearchHit, search, vector_literal
from kaagaz.search.indexing import recipe_tag
from kaagaz.search.scope import EVERYTHING, Scope
from tests.db_support import insert_document, new_customer

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
            "latin": piece_latin(piece_text),
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


def test_a_number_matches_only_as_written_while_words_keep_their_typos(db: Database) -> None:
    """#556 (UAT LC-04): after "Total: 150000/-" was corrected to "Total: 1500000/-", the typo
    list still found it for 150000 (all of its trigrams are in 1500000). A word with a digit
    must now be in the piece as written; the words around it keep their typos, and a Marathi
    amount is still found by the English number (UAT AM-07)."""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    corrected, marathi, _ = add_pieces(
        db,
        customer,
        document,
        [("Total: 1500000/-", None), ("एकूण रक्कम ९४००००/-", None), ("Rent paid for August", None)],
    )

    assert run(db, customer, "150000") == []
    assert [hit.piece_id for hit in run(db, customer, "1500000")] == [corrected]
    typo = run(db, customer, "totaal 1500000")  # Changed on purpose (#666): a typo of 5+ letters
    assert [hit.piece_id for hit in typo] == [corrected] and typo[0].fuzzy_score is not None
    assert [hit.piece_id for hit in run(db, customer, "940000")] == [marathi]
    assert run(db, customer, "94000") == []


def test_an_indian_amount_with_commas_is_one_number_in_the_typo_list(db: Database) -> None:
    """Review of #556 (11 Nov 2026): the database's word splitter cut "1,50,000" into 1, 50 and
    000, so the typo list kept a piece with any 1, 50 and 000 for "1,50,000". Numbers follow
    the viewer's rule (``page_words``): one word, commas dropped. (A question of 150000 against a
    page of 1,50,000 shares too few letter groups for the typo list at all, before and after.)"""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    amount, other, _ = add_pieces(
        db,
        customer,
        document,
        [
            ("Total: Rs. 1,50,000/-", None),
            ("Qty 1 Rate 50 Total 3,000", None),
            ("Rent paid for August", None),
        ],
    )

    grouped = {hit.piece_id: hit for hit in run(db, customer, "totaal 1,50,000")}  # Changed on purpose (#666)
    assert amount in grouped and grouped[amount].fuzzy_score is not None
    assert other not in grouped or grouped[other].fuzzy_score is None  # 1, 50 and 000 aren't 150000


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

    hits = run(db, customer, "shindye")  # a typo no word matches, by sound either (W2)

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


# --- the glossary word list (council 2 Oct 2026, PR2 C) ------------------------------


def test_an_english_word_finds_a_marathi_piece_through_the_glossary(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    owner, _ = add_pieces(db, customer, document, [("भोगवटादाराचे नाव रामचंद्र", None), ("करार झाला", None)])

    hits = run(db, customer, "owner")  # भोगवटादार:* takes the inflected भोगवटादाराचे

    assert [hit.piece_id for hit in hits] == [owner]
    assert hits[0].word_score is not None and hits[0].word_score > 0


def test_a_short_glossary_term_matches_a_whole_word_only(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    tax, _ = add_pieces(db, customer, document, [("कर भरला", None), ("करार झाला", None)])

    assert [hit.piece_id for hit in run(db, customer, "tax")] == [tax]  # कर, never करार


def test_an_empty_glossary_query_matches_nothing(db: Database) -> None:
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [("कर भरला", None)])
    words = {"c": customer, "q": "zzz", "k": "zzz", "sk": "", "n": 50} | EVERYTHING.params()
    words |= {"docs": None, "cap": hybrid.CANDIDATE_CAP}  # Changed on purpose (#617): the candidates' bounds
    with db.customer(customer) as conn:
        assert conn.execute(hybrid._WORD, words | {"g": "", "gc": "", "gk": ""}).all() == []
        assert (
            len(conn.execute(hybrid._WORD, words | {"g": "कर", "gc": "कर", "gk": ""}).all()) == 1
        )  # the control

    assert run(db, customer, "zzz") == []  # no keyword listed: :g is "", and nothing else matches


# --- the sound key list (council 3 Oct 2026, PR 2b-1) ----------------------------------


def by_sound(db: Database, customer_id: str, question: str, scope: Scope = EVERYTHING) -> list[str]:
    """The word list with only its sound query able to match: the pieces it finds. Changed on
    purpose (#619): the sound query is its own rung, after the exact queries."""
    words = {"c": customer_id, "q": "zzz", "k": "zzz", "g": "", "sk": hybrid.sound_query(question), "n": 50}
    with db.customer(customer_id) as conn:
        return [str(row[0]) for row in hybrid._word(conn, words | scope.params())]


@pytest.mark.parametrize(
    ("question", "piece_text"),
    [
        ("bhogwatdar", "भोगवटादार रामचंद्र पाटील"),
        ("Gaikwad", "गायकवाड सुरेश"),
        ("jaminicha", "जमीन वडगाव"),  # the piece's key is the start of the word's
        ("jamin", "जमिनीचा उतारा"),  # the word's key is the start of the piece's
        ("shindey", "पाटील शिंदे"),
        ("bhogwatdar", "Owner (bhogavatadar): Ramchandra"),  # a mostly English piece, no text_latin
    ],
)
def test_a_word_typed_the_way_it_sounds_finds_the_piece(db: Database, question: str, piece_text: str) -> None:
    customer, _ = new_customer(db)
    wanted, _ = add_pieces(
        db, customer, add_document(db, customer), [(piece_text, None), ("करार झाला", None)]
    )

    assert by_sound(db, customer, question) == [wanted]
    hits = run(db, customer, question)
    assert hits[0].piece_id == wanted
    assert hits[0].word_score is not None and hits[0].word_score > 0


def test_without_the_sound_query_the_word_list_misses_it(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [("गायकवाड सुरेश", None)])
    monkeypatch.setattr(hybrid, "sound_query", lambda _: "")

    # A name, so no glossary word stands in (bhogwatdar is the glossary's owner since PR 2b-2).
    assert all(hit.word_score is None for hit in run(db, customer, "gaikwad"))


def test_an_empty_sound_query_matches_nothing(db: Database) -> None:
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [("गायकवाड सुरेश", None)])
    words = {"c": customer, "q": "zzz", "k": "zzz", "g": "", "n": 50} | EVERYTHING.params()
    with db.customer(customer) as conn:  # Changed on purpose (#619): the word list's two rungs
        assert hybrid._word(conn, words | {"sk": ""}) == []
        assert len(hybrid._word(conn, words | {"sk": "gikvd"})) == 1  # the control


def _word_rows(db: Database, customer_id: str, question: str) -> list[tuple[str, float, bool]]:
    """(piece id, score, matched exactly) of the word list for ``question``, in order. Changed
    on purpose (#619): the exact rung (_WORD) says which matched exactly; (#617) with the
    candidates' bounds."""
    words = {"c": customer_id, "q": question, "k": question, "g": "", "sk": hybrid.sound_query(question)}
    params = words | {"n": hybrid.CANDIDATES} | EVERYTHING.params()
    bounds = {"gc": "", "gk": "", "docs": None, "cap": hybrid.CANDIDATE_CAP}
    with db.customer(customer_id) as conn:
        exact = {str(row[0]) for row in conn.execute(hybrid._WORD, params | bounds)}
        return [(str(row[0]), float(row[2]), str(row[0]) in exact) for row in hybrid._word(conn, params)]


def test_an_exact_match_comes_before_a_sound_match_that_ranks_higher(db: Database) -> None:
    customer, _ = new_customer(db)
    exact, by_key = add_pieces(
        db, customer, add_document(db, customer), [("Shetkari list", None), ("शेतकरी " * 6, None)]
    )

    rows = _word_rows(db, customer, "shetkari")

    assert [(piece, matched) for piece, _, matched in rows] == [(exact, True), (by_key, False)]
    assert rows[1][1] > rows[0][1]  # the repeated sound key ranks higher, yet comes second


def test_a_hundred_sound_matches_never_push_an_exact_one_out_of_the_list(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    pieces: list[tuple[str, list[float] | None]] = [("Shetkari list", None)]
    pieces += [("शेतकरी शेतकरी", None)] * 100
    (exact, *_) = add_pieces(db, customer, document, pieces)

    rows = _word_rows(db, customer, "shetkari")

    assert len(rows) == hybrid.CANDIDATES
    assert rows[0][0] == exact
    assert [matched for _, _, matched in rows[1:]] == [False] * (hybrid.CANDIDATES - 1)


@pytest.mark.parametrize("question", ["salary of Shinde", "Shinde salary slip", "rent receipt of Gaikwad"])
def test_a_rare_name_is_not_crowded_out_by_common_words(db: Database, question: str) -> None:
    """No exact match, so the sound tier is "any word": 120 salary slips and rent receipts
    match the common words, and the one piece with the name, uploaded last, still comes
    first, as its word is the rarest."""
    customer, _ = new_customer(db)
    document = add_document(db, customer)
    pieces: list[tuple[str, list[float] | None]] = [
        (f"Salary slip for September, staff {n}", None) for n in range(60)
    ]
    pieces += [(f"Rent receipt for godown {n}", None) for n in range(60)]
    pieces += [("शिंदे यांचे पत्र", None), ("गायकवाड यांचे पत्र", None)]
    ids = add_pieces(db, customer, document, pieces)
    name = ids[-2] if "Shinde" in question else ids[-1]

    rows = _word_rows(db, customer, question)

    assert not any(matched for _, _, matched in rows)
    assert rows[0][0] == name


# Business lines like the golden documents', for the reviewer's false-positive questions.
CORPUS = [
    "Shree Ganesh Textiles, 12 Cloth Market, Ring Road, Surat",
    "TAX INVOICE Invoice No: TX-2026-118 Invoice Date: 12/08/2026 Bill To: Patil Garments",
    "Cotton Poplin 120 m 25.00 3,000.00 Rayon Print 80 m 37.50 3,000.00",
    "Sub Total: 10,000.00 CGST: 900.00 SGST: 900.00 Grand Total: 11,800.00",
    "Amount in words: Rupees Eleven Thousand Eight Hundred Only",
    "Transport Services Agreement between Shree Ganesh Textiles and Rapid Roadways",
    "Fees: the monthly fee payable to the Carrier is Rs. 45,000, paid by the 10th",
    "Late payment: interest of 1.5% a month is charged on any unpaid fee",
    "Insurance: the Carrier insures every load up to Rs. 5,00,000",
    "Notice: either party may end this agreement by giving 60 days notice in writing",
    "Signed for the Client: Ramesh Shah. Signed for the Carrier: Imran Qureshi",
    "Opening balance 50000. NEFT from Patil Garments 11800. Rent for August 15000",
    "UPI to Shree Ganesh Textiles 10000. Closing balance 36800",
    "Mehta General Stores, Station Road, Nashik. Bill No: R-3071 Total: 675.00",
    "Toor Dal 1 kg 160.00 Sugar 5 kg 225.00 Sunflower Oil 1 L 290.00",
    "Paid by UPI. Thank you, visit again. Receipt of payment acknowledged",
    "Salary for September paid to staff: Shinde 18,000, Pawar 16,500",
    "Rental agreement for the godown at Bhiwandi, rent payable monthly",
    "गट क्रमांक 45/2 भोगवटादाराचे नाव पाटील रामचंद्र गणपत क्षेत्र 1.20",
    "शर्मा किराना स्टोर बिल नं. 245 चावल 5 किलो 300.00 दाल 2 किलो 150.00",
]


@pytest.mark.parametrize("question", ["invoices and receipts", "rent agreement", "salary of Shinde"])
def test_english_questions_bring_few_pieces_by_sound_alone(db: Database, question: str) -> None:
    """Measured on this corpus: 2, 3 and 0 pieces found by sound alone, each holding a word
    of the question in another form (Invoice, Receipt, Agreement)."""
    customer, _ = new_customer(db)
    ids = add_pieces(db, customer, add_document(db, customer), [(line, None) for line in CORPUS])

    rows = _word_rows(db, customer, question)

    assert sum(1 for _, _, matched in rows if not matched) <= 3
    assert by_sound(db, customer, "bhogwatdar") == [ids[-2]]  # the control


def test_the_sound_list_sees_only_the_current_version_out_of_trash_and_in_scope(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        old = insert_document(conn, customer, is_current=False)
        current = insert_document(conn, customer, family_id=old, version_no=2)
        binned = insert_document(
            conn, customer, deleted_at=datetime(2026, 10, 1, tzinfo=UTC), deleted_by=user
        )
        other = insert_document(conn, customer)
    pieces = {
        document: add_pieces(db, customer, document, [("भोगवटादार", None)])[0]
        for document in (old, current, binned, other)
    }

    assert set(by_sound(db, customer, "bhogwatdar")) == {pieces[current], pieces[other]}
    assert by_sound(db, customer, "bhogwatdar", Scope(document_ids=(current,))) == [pieces[current]]


# --- #619 and #622: production's "Kaagaz is busy" (15 Nov 2026) -------------------------


def _word_list(db: Database, customer_id: str, question: str) -> list[str]:
    """The pieces of ``question``'s word list, as the search runs it (``_lists``)."""
    with db.customer(customer_id) as conn:
        word, _, _ = hybrid._lists(
            conn, customer_id, question, None, RECIPE, EF_SEARCH, EVERYTHING, NO_EXPANSION, {}
        )
    return [piece for piece, _ in word]


def test_request_words_are_never_searched(db: Database) -> None:
    """Both production questions: their request words ("find", "seen", "other", "document",
    "name") matched most pieces by sound. Now only the name and the topic are searched."""
    customer, _ = new_customer(db)
    sound, exact, measured, filler = add_pieces(
        db,
        customer,
        add_document(db, customer),
        [
            ("Buyer: Krishna Jindaal, Wagholi", None),  # by sound only: jindaal is jindl
            ("Signed by Jindal Krishna", None),
            ("Measurement of the plot: 20 m by 30 m", None),
            ("Document name: land register. Other details are seen on page 2. Find it here.", None),
        ],
    )

    assert set(_word_list(db, customer, "which document have krishna jindal name")) == {sound, exact}
    asked = "Can you find me the measurement of this if it is seen in any other document?"
    assert _word_list(db, customer, asked) == [measured]
    # Chat searches the question's keywords (service._searched_as), so the typo list skips them too.
    searched = " ".join(normalise(asked).keywords)
    assert filler not in {hit.piece_id for hit in run(db, customer, searched)}


# A word or typo statement that takes a second, past a 100 ms statement timeout (``_slow``).
SLOW_WORD = (
    "SELECT p.id, p.document_id, 1.0 FROM pieces p CROSS JOIN pg_sleep(1) WHERE p.customer_id = :c LIMIT :n"
)
SLOW_FUZZY = (
    "SELECT p.id, p.document_id, 1.0, p.text, p.text_latin FROM pieces p CROSS JOIN pg_sleep(1)"
    " WHERE p.customer_id = :c LIMIT :n"
)


def _slow(monkeypatch: pytest.MonkeyPatch, name: str, statement: str) -> None:
    """``hybrid.<name>`` is ``statement``, and the statement timeout 100 ms."""
    monkeypatch.setattr(hybrid, "STATEMENT_TIMEOUT_MS", 100)
    monkeypatch.setattr(hybrid, name, text(statement))


def test_a_word_list_that_times_out_is_dropped_and_the_typo_list_still_answers(
    db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    customer, _ = new_customer(db)
    pieces: list[tuple[str, list[float] | None]] = [("Invoice from Sharma", None), ("Rent", None)]
    wanted, _ = add_pieces(db, customer, add_document(db, customer), pieces)
    _slow(monkeypatch, "_WORD", SLOW_WORD)
    caplog.set_level(logging.WARNING, logger="kaagaz.search.hybrid")

    hits = run(db, customer, "invioce sharma")  # until #619: OperationalError, then 503

    assert [hit.piece_id for hit in hits] == [wanted] and hits[0].word_score is None
    assert [getattr(r, "code", None) for r in caplog.records] == ["word_timeout"]
    assert "invioce" not in caplog.text


def test_a_typo_list_that_times_out_is_dropped_and_the_word_list_still_answers(
    db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    customer, _ = new_customer(db)
    pieces: list[tuple[str, list[float] | None]] = [("Invoice from Sharma", None), ("Rent", None)]
    wanted, _ = add_pieces(db, customer, add_document(db, customer), pieces)
    _slow(monkeypatch, "_FUZZY", SLOW_FUZZY)
    caplog.set_level(logging.WARNING, logger="kaagaz.search.hybrid")

    hits = run(db, customer, "invoice sharma")

    assert [hit.piece_id for hit in hits] == [wanted] and hits[0].fuzzy_score is None
    assert [getattr(r, "code", None) for r in caplog.records] == ["fuzzy_timeout"]


def test_the_sound_rung_runs_only_when_the_exact_rung_found_too_few(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#619: a question whose exact words fill the list never runs the sound query."""
    customer, _ = new_customer(db)
    pieces: list[tuple[str, list[float] | None]] = [("Shetkari list", None)] * hybrid.CANDIDATES
    add_pieces(db, customer, add_document(db, customer), [*pieces, ("शेतकरी", None)])
    monkeypatch.setattr(hybrid, "_SOUND", text("SELECT 1 / 0"))  # it would fail if it ran

    assert len(_word_list(db, customer, "shetkari")) == hybrid.CANDIDATES


def test_the_sound_rung_ranks_only_its_heaviest_pieces(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """#619: at most SOUND_RANKED sound-only pieces are ranked, the rarest words' first."""
    customer, _ = new_customer(db)
    pieces: list[tuple[str, list[float] | None]] = [(f"गायकवाड {n}", None) for n in range(5)]
    ids = add_pieces(db, customer, add_document(db, customer), [*pieces, ("गायकवाड शिंदे", None)])
    monkeypatch.setattr(hybrid, "SOUND_RANKED", 2)

    found = _word_list(db, customer, "gaikwad shinde")

    assert len(found) == 2 and found[0] == ids[-1]


def test_a_name_change_affidavit_is_still_found_without_the_word_name(db: Database) -> None:
    """#622 made "name" a request word ("which document have krishna jindal name"), so it is never
    searched as a word. A question about a name change still finds its document: by its other
    words, and by meaning, which reads the whole question."""
    customer, _ = new_customer(db)
    affidavit, _ = add_pieces(
        db,
        customer,
        add_document(db, customer),
        [("Affidavit for change of name: Ramesh Patil to Ramesh Pawar", axis(3)), ("Rent receipt", axis(4))],
    )

    hits = run(db, customer, "name change affidavit", axis(3))

    assert hits[0].piece_id == affidavit  # the receipt follows, by meaning alone
    assert hits[0].word_score is not None and hits[0].meaning_score == pytest.approx(1.0)


def test_after_a_dropped_list_the_others_have_one_second(
    db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Principal review of #619: once a list is dropped, the transaction's other statements have
    DROPPED_TIMEOUT_MS, so one slow text cannot hold its pooled connection for every list's 3 s.
    The typo list here takes 1.5 s: inside the 2 s timeout, past the 1 s left after the drop."""
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer), [("Invoice from Sharma", None)])
    monkeypatch.setattr(hybrid, "STATEMENT_TIMEOUT_MS", 2000)
    word = (
        "SELECT p.id, p.document_id, 1.0 FROM pieces p CROSS JOIN pg_sleep(3)"
        " WHERE p.customer_id = :c LIMIT :n"
    )
    fuzzy = (
        "SELECT p.id, p.document_id, 1.0, p.text, p.text_latin FROM pieces p CROSS JOIN pg_sleep(1.5)"
        " WHERE p.customer_id = :c LIMIT :n"
    )
    monkeypatch.setattr(hybrid, "_WORD", text(word))
    monkeypatch.setattr(hybrid, "_FUZZY", text(fuzzy))
    caplog.set_level(logging.WARNING, logger="kaagaz.search.hybrid")

    assert run(db, customer, "invoice sharma") == []
    assert [getattr(r, "code", None) for r in caplog.records] == ["word_timeout", "fuzzy_timeout"]


@pytest.mark.parametrize(
    ("question", "printed"),
    [("gat 61", "Gut No. 61, Kusgaon"), ("gut 61", "Gat No. 61, Kusgaon"), ("gut 61", "गट नंबर ६१ कुसगाव")],
)
def test_gut_and_gat_find_each_other(db: Database, question: str, printed: str) -> None:
    """#625: land records print the survey number as Gut and as Gat (गट): either finds the other."""
    customer, _ = new_customer(db)
    wanted, _ = add_pieces(
        db, customer, add_document(db, customer), [(printed, None), ("Rent receipt", None)]
    )

    hits = run(db, customer, question)

    assert [hit.piece_id for hit in hits] == [wanted] and hits[0].word_score is not None


# --- #666: the typo list only for long words, and only when the word list is short ----------


def _typo_runs(db: Database, customer_id: str, question: str) -> tuple[list[SearchHit], list[dict[str, Any]]]:
    """``run``'s hits for ``question``, and the parameters of each typo statement it sent."""
    sent: list[dict[str, Any]] = []

    def seen(_conn: Any, _cursor: Any, statement: str, parameters: Any, *_: Any) -> None:
        if "word_similarity(" in statement:
            sent.append(dict(parameters))

    event.listen(db.engine, "before_cursor_execute", seen)
    try:
        hits = run(db, customer_id, question)
    finally:
        event.remove(db.engine, "before_cursor_execute", seen)
    return hits, sent


def test_a_short_word_never_runs_the_typo_query(db: Database) -> None:
    """Production, 18 Nov 2026: "map" read 120 MB in 2 s, as almost every piece shares a trigram
    with a short word. Its exact matches are still found."""
    customer, _ = new_customer(db)
    wanted, _ = add_pieces(
        db, customer, add_document(db, customer), [("Site map of the plot", None), ("Rent paid", None)]
    )

    hits, sent = _typo_runs(db, customer, "map")

    assert sent == []
    assert [hit.piece_id for hit in hits] == [wanted] and hits[0].word_score is not None


@pytest.mark.parametrize(
    ("question", "q", "ql"), [("invioce", "invioce", "invioce"), ("shindey", "shindey", "shindey")]
)
def test_a_long_misspelled_word_still_finds_its_piece_by_typo(
    db: Database, question: str, q: str, ql: str
) -> None:
    customer, _ = new_customer(db)
    invoice, shinde, _ = add_pieces(
        db,
        customer,
        add_document(db, customer),
        [("Tax invoice No 42", None), ("पाटील शिंदे", None), ("Rent paid for August", None)],
    )
    wanted = invoice if question == "invioce" else shinde

    hits, sent = _typo_runs(db, customer, question)

    assert [(each["q"], each["ql"]) for each in sent] == [(q, ql)]
    assert hits[0].piece_id == wanted and hits[0].fuzzy_score is not None and hits[0].fuzzy_score >= 0.3


def test_only_the_long_words_are_typo_checked(db: Database) -> None:
    """A misspelled long word with few exact matches runs the typo query, with that word only:
    never the short word, the stop word or the number around it. A Devanagari word counts its
    letters on its Latin spelling (शिंदे is shinde, 6)."""
    customer, _ = new_customer(db)
    invoice, shinde = add_pieces(
        db, customer, add_document(db, customer), [("Tax invoice No 42 map", None), ("पाटील शिंदे", None)]
    )

    hits, sent = _typo_runs(db, customer, "map of the invioce 42")
    assert [(each["q"], each["ql"]) for each in sent] == [("invioce", "invioce")]
    assert hits[0].piece_id == invoice and hits[0].fuzzy_score is not None

    hits, sent = _typo_runs(db, customer, "शिंदे")
    assert [(each["q"], each["ql"]) for each in sent] == [("शिंदे", "shinde")]
    assert hits[0].piece_id == shinde


@pytest.mark.parametrize("question", ["gat 123", "123", "INV-123"])
def test_numbers_never_run_the_typo_query_and_are_still_found_as_written(db: Database, question: str) -> None:
    customer, _ = new_customer(db)
    wanted, _ = add_pieces(
        db,
        customer,
        add_document(db, customer),
        [("Gat 123 INV-123 Kusgaon", None), ("Survey 1234 INV-1234", None)],
    )

    hits, sent = _typo_runs(db, customer, question)

    assert sent == []
    assert [hit.piece_id for hit in hits] == [wanted] and hits[0].word_score is not None


def test_a_word_that_fills_the_exact_list_never_runs_the_typo_query(db: Database) -> None:
    full, _ = new_customer(db)
    short, _ = new_customer(db)
    for customer, count in ((full, hybrid.CANDIDATES), (short, hybrid.CANDIDATES - 1)):
        pieces: list[tuple[str, list[float] | None]] = [(f"Agreement copy {n}", None) for n in range(count)]
        add_pieces(db, customer, add_document(db, customer), pieces)

    hits, sent = _typo_runs(db, full, "agreement")
    assert sent == [] and len(hits) == 10 and all(hit.word_score is not None for hit in hits)

    _, sent = _typo_runs(db, short, "agreement")  # the control: one short of a full list
    assert [each["q"] for each in sent] == ["agreement"]
