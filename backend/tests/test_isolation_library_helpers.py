"""The library helpers, called by customer B with customer A's exact ids.

``kaagaz.db.folders``, ``tags`` and ``trash`` must give B "not found" (the 404 the
API will show) or False, and leave A unchanged. Each test passes while either
lock holds (the helper's own customer filter or row level security), so each one
also checks both customers' rows afterwards.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, text

from kaagaz.db.engine import Database
from kaagaz.db.folders import (
    FolderNotFound,
    create_folder,
    delete_folder,
    list_folders,
    move_folder,
    rename_folder,
)
from kaagaz.db.tags import TagNotFound, delete_tag, list_tags, set_document_tags, tags_for_documents
from kaagaz.db.trash import lock_family, purge_next_family, restore_document, trash_document
from tests.db_support import insert_document, insert_tag, new_customer, new_library

pytestmark = pytest.mark.db


def _count(conn: Connection, statement: str, params: dict[str, str] | None = None) -> int:
    return int(conn.execute(text(statement), params or {}).scalar_one())


def _seen(db: Database, customer_id: str, a: dict[str, str]) -> tuple[int, int, int]:
    """How many of A's folder, tag and document_tags rows ``customer_id`` can see, by id."""
    with db.customer(customer_id) as conn:
        return (
            _count(conn, "SELECT count(*) FROM folders WHERE id = :f", {"f": a["folder"]}),
            _count(conn, "SELECT count(*) FROM tags WHERE id = :t", {"t": a["tag"]}),
            _count(
                conn,
                "SELECT count(*) FROM document_tags WHERE document_id = :d AND tag_id = :t",
                {"d": a["document"], "t": a["tag"]},
            ),
        )


def _trashed_document(db: Database, customer_id: str, *, deleted_at: datetime) -> str:
    with db.customer(customer_id) as conn:
        return insert_document(conn, customer_id, deleted_at=deleted_at)


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _folders(db: Database, customer_id: str) -> list[tuple[str, str | None, str]]:
    with db.customer(customer_id) as conn:
        return [(row.id, row.parent_id, row.name) for row in list_folders(conn, customer_id)]


def _tag_ids(db: Database, customer_id: str, document_id: str) -> list[str]:
    with db.customer(customer_id) as conn:
        return [row.id for row in tags_for_documents(conn, customer_id, [document_id])[document_id]]


def test_create_folder_inside_another_customer_folder_is_not_found(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    with pytest.raises(FolderNotFound) as caught, db.customer(b["customer"]) as conn:
        create_folder(conn, b["customer"], name="Inside A", parent_id=a["folder"], created_by=b["user"])
    assert caught.value.code == "not_found"
    assert _folders(db, b["customer"]) == [(b["folder"], None, "GST")]
    assert _folders(db, a["customer"]) == [(a["folder"], None, "GST")]


def test_rename_move_and_delete_of_another_customer_folder_are_not_found(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    with db.customer(a["customer"]) as conn:
        # delete_folder first moves trashed documents out; it must not touch A's.
        a_trashed = insert_document(conn, a["customer"], folder_id=a["folder"], deleted_at=NOW)

    with pytest.raises(FolderNotFound) as caught, db.customer(b["customer"]) as conn:
        rename_folder(conn, b["customer"], a["folder"], name="Taken")
    assert caught.value.code == "not_found"
    with pytest.raises(FolderNotFound) as caught, db.customer(b["customer"]) as conn:
        move_folder(conn, b["customer"], a["folder"], parent_id=None)
    assert caught.value.code == "not_found"
    with pytest.raises(FolderNotFound) as caught, db.customer(b["customer"]) as conn:
        move_folder(conn, b["customer"], a["folder"], parent_id=b["folder"])
    assert caught.value.code == "not_found"
    with pytest.raises(FolderNotFound) as caught, db.customer(b["customer"]) as conn:
        move_folder(conn, b["customer"], b["folder"], parent_id=a["folder"])
    assert caught.value.code == "not_found"
    with pytest.raises(FolderNotFound) as caught, db.customer(b["customer"]) as conn:
        delete_folder(conn, b["customer"], a["folder"])
    assert caught.value.code == "not_found"

    assert _folders(db, a["customer"]) == [(a["folder"], None, "GST")]
    assert _folders(db, b["customer"]) == [(b["folder"], None, "GST")]
    with db.customer(a["customer"]) as conn:
        filed = conn.execute(text("SELECT id FROM documents WHERE folder_id = :f"), {"f": a["folder"]}).all()
    assert sorted(str(row[0]) for row in filed) == sorted([a["document"], a_trashed])


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
    assert _seen(db, a["customer"], a) == (1, 1, 1)


def test_trash_and_restore_of_another_customer_document_are_false(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    a_trashed = _trashed_document(db, a["customer"], deleted_at=NOW - timedelta(days=1))
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
        folders = [row.id for row in list_folders(conn, b["customer"])]
        tags = [row.id for row in list_tags(conn, b["customer"])]
        by_document = tags_for_documents(conn, b["customer"], [b["document"], a["document"]])
    assert folders == [b["folder"]]
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
    a_trashed = {_trashed_document(db, a, deleted_at=datetime(2000, 1, 1, tzinfo=UTC)) for _ in range(2)}
    b_trashed = _trashed_document(db, b, deleted_at=NOW - timedelta(days=40))

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
