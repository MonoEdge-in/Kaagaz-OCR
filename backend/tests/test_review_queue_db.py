"""The review queue's two fixed statements against the one they replaced (A7a).

The old queue and count had one catch-all, ``(:doc IS NULL OR v.document_id = :doc)``.
Once psycopg prepares a statement (its fifth run), PostgreSQL may keep one generic
plan for both cases, and that plan cannot use the document id to find the values.
Two fixed statements, one for the whole queue and one for a document, each get
their own plan. They must list and count exactly what the old one did.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db import review
from kaagaz.db.engine import Database
from tests.db_support import insert_document, new_customer
from tests.test_answering_fixed_queries_db import add_values, cell

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

# The statements as they were before A7a, word for word.
_OLD_QUEUE = (
    "SELECT v.id, v.document_id, v.record_key, v.field, v.value_text, v.value_raw,"
    " v.second_reading, v.page_no, v.region, v.label, v.label_reason, v.confidence,"
    " v.label_page_no, v.label_region,"
    " d.display_name AS document_name, d.detected_type,"
    " p.width, p.height, p.unit, p.has_image, p.route, p.effective_dpi, p.low_resolution"
    " FROM extracted_values v"
    " JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id"
    " JOIN pages p ON p.customer_id = v.customer_id AND p.document_id = v.document_id"
    " AND p.page_no = v.page_no"
    " WHERE v.customer_id = :c"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready' AND v.label = 'needs_review'"
    " AND (CAST(:doc AS uuid) IS NULL OR v.document_id = CAST(:doc AS uuid))"
    " ORDER BY v.created_at, v.id LIMIT :limit OFFSET :offset"
)
_OLD_COUNT = (
    "SELECT count(*) FROM extracted_values v"
    " JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id"
    " JOIN pages p ON p.customer_id = v.customer_id AND p.document_id = v.document_id"
    " AND p.page_no = v.page_no"
    " WHERE v.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND v.label = 'needs_review'"
    " AND (CAST(:doc AS uuid) IS NULL OR v.document_id = CAST(:doc AS uuid))"
)


def test_no_review_statement_has_a_catch_all_for_the_document() -> None:
    for name in ("_QUEUE", "_COUNT", "_QUEUE_FOR_DOCUMENT", "_COUNT_FOR_DOCUMENT"):
        sql = getattr(review, name, "")
        assert sql, f"review.{name} is missing"
        assert "IS NULL OR" not in sql, name


def _needs_review(key: str, field: str, *, page: int = 1) -> dict[str, Any]:
    return {**cell(key, field, "x", label="needs_review"), "page": page}


def _with_pages(db: Database, customer: str, document: str, pages: int) -> None:
    with db.customer(customer) as conn:
        for page_no in range(1, pages + 1):
            conn.execute(
                text(
                    "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit)"
                    " VALUES (:c, :d, :p, 10, 10, 'cells')"
                ),
                {"c": customer, "d": document, "p": page_no},
            )


@pytest.mark.db
def test_the_queue_and_its_total_are_what_the_old_statement_gave(db: Database) -> None:
    customer, admin_user = new_customer(db)
    other, _ = new_customer(db)
    with db.customer(customer) as conn:
        first = insert_document(conn, customer)
        second = insert_document(conn, customer)
        binned = insert_document(conn, customer, deleted_at=NOW, deleted_by=admin_user)
        old = insert_document(conn, customer, is_current=False)
        waiting = insert_document(conn, customer, status="clean")
    with db.customer(other) as conn:
        elsewhere = insert_document(conn, other)
    for document in (first, second, binned, old, waiting):
        _with_pages(db, customer, document, 2)
    _with_pages(db, other, elsewhere, 1)
    add_values(db, customer, first, [_needs_review(f"r{n}", "a", page=1 + n % 2) for n in range(5)])
    add_values(db, customer, first, [cell("r9", "b", "x"), _needs_review("r8", "c", page=3)])  # no page 3
    add_values(db, customer, second, [_needs_review(f"r{n}", "d", page=2) for n in range(3)])
    for document in (binned, old, waiting):
        add_values(db, customer, document, [_needs_review("r1", "e")])
    add_values(db, other, elsewhere, [_needs_review("r1", "f")])

    cases = 0
    for document in (None, first, second, binned, old, waiting, elsewhere):
        for limit, offset in ((50, 0), (2, 0), (2, 3), (1, 7)):
            params = {"c": customer, "doc": document, "limit": limit, "offset": offset}
            with db.customer(customer) as conn:
                rows, total = review.list_queue(
                    conn, customer, limit=limit, offset=offset, document_id=document
                )
                old_rows = conn.execute(text(_OLD_QUEUE), params).mappings().all()
                old_total = conn.execute(text(_OLD_COUNT), params).scalar_one()
            assert [dict(row) for row in rows] == [dict(row) for row in old_rows], (document, limit, offset)
            assert total == old_total
            cases += bool(old_rows)
    assert cases >= 5  # the comparison was made on real rows, not only on empty pages
    with db.customer(customer) as conn:
        _, everything = review.list_queue(conn, customer, limit=50, offset=0, document_id=None)
        _, of_first = review.list_queue(conn, customer, limit=50, offset=0, document_id=first)
    assert (everything, of_first) == (8, 5)  # the value on page 3 has no page, so it is left out
