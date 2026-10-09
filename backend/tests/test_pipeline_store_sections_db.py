"""Sections and topics saved with the pieces, in the one replacing save (PRD 5 CX2 to CX4),
against a real PostgreSQL. Every save is followed by CX4's checks (``invariants``)."""

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.chunking.context import with_context
from kaagaz.chunking.lines import pieces_from_lines
from kaagaz.chunking.piece import Piece
from kaagaz.chunking.sections import MapBudget, Topic, map_document, sections_from_pieces
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.pipeline import DocumentContent, PageRecord
from kaagaz.reading.spans import BoxLocation, PageInfo, Span
from tests.db_support import new_customer
from tests.fakes import FakeChat
from tests.test_pipeline_store_db import new_document

pytestmark = pytest.mark.db

BUDGET = MapBudget(whole_tokens=120_000, context_tokens=200_000, topic_max=40)


def line(text_: str, page: int, top: float, kind: str | None = None) -> Span:
    return Span(text_, text_, BoxLocation(page, 0.1, top, 0.9, top + 0.02), kind=kind, origin="text_layer")


SPANS = [
    line("Offer of employment", 1, 0.1, "title"),
    line("This letter offers you the post of accountant.", 1, 0.13),
    line("Leave", 2, 0.1, "heading"),
    line("You get twelve public holidays a year.", 2, 0.13),
    line("Casual leave is eight days a year, and", 2, 0.9),
    line("it cannot be carried forward.", 3, 0.05),
    line("Notice", 4, 0.1, "heading"),
    line("The notice period is ninety days.", 4, 0.13),
]


def pieces_of(customer_id: str, document_id: str) -> list[Piece]:
    pieces = pieces_from_lines(customer_id, document_id, SPANS)
    return with_context(pieces, document_name="offer.pdf", doc_type="letter", document_date=None)


def mapped_content(customer_id: str, document_id: str, chat: FakeChat | None) -> DocumentContent:
    pieces = pieces_of(customer_id, document_id)
    found = map_document(chat, pieces, sections_from_pieces(pieces), BUDGET)
    return DocumentContent(
        customer_id,
        document_id,
        [PageRecord(PageInfo(n, 612.0, 792.0, "pt"), True) for n in range(1, 5)],
        [],
        pieces,
        [],
        reader="pdfplumber",
        sections=found.sections,
        topics=found.topics,
    )


def summaries(*texts: str) -> list[dict[str, Any]]:
    return [{"seq": seq, "summary": summary} for seq, summary in enumerate(texts)]


SUMMARIES = ("The offer.", "Holidays and casual leave.", "A notice period of ninety days.")
MAP_REPLY = {
    "sections": summaries(*SUMMARIES),
    "topics": [
        {"topic": "Leave", "aliases": ["Holidays", "Casual Leave", "छुट्टी"], "piece_seqs": [1, 2, 99]},
        {"topic": "bonus", "aliases": [], "piece_seqs": [42]},  # no such piece: dropped
    ],
}


def invariants(db: Database, customer_id: str, document_id: str) -> dict[str, list[dict[str, Any]]]:
    """CX4's checks after a save; returns the rows."""
    ids = {"c": customer_id, "d": document_id}
    where = " WHERE customer_id = :c AND document_id = :d"
    with db.customer(customer_id) as conn:
        found = {
            table: [dict(row) for row in conn.execute(text(sql + where + order), ids).mappings()]
            for table, sql, order in (
                (
                    "pieces",
                    "SELECT seq, page_from, page_to, regions, context_header FROM pieces",
                    " ORDER BY seq",
                ),
                (
                    "sections",
                    "SELECT seq, path, page_from, page_to, piece_seq_from, piece_seq_to, summary,"
                    " tsv::text AS tsv, embedding IS NULL AS no_vector FROM document_sections",
                    " ORDER BY seq",
                ),
                ("topics", "SELECT topic, aliases, piece_seqs FROM document_topics", " ORDER BY topic"),
            )
        }
    pieces = found["pieces"]
    assert [piece["seq"] for piece in pieces] == list(range(len(pieces)))  # 0 to n-1, no gaps (plan Q1)
    for piece in pieces:
        assert piece["page_from"] <= piece["page_to"]
        assert {region["page"] for region in piece["regions"]} == set(
            range(piece["page_from"], piece["page_to"] + 1)
        )
        assert piece["context_header"]
    seqs = {piece["seq"] for piece in pieces}
    assert [section["seq"] for section in found["sections"]] == list(range(len(found["sections"])))
    for section in found["sections"]:
        assert {section["piece_seq_from"], section["piece_seq_to"]} <= seqs
        assert section["no_vector"]  # vectors come after the save (kaagaz.search.indexing)
    for topic in found["topics"]:
        assert topic["piece_seqs"] and set(topic["piece_seqs"]) <= seqs
        assert topic["topic"] == topic["topic"].lower()
        assert all(alias == alias.casefold() for alias in topic["aliases"])
    return found


def test_cx4_sections_and_topics_are_saved_with_the_pieces(db: Database) -> None:
    customer, _ = new_customer(db)
    document = new_document(db, customer)
    PgPipelineStore(db).save(mapped_content(customer, document, FakeChat(MAP_REPLY)))

    found = invariants(db, customer, document)
    assert [
        (s["path"], s["page_from"], s["page_to"], s["piece_seq_from"], s["piece_seq_to"])
        for s in found["sections"]
    ] == [
        (["Offer of employment"], 1, 1, 0, 0),
        (["Offer of employment", "Leave"], 2, 3, 1, 2),
        (["Offer of employment", "Notice"], 4, 4, 3, 3),
    ]
    assert [s["summary"] for s in found["sections"]] == list(SUMMARIES)
    # The word index of the path and the summary, in the simple configuration (no stemming).
    assert "'ninety'" in found["sections"][2]["tsv"] and "'notice'" in found["sections"][2]["tsv"]
    assert found["topics"] == [
        {"topic": "leave", "aliases": ["holidays", "casual leave", "छुट्टी"], "piece_seqs": [1, 2]}
    ]


def test_cx4_a_read_again_replaces_the_map_and_never_doubles_it(db: Database) -> None:
    customer, _ = new_customer(db)
    document = new_document(db, customer)
    store = PgPipelineStore(db)
    store.save(mapped_content(customer, document, FakeChat(MAP_REPLY)))
    again = {"sections": summaries("Offer.", "Leave.", "Notice."), "topics": [MAP_REPLY["topics"][0]]}
    store.save(mapped_content(customer, document, FakeChat(again)))

    found = invariants(db, customer, document)
    assert [s["summary"] for s in found["sections"]] == ["Offer.", "Leave.", "Notice."]
    assert [t["topic"] for t in found["topics"]] == ["leave"]


def test_cx4_keyless_mode_saves_heading_only_sections_and_no_topics(db: Database) -> None:
    customer, _ = new_customer(db)
    document = new_document(db, customer)
    PgPipelineStore(db).save(mapped_content(customer, document, None))

    found = invariants(db, customer, document)
    assert [s["path"][-1] for s in found["sections"]] == ["Offer of employment", "Leave", "Notice"]
    assert [s["summary"] for s in found["sections"]] == [None, None, None]
    assert "'leave'" in found["sections"][1]["tsv"]  # the heading path alone is indexed
    assert found["topics"] == []


def test_cx4_a_bad_map_writes_nothing_and_keeps_the_earlier_save(db: Database) -> None:
    customer, _ = new_customer(db)
    document = new_document(db, customer)
    store = PgPipelineStore(db)
    good = mapped_content(customer, document, FakeChat(MAP_REPLY))
    store.save(good)
    bad = replace(good, topics=(Topic("leave", (), (9,)),))  # no piece 9

    with pytest.raises(ValueError, match="topic"):
        store.save(bad)
    found = invariants(db, customer, document)
    assert (len(found["pieces"]), len(found["sections"]), len(found["topics"])) == (4, 3, 1)


def test_cx4_another_customer_sees_no_section_or_topic(db: Database) -> None:
    customer, _ = new_customer(db)
    other, _ = new_customer(db)
    document = new_document(db, customer)
    PgPipelineStore(db).save(mapped_content(customer, document, FakeChat(MAP_REPLY)))

    seen = text(
        "SELECT (SELECT count(*) FROM document_sections WHERE document_id = :d)"
        " + (SELECT count(*) FROM document_topics WHERE document_id = :d)"
    )
    with db.customer(other) as conn:
        assert conn.execute(seen, {"d": document}).scalar_one() == 0
    with db.customer(customer) as conn:
        assert conn.execute(seen, {"d": document}).scalar_one() == 4  # the positive control
