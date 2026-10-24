"""Your words on the page (package H): GET /documents/{id}/words against a real database.
The lines of every page that hold the words, by the four ways (same word, Latin spelling,
sound, near spelling), and every page with one; one statement on an index as the app role.
Customer isolation is in tests/test_isolation_api_page_words.py."""

import json
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.chunking.script import is_devanagari, latin_spelling
from kaagaz.db import listing
from kaagaz.db.engine import Database
from tests.test_api_support import NOW, Api, Signed

pytestmark = pytest.mark.db


def box(page: int, top: float) -> dict[str, Any]:
    return {"kind": "box", "page": page, "box": [0.1, top, 0.9, top + 0.03]}


def add_pieces(db: Database, customer_id: str, document_id: str, pieces: list[tuple[str, list[Any]]]) -> None:
    """(text, regions) pieces in seq order, with the Latin spelling the pipeline stores."""
    with db.customer(customer_id) as conn:
        for seq, (piece_text, regions) in enumerate(pieces):
            pages = [region["page"] for region in regions]
            conn.execute(
                text(
                    "INSERT INTO pieces (customer_id, document_id, seq, page_from, page_to, text, regions,"
                    " label, text_latin) VALUES (:c, :d, :seq, :lo, :hi, :t, CAST(:r AS jsonb),"
                    " 'needs_review', :latin)"
                ),
                {
                    "c": customer_id,
                    "d": document_id,
                    "seq": seq,
                    "lo": min(pages),
                    "hi": max(pages),
                    "t": piece_text,
                    "r": json.dumps(regions),
                    "latin": latin_spelling(piece_text) if is_devanagari(piece_text) else None,
                },
            )


def sale_deed(api: Api, customer_id: str) -> str:
    """Three pages: an English page, a Marathi page and a page with neither word."""
    document_id = api.add_document(customer_id, detected_type="png", name="sale-deed.png", page_count=3)
    add_pieces(
        api.db,
        customer_id,
        document_id,
        [
            (
                "Sale deed\nBuyer: Krishna Sohoni\nSeller: Nikhil Varde",
                [box(1, 0.1), box(1, 0.2), box(1, 0.3)],
            ),
            ("Village: Maval", [box(1, 0.5)]),
            ("खरेदीदार कृष्ण सोहोनी\nगाव पुणे", [box(2, 0.1), box(2, 0.2)]),
            ("Stamp duty paid", [box(3, 0.1)]),
        ],
    )
    return document_id


def words(signed: Signed, document_id: str, *typed: str) -> dict[str, Any]:
    query = "&".join(f"w={word}" for word in typed)
    reply = signed.client.get(f"/api/documents/{document_id}/words?{query}")
    assert reply.status_code == 200, reply.text
    body: dict[str, Any] = reply.json()
    return body


def test_every_pages_lines_holding_the_words_and_every_page_with_one(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    deed = sale_deed(api, signed.customer_id)

    # Latin and Devanagari: "krishna" lights कृष्ण on page 2; page 3 holds none.
    found = words(signed, deed, "krishna", "maval")
    assert found["boxes"] == [box(1, 0.2), box(1, 0.5), box(2, 0.1)]
    assert found["pages"] == [1, 2]
    # Changed on purpose (24 Oct 2026): which word lit each line (its colour), and each word's
    # lines and pages; the asker's own words come back, never the document's text.
    assert found["hits"] == [0, 1, 0]
    assert found["found"] == [
        {"word": "krishna", "lines": 2, "pages": [1, 2]},
        {"word": "maval", "lines": 1, "pages": [1]},
    ]


@pytest.mark.parametrize(
    ("typed", "page", "lit"),
    [
        ("KRISHNA", 1, [box(1, 0.2)]),  # the same word, any case
        ("कृष्ण", 1, [box(1, 0.2)]),  # the same Latin spelling, Devanagari typed
        ("krishana", 2, [box(2, 0.1)]),  # the same sound
        ("sohonee", 1, [box(1, 0.2)]),  # a near spelling
        ("krishnamurthy", 1, []),  # never a longer name
        ("mohan", 1, []),
    ],
)
def test_each_way_a_word_lights_a_line(db: Database, typed: str, page: int, lit: list[Any]) -> None:
    api = Api(db)
    signed = api.new_signed()
    deed = sale_deed(api, signed.customer_id)
    assert [region for region in words(signed, deed, typed)["boxes"] if region["page"] == page] == lit


def test_more_than_eight_words_is_refused_and_no_words_light_nothing(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    deed = sale_deed(api, signed.customer_id)
    nine = "&".join(f"w=word{n}" for n in range(9))
    assert signed.client.get(f"/api/documents/{deed}/words?{nine}").status_code == 400
    assert words(signed, deed, "the", "kiti") == {"boxes": [], "hits": [], "pages": [], "found": []}


@pytest.mark.parametrize(
    ("columns", "status", "code"),
    [({"deleted_at": NOW}, 409, "in_trash"), ({"status": "rejected"}, 404, "not_found")],
)
def test_a_document_in_trash_or_rejected_is_refused_as_its_page_image_is(
    db: Database, columns: dict[str, Any], status: int, code: str
) -> None:
    api = Api(db)
    signed = api.new_signed()
    document_id = api.add_document(signed.customer_id, detected_type="png", **columns)
    add_pieces(db, signed.customer_id, document_id, [("Buyer: Krishna Sohoni", [box(1, 0.2)])])
    reply = signed.client.get(f"/api/documents/{document_id}/words?w=krishna")
    assert reply.status_code == status
    assert reply.json()["error"]["code"] == code
    assert "0.2" not in reply.text


def test_one_statement_on_the_pieces_index_as_the_app_role(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    deed = sale_deed(api, signed.customer_id)
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        bound = {"c": signed.customer_id, "d": deed}
        plan = "\n".join(conn.execute(text("EXPLAIN " + listing._WORD_LINES.text), bound).scalars())
    assert "Seq Scan" not in plan, plan
    assert "on pieces" in plan and "Index" in plan, plan
