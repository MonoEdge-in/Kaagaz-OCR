"""PRD 5's reads for answers on real PostgreSQL (CX5, CX6, M2): whole documents, topic and
section pieces, and cards, through ``kaagaz.search.hybrid``.

Sections and topics are written here the way the replacing save writes them (migration
0021): a section's ``tsv`` is ``to_tsvector('simple', path joined + ' ' + summary)`` and
its vector is tagged like a piece's; topic aliases are casefolded. Another customer's rows
are in test_isolation_context_db.py.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, text

from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.hybrid import vector_literal
from kaagaz.search.scope import EVERYTHING, Scope
from tests.db_support import new_customer
from tests.test_search_db import RECIPE, add_document, add_pieces, axis

pytestmark = pytest.mark.db

OFFER = [
    "Leave policy: how many leaves you get is set out below.",
    "Holidays: 12 paid holidays a year.",
    "Salary: Rs. 50,000 a month.",
    "Casual leave: 8 days a year.",
    "Maternity leave: 26 weeks.",
]
SECTION = (
    "INSERT INTO document_sections (customer_id, document_id, seq, path, page_from, page_to,"
    " piece_seq_from, piece_seq_to, summary, tsv, embedding, embed_recipe)"
    " VALUES (:c, :d, :n, CAST(:path AS text[]), 1, 1, :lo, :hi, :summary,"
    " to_tsvector('simple', array_to_string(CAST(:path AS text[]), ' ') || ' ' || :summary),"
    " CAST(:v AS halfvec(1024)), :r)"
)
TOPIC = (
    "INSERT INTO document_topics (customer_id, document_id, topic, aliases, piece_seqs)"
    " VALUES (:c, :d, :topic, CAST(:aliases AS text[]), CAST(:seqs AS integer[]))"
)


def _bin(conn: Connection, customer_id: str, document_id: str) -> None:
    """Move a document to Trash."""
    conn.execute(
        text("UPDATE documents SET deleted_at = :t WHERE customer_id = :c AND id = :d"),
        {"t": datetime.now(UTC), "c": customer_id, "d": document_id},
    )


def _offer(db: Database, customer_id: str, name: str = "offer.pdf") -> tuple[str, list[str]]:
    document = add_document(db, customer_id, name=name)
    return document, add_pieces(db, customer_id, document, [(each, None) for each in OFFER])


def _topic(
    db: Database, customer_id: str, document: str, topic: str, aliases: list[str], seqs: list[int]
) -> None:
    with db.customer(customer_id) as conn:
        params = {"c": customer_id, "d": document, "topic": topic, "aliases": aliases, "seqs": seqs}
        conn.execute(text(TOPIC), params)


def _section(
    db: Database,
    customer_id: str,
    document: str,
    n: int,
    pieces: tuple[int, int],
    summary: str,
    vector: list[float] | None = None,
) -> None:
    with db.customer(customer_id) as conn:
        conn.execute(
            text(SECTION),
            {
                "c": customer_id,
                "d": document,
                "n": n,
                "path": [f"Part {n}"],
                "lo": pieces[0],
                "hi": pieces[1],
                "summary": summary,
                "v": None if vector is None else vector_literal(vector),
                "r": None if vector is None else RECIPE,
            },
        )


def test_cx5_whole_gives_every_piece_in_reading_order_and_skips_a_big_scope(db: Database) -> None:
    customer, _ = new_customer(db)
    first, _ = _offer(db, customer, "first.pdf")
    second = add_document(db, customer, name="second.pdf")
    add_pieces(db, customer, second, [("Notice period: 30 days.", None)])
    binned = add_document(db, customer, name="binned.pdf")
    add_pieces(db, customer, binned, [("Bonus: 99 days.", None)])
    with db.customer(customer) as conn:
        _bin(conn, customer, binned)
        found = hybrid.whole(conn, customer_id=customer, scope=EVERYTHING, max_bytes=10_000)
        only_first = hybrid.whole(
            conn, customer_id=customer, scope=Scope(document_ids=(first,)), max_bytes=10_000
        )
        too_big = hybrid.whole(conn, customer_id=customer, scope=EVERYTHING, max_bytes=100)

    assert found is not None and [hit.text for hit in found] == [*OFFER, "Notice period: 30 days."]
    assert only_first is not None and [hit.seq for hit in only_first] == [0, 1, 2, 3, 4]
    assert too_big is None


def test_cx5_the_size_check_takes_an_exact_fit_and_refuses_one_byte_less(db: Database) -> None:
    """``_WHOLE_SIZE`` stops at the first piece past the limit (a running sum), so it answers
    "more than max_bytes" without summing the whole scope; the edge is exact."""
    customer, _ = new_customer(db)
    document = add_document(db, customer, name="one.pdf")
    add_pieces(db, customer, document, [("Casual leave: 8 days.", None), ("Notice: 30 days.", None)])
    size_sql = text(
        "SELECT sum(octet_length(text) + octet_length(context_header)) FROM pieces WHERE customer_id = :c"
    )
    with db.customer(customer) as conn:
        size = int(conn.execute(size_sql, {"c": customer}).scalar_one())
        exact = hybrid.whole(conn, customer_id=customer, scope=EVERYTHING, max_bytes=size)
        short = hybrid.whole(conn, customer_id=customer, scope=EVERYTHING, max_bytes=size - 1)
        first_only = hybrid.whole(conn, customer_id=customer, scope=EVERYTHING, max_bytes=1)
        nothing = hybrid.whole(
            conn, customer_id=customer, scope=Scope(document_ids=(str(uuid.uuid4()),)), max_bytes=size
        )

    assert exact is not None and [hit.seq for hit in exact] == [0, 1]
    assert short is None and first_only is None and nothing is None


def test_cx6_topic_pieces_stay_inside_each_document_and_are_capped(db: Database) -> None:
    customer, _ = new_customer(db)
    mine, my_pieces = _offer(db, customer, "mine.pdf")
    theirs, their_pieces = _offer(db, customer, "staff-rules.pdf")
    _topic(db, customer, mine, "leave", ["holidays", "casual leave", "maternity leave"], [1, 3, 4])
    _topic(db, customer, theirs, "pay", ["salary"], [2])
    _topic(db, customer, theirs, "leave", ["sick leave"], [3])
    with db.customer(customer) as conn:
        by_name = hybrid.topic_pieces(conn, customer_id=customer, terms=["leave"], hits=[], per_document=12)
        capped = hybrid.topic_pieces(conn, customer_id=customer, terms=["holidays"], hits=[], per_document=2)
        hit = hybrid.whole(conn, customer_id=customer, scope=Scope(document_ids=(theirs,)), max_bytes=10_000)
        assert hit is not None
        by_hit = hybrid.topic_pieces(conn, customer_id=customer, terms=[], hits=[hit[2]], per_document=12)

    assert {(h.document_id, h.seq) for h in by_name} == {(mine, 1), (mine, 3), (mine, 4), (theirs, 3)}
    assert [(h.document_id, h.seq) for h in capped] == [(mine, 1), (mine, 3)]
    assert by_hit == []  # its own topic's only piece is the hit itself, never another document's
    assert my_pieces and their_pieces


def test_cx6_section_pieces_match_words_first_then_meaning(db: Database) -> None:
    customer, _ = new_customer(db)
    document, _ = _offer(db, customer)
    _section(db, customer, document, 0, (0, 1), "Leave and holidays.", axis(0))
    _section(db, customer, document, 1, (2, 2), "Pay and salary.", axis(1))
    _section(db, customer, document, 2, (3, 4), "Casual and maternity leave.", axis(2))
    with db.customer(customer) as conn:
        worded = hybrid.section_pieces(
            conn, customer_id=customer, keywords=["salary"], embedding=None, recipe=RECIPE, hits=[]
        )
        meant = hybrid.section_pieces(
            conn, customer_id=customer, keywords=["salary"], embedding=axis(2), recipe=RECIPE, hits=[]
        )
        other_recipe = hybrid.section_pieces(
            conn, customer_id=customer, keywords=["nothing"], embedding=axis(2), recipe="other#ctx1", hits=[]
        )

    assert [h.seq for h in worded] == [2]
    assert [h.seq for h in meant] == [2, 3, 4]  # the word match first, then the nearest summary
    assert other_recipe == []


def test_m2_cards_of_current_documents_out_of_trash_only(db: Database) -> None:
    customer, _ = new_customer(db)
    live = add_document(db, customer, name="offer.pdf")
    binned = add_document(db, customer, name="old.pdf")
    card = {"title": "Offer of employment", "doc_type": "offer_letter"}
    with db.customer(customer) as conn:
        conn.execute(
            text("UPDATE documents SET card = CAST(:card AS jsonb) WHERE customer_id = :c"),
            {"c": customer, "card": json.dumps(card)},
        )
        _bin(conn, customer, binned)
        found = hybrid.cards(conn, customer_id=customer, document_ids=[live, binned])

    assert found == {live: ("offer.pdf", card)}
