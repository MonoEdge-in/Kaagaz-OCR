"""Customer separation for what PRD 2 WP2.4 adds to pieces and documents: the
context header (which holds the file name and document text), the Latin
spelling and the languages. Customer B uses A's exact ids and sees nothing.
"""

import pytest

from kaagaz.db.engine import Database
from kaagaz.ingestion import sniff
from tests.db_support import new_customer
from tests.fakes import PresetSocket
from tests.support import PDF_BYTES
from tests.test_pieces_db import PIECES, devanagari_spans, process, rows

pytestmark = pytest.mark.db


def test_b_never_sees_a_headers_latin_spellings_or_languages(db: Database) -> None:
    a, _ = new_customer(db, name="Customer A")
    b, _ = new_customer(db, name="Customer B")
    socket = PresetSocket({sniff.PDF: devanagari_spans()})
    doc_a = process(db, a, PDF_BYTES, socket, name="a-private.pdf")
    doc_b = process(db, b, PDF_BYTES, socket, name="b-own.pdf")  # the same file, as B's own

    before = rows(db, a, PIECES, doc_a)
    assert rows(db, b, PIECES, doc_a) == []  # A's ids from B's side: nothing
    assert rows(db, b, "SELECT languages FROM documents WHERE id = :d", doc_a) == []
    b_headers = [p["context_header"] for p in rows(db, b, PIECES, doc_b)]
    assert b_headers and all(h.startswith("Document: b-own.pdf |") for h in b_headers)
    assert not any("a-private" in h for h in b_headers)
    assert rows(db, a, PIECES, doc_a) == before  # A is unchanged
    assert all(p["context_header"].startswith("Document: a-private.pdf |") for p in before)
