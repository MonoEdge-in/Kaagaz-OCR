"""Customer B never reads, writes, changes or deletes customer A's page cache (PRD 5 G7,
migration 0021; real PostgreSQL).

B uses A's exact document id and page key. The two locks hold on their own: the
``customer_only`` policy hides A's rows from B whatever B's statement says, and the
(customer_id, document_id) foreign key refuses a row of B's under A's document.
Marked ``isolation`` by its file name.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from kaagaz.db.engine import Database
from kaagaz.db.page_reads import PageKey, PgPageReads
from tests.db_support import count_rows, insert_document, new_customer, violation

pytestmark = pytest.mark.db

SHA = "0" * 64
RESULT = {"page": {"quality": "good"}, "lines": [{"order": 0, "text": "Total 1,180"}]}
_ALL_ROWS = "SELECT count(*) FROM page_reads WHERE document_id = :d"


def _a_with_a_cached_page(db: Database) -> tuple[str, str, str]:
    """(A, B, A's document with page 1 cached)."""
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    with db.customer(a) as conn:
        doc = insert_document(conn, a)
    PgPageReads(db).put(PageKey(a, doc, 1, SHA, "gemini-3.1-pro-preview", "g1"), RESULT, 10, 20)
    return a, b, doc


def _seen_by(db: Database, customer_id: str, document_id: str) -> int:
    with db.customer(customer_id) as conn:
        return count_rows(conn, _ALL_ROWS, {"d": document_id})


def test_another_customer_never_gets_a_cached_page(db: Database) -> None:
    a, b, doc = _a_with_a_cached_page(db)

    assert PgPageReads(db).get(PageKey(b, doc, 1, SHA, "gemini-3.1-pro-preview", "g1")) is None
    assert _seen_by(db, b, doc) == 0
    assert _seen_by(db, a, doc) == 1


def test_another_customer_cannot_put_a_page_under_a_document_not_theirs(db: Database) -> None:
    """B's put with A's document id and A's whole key: refused, A's row unchanged."""
    a, b, doc = _a_with_a_cached_page(db)

    with pytest.raises(DBAPIError):
        PgPageReads(db).put(PageKey(b, doc, 1, SHA, "gemini-3.1-pro-preview", "g1"), {"lines": []}, 0, 0)
    with pytest.raises(DBAPIError):  # a page A has not cached: the foreign key alone
        PgPageReads(db).put(PageKey(b, doc, 2, SHA, "gemini-3.1-pro-preview", "g1"), {"lines": []}, 0, 0)

    assert PgPageReads(db).get(PageKey(a, doc, 1, SHA, "gemini-3.1-pro-preview", "g1")) == (RESULT, 10, 20)
    assert _seen_by(db, a, doc) == 1


def test_the_composite_foreign_key_refuses_another_customers_document(db: Database) -> None:
    """Even a row B is allowed to write (its own customer_id) can't name A's document."""
    _, b, doc = _a_with_a_cached_page(db)

    with pytest.raises(IntegrityError) as refused, db.customer(b) as conn:
        conn.execute(
            text(
                "INSERT INTO page_reads (customer_id, document_id, page_no, image_sha256, model,"
                " prompt_version, result) VALUES (:c, :d, 2, :h, 'm', 'g1', '{}')"
            ),
            {"c": b, "d": doc, "h": SHA},
        )
    assert violation(refused.value)[0] == "23503"


def test_a_row_for_another_customer_is_refused_by_the_policy(db: Database) -> None:
    """B writing a row that says it is A's: WITH CHECK refuses it (42501)."""
    a, b, doc = _a_with_a_cached_page(db)

    with pytest.raises(DBAPIError) as refused, db.customer(b) as conn:
        conn.execute(
            text(
                "INSERT INTO page_reads (customer_id, document_id, page_no, image_sha256, model,"
                " prompt_version, result) VALUES (:c, :d, 3, :h, 'm', 'g1', '{}')"
            ),
            {"c": a, "d": doc, "h": SHA},
        )
    assert violation(refused.value)[0] == "42501"
    assert _seen_by(db, a, doc) == 1


def test_another_customer_can_neither_change_nor_delete_a_cached_page(db: Database) -> None:
    a, b, doc = _a_with_a_cached_page(db)

    with db.customer(b) as conn:
        changed = conn.execute(
            text("UPDATE page_reads SET result = '{}', input_tokens = 0 WHERE document_id = :d"), {"d": doc}
        )
        deleted = conn.execute(text("DELETE FROM page_reads WHERE document_id = :d"), {"d": doc})
        assert (changed.rowcount, deleted.rowcount) == (0, 0)

    assert PgPageReads(db).get(PageKey(a, doc, 1, SHA, "gemini-3.1-pro-preview", "g1")) == (RESULT, 10, 20)
