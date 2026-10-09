"""The page cache, ``PgPageReads`` (PRD 5 G7), against a real PostgreSQL.

A read is found only by its whole key; ``put`` saves at once, in its own transaction,
and replaces a read with the same key (an upsert, never a second row); the rows go with
their document; a bad row is refused before or by the database, never half written.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.documents import delete_forever
from kaagaz.db.engine import Database
from kaagaz.db.page_reads import PageKey, PgPageReads
from tests.db_support import count_rows, insert_document, new_customer
from tests.fakes import MemoryStorage

pytestmark = pytest.mark.db

MODEL = "google/gemini-3.1-pro-preview"
RESULT = {
    "page": {"quality": "good", "handwritten": False, "scripts": ["devanagari"], "issues": []},
    "lines": [{"order": 0, "kind": "paragraph", "text": "सर्वे नंबर [?]", "box_2d": [10, 20, 30, 40]}],
}
_ROWS = "SELECT count(*) FROM page_reads WHERE document_id = :d"


def key(
    customer_id: str,
    document_id: str,
    page_no: int = 1,
    *,
    image_sha256: str = "0" * 64,
    model: str = MODEL,
    prompt_version: str = "g1",
) -> PageKey:
    return PageKey(customer_id, document_id, page_no, image_sha256, model, prompt_version)


def new_document(db: Database) -> tuple[str, str]:
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        return customer, insert_document(conn, customer)


def rows_of(db: Database, customer_id: str, document_id: str) -> int:
    with db.customer(customer_id) as conn:
        return count_rows(conn, _ROWS, {"d": document_id})


def test_a_page_not_read_yet_is_a_miss(db: Database) -> None:
    customer, doc = new_document(db)
    assert PgPageReads(db).get(key(customer, doc)) is None


def test_a_saved_read_comes_back_with_its_token_counts(db: Database) -> None:
    customer, doc = new_document(db)
    cache = PgPageReads(db)
    cache.put(key(customer, doc), RESULT, 1700, 4000)

    assert cache.get(key(customer, doc)) == (RESULT, 1700, 4000)  # Devanagari and [?] kept as sent


@pytest.mark.parametrize(
    ("image_sha256", "model", "prompt_version"),
    [
        ("1" * 64, MODEL, "g1"),  # the page image changed (a new scan, another DPI)
        ("0" * 64, "google/gemini-3.8-flash", "g1"),  # OCR_MODEL changed
        ("0" * 64, MODEL, "g2"),  # the reading instructions changed
    ],
)
def test_any_other_image_model_or_prompt_version_is_a_miss(
    db: Database, image_sha256: str, model: str, prompt_version: str
) -> None:
    customer, doc = new_document(db)
    cache = PgPageReads(db)
    cache.put(key(customer, doc), RESULT, 1, 2)

    other = key(customer, doc, image_sha256=image_sha256, model=model, prompt_version=prompt_version)
    assert cache.get(other) is None
    assert cache.get(key(customer, doc, page_no=2)) is None


def test_a_page_read_again_replaces_its_row_and_never_adds_one(db: Database) -> None:
    """F5: the harder read is put with the same key, and wins."""
    customer, doc = new_document(db)
    cache = PgPageReads(db)
    cache.put(key(customer, doc), RESULT, 1700, 4000)
    written_at = text("SELECT created_at FROM page_reads WHERE document_id = :d")
    with db.customer(customer) as conn:
        written = conn.execute(written_at, {"d": doc}).scalar_one()
    harder = {**RESULT, "lines": []}

    cache.put(key(customer, doc), harder, 2800, 9000)

    assert cache.get(key(customer, doc)) == (harder, 2800, 9000)
    assert rows_of(db, customer, doc) == 1
    with db.customer(customer) as conn:
        assert conn.execute(written_at, {"d": doc}).scalar_one() >= written


def test_each_page_of_a_document_has_its_own_row(db: Database) -> None:
    customer, doc = new_document(db)
    cache = PgPageReads(db)
    for page_no in (1, 2, 3):
        cache.put(key(customer, doc, page_no), {**RESULT, "lines": [{"order": page_no}]}, page_no, 0)

    assert rows_of(db, customer, doc) == 3
    assert cache.get(key(customer, doc, 2)) == ({**RESULT, "lines": [{"order": 2}]}, 2, 0)


def test_the_reads_go_with_their_document(db: Database) -> None:
    """PRD 2's rule, kept: a deleted document is forgotten at once (ON DELETE CASCADE)."""
    customer, doc = new_document(db)
    with db.customer(customer) as conn:
        kept = insert_document(conn, customer)
    cache = PgPageReads(db)
    cache.put(key(customer, doc), RESULT, 1, 2)
    cache.put(key(customer, kept), RESULT, 1, 2)

    with db.customer(customer) as conn:
        delete_forever(conn, MemoryStorage(), customer, doc)

    assert rows_of(db, customer, doc) == 0
    assert cache.get(key(customer, doc)) is None
    assert cache.get(key(customer, kept)) == (RESULT, 1, 2)


def test_a_negative_token_count_is_refused_before_anything_is_written(db: Database) -> None:
    customer, doc = new_document(db)
    for counts in ((-1, 0), (0, -1)):
        with pytest.raises(ValueError, match="never negative"):
            PgPageReads(db).put(key(customer, doc), RESULT, *counts)
    assert rows_of(db, customer, doc) == 0


def test_a_missing_document_or_a_bad_key_is_refused_and_nothing_is_written(db: Database) -> None:
    customer, doc = new_document(db)
    cache = PgPageReads(db)
    with pytest.raises(IntegrityError):  # the (customer_id, document_id) foreign key
        cache.put(key(customer, str(uuid.uuid4())), RESULT, 1, 2)
    with pytest.raises(IntegrityError):  # not 64 lower-case hex digits
        cache.put(key(customer, doc, image_sha256="ABC"), RESULT, 1, 2)
    with pytest.raises(IntegrityError):  # pages count from 1
        cache.put(key(customer, doc, page_no=0), RESULT, 1, 2)
    assert rows_of(db, customer, doc) == 0
