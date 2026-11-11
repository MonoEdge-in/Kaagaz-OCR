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
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

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
    typo = run(db, customer, "totl 1500000")
    assert [hit.piece_id for hit in typo] == [corrected] and typo[0].fuzzy_score is not None
    assert [hit.piece_id for hit in run(db, customer, "940000")] == [marathi]
    assert run(db, customer, "94000") == []


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
    with db.customer(customer) as conn:
        assert conn.execute(hybrid._WORD, words | {"g": ""}).all() == []
        assert len(conn.execute(hybrid._WORD, words | {"g": "कर"}).all()) == 1  # the control

    assert run(db, customer, "zzz") == []  # no keyword listed: :g is "", and nothing else matches


# --- the sound key list (council 3 Oct 2026, PR 2b-1) ----------------------------------


def by_sound(db: Database, customer_id: str, question: str, scope: Scope = EVERYTHING) -> list[str]:
    """The word statement with only its sound query able to match: the pieces it finds."""
    words = {"c": customer_id, "q": "zzz", "k": "zzz", "g": "", "sk": hybrid.sound_query(question), "n": 50}
    with db.customer(customer_id) as conn:
        return [str(row[0]) for row in conn.execute(hybrid._WORD, words | scope.params())]


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
    with db.customer(customer) as conn:
        assert conn.execute(hybrid._WORD, words | {"sk": ""}).all() == []
        assert len(conn.execute(hybrid._WORD, words | {"sk": "gikvd"}).all()) == 1  # the control


def _word_rows(db: Database, customer_id: str, question: str) -> list[tuple[str, float, bool]]:
    """(piece id, score, matched exactly) of the word statement for ``question``, in order."""
    words = {"c": customer_id, "q": question, "k": question, "g": "", "sk": hybrid.sound_query(question)}
    with db.customer(customer_id) as conn:
        rows = conn.execute(hybrid._WORD, words | {"n": hybrid.CANDIDATES} | EVERYTHING.params())
        return [(str(row[0]), float(row[2]), bool(row[3])) for row in rows]


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
