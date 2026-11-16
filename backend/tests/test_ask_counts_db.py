"""Every document holding a word, on PostgreSQL (Tushar, 16 Oct 2026; kaagaz/answering/
exhaustive.py): whole words in any case, the scope, current ready documents out of Trash,
one customer only; the answer end to end with no model; and the search's order on ties the
same every run (steady answers)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.answering.exhaustive import PgExhaustive
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from kaagaz.search.hybrid import search
from kaagaz.search.scope import EVERYTHING, Scope
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_search_db import EF_SEARCH, RECIPE, add_pieces, axis

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 16, 6, 30, tzinfo=UTC)


def add(db: Database, customer_id: str, name: str, *texts: str, **columns: object) -> str:
    with db.customer(customer_id) as conn:
        document_id = insert_document(conn, customer_id, display_name=name, **columns)
    add_pieces(db, customer_id, document_id, [(piece, axis(0)) for piece in texts])
    return document_id


def count(
    db: Database, customer_id: str, term: str, scope: Scope = EVERYTHING, listed: int = 50
) -> tuple[int, list[str]]:
    total, documents = PgExhaustive(db).documents_with(customer_id, term, scope, listed, user_id=None)
    return total, [hits[0].document_name for hits in documents]


def test_every_document_with_the_word_counts_once_in_any_case_and_no_other(db: Database) -> None:
    customer, _ = new_customer(db)
    add(db, customer, "sale-deed.pdf", "Village Jalna, taluka Ambad", "Registered at JALNA.")
    add(db, customer, "lease.pdf", "Shop on jalna road")
    add(db, customer, "sweets.pdf", "Jalnawala sweets")  # another word
    add(db, customer, "bill.pdf", "Sharma Traders, Pune")

    assert count(db, customer, "Jalna") == (2, ["lease.pdf", "sale-deed.pdf"])
    assert count(db, customer, "jalna road") == (1, ["lease.pdf"])  # a phrase, word by word
    assert count(db, customer, "Nagpur") == (0, [])


def test_a_devanagari_word_counts_its_script_and_its_latin_spelling(db: Database) -> None:
    # 16 Oct 2026: जालना also matches its stored Latin spelling, "jalana" (text_latin's
    # recipe keeps the middle vowels). "Jalna" is another spelling: each counts its own
    # documents, as the exact word index holds them (exhaustive.py's module docstring).
    customer, _ = new_customer(db)
    add(db, customer, "satbara.pdf", "गाव जालना, तालुका अंबड")
    add(db, customer, "typed.pdf", "Village jalana, taluka Ambad")
    add(db, customer, "lease.pdf", "Shop on Jalna road")
    add(db, customer, "sweets.pdf", "जालनावाला मिठाई")  # another word

    assert count(db, customer, "जालना") == (2, ["satbara.pdf", "typed.pdf"])
    assert count(db, customer, "Jalna") == (1, ["lease.pdf"])
    service = AnswerService(
        db=db, model=None, settings=make_settings(answer_max_evidence=5), clock=lambda: NOW
    )
    answer = service.answer(customer, "कितने दस्तावेज़ों में जालना है?")
    assert answer.sentences[0].text == '2 documents mention "जालना".'


def test_trash_older_versions_and_unread_documents_are_never_counted(db: Database) -> None:
    customer, _ = new_customer(db)
    add(db, customer, "live.pdf", "Jalna")
    add(db, customer, "trashed.pdf", "Jalna", deleted_at=NOW)
    add(db, customer, "old.pdf", "Jalna", is_current=False)
    add(db, customer, "reading.pdf", "Jalna", status="clean")

    assert count(db, customer, "Jalna") == (1, ["live.pdf"])


def test_another_customers_documents_are_never_counted(db: Database) -> None:
    mine, _ = new_customer(db)
    theirs, _ = new_customer(db)
    add(db, mine, "mine.pdf", "Jalna")
    for i in range(3):
        add(db, theirs, f"theirs-{i}.pdf", "Jalna")

    assert count(db, mine, "Jalna") == (1, ["mine.pdf"])
    assert count(db, theirs, "Jalna")[0] == 3


def test_the_scope_holds_and_the_total_counts_past_the_list(db: Database) -> None:
    customer, _ = new_customer(db)
    ids = [add(db, customer, f"file-{i}.pdf", f"Plot {i}, Jalna") for i in range(5)]

    assert count(db, customer, "Jalna", listed=2) == (5, ["file-0.pdf", "file-1.pdf"])
    assert count(db, customer, "Jalna", Scope(document_ids=tuple(ids[3:]))) == (
        2,
        ["file-3.pdf", "file-4.pdf"],
    )


def test_the_answer_counts_every_document_with_no_model(db: Database) -> None:
    customer, _ = new_customer(db)
    for i in range(12):  # more than ANSWER_MAX_EVIDENCE: top hits alone would under-count
        add(db, customer, f"deed-{i:02}.pdf", f"Survey {i}, village Jalna")
    add(db, customer, "bill.pdf", "Sharma Traders, Pune")
    service = AnswerService(
        db=db, model=None, settings=make_settings(answer_max_evidence=5), clock=lambda: NOW
    )

    answer = service.answer(customer, "How many documents mention Jalna?")
    assert answer.sentences[0].text == '12 documents mention "Jalna".'
    assert len(answer.citations) == 12 and answer.route == "fixed_query"
    assert answer.sentences[0].citations == []  # the count sums up the lines, each citing one
    assert [s.citations for s in answer.sentences[1:]] == [[n] for n in range(1, 13)]


def test_the_search_order_on_tied_pieces_is_the_same_every_run(db: Database) -> None:
    customer, _ = new_customer(db)
    for name in ("b.pdf", "a.pdf", "c.pdf"):
        add(db, customer, name, "Invoice INV-7001 from Patil Traders")

    def once() -> list[tuple[str, int]]:
        with db.customer(customer) as conn:
            found = search(
                conn,
                customer_id=customer,
                query_text="INV-7001 Patil",
                embedding=axis(0),
                limit=10,
                recipe=RECIPE,
                ef_search=EF_SEARCH,
            )
        return [(hit.document_id, hit.seq) for hit in found]

    first = once()
    assert len(first) == 3
    assert all(once() == first for _ in range(3))


def test_the_manual_count_query_matches(db: Database) -> None:
    """The query in the report for Tushar, counting as the route does."""
    customer, _ = new_customer(db)
    add(db, customer, "a.pdf", "Jalna")
    add(db, customer, "b.pdf", "JALNA road")
    add(db, customer, "c.pdf", "Jalnawala")
    with db.customer(customer) as conn:
        manual: int = conn.execute(
            text(
                "SELECT count(DISTINCT p.document_id) FROM pieces p"
                " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
                " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
                " AND p.tsv_simple @@ phraseto_tsquery('simple', 'Jalna')"
            ),
            {"c": customer},
        ).scalar_one()
    assert manual == count(db, customer, "Jalna")[0] == 2


def test_a_type_before_documents_of_counts_only_that_type(db: Database) -> None:
    # Tushar, 17 Oct 2026: "how many land document of jalna i have" went to the model and got 503.
    customer, _ = new_customer(db)
    add(db, customer, "7-12.pdf", "Village Jalna", doc_type="land_record")
    add(db, customer, "8a.pdf", "Taluka Jalna", doc_type="land_record")
    add(db, customer, "bill.pdf", "Jalna depot", doc_type="invoice")
    service = AnswerService(
        db=db, model=None, settings=make_settings(answer_max_evidence=5), clock=lambda: NOW
    )

    answer = service.answer(customer, "how many land document of jalna i have")
    assert answer.sentences[0].text == '2 land records mention "jalna".'
    assert {c.document_name for c in answer.citations} == {"7-12.pdf", "8a.pdf"}
    assert (
        service.answer(customer, "how many documents of jalna").sentences[0].text
        == '3 documents mention "jalna".'
    )


def mentioned(
    db: Database, customer_id: str, terms: list[str], exclude: tuple[str, ...] = (), scope: Scope = EVERYTHING
) -> tuple[int, list[str]]:
    total, rows = PgExhaustive(db).mentions(customer_id, terms, scope, exclude, 50, user_id=None)
    return total, [row["document_name"] for row in rows]


def test_also_mentions_unions_the_terms_most_matching_first_and_leaves_out_the_cited(db: Database) -> None:
    customer, user = new_customer(db)
    cited = add(db, customer, "cited.pdf", "Jalna")
    add(db, customer, "deed.pdf", "Village Jalna", "Registered at JALNA", "District Jalna")
    add(db, customer, "satbara.pdf", "गाव दरेगांव\nजिल्हा जालना")
    add(db, customer, "lease.pdf", "Shop at Jalna road")
    add(db, customer, "sweets.pdf", "Jalnawala sweets")  # another word
    add(db, customer, "trashed.pdf", "Jalna", deleted_at=NOW)

    total, names = mentioned(db, customer, ["Jalna", "जालना"], (cited,))
    assert (total, names) == (3, ["deed.pdf", "lease.pdf", "satbara.pdf"])  # 3 pieces first, then by name
    _, rows = PgExhaustive(db).mentions(customer, ["जालना"], EVERYTHING, [], 50, user_id=user)
    assert rows[0]["page_no"] == 1 and rows[0]["pages"] == [1]
    assert rows[0]["snippet"] == "जिल्हा जालना"
    assert mentioned(db, customer, ["Jalna"], (), Scope(document_ids=(cited,))) == (1, ["cited.pdf"])
    assert mentioned(db, customer, ["Nagpur"]) == (0, [])


def test_also_mentions_never_lists_another_customers_documents(db: Database) -> None:
    mine, _ = new_customer(db)
    theirs, _ = new_customer(db)
    add(db, mine, "mine.pdf", "Jalna")
    for i in range(3):
        add(db, theirs, f"theirs-{i}.pdf", "Jalna")

    assert mentioned(db, mine, ["Jalna"]) == (1, ["mine.pdf"])
    assert mentioned(db, theirs, ["Jalna"])[0] == 3


def test_an_answer_carries_also_mentions_past_its_sources(db: Database) -> None:
    customer, _ = new_customer(db)
    for i in range(8):  # more than ANSWER_MAX_EVIDENCE: the search alone shows a few
        add(db, customer, f"deed-{i}.pdf", f"Survey {i}, owner Patil, village Jalna")
    service = AnswerService(
        db=db, model=None, settings=make_settings(answer_max_evidence=3), clock=lambda: NOW
    )

    data = service.answer(customer, "Who is the owner in Jalna?").to_json()
    shown = {c["document_id"] for c in data["citations"]} | {
        d["document_id"] for d in data["related_documents"]
    }
    assert data["mentions"]["terms"] == ["Jalna"]
    assert data["mentions"]["total"] == 8 - len(shown)
    assert not shown & {d["document_id"] for d in data["mentions"]["documents"]}
