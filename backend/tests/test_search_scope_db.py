"""The scope (PRD 3 Q1) and search inside documents (Q2, W3) on PostgreSQL. Every document holds
the same words, so only the scope decides what is found; each test has a positive control.
Pieces come from ``test_search_db.add_pieces`` (production's ``latin_spelling``)."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, event, text

from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.hybrid import DocumentResult, SearchHit, search, search_documents
from kaagaz.search.scope import EVERYTHING, Scope, ScopeBody, resolve
from tests.db_support import insert_document, insert_folder, insert_tag, new_customer, tag_document
from tests.test_search_db import EF_SEARCH, RECIPE, add_pieces, axis

pytestmark = pytest.mark.db

WORDS = "Invoice INV-9001 from Sharma Traders"
ZONE = "Asia/Kolkata"
# The three lists, each on its own: words, a typo, meaning only.
QUESTIONS = [("INV-9001", None), ("invioce sharma", None), ("", axis(0))]


def add(db: Database, customer_id: str, pieces: list[str] | None = None, **columns: Any) -> str:
    """A ready document with its pieces (each on axis 0); ``doc_type`` and
    ``document_date`` are set after the insert. Returns the document id."""
    understood = {"t": columns.pop("doc_type", None), "dd": columns.pop("document_date", None)}
    with db.customer(customer_id) as conn:
        document_id = insert_document(conn, customer_id, **columns)
        conn.execute(
            text(
                "UPDATE documents SET doc_type = :t, document_date = :dd WHERE customer_id = :c AND id = :d"
            ),
            {**understood, "c": customer_id, "d": document_id},
        )
    add_pieces(db, customer_id, document_id, [(piece, axis(0)) for piece in pieces or [WORDS]])
    return document_id


def run(
    conn: Connection,
    customer_id: str,
    scope: Scope,
    query: str,
    embedding: list[float] | None,
    most: int = 20,
) -> tuple[list[SearchHit], list[DocumentResult]]:
    """``search`` and ``search_documents``, both with this scope."""
    common: dict[str, Any] = {"customer_id": customer_id, "query_text": query, "embedding": embedding}
    common |= {"scope": scope, "recipe": RECIPE, "ef_search": EF_SEARCH}
    return search(conn, limit=100, **common), search_documents(conn, max_documents=most, **common)


def hits(
    db: Database,
    customer_id: str,
    body: dict[str, Any] | None = None,
    query: str = "INV-9001",
    embedding: list[float] | None = None,
    detected_type: str | None = None,
) -> list[SearchHit]:
    with db.customer(customer_id) as conn:
        body_ = ScopeBody.model_validate(body or {})
        scope = resolve(conn, customer_id, body_, timezone=ZONE, detected_type=detected_type)
        found, grouped = run(conn, customer_id, scope, query, embedding)
    assert {result.best.document_id for result in grouped} == {hit.document_id for hit in found}
    return found


def found(
    db: Database, customer_id: str, body: dict[str, Any] | None = None, kind: str | None = None
) -> set[str]:
    """The documents the scope lets through, the same in each of the three lists on its own."""
    each = [
        {hit.document_id for hit in hits(db, customer_id, body, *question, kind)} for question in QUESTIONS
    ]
    assert each[0] == each[1] == each[2], each
    return each[0]


def documents(db: Database, customer_id: str, query: str, most: int = 20) -> list[DocumentResult]:
    with db.customer(customer_id) as conn:
        return run(conn, customer_id, EVERYTHING, query, None, most)[1]


# --- each filter -----------------------------------------------------------------


def test_a_folder_holds_its_subfolders_only_with_include_subfolders(db: Database) -> None:
    """Edge case 2."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        parent = insert_folder(conn, customer, user, "Bills")
        child = insert_folder(conn, customer, user, "August", parent)
    in_parent, in_child, top = (
        add(db, customer, folder_id=parent),
        add(db, customer, folder_id=child),
        add(db, customer),
    )

    assert found(db, customer) == {in_parent, in_child, top}
    assert found(db, customer, {"folder_id": parent}) == {in_parent}
    assert found(db, customer, {"folder_id": parent, "include_subfolders": True}) == {in_parent, in_child}


def test_every_tag_asked_for_must_be_on_the_document(db: Database) -> None:
    customer, user = new_customer(db)
    both, gst_only, _ = add(db, customer), add(db, customer), add(db, customer)
    with db.customer(customer) as conn:
        gst, paid = insert_tag(conn, customer, user, "gst"), insert_tag(conn, customer, user, "paid")
        for document, tag in ((both, gst), (both, paid), (gst_only, gst)):
            tag_document(conn, customer, document, tag)

    assert found(db, customer, {"tag_ids": [gst]}) == {both, gst_only}
    assert found(db, customer, {"tag_ids": [gst, paid, gst]}) == {both}


def test_doc_types_is_what_the_document_is_not_its_file_type(db: Database) -> None:
    customer, _ = new_customer(db)
    invoice_photo = add(db, customer, doc_type="invoice", detected_type="png")
    receipt_pdf = add(db, customer, doc_type="receipt", detected_type="pdf")
    add(db, customer)  # not understood yet: no doc_type

    assert found(db, customer, {"doc_types": ["invoice"]}) == {invoice_photo}
    assert found(db, customer, {"doc_types": ["invoice", "receipt"]}) == {invoice_photo, receipt_pdf}
    assert found(db, customer, None, "png") == {invoice_photo}  # the list's Type filter (Q3): the file type


def test_document_ids_keep_only_those_documents(db: Database) -> None:
    customer, _ = new_customer(db)
    first, second, _ = add(db, customer), add(db, customer), add(db, customer)

    assert found(db, customer, {"document_ids": [first, second]}) == {first, second}


def test_dates_are_the_document_date_or_else_the_upload_day_in_the_customers_zone(db: Database) -> None:
    customer, _ = new_customer(db)
    sept, mid_march = datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 3, 12, 6, tzinfo=UTC)
    dated_march = add(db, customer, document_date=date(2026, 3, 10), created_at=sept)
    uploaded_march = add(db, customer, created_at=mid_march)
    add(db, customer, created_at=datetime(2026, 3, 31, 20, tzinfo=UTC))  # 1 April in India
    add(db, customer, document_date=date(2026, 5, 1), created_at=mid_march)

    march = {"date_from": "2026-03-01", "date_to": "2026-03-31"}
    assert found(db, customer, march) == {dated_march, uploaded_march}
    assert found(db, customer, {"date_to": "2026-03-10"}) == {dated_march}
    assert found(db, customer, {"date_from": "2026-03-12", "date_to": "2026-03-12"}) == {uploaded_march}


# --- the matches-nothing trap (edge case 1) ----------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"document_ids": []},
        {"tag_ids": []},
        {"doc_types": []},
        {"folder_id": str(uuid.uuid4())},  # no such folder
        {"tag_ids": ["real", str(uuid.uuid4())]},  # one real tag and one there is not
    ],
)
def test_a_scope_that_matches_nothing_finds_nothing_and_runs_no_search_sql(
    db: Database, body: dict[str, Any]
) -> None:
    customer, user = new_customer(db)
    document = add(db, customer)
    with db.customer(customer) as conn:
        real_tag = insert_tag(conn, customer, user, "gst")
        tag_document(conn, customer, document, real_tag)
    body = {
        key: [real_tag if v == "real" else v for v in value] if key == "tag_ids" else value
        for key, value in body.items()
    }

    assert found(db, customer) == {document}  # the control: it is there to find
    statements: list[str] = []
    with db.customer(customer) as conn:
        scope = resolve(conn, customer, ScopeBody.model_validate(body), timezone=ZONE)
        event.listen(conn, "before_cursor_execute", lambda *args: statements.append(args[2]))
        for query, embedding in QUESTIONS:
            assert run(conn, customer, scope, query, embedding) == ([], []), query

    assert scope.matches_nothing
    assert statements == []


def test_an_empty_array_in_the_sql_matches_nothing_too(db: Database) -> None:
    """Defence in depth: the statements never read an empty list as "no filter"."""
    customer, _ = new_customer(db)
    add(db, customer)
    words = {"c": customer, "q": "INV-9001", "k": "inv-9001", "g": "", "ql": "INV-9001", "n": 50}
    vector = {"c": customer, "vec": hybrid.vector_literal(axis(0)), "recipe": RECIPE, "n": 50}
    statements = [
        (hybrid._WORD, words),
        (hybrid._FUZZY, words),
        (hybrid._MEANING, vector),
        (hybrid._MEANING_EXACT, vector),
    ]
    with db.customer(customer) as conn:
        conn.execute(text("SELECT set_config('pg_trgm.word_similarity_threshold', '0.3', true)"))
        for empty in ("s_folders", "s_tags", "s_doc_types", "s_documents"):
            for statement, params in statements:
                assert conn.execute(statement, params | EVERYTHING.params()).all(), empty  # the control
                assert conn.execute(statement, params | EVERYTHING.params() | {empty: []}).all() == [], empty


# --- three writing forms (W3, edge case 6) -----------------------------------------


def test_patil_in_either_script_finds_the_devanagari_piece(db: Database) -> None:
    """PRD 3 W3's acceptance: "patil", "Patil" and "पाटील" find a piece whose text is "पाटील"."""
    customer, _ = new_customer(db)
    wanted = add(db, customer, ["पाटील"])
    add(db, customer, ["Rent for August"])

    for query in ("patil", "Patil", "पाटील"):
        assert [hit.document_id for hit in hits(db, customer, None, query)] == [wanted], query


def test_a_devanagari_question_finds_latin_letters_and_other_spellings(db: Database) -> None:
    """The simple word query takes the question's Latin spelling; the typo list
    meets पाटिल (short i) on the Latin column, where both are "patil"."""
    customer, _ = new_customer(db)
    latin = add(db, customer, ["Bill from Patil Traders"])
    other_spelling = add(db, customer, ["पाटिल"])
    add(db, customer, ["Rent for August"])

    by_document = {hit.document_id: hit for hit in hits(db, customer, None, "पाटील")}

    assert set(by_document) == {latin, other_spelling}
    assert by_document[latin].word_score is not None
    assert by_document[other_spelling].fuzzy_score == pytest.approx(1.0)


def test_a_devanagari_question_with_a_typo_is_still_found(db: Database) -> None:
    """Edge case 6: no word matches "पाटीक", the typo list still finds "पाटील"."""
    customer, _ = new_customer(db)
    document = add(db, customer, ["पाटील"])
    add(db, customer, ["Rent for August"])

    (hit,) = hits(db, customer, None, "पाटीक")

    assert hit.document_id == document
    assert hit.word_score is None
    assert hit.fuzzy_score is not None and hit.fuzzy_score >= 0.3


# --- grouped by document (Q2) ------------------------------------------------------


def test_results_are_grouped_by_document_capped_and_count_the_other_pieces(db: Database) -> None:
    """22 documents, 45 matching pieces (all inside the 50 candidates of each list)."""
    customer, _ = new_customer(db)
    three = add(db, customer, ["INV-9001 grand total", "INV-9001 grand tax", "INV-9001 grand seller"])
    for n in range(21):
        add(db, customer, [f"INV-9001 copy {n}", f"INV-9001 line {n}"])

    grouped = documents(db, customer, "INV-9001 grand")

    assert len({result.best.document_id for result in grouped}) == len(grouped) == 20
    assert grouped[0].best.document_id == three
    assert [result.more for result in grouped] == [2] + [1] * 19
    scores = [result.best.score for result in grouped]
    assert scores == sorted(scores, reverse=True)
    top3 = documents(db, customer, "INV-9001 grand", most=3)
    assert [result.best for result in top3] == [result.best for result in grouped[:3]]
    for bad in (0, 101):
        with pytest.raises(ValueError, match="max_documents"):
            documents(db, customer, "INV-9001", most=bad)


def test_the_best_piece_is_the_documents_best_ranked_piece(db: Database) -> None:
    customer, _ = new_customer(db)
    document = add(db, customer, ["Rent paid for August", "Invoice INV-9001 grand total"], doc_type="invoice")

    (result,) = documents(db, customer, "INV-9001 grand total")

    assert (result.best.document_id, result.best.doc_type, result.more) == (document, "invoice", 0)
    assert result.best.text == "Invoice INV-9001 grand total"


def test_with_no_dates_the_date_filter_is_not_in_the_plan(db: Database) -> None:
    """An always-on date filter shrinks the row estimate and can push meaning search off the index."""
    customer, _ = new_customer(db)
    vector = {"c": customer, "vec": hybrid.vector_literal(axis(0)), "recipe": RECIPE, "n": 50}
    with db.customer(customer) as conn:
        for dates, filtered in (({}, False), ({"s_from": date(2026, 3, 1)}, True)):
            bound = vector | EVERYTHING.params() | dates
            plan = "\n".join(conn.execute(text("EXPLAIN " + hybrid._MEANING.text), bound).scalars())
            assert ("document_date" in plan) is filtered, dates
