"""A folder in Trash keeps its structure, and Restore puts it back where it was (#542, Tushar,
7 Nov 2026), like the Windows Recycle Bin.

``DELETE /api/folders/{id}?contents=trash`` (#537) copies the subtree into ``trashed_folders``
with one ``trash_id`` and keeps each trashed document's folder in ``trashed_folder_id``. Trash
lists the folder as one item (``GET /api/documents/trash/folders``), opens it read only
(``GET /api/documents/trash/folders/{id}`` and ``GET /api/documents?trashed=true&trashed_folder_id=``),
restores it whole (``POST .../{id}/restore``: the same ids, parents, names, kinds and order; the
top level when its folder is gone; "Name (2)" when a live folder took its name) and deletes it
forever (``DELETE .../{id}/permanent``, delete forever's own rules). Restoring one file from
inside brings back only its chain of folders. The purge removes a folder past
TRASH_DAYS after its documents. Customer separation: test_isolation_trashed_folders.
"""

import threading
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import event, text

from kaagaz.api.routers import folders as folders_router
from kaagaz.db import folders as folders_sql
from kaagaz.db import trashed_folders as trashed_folders_sql
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.jobs.purge import _purge_trash_of
from tests.test_api_admin_sign_in import _turned_off
from tests.test_api_delete_forever_grant import _confirm, _grant
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db


def _trash(signed: Signed, folder_id: str) -> httpx.Response:
    reply: httpx.Response = signed.client.delete(f"/api/folders/{folder_id}?contents=trash", headers=CSRF)
    assert reply.status_code == 200, reply.text
    return reply


def _restore(signed: Signed, folder_id: str) -> httpx.Response:
    reply: httpx.Response = signed.client.post(
        f"/api/documents/trash/folders/{folder_id}/restore", headers=CSRF
    )
    return reply


def _forever(signed: Signed, folder_id: str) -> httpx.Response:
    reply: httpx.Response = signed.client.delete(
        f"/api/documents/trash/folders/{folder_id}/permanent", headers=CSRF
    )
    return reply


def _rows(db: Database, customer_id: str, statement: str) -> dict[str, tuple[Any, ...]]:
    with db.customer(customer_id) as conn:
        return {
            str(row[0]): tuple(None if v is None else str(v) for v in row[1:])
            for row in conn.execute(text(statement), {"c": customer_id})
        }


def _folders(db: Database, c: str) -> dict[str, tuple[Any, ...]]:
    """Each live folder: (parent_id, name, kind, sort_order)."""
    return _rows(db, c, "SELECT id, parent_id, name, kind, sort_order FROM folders WHERE customer_id = :c")


def _trashed(db: Database, c: str) -> dict[str, tuple[Any, ...]]:
    """Each trashed folder: (parent_id, name, trash_id, root)."""
    return _rows(
        db, c, "SELECT id, parent_id, name, trash_id, root FROM trashed_folders WHERE customer_id = :c"
    )


def _documents(db: Database, c: str) -> dict[str, tuple[Any, ...]]:
    """Each document: (folder_id, in Trash, trashed_folder_id, trash_id)."""
    return _rows(
        db,
        c,
        "SELECT id, folder_id, deleted_at IS NOT NULL, trashed_folder_id, trash_id FROM documents"
        " WHERE customer_id = :c",
    )


def _family(api: Api, signed: Signed, folder_id: str | None) -> list[str]:
    """A file with two versions in ``folder_id``."""
    first = api.add_document(signed.customer_id, uploaded_by=signed.user_id, folder_id=folder_id)
    second = api.add_document(
        signed.customer_id,
        uploaded_by=signed.user_id,
        folder_id=folder_id,
        family_id=first,
        version_no=2,
        is_current=False,
    )
    return [first, second]


def _tree(api: Api, admin: Signed) -> dict[str, Any]:
    """Accounts / 2025 / {Bills / Old, Bank} with files at several levels, a file trashed alone
    earlier, and a file outside."""
    c, u = admin.customer_id, admin.user_id
    accounts = api.add_folder(c, u, "Accounts")
    year = api.add_folder(c, u, "2025", accounts)
    bills = api.add_folder(c, u, "Bills", year)
    old = api.add_folder(c, u, "Old", bills)
    bank = api.add_folder(c, u, "Bank", year)
    with api.db.customer(c) as conn:
        conn.execute(text("UPDATE folders SET kind = 'year', sort_order = 7 WHERE id = :f"), {"f": year})
    return {
        "accounts": accounts,
        "year": year,
        "bills": bills,
        "old": old,
        "bank": bank,
        "year_file": api.add_document(c, uploaded_by=u, folder_id=year),
        "bill": _family(api, admin, bills),
        "old_file": api.add_document(c, uploaded_by=u, folder_id=old),
        "statement": api.add_document(c, uploaded_by=u, folder_id=bank),
        "alone": api.add_document(
            c, uploaded_by=u, folder_id=bills, deleted_at=api.clock() - timedelta(days=1), deleted_by=u
        ),
        "outside": api.add_document(c, uploaded_by=u, folder_id=accounts),
    }


def _listed(signed: Signed) -> list[dict[str, Any]]:
    reply = signed.client.get("/api/documents/trash/folders")
    assert reply.status_code == 200, reply.text
    items: list[dict[str, Any]] = reply.json()["items"]
    return items


def _loose(signed: Signed, **params: str) -> list[str]:
    reply = signed.client.get("/api/documents", params={"trashed": "true", **params})
    assert reply.status_code == 200, reply.text
    return sorted(item["id"] for item in reply.json()["items"])


# --- the delete keeps the structure ----------------------------------------------------------


def test_trash_keeps_the_subtree_in_trashed_folders_and_each_document_s_folder(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)

    assert _trash(admin, t["year"]).json() == {"files": 4, "folders": 3}

    assert set(_folders(db, c)) == {t["accounts"]}  # the live tree no longer has them
    trashed = _trashed(db, c)
    trash_ids = {row[2] for row in trashed.values()}
    assert len(trash_ids) == 1
    (trash_id,) = trash_ids
    assert trashed == {
        t["year"]: (t["accounts"], "2025", trash_id, "True"),
        t["bills"]: (t["year"], "Bills", trash_id, "False"),
        t["old"]: (t["bills"], "Old", trash_id, "False"),
        t["bank"]: (t["year"], "Bank", trash_id, "False"),
    }
    documents = _documents(db, c)
    assert documents[t["year_file"]] == (None, "True", t["year"], trash_id)
    assert [documents[d] for d in t["bill"]] == [(None, "True", t["bills"], trash_id)] * 2
    assert documents[t["old_file"]] == (None, "True", t["old"], trash_id)
    assert documents[t["alone"]] == (None, "True", None, None)  # trashed alone earlier: unchanged
    assert documents[t["outside"]] == (t["accounts"], "False", None, None)
    audit = [row for row in api.audit_rows(c) if row[0].startswith("folder.")]
    assert audit == [
        (
            "folder.trash",
            "folder",
            t["year"],
            {"contents": "trash", "files": 4, "folders": 3, "trash_id": trash_id},
        )
    ]


def test_trash_shows_the_folder_as_one_item_and_its_files_not_as_loose_ones(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id)
    t = _tree(api, admin)
    _trash(admin, t["year"])

    for signed in (admin, member):
        (item,) = _listed(signed)
        assert item["deleted_at"] is not None
        assert {k: v for k, v in item.items() if k != "deleted_at"} == {
            "id": t["year"],
            "name": "2025",
            "kind": "year",
            "was_in": ["Accounts"],
            "files": 4,
            "folders": 3,
            "deleted_by_name": "Test admin",
            "can_restore": signed is admin,  # restore follows a document's rule: the member uploaded none
        }
        assert _loose(signed) == [t["alone"]]


def test_opening_a_trashed_folder_shows_its_folders_and_files_with_the_path(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    t = _tree(api, admin)
    _trash(admin, t["year"])

    reply = admin.client.get(f"/api/documents/trash/folders/{t['bills']}")
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["path"] == [{"id": t["year"], "name": "2025"}, {"id": t["bills"], "name": "Bills"}]
    assert (body["folder"]["id"], body["folder"]["files"], body["folder"]["folders"]) == (t["bills"], 2, 1)
    assert [(f["id"], f["name"], f["files"], f["folders"]) for f in body["folders"]] == [
        (t["old"], "Old", 1, 0)
    ]
    assert _loose(admin, trashed_folder_id=t["bills"]) == [t["bill"][0]]  # the current version, once
    assert _loose(admin, trashed_folder_id=t["year"]) == [t["year_file"]]
    missing = admin.client.get(f"/api/documents?trashed=true&trashed_folder_id={uuid.uuid4()}")
    assert missing.status_code == 404
    assert admin.client.get(f"/api/documents/trash/folders/{t['accounts']}").status_code == 404  # a live one


# --- restore -----------------------------------------------------------------------------------


def test_restore_brings_back_the_same_ids_parents_names_kinds_and_files_with_every_version(
    db: Database,
) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    folders_before, documents_before = _folders(db, c), _documents(db, c)
    _trash(admin, t["year"])
    trash_id = next(iter(_trashed(db, c).values()))[2]

    reply = _restore(admin, t["year"])

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["folder"]["id"] == t["year"]
    assert {k: v for k, v in body.items() if k != "folder"} == {
        "location": ["Accounts"],
        "renamed": False,
        "relocated": False,
        "sealed": False,  # #594: it was in no department or private folder that is gone
        "files": 4,
        "folders": 3,
    }
    assert _folders(db, c) == folders_before
    after = _documents(db, c)
    alone = after.pop(t["alone"])
    assert alone == (None, "True", None, None)  # trashed alone earlier: still in Trash, untouched
    documents_before.pop(t["alone"])
    assert after == documents_before
    assert _trashed(db, c) == {}
    assert _listed(admin) == []
    audit = [row for row in api.audit_rows(c) if row[0] == "folder.restore"]
    assert audit == [
        ("folder.restore", "folder", t["year"], {"trash_id": trash_id, "folders": 3, "files": 4})
    ]


def test_a_folder_keeps_its_access_switches_and_stored_values_through_trash_and_restore(db: Database) -> None:
    """PRD 7 (#590, 0084): both copies carry Department only, Private and the stored department
    and private folder, which no screen sets yet."""
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    accounts, bills = t["accounts"], t["bills"]
    with db.customer(c) as conn:
        conn.execute(
            text("UPDATE folders SET kind = 'department', department_only = true WHERE id = :a"),
            {"a": accounts},
        )
        conn.execute(
            text(
                "UPDATE folders SET department_id = :a, private = (id = :b), private_folder_id = CASE"
                " WHEN id IN (:b, :o) THEN CAST(:b AS uuid) END WHERE customer_id = :c"
            ),
            {"a": accounts, "b": bills, "o": t["old"], "c": c},
        )
    access = (
        "SELECT id, department_only, private, department_id, private_folder_id FROM {} WHERE customer_id = :c"
    )
    before = _rows(db, c, access.format("folders"))
    assert before[accounts] == ("True", "False", accounts, None)
    assert before[bills] == ("False", "True", accounts, bills)

    _trash(admin, accounts)
    assert _rows(db, c, access.format("trashed_folders")) == before
    assert _restore(admin, accounts).status_code == 200
    assert _rows(db, c, access.format("folders")) == before


def test_a_folder_whose_parent_is_gone_comes_back_at_the_top_level(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    _trash(admin, t["year"])
    admin.client.delete(f"/api/documents/{t['outside']}", headers=CSRF)
    assert admin.client.delete(f"/api/folders/{t['accounts']}", headers=CSRF).status_code == 204
    assert _listed(admin)[0]["was_in"] is None  # its folder is gone

    body = _restore(admin, t["year"]).json()

    assert (body["relocated"], body["location"], body["renamed"]) == (True, [], False)
    assert _folders(db, c)[t["year"]] == (None, "2025", "year", "7")
    assert _folders(db, c)[t["bills"]] == (t["year"], "Bills", "custom", None)
    restored = [row for row in api.audit_rows(c) if row[0] == "folder.restore"]
    assert restored[0][3]["to"] is None


def test_a_live_folder_with_the_same_name_gives_the_next_free_number(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    _trash(admin, t["year"])
    api.add_folder(c, admin.user_id, "2025", t["accounts"])
    api.add_folder(c, admin.user_id, "2025 (2)", t["accounts"])

    body = _restore(admin, t["year"]).json()

    assert (body["renamed"], body["folder"]["name"], body["relocated"]) == (True, "2025 (3)", False)
    assert _folders(db, c)[t["year"]][:2] == (t["accounts"], "2025 (3)")


def test_a_department_comes_back_at_the_top_level_and_a_clash_gives_2(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    made = admin.client.post("/api/folders", json={"name": "Finance", "kind": "department"}, headers=CSRF)
    department = made.json()["id"]
    inside = api.add_folder(c, admin.user_id, "Inside", department)
    api.add_document(c, uploaded_by=admin.user_id, folder_id=inside)
    _trash(admin, department)
    api.add_folder(c, admin.user_id, "finance")  # any letter case clashes

    body = _restore(admin, department).json()

    assert (body["folder"]["name"], body["folder"]["kind"], body["location"]) == (
        "Finance (2)",
        "department",
        [],
    )
    assert _folders(db, c)[inside][0] == department


def test_restoring_one_file_from_inside_brings_back_only_its_chain_of_folders(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    _trash(admin, t["year"])
    trash_id = next(iter(_trashed(db, c).values()))[2]

    reply = admin.client.post(f"/api/documents/{t['bill'][1]}/restore", headers=CSRF)

    assert reply.status_code == 200, reply.text
    folders = _folders(db, c)
    assert set(folders) == {t["accounts"], t["year"], t["bills"]}  # 2025 and Bills only
    documents = _documents(db, c)
    assert [documents[d] for d in t["bill"]] == [(t["bills"], "False", None, None)] * 2
    # The chain's other files stay in Trash, now in a live folder; the rest stay inside Trash's folders.
    assert documents[t["year_file"]] == (t["year"], "True", None, None)
    assert documents[t["old_file"]] == (None, "True", t["old"], trash_id)
    assert documents[t["statement"]] == (None, "True", t["bank"], trash_id)
    assert _loose(admin) == sorted([t["alone"], t["year_file"]])
    items = {item["id"]: item for item in _listed(admin)}
    assert set(items) == {t["old"], t["bank"]}
    assert (items[t["old"]]["was_in"], items[t["bank"]]["was_in"]) == (
        ["Accounts", "2025", "Bills"],
        ["Accounts", "2025"],
    )
    assert _trashed(db, c) == {
        t["old"]: (t["bills"], "Old", trash_id, "False"),
        t["bank"]: (t["year"], "Bank", trash_id, "False"),
    }
    # An emptied trashed folder is still listed and restorable.
    assert admin.client.delete(f"/api/documents/{t['statement']}/permanent", headers=CSRF).status_code == 204
    assert {item["id"]: item["files"] for item in _listed(admin)}[t["bank"]] == 0
    assert _restore(admin, t["bank"]).json()["location"] == ["Accounts", "2025"]


def test_a_file_trashed_alone_earlier_restores_as_today(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Live")
    alone = api.add_document(c, uploaded_by=admin.user_id, folder_id=folder, deleted_at=api.clock())
    loose = api.add_document(c, uploaded_by=admin.user_id, deleted_at=api.clock())

    for document in (alone, loose):
        assert admin.client.post(f"/api/documents/{document}/restore", headers=CSRF).status_code == 200

    documents = _documents(db, c)
    assert (documents[alone], documents[loose]) == (
        (folder, "False", None, None),
        (None, "False", None, None),
    )
    assert set(_folders(db, c)) == {folder}


def test_restore_follows_restore_s_rule_an_admin_or_who_uploaded_every_version(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    member = api.sign_in(c)
    mine = api.add_folder(c, admin.user_id, "Mine")
    api.add_document(c, uploaded_by=member.user_id, folder_id=mine)
    theirs = api.add_folder(c, admin.user_id, "Theirs")
    api.add_document(c, uploaded_by=member.user_id, folder_id=theirs)
    api.add_document(c, uploaded_by=admin.user_id, folder_id=theirs)
    _trash(admin, mine)
    _trash(admin, theirs)

    assert {item["id"]: item["can_restore"] for item in _listed(member)} == {mine: True, theirs: False}
    refused = _restore(member, theirs)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert _restore(member, mine).status_code == 200
    assert set(_folders(db, c)) == {mine}


def test_a_folder_id_that_is_live_again_is_409_and_changes_nothing(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    _trash(admin, t["year"])
    with db.customer(c) as conn:  # it can't happen through the app; guard it all the same
        conn.execute(
            text("INSERT INTO folders (id, customer_id, name, created_by) VALUES (:f, :c, 'Again', :u)"),
            {"f": t["bank"], "c": c, "u": admin.user_id},
        )
    trashed, documents = _trashed(db, c), _documents(db, c)

    reply = _restore(admin, t["year"])

    assert (reply.status_code, reply.json()["error"]) == (
        409,
        {"code": "folder_live", "message": "This folder is already in Documents. Refresh the page."},
    )
    assert (_trashed(db, c), _documents(db, c)) == (trashed, documents)


def test_restoring_a_large_folder_takes_the_same_statements_as_a_small_one(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id

    def statements(files: int) -> int:
        folder = api.add_folder(c, admin.user_id, f"Folder {files}")
        sub = api.add_folder(c, admin.user_id, "Sub", folder)
        for _ in range(files):
            _family(api, admin, sub)
        _trash(admin, folder)
        return _statements(db, lambda: _restore(admin, folder))

    assert statements(30) == statements(2)


def test_a_restore_and_a_new_folder_of_the_same_name_at_once_never_clash(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    _trash(admin, t["year"])
    replies: list[httpx.Response] = []

    with db.customer(c) as conn:  # a new "2025" in Accounts, still uncommitted, holds the tree lock
        folders_sql.create_folder(conn, c, name="2025", parent_id=t["accounts"], created_by=admin.user_id)
        restoring = threading.Thread(target=lambda: replies.append(_restore(admin, t["year"])))
        restoring.start()
        restoring.join(timeout=1)
        assert restoring.is_alive()  # waits for the tree lock
    restoring.join(timeout=10)

    assert replies[0].json()["folder"]["name"] == "2025 (2)"
    assert sorted(name for _, name, *_ in _folders(db, c).values() if name.startswith("2025")) == [
        "2025",
        "2025 (2)",
    ]


# --- delete forever ---------------------------------------------------------------------------


def test_delete_forever_removes_the_folders_and_every_file_with_its_stored_objects(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    _trash(admin, t["year"])
    trash_id = next(iter(_trashed(db, c).values()))[2]

    reply = _forever(admin, t["year"])

    assert reply.status_code == 204, reply.text
    assert _trashed(db, c) == {}
    assert set(_documents(db, c)) == {t["alone"], t["outside"]}
    for gone in (t["year_file"], *t["bill"], t["old_file"], t["statement"]):
        assert not api.storage.has(c, gone)
    assert api.storage.has(c, t["alone"])
    audit = [row for row in api.audit_rows(c) if row[0] == "folder.delete_forever"]
    assert audit == [
        ("folder.delete_forever", "folder", t["year"], {"trash_id": trash_id, "folders": 3, "files": 4})
    ]
    assert _listed(admin) == []


def test_delete_forever_follows_the_rules_of_a_document_s_delete_forever(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folders = [api.add_folder(c, admin.user_id, name) for name in ("A", "B", "C")]
    for folder in folders:
        api.add_document(c, uploaded_by=admin.user_id, folder_id=folder)
        _trash(admin, folder)
    member = api.sign_in(c)
    refused = _forever(member, folders[0])
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert _grant(admin, member.user_id) == (200, True)
    assert _forever(member, folders[0]).json()["error"]["code"] == "password_confirm_required"
    assert _confirm(member) == 204
    assert _forever(member, folders[0]).status_code == 204
    no_two_step = _turned_off(api)
    other = api.add_folder(no_two_step.customer_id, no_two_step.user_id, "D")
    _trash(no_two_step, other)
    assert _forever(no_two_step, other).json()["error"]["code"] == "password_confirm_required"
    assert _confirm(no_two_step) == 204
    assert _forever(no_two_step, other).status_code == 204
    audit = [row[3] for row in api.audit_rows(c) if row[0] == "folder.delete_forever"]
    assert audit[0]["by_grant"] is True
    assert set(_trashed(db, c)) == {folders[1], folders[2]}


# --- the purge --------------------------------------------------------------------------------


def test_the_purge_removes_folders_after_their_files_and_leaves_no_orphan(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    empty = api.add_folder(c, admin.user_id, "Empty")
    _trash(admin, t["year"])
    _trash(admin, empty)

    def purge(days: int, room: int = 100) -> int:  # purge_expired's turn for this customer alone
        return _purge_trash_of(db, api.storage, c, api.clock() + timedelta(days=days - 30), room)

    assert purge(29) == 0
    assert set(_trashed(db, c)) == {t["year"], t["bills"], t["old"], t["bank"], empty}

    assert purge(31) == 5  # alone too

    assert _trashed(db, c) == {}
    assert set(_documents(db, c)) == {t["outside"]}
    assert not api.storage.has(c, t["old_file"])


def test_the_purge_keeps_a_folder_while_one_of_its_files_is_still_waiting(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    _trash(admin, t["year"])

    def purge(room: int) -> int:  # purge_expired's turn for this customer alone, 31 days on
        return _purge_trash_of(db, api.storage, c, api.clock() + timedelta(days=1), room)

    assert purge(2) == 2
    left = _documents(db, c)
    assert any(row[3] is not None for row in left.values())  # a file of the delete is still waiting
    assert set(_trashed(db, c)) == {t["year"], t["bills"], t["old"], t["bank"]}
    for row in left.values():
        assert row[2] is None or row[2] in _trashed(db, c)

    assert purge(100) == 3
    assert _trashed(db, c) == {}


# --- the audit -------------------------------------------------------------------------------


def test_the_audit_rows_hold_ids_and_counts_never_a_name(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folders = [api.add_folder(c, admin.user_id, f"Secret folder {n}") for n in (1, 2, 3)]
    inside = api.add_folder(c, admin.user_id, "Secret sub", folders[2])
    api.add_document(c, uploaded_by=admin.user_id, folder_id=inside, name="secret.pdf")
    for folder in folders:
        _trash(admin, folder)
    _restore(admin, folders[0])
    _forever(admin, folders[1])
    document = next(d for d, row in _documents(db, c).items() if row[2] == inside)
    admin.client.post(f"/api/documents/{document}/restore", headers=CSRF)

    actions = [row[0] for row in api.audit_rows(c) if row[0].startswith("folder.")]
    assert actions.count("folder.restore") == 2 and "folder.delete_forever" in actions
    assert "ecret" not in repr(api.audit_rows(c))


def _statements(db: Database, run: Callable[[], object]) -> int:
    """How many SQL statements ``run`` sends to the database."""
    count = 0

    def counted(*_: object) -> None:
        nonlocal count
        count += 1

    event.listen(db.engine, "before_cursor_execute", counted)
    try:
        run()
    finally:
        event.remove(db.engine, "before_cursor_execute", counted)
    return count


# --- the reviews' findings (#542) -------------------------------------------------------------


def test_delete_forever_stopped_by_storage_keeps_every_other_file_whole_and_a_retry_finishes(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Folder")
    files = [api.add_document(c, uploaded_by=admin.user_id, folder_id=folder) for _ in range(3)]
    _trash(admin, folder)
    real, deletes = api.storage.delete, []

    def falls_over(customer_id: str, document_id: str) -> None:
        deletes.append(document_id)
        if len(deletes) == 2:
            raise BackendUnavailable("storage")
        real(customer_id, document_id)

    monkeypatch.setattr(api.storage, "delete", falls_over)
    assert _forever(admin, folder).status_code == 503

    left = _documents(db, c)
    assert len(left) == 2 and all(row[2] == folder for row in left.values())  # still in the folder in Trash
    assert all(api.storage.has(c, document) for document in left)  # their files are whole
    assert set(_trashed(db, c)) == {folder}
    stopped = [row[3] for row in api.audit_rows(c) if row[0] == "folder.delete_forever"]
    assert stopped == [{"trash_id": stopped[0]["trash_id"], "files": 1, "finished": False}]

    assert _forever(admin, folder).status_code == 204
    assert _documents(db, c) == {} and _trashed(db, c) == {}
    assert not any(api.storage.has(c, document) for document in files)


def test_a_folder_kaagaz_made_stays_while_a_folder_inside_it_is_in_trash(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    made = api.add_folder(c, admin.user_id, "Accounts")
    with db.customer(c) as conn:  # made by auto-filing, as its folder.create row says
        conn.execute(
            text(
                "INSERT INTO audit_log (customer_id, action, target_type, target_id, details, folder_id)"
                " VALUES (:c, 'folder.create', 'folder', CAST(:f AS text), '{\"auto\": true}',"
                " CAST(:f AS uuid))"
            ),
            {"c": c, "f": made},
        )
    inside = api.add_folder(c, admin.user_id, "Invoices", made)
    api.add_document(c, uploaded_by=admin.user_id, folder_id=inside)
    _trash(admin, inside)

    assert made in _folders(db, c)  # not pruned: Restore puts Invoices back into it
    body = _restore(admin, inside).json()
    assert (body["location"], body["relocated"]) == (["Accounts"], False)


def test_only_an_admin_brings_a_department_back_by_either_restore(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    member = api.sign_in(c)
    made = admin.client.post("/api/folders", json={"name": "Finance", "kind": "department"}, headers=CSRF)
    department = made.json()["id"]
    file = api.add_document(c, uploaded_by=member.user_id, folder_id=department)
    _trash(admin, department)

    refused = [
        _restore(member, department),
        member.client.post(f"/api/documents/{file}/restore", headers=CSRF),
    ]
    assert [(reply.status_code, reply.json()["error"]["code"]) for reply in refused] == [
        (403, "forbidden")
    ] * 2
    assert department not in _folders(db, c)
    assert _restore(admin, department).status_code == 200


def test_restoring_a_file_says_which_folder_came_back_with_it(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    _trash(admin, t["year"])
    api.add_folder(c, admin.user_id, "2025", t["accounts"])

    came = admin.client.post(f"/api/documents/{t['old_file']}/restore", headers=CSRF).json()[
        "restored_folder"
    ]
    plain = admin.client.post(f"/api/documents/{t['alone']}/restore", headers=CSRF).json()["restored_folder"]

    assert {k: v for k, v in came.items() if k != "folder"} == {
        "location": ["Accounts"],
        "renamed": True,
        "relocated": False,
        "sealed": False,  # #594
    }
    assert came["folder"] == {**came["folder"], "id": t["year"], "name": "2025 (2)"}
    assert plain is None


def test_a_file_whose_folder_has_no_free_name_says_so(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    _trash(admin, t["year"])
    with db.customer(c) as conn:  # "2025" and "2025 (2)" to "2025 (100)" all taken
        conn.execute(
            text(
                "INSERT INTO folders (customer_id, parent_id, name, created_by) SELECT :c, :p,"
                " CASE WHEN n = 1 THEN '2025' ELSE '2025 (' || n || ')' END, :u"
                " FROM generate_series(1, 100) n"
            ),
            {"c": c, "p": t["accounts"], "u": admin.user_id},
        )

    reply = admin.client.post(f"/api/documents/{t['year_file']}/restore", headers=CSRF)

    assert (reply.status_code, reply.json()["error"]) == (
        409,
        {
            "code": "folder_names_taken",
            "message": "Its folder can't come back: too many folders there have its name.",
        },
    )


def test_a_search_in_trash_finds_files_inside_its_folders(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    t = _tree(api, admin)
    _trash(admin, t["year"])

    found = _loose(admin, q="invoice")
    assert {t["old_file"], t["bill"][0], t["alone"]} <= set(found)
    assert admin.client.get(f"/api/documents?trashed_folder_id={t['year']}").status_code == 400


def test_trash_at_the_top_level_with_a_filter_never_shows_a_trashed_folder_s_files_as_loose(
    db: Database,
) -> None:
    """A11 (stability pass, 7 Nov 2026): a file that went to Trash with its folder has no folder
    of its own any more, but it is not a loose file at the top level: it is inside that folder."""
    api = Api(db)
    admin = api.new_admin()
    t = _tree(api, admin)
    _trash(admin, t["year"])

    assert _loose(admin, folder_id="root") == [t["alone"]]
    assert _loose(admin, folder_id="root", detected_type="pdf") == [t["alone"]]


def test_delete_forever_goes_in_parts_of_200_saying_how_many_are_left(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A12 (stability pass, 7 Nov 2026): at most FOREVER_FAMILIES files a request, then 202
    ``{remaining}``; the screen asks again until the 204. Each part writes its own audit row:
    ``finished: false`` until the last, which also counts the folders."""
    assert folders_router.FOREVER_FAMILIES == 200
    monkeypatch.setattr(folders_router, "FOREVER_FAMILIES", 2)
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Folder")
    inside = api.add_folder(c, admin.user_id, "Inside", folder)
    files = [api.add_document(c, uploaded_by=admin.user_id, folder_id=f) for f in (folder, folder, inside)]
    _family(api, admin, inside)
    _trash(admin, folder)
    trash_id = next(iter(_trashed(db, c).values()))[2]

    first = _forever(admin, folder)
    assert (first.status_code, first.json()) == (202, {"remaining": 2})
    assert _documents(db, c) and set(_trashed(db, c)) == {folder, inside}
    last = _forever(admin, folder)
    assert last.status_code == 204, last.text
    assert _documents(db, c) == {} and _trashed(db, c) == {}
    assert not any(api.storage.has(c, document) for document in files)
    audit = [row[3] for row in api.audit_rows(c) if row[0] == "folder.delete_forever"]
    assert audit == [
        {"trash_id": trash_id, "files": 2, "finished": False},
        {"trash_id": trash_id, "folders": 1, "files": 2},
    ]


def test_two_deletes_forever_of_one_folder_at_once_finish_it_once(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A12: two browsers (or a double click) delete the same folder in parts at the same time."""
    monkeypatch.setattr(folders_router, "FOREVER_FAMILIES", 2)
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Folder")
    files = [api.add_document(c, uploaded_by=admin.user_id, folder_id=folder) for _ in range(5)]
    _trash(admin, folder)
    replies: list[int] = []

    def until_done() -> None:
        while True:
            reply = _forever(admin, folder)
            replies.append(reply.status_code)
            if reply.status_code != 202:
                return

    threads = [threading.Thread(target=until_done) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert set(replies) <= {202, 204, 404} and 204 in replies
    assert _documents(db, c) == {} and _trashed(db, c) == {}
    assert not any(api.storage.has(c, document) for document in files)


def test_a_folder_deleted_keeping_its_contents_hands_its_trashed_folders_to_where_they_went(
    db: Database,
) -> None:
    """A13 (stability pass, 7 Nov 2026): F holds a folder in Trash; F is deleted with
    ``contents=keep&to=X``, so that folder's Restore lands in X, where F's other contents went."""
    api = Api(db)
    admin = api.new_admin()
    c, u = admin.customer_id, admin.user_id
    destination = api.add_folder(c, u, "X")
    parent = api.add_folder(c, u, "F")
    child = api.add_folder(c, u, "Old", parent)
    _trash(admin, child)
    gone = admin.client.delete(f"/api/folders/{parent}?contents=keep&to={destination}", headers=CSRF)
    assert gone.status_code == 200, gone.text

    assert _trashed(db, c)[child][0] == destination
    assert _restore(admin, child).status_code == 200
    assert _folders(db, c)[child][0] == destination


def test_a_part_that_deletes_nothing_stops_instead_of_asking_again_for_ever(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A12 review: were every file of a part skipped (gone from Trash meanwhile), a 202 with the same
    count would make the screen ask again for ever; it ends as a folder not emptied (409)."""
    monkeypatch.setattr(folders_router, "FOREVER_FAMILIES", 2)
    monkeypatch.setattr(trashed_folders_sql, "delete_family", lambda *args: False)
    api = Api(db)
    admin = api.new_admin()
    folder = api.add_folder(admin.customer_id, admin.user_id, "Folder")
    for _ in range(3):
        api.add_document(admin.customer_id, uploaded_by=admin.user_id, folder_id=folder)
    _trash(admin, folder)
    reply = _forever(admin, folder)
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "folder_not_emptied")
