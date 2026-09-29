"""Folders: a tree at most 5 levels deep, per customer (PRD 1 F2, WP1.2).

Every create, move and delete takes the customer's tree lock first, so two
requests can never together make a cycle or a tree deeper than 5. The
concurrency tests force the order with ``wait_until_blocked``: they never
depend on timing.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, text

from kaagaz.db.engine import Database
from kaagaz.db.folders import (
    FolderCycle,
    FolderError,
    FolderNameTaken,
    FolderNotEmpty,
    FolderNotFound,
    FolderRow,
    FolderTooDeep,
    create_folder,
    delete_folder,
    descendant_ids,
    folder_path,
    list_folders,
    move_folder,
    rename_folder,
)
from tests.db_support import insert_document, new_customer, wait_until_blocked

pytestmark = pytest.mark.db

TRASHED_AT = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _chain(conn: Connection, customer: str, admin: str, depth: int, *, prefix: str) -> list[FolderRow]:
    """Folders nested ``depth`` levels deep: result[0] is top level (depth 1)."""
    folders: list[FolderRow] = []
    parent: str | None = None
    for level in range(1, depth + 1):
        folder = create_folder(conn, customer, name=f"{prefix}{level}", parent_id=parent, created_by=admin)
        folders.append(folder)
        parent = folder.id
    return folders


def _parents(db: Database, customer: str) -> dict[str, str | None]:
    with db.customer(customer) as conn:
        return {folder.id: folder.parent_id for folder in list_folders(conn, customer)}


def _named(conn: Connection, application_name: str) -> None:
    """Name this transaction's session, so ``wait_until_blocked`` can find it."""
    conn.execute(text("SELECT set_config('application_name', :name, true)"), {"name": application_name})


def _session_name() -> str:
    return f"folders-{uuid.uuid4().hex[:16]}"


# --- create ---------------------------------------------------------------------


def test_create_returns_the_row_and_lists_it_by_name(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        tax = create_folder(conn, customer, name="tax", parent_id=None, created_by=admin)
        bills = create_folder(conn, customer, name="Bills", parent_id=None, created_by=admin)
        march = create_folder(conn, customer, name="March", parent_id=bills.id, created_by=admin)

    assert (tax.name, tax.parent_id) == ("tax", None)
    assert (march.name, march.parent_id) == ("March", bills.id)
    assert isinstance(march.created_at, datetime)
    with db.customer(customer) as conn:
        assert [folder.name for folder in list_folders(conn, customer)] == ["Bills", "March", "tax"]


def test_create_at_depth_six_is_refused(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        chain = _chain(conn, customer, admin, 5, prefix="L")

    with pytest.raises(FolderTooDeep) as refused, db.customer(customer) as conn:
        create_folder(conn, customer, name="Six", parent_id=chain[4].id, created_by=admin)
    assert refused.value.code == "folder_too_deep"

    with db.customer(customer) as conn:
        fifth = create_folder(conn, customer, name="Also five", parent_id=chain[3].id, created_by=admin)
        assert fifth.parent_id == chain[3].id
        assert len(list_folders(conn, customer)) == 6


def test_create_under_a_missing_or_other_customers_parent_is_not_found(db: Database) -> None:
    customer, admin = new_customer(db)
    other, other_admin = new_customer(db)
    with db.customer(other) as conn:
        theirs = create_folder(conn, other, name="Theirs", parent_id=None, created_by=other_admin)

    for parent in (str(uuid.uuid4()), theirs.id):
        with pytest.raises(FolderNotFound) as missing, db.customer(customer) as conn:
            create_folder(conn, customer, name="Child", parent_id=parent, created_by=admin)
        assert missing.value.code == "not_found"
    assert _parents(db, customer) == {}


# --- names ----------------------------------------------------------------------


def test_a_sibling_name_clash_is_name_taken(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        bills = create_folder(conn, customer, name="Bills", parent_id=None, created_by=admin)
        tax = create_folder(conn, customer, name="Tax", parent_id=None, created_by=admin)
        inner = create_folder(conn, customer, name="bills", parent_id=tax.id, created_by=admin)

    with pytest.raises(FolderNameTaken) as taken, db.customer(customer) as conn:
        create_folder(conn, customer, name="BILLS", parent_id=None, created_by=admin)
    assert taken.value.code == "conflict"
    with pytest.raises(FolderNameTaken), db.customer(customer) as conn:
        rename_folder(conn, customer, tax.id, name="bills")
    with pytest.raises(FolderNameTaken), db.customer(customer) as conn:
        move_folder(conn, customer, inner.id, parent_id=None)

    with db.customer(customer) as conn:
        names = sorted((folder.name, folder.parent_id) for folder in list_folders(conn, customer))
    assert names == [("Bills", None), ("Tax", None), ("bills", tax.id)]
    assert bills.parent_id is None


def test_rename_changes_only_the_name(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        top = create_folder(conn, customer, name="Top", parent_id=None, created_by=admin)
        child = create_folder(conn, customer, name="Old", parent_id=top.id, created_by=admin)
        renamed = rename_folder(conn, customer, child.id, name="GST 2026/27")

    assert renamed == FolderRow(child.id, top.id, "GST 2026/27", child.created_at)
    with pytest.raises(FolderNotFound), db.customer(customer) as conn:
        rename_folder(conn, customer, str(uuid.uuid4()), name="Nothing")


# --- move -----------------------------------------------------------------------


def test_a_leaf_moves_under_depth_four_but_not_under_depth_five(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        chain = _chain(conn, customer, admin, 5, prefix="L")
        leaf = create_folder(conn, customer, name="Leaf", parent_id=None, created_by=admin)

    with pytest.raises(FolderTooDeep), db.customer(customer) as conn:
        move_folder(conn, customer, leaf.id, parent_id=chain[4].id)
    assert _parents(db, customer)[leaf.id] is None

    with db.customer(customer) as conn:
        moved = move_folder(conn, customer, leaf.id, parent_id=chain[3].id)
    assert moved.parent_id == chain[3].id
    assert _parents(db, customer)[leaf.id] == chain[3].id


def test_a_two_level_subtree_moves_under_depth_three_but_not_under_depth_four(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        chain = _chain(conn, customer, admin, 5, prefix="L")
        top = create_folder(conn, customer, name="Subtree", parent_id=None, created_by=admin)
        below = create_folder(conn, customer, name="Below", parent_id=top.id, created_by=admin)

    with pytest.raises(FolderTooDeep), db.customer(customer) as conn:
        move_folder(conn, customer, top.id, parent_id=chain[3].id)
    assert _parents(db, customer)[top.id] is None

    with db.customer(customer) as conn:
        move_folder(conn, customer, top.id, parent_id=chain[2].id)
    parents = _parents(db, customer)
    assert (parents[top.id], parents[below.id]) == (chain[2].id, top.id)


def test_a_move_into_itself_its_child_or_its_grandchild_is_a_cycle(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        folder, child, grandchild = _chain(conn, customer, admin, 3, prefix="C")
    before = _parents(db, customer)

    for target in (folder, child, grandchild):
        with pytest.raises(FolderCycle) as cycle, db.customer(customer) as conn:
            move_folder(conn, customer, folder.id, parent_id=target.id)
        assert cycle.value.code == "folder_cycle"
    assert _parents(db, customer) == before


def test_a_move_to_the_top_level_works(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        _top, middle, leaf = _chain(conn, customer, admin, 3, prefix="T")
        moved = move_folder(conn, customer, middle.id, parent_id=None)

    assert moved.parent_id is None
    parents = _parents(db, customer)
    assert (parents[middle.id], parents[leaf.id]) == (None, middle.id)


def test_a_move_of_or_to_a_missing_folder_is_not_found(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        folder = create_folder(conn, customer, name="Here", parent_id=None, created_by=admin)

    with pytest.raises(FolderNotFound), db.customer(customer) as conn:
        move_folder(conn, customer, str(uuid.uuid4()), parent_id=folder.id)
    with pytest.raises(FolderNotFound), db.customer(customer) as conn:
        move_folder(conn, customer, str(uuid.uuid4()), parent_id=None)
    with pytest.raises(FolderNotFound), db.customer(customer) as conn:
        move_folder(conn, customer, folder.id, parent_id=str(uuid.uuid4()))
    assert _parents(db, customer) == {folder.id: None}


# --- delete ---------------------------------------------------------------------


def test_delete_of_a_folder_with_a_child_is_not_empty(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        parent, child = _chain(conn, customer, admin, 2, prefix="P")

    with pytest.raises(FolderNotEmpty) as refused, db.customer(customer) as conn:
        delete_folder(conn, customer, parent.id)
    assert refused.value.code == "folder_not_empty"
    assert _parents(db, customer) == {parent.id: None, child.id: parent.id}


def test_delete_of_a_folder_with_a_live_document_is_not_empty_and_changes_nothing(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        folder = create_folder(conn, customer, name="Full", parent_id=None, created_by=admin)
        live = insert_document(conn, customer, folder_id=folder.id)
        trashed = insert_document(
            conn, customer, folder_id=folder.id, deleted_at=TRASHED_AT, deleted_by=admin
        )

    with pytest.raises(FolderNotEmpty), db.customer(customer) as conn:
        delete_folder(conn, customer, folder.id)

    with db.customer(customer) as conn:
        rows = conn.execute(
            text("SELECT id, folder_id FROM documents WHERE customer_id = :c"), {"c": customer}
        ).all()
    # The trashed document is not detached either: the failed delete rolled it back.
    assert {str(row[0]): str(row[1]) for row in rows} == {live: folder.id, trashed: folder.id}
    assert _parents(db, customer) == {folder.id: None}


def test_delete_of_a_folder_holding_only_trashed_documents_moves_them_to_the_top_level(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        folder = create_folder(conn, customer, name="Old", parent_id=None, created_by=admin)
        binned = {"deleted_at": TRASHED_AT, "deleted_by": admin}
        first = insert_document(conn, customer, folder_id=folder.id, **binned)
        second = insert_document(conn, customer, folder_id=folder.id, **binned)
        elsewhere = create_folder(conn, customer, name="Keep", parent_id=None, created_by=admin)
        kept = insert_document(conn, customer, folder_id=elsewhere.id, **binned)

    with db.customer(customer) as conn:
        delete_folder(conn, customer, folder.id)

    with db.customer(customer) as conn:
        rows = conn.execute(
            text("SELECT id, folder_id FROM documents WHERE customer_id = :c"), {"c": customer}
        ).all()
    folders = {str(row[0]): (str(row[1]) if row[1] else None) for row in rows}
    assert folders == {first: None, second: None, kept: elsewhere.id}
    assert _parents(db, customer) == {elsewhere.id: None}


def test_delete_of_a_missing_folder_is_not_found(db: Database) -> None:
    customer, _admin = new_customer(db)
    with pytest.raises(FolderNotFound), db.customer(customer) as conn:
        delete_folder(conn, customer, str(uuid.uuid4()))


# --- paths and subtrees -----------------------------------------------------------


def test_folder_path_is_root_first(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        chain = _chain(conn, customer, admin, 5, prefix="R")
        create_folder(conn, customer, name="Side", parent_id=chain[1].id, created_by=admin)

        assert folder_path(conn, customer, chain[4].id) == chain
        assert folder_path(conn, customer, chain[0].id) == chain[:1]
        assert folder_path(conn, customer, str(uuid.uuid4())) == []


def test_descendant_ids_cover_five_levels_and_include_the_folder(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        chain = _chain(conn, customer, admin, 5, prefix="D")
        side = create_folder(conn, customer, name="Side", parent_id=chain[1].id, created_by=admin)
        side_child = create_folder(conn, customer, name="Side child", parent_id=side.id, created_by=admin)
        outside = create_folder(conn, customer, name="Outside", parent_id=None, created_by=admin)

        everything = descendant_ids(conn, customer, chain[0].id)
        from_side = descendant_ids(conn, customer, side.id)
        from_leaf = descendant_ids(conn, customer, chain[4].id)
        missing = descendant_ids(conn, customer, str(uuid.uuid4()))

    assert sorted(everything) == sorted([folder.id for folder in chain] + [side.id, side_child.id])
    assert outside.id not in everything
    assert sorted(from_side) == sorted([side.id, side_child.id])
    assert from_leaf == [chain[4].id]
    assert missing == []


# --- concurrency (forced order) -----------------------------------------------------


def test_parallel_opposite_moves_cannot_make_a_cycle(db: Database) -> None:
    customer, admin = new_customer(db)
    with db.customer(customer) as conn:
        x = create_folder(conn, customer, name="X", parent_id=None, created_by=admin)
        y = create_folder(conn, customer, name="Y", parent_id=None, created_by=admin)
    session = _session_name()

    def move_y_under_x() -> FolderRow:
        with db.customer(customer) as conn:
            _named(conn, session)
            return move_folder(conn, customer, y.id, parent_id=x.id)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            move_folder(conn, customer, x.id, parent_id=y.id)
            second = pool.submit(move_y_under_x)
            wait_until_blocked(db, application_name=session)
        with pytest.raises(FolderCycle):
            second.result(timeout=10)

    assert _parents(db, customer) == {x.id: y.id, y.id: None}


def test_parallel_same_name_creates_give_one_folder_and_one_name_taken(db: Database) -> None:
    customer, admin = new_customer(db)
    session = _session_name()

    def create_again() -> FolderRow:
        with db.customer(customer) as conn:
            _named(conn, session)
            return create_folder(conn, customer, name="tax", parent_id=None, created_by=admin)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(customer) as conn:
            first = create_folder(conn, customer, name="Tax", parent_id=None, created_by=admin)
            second = pool.submit(create_again)
            wait_until_blocked(db, application_name=session)
        with pytest.raises(FolderNameTaken):
            second.result(timeout=10)

    assert _parents(db, customer) == {first.id: None}


def test_every_folder_error_carries_its_code() -> None:
    errors: list[FolderError] = [
        FolderNotFound(),
        FolderNameTaken(),
        FolderCycle(),
        FolderTooDeep(),
        FolderNotEmpty(),
    ]
    assert [error.code for error in errors] == [
        "not_found",
        "conflict",
        "folder_cycle",
        "folder_too_deep",
        "folder_not_empty",
    ]
