"""The folder helpers, called by customer B with customer A's exact ids.

``kaagaz.db.folders`` must give B "not found" (the 404 the API will show) or an
empty result, and leave A unchanged. Each test passes while either lock holds
(the helper's own customer filter or row level security), so each one also
checks both customers' rows afterwards.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.db.folders import (
    FolderNotFound,
    create_folder,
    delete_folder,
    descendant_ids,
    folder_path,
    list_folders,
    move_folder,
    rename_folder,
)
from tests.db_support import insert_document, insert_folder, new_library

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _folders(db: Database, customer_id: str) -> list[tuple[str, str | None, str]]:
    with db.customer(customer_id) as conn:
        return [(row.id, row.parent_id, row.name) for row in list_folders(conn, customer_id)]


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


def test_list_path_and_subtree_never_show_another_customer_folders(db: Database) -> None:
    a = new_library(db)
    b = new_library(db)
    with db.customer(a["customer"]) as conn:
        a_child = insert_folder(conn, a["customer"], a["user"], "Inside", parent_id=a["folder"])

    with db.customer(b["customer"]) as conn:
        assert [row.id for row in list_folders(conn, b["customer"])] == [b["folder"]]
        assert folder_path(conn, b["customer"], a_child) == []
        assert descendant_ids(conn, b["customer"], a["folder"]) == []
        # Naming A with B's connection finds nothing: row level security still holds.
        assert folder_path(conn, a["customer"], a_child) == []
        assert descendant_ids(conn, a["customer"], a["folder"]) == []

    with db.customer(a["customer"]) as conn:
        assert [row.id for row in folder_path(conn, a["customer"], a_child)] == [a["folder"], a_child]
        assert sorted(descendant_ids(conn, a["customer"], a["folder"])) == sorted([a["folder"], a_child])
