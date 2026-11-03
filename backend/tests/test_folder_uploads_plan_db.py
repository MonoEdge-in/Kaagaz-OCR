"""Planning a folder upload (#484) against PostgreSQL: the same folders, made or merged in one
transaction under the tree lock, their ids in the list's order; a re-plan reuses them by index;
the limits, the depth of 100 and the department rule hold, and a refusal writes nothing."""

import unicodedata
import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.db.folder_upload_items import NewItem, register_items
from kaagaz.db.folder_uploads import (
    GIB,
    FolderUploadLimits,
    Plan,
    PlannedFolder,
    PlanRefused,
    plan_folders,
)
from kaagaz.db.folders import (
    FolderNotFound,
    FolderTooDeep,
    create_folder,
    delete_folder,
    list_folders,
    move_folder,
    prune_empty,
    rename_folder,
)
from kaagaz.db.users import create_user
from tests.db_support import insert_folder, new_customer
from tests.folder_upload_support import path_key

pytestmark = pytest.mark.db

LIMITS = FolderUploadLimits(max_files=50_000, max_bytes=5 * GIB, max_folders=10_000)
# The tree: A, with B and an empty D in it, C in B, E in C.
TREE = [PlannedFolder("A", None), PlannedFolder("B", 0), PlannedFolder("C", 1), PlannedFolder("D", 0)]
TREE.append(PlannedFolder("E", 2))


def _plan(
    db: Database,
    customer: str,
    user: str,
    folders: Sequence[PlannedFolder] = TREE,
    *,
    upload_id: str | None = None,
    **changes: Any,
) -> Plan:
    args: dict[str, Any] = {
        "upload_id": upload_id or str(uuid.uuid4()),
        "user_id": user,
        "target": "folder",
        "parent_folder_id": None,
        "merge": True,
        "folders": folders,
        "file_count": 3,
        "total_bytes": 300,
        "skipped_count": 0,
        "limits": LIMITS,
    } | changes
    with db.customer(customer) as conn:
        return plan_folders(conn, customer, **args)


def _tree(db: Database, customer: str) -> dict[str, tuple[str | None, str, str]]:
    with db.customer(customer) as conn:
        return {row.id: (row.parent_id, row.name, row.kind) for row in list_folders(conn, customer)}


def test_a_tree_is_made_in_one_go_and_its_ids_follow_the_list(db: Database) -> None:
    customer, user = new_customer(db)
    plan = _plan(db, customer, user)
    tree = _tree(db, customer)
    assert (plan.created, plan.merged, plan.root_name) == (5, 0, "A")
    assert plan.root_folder_id == plan.folder_ids[0]
    for index, folder in enumerate(TREE):
        parent, name, kind = tree[plan.folder_ids[index]]
        assert (name, kind) == (folder.name, "custom")
        assert parent == (None if folder.parent is None else plan.folder_ids[folder.parent])
    assert len(tree) == 5


def test_a_replan_reuses_every_folder_by_index_even_renamed_and_makes_only_new_ones(db: Database) -> None:
    customer, user = new_customer(db)
    upload = str(uuid.uuid4())
    first = _plan(db, customer, user, upload_id=upload)
    with db.customer(customer) as conn:
        rename_folder(conn, customer, first.folder_ids[1], name="B renamed in Kaagaz")
    again = _plan(db, customer, user, upload_id=upload)
    assert (again.folder_ids, again.created) == (first.folder_ids, 0)
    more = _plan(db, customer, user, [*TREE, PlannedFolder("F", 1)], upload_id=upload, file_count=4)
    assert (more.folder_ids[:5], more.created) == (first.folder_ids, 1)
    assert _tree(db, customer)[more.folder_ids[5]][:2] == (first.folder_ids[1], "F")


def test_merge_uses_existing_folders_at_every_level_and_keep_separate_renames_only_the_root(
    db: Database,
) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        a = create_folder(conn, customer, name="a", parent_id=None, created_by=user)
        b = create_folder(conn, customer, name="B", parent_id=a.id, created_by=user)
    merged = _plan(db, customer, user)
    assert merged.folder_ids[:2] == [a.id, b.id]
    assert (merged.created, merged.merged, merged.root_name) == (3, 2, "a")
    separate = _plan(db, customer, user, merge=False)
    assert (separate.root_name, separate.created) == ("A (2)", 5)
    third = _plan(db, customer, user, merge=False)
    assert third.root_name == "A (3)"
    long = "x" * 100
    with db.customer(customer) as conn:
        create_folder(conn, customer, name=long, parent_id=None, created_by=user)
    cut = _plan(db, customer, user, [PlannedFolder(long, None)], merge=False)
    assert cut.root_name == "x" * 96 + " (2)"  # cut to fit 100 characters


def test_case_only_siblings_merge_and_a_department_never_merges_into_a_custom_folder(db: Database) -> None:
    customer, user = new_customer(db)
    siblings = [PlannedFolder("A", None), PlannedFolder("Bills", 0), PlannedFolder("bills", 0)]
    plan = _plan(db, customer, user, siblings)
    assert plan.folder_ids[1] == plan.folder_ids[2]
    assert plan.created == 2
    department = _plan(db, customer, user, [PlannedFolder("A", None)], target="department")
    assert department.root_name == "A (2)"  # the custom "A" is never merged into
    assert _tree(db, customer)[department.root_folder_id] == (None, "A (2)", "department")
    again = _plan(
        db, customer, user, [PlannedFolder("a (2)", None), PlannedFolder("X", 0)], target="department"
    )
    assert again.root_folder_id == department.root_folder_id  # a department of that name is


def test_101_levels_under_the_parent_is_too_deep_and_nothing_is_written(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        top = create_folder(conn, customer, name="Top", parent_id=None, created_by=user)
    chain = [PlannedFolder(f"L{i}", None if i == 0 else i - 1) for i in range(100)]
    with pytest.raises(FolderTooDeep):
        _plan(db, customer, user, chain, parent_folder_id=top.id)
    assert len(_tree(db, customer)) == 1
    assert _plan(db, customer, user, chain).created == 100  # 100 at the top level fits
    with pytest.raises(FolderNotFound):
        _plan(db, customer, user, chain, parent_folder_id=str(uuid.uuid4()))


def test_counts_over_the_limits_are_refused_before_anything_is_written(db: Database) -> None:
    customer, user = new_customer(db)
    small = FolderUploadLimits(max_files=3, max_bytes=300, max_folders=4)
    refusals: list[tuple[dict[str, Any], str]] = [
        ({"file_count": 4}, "too_many_files"),
        ({"total_bytes": 301}, "too_large_upload"),
        ({"folders": TREE}, "too_many_folders"),  # 5 folders listed, 4 allowed
    ]
    for changes, code in refusals:
        args: dict[str, Any] = {"folders": TREE[:4], "limits": small} | changes
        with pytest.raises(PlanRefused) as refused:
            _plan(db, customer, user, **args)
        assert refused.value.code == code
    assert _tree(db, customer) == {}


def test_folders_made_past_the_limit_across_plans_roll_the_plan_back(db: Database) -> None:
    customer, user = new_customer(db)
    small = FolderUploadLimits(max_files=3, max_bytes=300, max_folders=4)
    upload = str(uuid.uuid4())
    first = _plan(db, customer, user, TREE[:3], upload_id=upload, limits=small)
    with db.customer(customer) as conn:  # deleted in Kaagaz since: a re-plan would make them again
        for folder in reversed(first.folder_ids):
            delete_folder(conn, customer, folder)
    with pytest.raises(PlanRefused) as refused:
        _plan(db, customer, user, TREE[:3], upload_id=upload, limits=small)
    assert refused.value.code == "too_many_folders"  # 3 made + 3 again > 4
    assert _tree(db, customer) == {}


def test_a_replan_after_a_planned_folder_was_deleted_moves_its_waiting_items_to_the_new_one(
    db: Database,
) -> None:
    customer, user = new_customer(db)
    upload = str(uuid.uuid4())
    first = _plan(db, customer, user, upload_id=upload)
    b, c, e = first.folder_ids[1], first.folder_ids[2], first.folder_ids[4]
    files = [
        NewItem(path_key(f"A/{n}"), n, folder, 10) for n, folder in (("c.pdf", c), ("e.pdf", e), ("b.pdf", b))
    ]
    with db.customer(customer) as conn:
        register_items(
            conn,
            customer,
            upload,
            user_id=user,
            items=files,
            batch_cap=100,
            largest_limit=10**6,
            limits=LIMITS,
        )
        for folder in (e, c):  # E is inside C
            delete_folder(conn, customer, folder)
    again = _plan(db, customer, user, upload_id=upload)
    assert (again.created, again.folder_ids[1]) == (2, b)
    with db.customer(customer) as conn:
        moved = conn.execute(text("SELECT name, folder_id::text FROM folder_upload_items ORDER BY seq")).all()
    assert [tuple(row) for row in moved] == [
        ("c.pdf", again.folder_ids[2]),
        ("e.pdf", again.folder_ids[4]),
        ("b.pdf", b),
    ]


def test_a_replan_listing_the_folders_in_another_order_reuses_them_by_path(db: Database) -> None:
    customer, user = new_customer(db)
    upload = str(uuid.uuid4())
    first = _plan(
        db,
        customer,
        user,
        [PlannedFolder("A", None), PlannedFolder("x", 0), PlannedFolder("y", 0)],
        upload_id=upload,
    )
    _, x, y = first.folder_ids
    with db.customer(customer) as conn:
        register_items(
            conn,
            customer,
            upload,
            user_id=user,
            items=[NewItem(path_key("A/y/a.pdf"), "a.pdf", y, 10)],
            batch_cap=100,
            largest_limit=10**6,
            limits=LIMITS,
        )
    # After a reload: another walk lists an empty z first, then y before x.
    again_list = [
        PlannedFolder("A", None),
        PlannedFolder("z", 0),
        PlannedFolder("y", 0),
        PlannedFolder("x", 0),
    ]
    again = _plan(db, customer, user, again_list, upload_id=upload)
    assert (again.folder_ids[2:], again.created) == ([y, x], 1)
    with db.customer(customer) as conn:
        assert conn.execute(text("SELECT folder_id::text FROM folder_upload_items")).scalar_one() == y
    assert _tree(db, customer)[again.folder_ids[1]][:2] == (first.root_folder_id, "z")


def test_a_replan_measures_a_reused_folder_moved_deeper_since(db: Database) -> None:
    customer, user = new_customer(db)
    upload = str(uuid.uuid4())
    two = [PlannedFolder("A", None), PlannedFolder("B", 0)]
    first = _plan(db, customer, user, two, upload_id=upload)
    with db.customer(customer) as conn:
        parent = None
        for level in range(98):
            parent = create_folder(conn, customer, name=f"L{level}", parent_id=parent, created_by=user).id
        move_folder(conn, customer, first.folder_ids[1], parent_id=parent)  # B is at level 99 now
    fits = _plan(db, customer, user, [*two, PlannedFolder("C", 1)], upload_id=upload)  # C at 100
    assert fits.created == 1
    with pytest.raises(FolderTooDeep):  # D would be at 101
        _plan(db, customer, user, [*two, PlannedFolder("C", 1), PlannedFolder("D", 2)], upload_id=upload)


def test_merge_compares_names_as_nfc_so_a_mac_folder_already_there_is_used(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        root = insert_folder(conn, customer, user, unicodedata.normalize("NFD", "Café"))
        child = insert_folder(conn, customer, user, unicodedata.normalize("NFD", "Reçus"), root)
    plan = _plan(db, customer, user, [PlannedFolder("Café", None), PlannedFolder("Reçus", 0)])
    assert (plan.folder_ids, plan.created) == ([root, child], 0)
    separate = _plan(db, customer, user, [PlannedFolder("Café", None)], merge=False)
    assert separate.root_name == "Café (2)"


def test_another_users_upload_is_not_found_and_another_place_is_plan_changed(db: Database) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        email = f"other-{uuid.uuid4().hex[:12]}@example.test"
        row = create_user(
            conn,
            customer,
            email=email,
            display_name="Other",
            role="member",
            password_hash="x",
            must_change_password=False,
        )
        other = str(row["id"])
    upload = str(uuid.uuid4())
    _plan(db, customer, user, upload_id=upload)
    with pytest.raises(FolderNotFound):
        _plan(db, customer, other, upload_id=upload)
    for changes in ({"target": "department"}, {"folders": [PlannedFolder("Z", None)]}):
        with pytest.raises(PlanRefused) as refused:
            _plan(db, customer, user, upload_id=upload, **changes)
        assert refused.value.code == "plan_changed"


def test_empty_folders_made_by_an_upload_are_never_pruned(db: Database) -> None:
    customer, user = new_customer(db)
    plan = _plan(db, customer, user)
    with db.customer(customer) as conn:
        assert prune_empty(conn, customer, [plan.folder_ids[3], plan.folder_ids[4]]) == []
    assert len(_tree(db, customer)) == 5
