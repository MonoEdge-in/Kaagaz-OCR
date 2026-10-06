"""Migration 0013 (PRD 4 #17): ``documents.ocr_choice``, a NULL text with its comment
and a CHECK on the id's shape (not a fixed list). Up, down (0012's schema exactly)
and up again; a stored choice round trips and a bad one is refused."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate, new_customer

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
_COLUMN = text(
    "SELECT format_type(atttypid, atttypmod), attnotnull, atthasdef, col_description(attrelid, attnum)"
    " FROM pg_attribute WHERE attrelid = 'public.documents'::regclass AND attname = 'ocr_choice'"
)


def test_0013_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0012")
    at_0012 = m0002._schema(database)
    migrate(url, "head")
    at_head = m0002._schema(database)
    with database.system() as conn:
        kind, not_null, has_default, comment = conn.execute(_COLUMN).one()
    assert (kind, not_null, has_default) == ("text", False, False)
    assert "NULL = Auto" in comment

    m0002._downgrade(url, "0012")
    assert m0002._schema(database) == at_0012
    with database.system() as conn:
        assert conn.execute(_COLUMN).all() == []
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def _record(customer_id: str, ocr_choice: str | None) -> DocumentRecord:
    return DocumentRecord(
        customer_id=customer_id,
        document_id=str(uuid.uuid4()),
        sha256=uuid.uuid4().hex * 2,
        size=10,
        detected_type="png",
        ocr_choice=ocr_choice,
    )


def test_a_choice_round_trips_and_a_bad_one_is_refused(db: Database) -> None:
    customer_id, _ = new_customer(db)
    documents = PgDocuments(db)
    chosen = _record(customer_id, "rapidocr")
    auto = _record(customer_id, None)
    assert documents.add_if_absent(chosen) == (chosen, True)
    documents.add_if_absent(auto)
    stored = documents.get(customer_id, chosen.document_id)
    assert stored is not None and stored.ocr_choice == "rapidocr"
    stored_auto = documents.get(customer_id, auto.document_id)
    assert stored_auto is not None and stored_auto.ocr_choice is None
    for bad in ("Rapid OCR", "", "x" * 41, "paddle-vl"):
        with pytest.raises(IntegrityError):
            documents.add_if_absent(_record(customer_id, bad))
