"""The library helpers, called by customer B with customer A's exact ids.

``kaagaz.db.tags`` and ``trash`` must give B "not found" (the 404 the API will
show) or False, and leave A unchanged. Each test passes while either lock holds
(the helper's own customer filter or row level security), so each one also
checks both customers' rows afterwards. The folder helpers have their own file,
test_isolation_folder_helpers.py.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.db.tags import TagNotFound, delete_tag, list_tags, set_document_tags, tags_for_documents
from kaagaz.db.trash import lock_family, purge_next_family, restore_document, trash_document
from tests.db_support import (
    insert_tag,
    library_rows_seen,
    new_customer,
    new_library,
    new_trashed_document,
)

pytestmark = pytest.mark.db


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _tag_ids(db: Database, customer_id: str, document_id: str) -> list[str]:
    with db.customer(customer_id) as conn:
        return [row.id for row in tags_for_documents(conn, customer_id, [document_id])[document_id]]


def test_set_document_tags_with_another_customer_tag_or_document_is_not_found(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    with db.customer(b["customer"]) as conn:
        b_spare = insert_tag(conn, b["customer"], b["user"], "Spare")
    attempts: tuple[tuple[str, list[str]], ...] = (
        (b["document"], [a["tag"]]),  # B's document, A's tag
        (b["document"], [b_spare, a["tag"]]),  # one of each: nothing may be applied
        (a["document"], [b_spare]),  # A's document, B's tag
        (a["document"], []),  # clearing A's document
    )
    for document_id, tag_ids in attempts:
        with pytest.raises(TagNotFound) as caught, db.customer(b["customer"]) as conn:
            set_document_tags(conn, b["customer"], document_id, tag_ids)
        assert caught.value.code == "not_found", (document_id, tag_ids)

    assert _tag_ids(db, a["customer"], a["document"]) == [a["tag"]]
    assert _tag_ids(db, b["customer"], b["document"]) == [b["tag"]]


def test_delete_tag_of_another_customer_is_false(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    with db.customer(b["customer"]) as conn:
        assert delete_tag(conn, b["customer"], a["tag"]) is False
    assert library_rows_seen(db, a["customer"], a) == (1, 1, 1)


def test_trash_and_restore_of_another_customer_document_are_false(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    a_trashed = new_trashed_document(db, a["customer"], deleted_at=NOW - timedelta(days=1))
    with db.customer(b["customer"]) as conn:
        assert trash_document(conn, b["customer"], a["document"], by=b["user"], now=NOW) is False
        assert restore_document(conn, b["customer"], a_trashed) is False

    with db.customer(a["customer"]) as conn:
        rows = conn.execute(
            text("SELECT id, deleted_at, deleted_by FROM documents WHERE id = ANY(:ids)"),
            {"ids": [a["document"], a_trashed]},
        ).all()
    state = {str(row[0]): (row[1], row[2]) for row in rows}
    assert state == {a["document"]: (None, None), a_trashed: (NOW - timedelta(days=1), None)}


def test_lock_family_of_another_customer_document_is_none(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    with db.customer(b["customer"]) as conn:
        assert lock_family(conn, b["customer"], a["document"]) is None
        assert lock_family(conn, b["customer"], b["document"]) == b["document"]  # its own first version


def test_lists_never_show_another_customer_rows(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    with db.customer(b["customer"]) as conn:
        tags = [row.id for row in list_tags(conn, b["customer"])]
        by_document = tags_for_documents(conn, b["customer"], [b["document"], a["document"]])
    assert tags == [b["tag"]]
    assert {document: [row.id for row in rows] for document, rows in by_document.items()} == {
        b["document"]: [b["tag"]],
        a["document"]: [],
    }


def test_purge_next_family_never_returns_another_customer_documents(db: Database) -> None:
    a, _ = new_customer(db)
    b, _ = new_customer(db)
    clean, _ = new_customer(db)
    # A's trash is far older than B's, so a purge without the customer filter would pick it first.
    a_trashed = {new_trashed_document(db, a, deleted_at=datetime(2000, 1, 1, tzinfo=UTC)) for _ in range(2)}
    b_trashed = new_trashed_document(db, b, deleted_at=NOW - timedelta(days=40))

    with db.customer(b) as conn:
        picked = purge_next_family(conn, b, before=NOW)
        # Naming A with B's connection finds nothing: row level security still holds.
        named_a = purge_next_family(conn, a, before=NOW)
    with db.customer(clean) as conn:
        nothing = purge_next_family(conn, clean, before=NOW)

    assert picked == [b_trashed]
    assert named_a == []
    assert nothing == []
    assert not a_trashed & set(picked)
