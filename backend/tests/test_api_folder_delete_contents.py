"""An admin deletes a folder or a department that still holds files (#537, Tushar, 7 Nov 2026).

``DELETE /api/folders/{id}?contents=keep&to=...`` moves what is directly inside into the folder
the admin chose (no ``to``: the top level) and deletes only the folder; ``?contents=trash``
sends every document family in the whole subtree to Trash (the existing trash rules: all
versions, the first deleted_at kept, restorable, purged after TRASH_DAYS) and deletes the
folder with its subfolders. Both are admin only, one transaction under the tree lock, refused
while a folder upload into the subtree still has files waiting, and audited once with counts
only. ``GET /api/folders/{id}/summary`` counts the live files, the subfolders and the bytes of
the subtree. With no ``contents`` the delete is today's.
"""

from datetime import datetime, timedelta
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import Connection, text

from kaagaz.db import trash
from kaagaz.db.engine import Database
from kaagaz.jobs.purge import _purge_one_family
from tests.db_support import insert_document
from tests.folder_upload_support import new_folder_upload, path_key
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

UPLOAD_RUNNING = {
    "code": "folder_upload_running",
    "message": "An upload into this folder is still running. Try again when it finishes.",
}


def _delete(
    signed: Signed, folder_id: str, contents: str | None = None, to: str | None = None
) -> httpx.Response:
    query = "" if contents is None else f"?contents={contents}"
    query += "" if to is None else f"&to={to}"
    reply: httpx.Response = signed.client.delete(f"/api/folders/{folder_id}{query}", headers=CSRF)
    return reply


def _folders(db: Database, customer_id: str) -> dict[str, tuple[str | None, str, str]]:
    """Each folder: (parent_id, name, kind)."""
    with db.customer(customer_id) as conn:
        rows = conn.execute(
            text("SELECT id, parent_id, name, kind FROM folders WHERE customer_id = :c"), {"c": customer_id}
        )
        return {str(r[0]): (None if r[1] is None else str(r[1]), str(r[2]), str(r[3])) for r in rows}


def _documents(db: Database, customer_id: str) -> dict[str, tuple[str | None, datetime | None, str | None]]:
    """Each document: (folder_id, deleted_at, deleted_by)."""
    with db.customer(customer_id) as conn:
        rows = conn.execute(
            text("SELECT id, folder_id, deleted_at, deleted_by FROM documents WHERE customer_id = :c"),
            {"c": customer_id},
        )
        return {
            str(r[0]): (None if r[1] is None else str(r[1]), r[2], None if r[3] is None else str(r[3]))
            for r in rows
        }


def _family(api: Api, signed: Signed, folder_id: str | None, **columns: Any) -> list[str]:
    """A document with two versions in ``folder_id``: [version 1, version 2]."""
    first = api.add_document(signed.customer_id, uploaded_by=signed.user_id, folder_id=folder_id, **columns)
    second = api.add_document(
        signed.customer_id,
        uploaded_by=signed.user_id,
        folder_id=folder_id,
        family_id=first,
        version_no=2,
        is_current=False,
        **columns,
    )
    return [first, second]


def _folder_audit(api: Api, customer_id: str) -> list[tuple[str, str | None, str | None, dict[str, Any]]]:
    return [row for row in api.audit_rows(customer_id) if row[0] == "folder.delete"]


# --- keep: what is inside moves where the admin chose -------------------------------------


def test_keep_moves_the_documents_and_subfolders_to_the_parent_and_trashes_nothing(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    top = api.add_folder(c, admin.user_id, "Top")
    folder = api.add_folder(c, admin.user_id, "Middle", top)
    sub = api.add_folder(c, admin.user_id, "Sub", folder)
    deep = api.add_document(c, uploaded_by=admin.user_id, folder_id=sub)
    family = _family(api, admin, folder)
    gone_at = api.clock() - timedelta(days=2)
    binned = api.add_document(
        c, uploaded_by=admin.user_id, folder_id=folder, deleted_at=gone_at, deleted_by=admin.user_id
    )

    assert _delete(admin, folder, "keep", to=top).status_code == 200

    folders = _folders(db, c)
    assert set(folders) == {top, sub}
    assert folders[sub] == (top, "Sub", "custom")
    documents = _documents(db, c)
    assert documents[deep] == (sub, None, None)  # a moved subfolder keeps its own contents
    assert [documents[d] for d in family] == [(top, None, None)] * 2
    assert documents[binned] == (top, gone_at, admin.user_id)  # still in Trash, restores into Top
    assert _folder_audit(api, c) == [
        ("folder.delete", "folder", folder, {"contents": "keep", "files": 2, "folders": 1, "to": top})
    ]


def test_keep_moves_to_a_folder_elsewhere_in_the_tree_or_to_the_top_level(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    first, second = (api.add_folder(c, admin.user_id, name) for name in ("Old", "Older"))
    target = api.add_folder(c, admin.user_id, "Target", api.add_folder(c, admin.user_id, "Elsewhere"))
    moved = [api.add_folder(c, admin.user_id, "Sub", folder) for folder in (first, second)]
    files = [api.add_document(c, uploaded_by=admin.user_id, folder_id=folder) for folder in (first, second)]

    assert _delete(admin, first, "keep", to=target).status_code == 200
    assert _delete(admin, second, "keep").status_code == 200  # no ``to``: the top level

    folders = _folders(db, c)
    assert (folders[moved[0]], folders[moved[1]]) == ((target, "Sub", "custom"), (None, "Sub", "custom"))
    documents = _documents(db, c)
    assert (documents[files[0]], documents[files[1]]) == ((target, None, None), (None, None, None))
    assert [row[3]["to"] for row in _folder_audit(api, c)] == [target, None]


def test_keep_into_the_folder_itself_or_one_inside_it_is_400_and_changes_nothing(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Folder")
    inside = api.add_folder(c, admin.user_id, "Inside", folder)
    deeper = api.add_folder(c, admin.user_id, "Deeper", inside)
    api.add_document(c, uploaded_by=admin.user_id, folder_id=folder)
    folders, documents = _folders(db, c), _documents(db, c)

    for to in (folder, inside, deeper):
        reply = _delete(admin, folder, "keep", to=to)
        assert (reply.status_code, reply.json()["error"]) == (
            400,
            {"code": "bad_request", "message": "Choose a folder outside the one you are deleting."},
        )
    unknown = _delete(admin, folder, "keep", to="7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e")
    assert (unknown.status_code, unknown.json()["error"]) == (
        404,
        {"code": "not_found", "message": "The folder you chose isn't there any more. Choose another."},
    )
    assert _delete(admin, folder, "trash", to=inside).status_code == 400  # ``to`` is for keep only
    assert (_folders(db, c), _documents(db, c)) == (folders, documents)


def test_keep_names_a_clashing_subfolder_with_the_next_free_number(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    for name in ("bills", "Bills (2)", "x" * 100, "Archive"):  # the destination at the top level
        api.add_folder(c, admin.user_id, name)
    folder = api.add_folder(c, admin.user_id, "Archive (old)")
    bills = api.add_folder(c, admin.user_id, "Bills", folder)
    other = api.add_folder(c, admin.user_id, "Other", folder)
    same = api.add_folder(
        c, admin.user_id, "archive (OLD)", folder
    )  # its own folder's name: free once it goes
    long = api.add_folder(c, admin.user_id, "X" * 100, folder)
    archive = api.add_folder(c, admin.user_id, "archive", folder)
    chosen = api.add_folder(c, admin.user_id, "Chosen")
    api.add_folder(c, admin.user_id, "Taxes", chosen)
    second = api.add_folder(c, admin.user_id, "Second")
    taxes = api.add_folder(c, admin.user_id, "TAXES", second)

    assert _delete(admin, folder, "keep").status_code == 200
    assert _delete(admin, second, "keep", to=chosen).status_code == 200

    folders = _folders(db, c)
    assert folder not in folders
    assert folders[bills] == (None, "Bills (3)", "custom")  # never merged into "bills"
    assert folders[other] == (None, "Other", "custom")
    assert folders[same] == (None, "archive (OLD)", "custom")
    assert folders[long] == (None, "X" * 96 + " (2)", "custom")  # the 100-character cap holds
    assert folders[archive] == (None, "archive (2)", "custom")
    assert folders[taxes] == (chosen, "TAXES (2)", "custom")  # a clash in a chosen folder too


def test_keep_compares_names_as_one_whatever_their_unicode_form(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    nfd = "Cafe\u0301"  # "Café" as a Mac writes it
    for name in (nfd, f"{nfd} (2)"):
        api.add_folder(c, admin.user_id, name)
    folder = api.add_folder(c, admin.user_id, "Folder")
    moved = api.add_folder(c, admin.user_id, nfd, folder)

    assert _delete(admin, folder, "keep").status_code == 200

    assert _folders(db, c)[moved] == (None, f"{nfd} (3)", "custom")


def test_keep_with_no_free_number_left_is_409_and_changes_nothing(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    for name in ("A", *(f"A ({n})" for n in range(2, 101))):
        api.add_folder(c, admin.user_id, name)
    folder = api.add_folder(c, admin.user_id, "Folder")
    api.add_folder(c, admin.user_id, "a", folder)
    folders = _folders(db, c)

    reply = _delete(admin, folder, "keep")

    assert (reply.status_code, reply.json()["error"]) == (
        409,
        {
            "code": "folder_names_taken",
            "message": "The folder you chose already has too many folders with these names. Choose another.",
        },
    )
    assert _folders(db, c) == folders


def test_keep_lets_the_folders_inside_land_down_to_level_100_and_no_deeper(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    chain: list[str] = []
    for level in range(1, 100):
        chain.append(api.add_folder(c, admin.user_id, f"L{level}", chain[-1] if chain else None))
    two = api.add_folder(c, admin.user_id, "Two levels")
    inner = api.add_folder(c, admin.user_id, "Inner", two)
    three = api.add_folder(c, admin.user_id, "Three levels")
    api.add_folder(c, admin.user_id, "Deeper", api.add_folder(c, admin.user_id, "Inner", three))

    too_deep = _delete(admin, three, "keep", to=chain[-1])  # its Deeper would be at level 101
    assert (too_deep.status_code, too_deep.json()["error"]["code"]) == (409, "folder_too_deep")
    assert _delete(admin, two, "keep", to=chain[-1]).status_code == 200  # Inner lands at level 100
    assert _folders(db, c)[inner][0] == chain[-1]


def test_keep_of_a_department_puts_its_folders_at_the_top_level_with_their_kinds(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    department = admin.client.post(
        "/api/folders", json={"name": "Accounts", "kind": "department"}, headers=CSRF
    ).json()["id"]
    category = admin.client.post(
        "/api/folders", json={"name": "Invoices", "parent_id": department, "kind": "category"}, headers=CSRF
    ).json()["id"]
    custom = api.add_folder(c, admin.user_id, "Loose", department)
    inner = api.add_folder(c, admin.user_id, "Inner", category)
    filed = api.add_document(c, uploaded_by=admin.user_id, folder_id=department)

    assert _delete(admin, department, "keep").status_code == 200

    folders = _folders(db, c)
    assert department not in folders
    assert folders[category] == (None, "Invoices", "category")
    assert folders[custom] == (None, "Loose", "custom")
    assert folders[inner] == (category, "Inner", "custom")
    assert _documents(db, c)[filed] == (None, None, None)


# --- trash: every family in the subtree goes to Trash --------------------------------------


def _deep_tree(api: Api, admin: Signed) -> dict[str, Any]:
    """Parent / Folder / L1 / L2 / L3 / L4 with files at several levels, one file beside it."""
    c = admin.customer_id
    parent = api.add_folder(c, admin.user_id, "Parent")
    folder = api.add_folder(c, admin.user_id, "Folder", parent)
    levels = [folder]
    for level in range(1, 5):
        levels.append(api.add_folder(c, admin.user_id, f"L{level}", levels[-1]))
    early = api.clock() - timedelta(days=3)
    return {
        "parent": parent,
        "folder": folder,
        "levels": levels,
        "top_file": api.add_document(c, uploaded_by=admin.user_id, folder_id=folder),
        "deep_family": _family(api, admin, levels[-1]),
        "middle_file": api.add_document(c, uploaded_by=admin.user_id, folder_id=levels[2], status="received"),
        "binned": api.add_document(
            c, uploaded_by=admin.user_id, folder_id=levels[1], deleted_at=early, deleted_by=admin.user_id
        ),
        "early": early,
        "outside": api.add_document(c, uploaded_by=admin.user_id, folder_id=parent),
    }


def test_trash_sends_every_family_of_the_subtree_to_trash_and_removes_its_folders(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    tree = _deep_tree(api, admin)

    reply = _delete(admin, tree["folder"], "trash")
    assert (reply.status_code, reply.json()) == (200, {"files": 3, "folders": 4})

    assert set(_folders(db, c)) == {tree["parent"]}
    documents = _documents(db, c)
    now = (None, api.clock(), admin.user_id)
    # Every version, at the top level once restored; one still being read goes too (as a single delete).
    for document in (tree["top_file"], *tree["deep_family"], tree["middle_file"]):
        assert documents[document] == now
    assert documents[tree["binned"]] == (None, tree["early"], admin.user_id)  # keeps its first time
    assert documents[tree["outside"]] == (tree["parent"], None, None)
    assert _folder_audit(api, c) == [
        ("folder.delete", "folder", tree["folder"], {"contents": "trash", "files": 3, "folders": 4})
    ]


def test_a_family_trashed_with_its_folder_is_restored_at_the_top_level(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    tree = _deep_tree(api, admin)
    assert _delete(admin, tree["folder"], "trash").status_code == 200

    first, second = tree["deep_family"]
    assert admin.client.post(f"/api/documents/{second}/restore", headers=CSRF).status_code == 200

    documents = _documents(db, admin.customer_id)
    assert [documents[first], documents[second]] == [(None, None, None)] * 2


def test_the_trash_purge_deletes_them_for_good_once_their_time_is_up(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    tree = _deep_tree(api, admin)
    assert _delete(admin, tree["folder"], "trash").status_code == 200

    def purge(before: datetime) -> int:
        purged = 0
        while _purge_one_family(db, api.storage, c, before):
            purged += 1
        return purged

    # Their time in Trash starts now; the one binned three days before keeps its own.
    assert purge(api.clock() - timedelta(seconds=1)) == 1
    assert purge(api.clock() + timedelta(seconds=1)) == 3  # top_file, the deep family, middle_file
    assert set(_documents(db, c)) == {tree["outside"]}
    assert not api.storage.has(c, tree["deep_family"][1])


# --- who may, and today's delete unchanged ---------------------------------------------------


def test_a_member_or_a_reviewer_gets_403_for_both_modes_and_nothing_changes(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Folder")
    api.add_folder(c, admin.user_id, "Sub", folder)
    api.add_document(c, uploaded_by=admin.user_id, folder_id=folder)
    folders, documents = _folders(db, c), _documents(db, c)

    for role in ("member", "reviewer"):
        signed = api.sign_in(c, role)
        for contents in ("keep", "trash"):
            reply = _delete(signed, folder, contents)
            assert reply.status_code == 403, (role, contents)
            assert reply.json()["error"]["code"] == "forbidden"

    assert (_folders(db, c), _documents(db, c)) == (folders, documents)
    assert _folder_audit(api, c) == []


def test_with_no_contents_the_delete_is_todays_for_everyone(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    member = api.sign_in(c, "member")
    holder = api.add_folder(c, admin.user_id, "Holder")
    api.add_document(c, uploaded_by=admin.user_id, folder_id=holder)
    empty = api.add_folder(c, admin.user_id, "Empty")

    for signed in (admin, member):
        assert _delete(signed, holder).json()["error"]["code"] == "folder_not_empty"
    assert _delete(member, empty).status_code == 204
    assert _folder_audit(api, c) == [("folder.delete", "folder", empty, {})]


def test_an_unknown_contents_value_is_400(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    folder = api.add_folder(admin.customer_id, admin.user_id, "Folder")
    assert _delete(admin, folder, "forever").status_code == 400
    assert folder in _folders(db, admin.customer_id)


# --- safety ----------------------------------------------------------------------------------


def _waiting_item(
    db: Database, customer_id: str, upload_id: str, folder_id: str, state: str = "waiting"
) -> None:
    with db.customer(customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO folder_upload_items (customer_id, folder_upload_id, seq, path_key, name,"
                " folder_id, size_bytes, batch_id, state, failure_code)"
                " VALUES (:c, :u, 0, :k, 'a.pdf', :f, 10, gen_random_uuid(), :s,"
                " CASE WHEN :s = 'failed' THEN 'too_large' END)"
            ),
            {"c": customer_id, "u": upload_id, "k": path_key("A/a.pdf"), "f": folder_id, "s": state},
        )


def _quiet(db: Database, customer_id: str, upload_id: str, since: datetime) -> None:
    """Nothing planned, registered or sent in this upload since ``since`` (a closed tab)."""
    with db.customer(customer_id) as conn:
        params = {"c": customer_id, "u": upload_id, "t": since}
        conn.execute(
            text("UPDATE folder_uploads SET updated_at = :t WHERE customer_id = :c AND id = :u"), params
        )
        conn.execute(
            text(
                "UPDATE folder_upload_items SET updated_at = :t"
                " WHERE customer_id = :c AND folder_upload_id = :u"
            ),
            params,
        )


def test_trash_is_409_while_a_folder_upload_into_a_subfolder_has_files_waiting(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Folder")
    sub = api.add_folder(c, admin.user_id, "Uploaded", folder)
    api.add_document(c, uploaded_by=admin.user_id, folder_id=folder)
    finished = new_folder_upload(db, c, admin.user_id, folder_ids=[sub])
    _waiting_item(db, c, finished, sub, state="failed")  # an upload with nothing waiting is no bar
    running = new_folder_upload(db, c, admin.user_id, folder_ids=[sub])
    _waiting_item(db, c, running, sub)
    elsewhere = api.add_folder(c, admin.user_id, "Elsewhere")
    other = new_folder_upload(db, c, admin.user_id, folder_ids=[elsewhere])
    _waiting_item(db, c, other, elsewhere)
    folders, documents = _folders(db, c), _documents(db, c)

    reply = _delete(admin, folder, "trash")
    assert (reply.status_code, reply.json()["error"]) == (409, UPLOAD_RUNNING)
    assert (_folders(db, c), _documents(db, c)) == (folders, documents)

    # Keep deletes only the folder: the subfolder moves with its id, and its upload carries on.
    assert _delete(admin, folder, "keep").status_code == 200
    assert _folders(db, c)[sub] == (None, "Uploaded", "custom")


def test_keep_is_409_while_a_folder_upload_into_the_folder_itself_has_files_waiting(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Folder")
    api.add_document(c, uploaded_by=admin.user_id, folder_id=folder)
    running = new_folder_upload(db, c, admin.user_id, folder_ids=[folder])
    _waiting_item(db, c, running, folder)

    for contents in ("keep", "trash"):
        reply = _delete(admin, folder, contents)
        assert (reply.status_code, reply.json()["error"]) == (409, UPLOAD_RUNNING)

    with db.customer(c) as conn:
        conn.execute(
            text(
                "UPDATE folder_upload_items SET state = 'failed', failure_code = 'too_large'"
                " WHERE customer_id = :c AND folder_upload_id = :u"
            ),
            {"c": c, "u": running},
        )
    assert _delete(admin, folder, "trash").status_code == 200


def test_an_upload_quiet_for_over_an_hour_no_longer_blocks_the_delete(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Folder")
    api.add_document(c, uploaded_by=admin.user_id, folder_id=folder)
    upload = new_folder_upload(db, c, admin.user_id, folder_ids=[folder])
    _waiting_item(db, c, upload, folder)

    _quiet(db, c, upload, api.clock() - timedelta(minutes=59))
    assert _delete(admin, folder, "trash").status_code == 409  # still within the hour
    _quiet(db, c, upload, api.clock() - timedelta(minutes=61))  # the tab was closed
    assert _delete(admin, folder, "trash").status_code == 200


def test_the_audit_row_holds_ids_and_counts_only_never_a_name(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Secret folder")
    api.add_folder(c, admin.user_id, "Secret sub", folder)
    api.add_document(c, uploaded_by=admin.user_id, folder_id=folder, name="secret.pdf")

    assert _delete(admin, folder, "trash").status_code == 200

    assert _folder_audit(api, c) == [
        ("folder.delete", "folder", folder, {"contents": "trash", "files": 1, "folders": 1})
    ]
    assert "ecret" not in repr(api.audit_rows(c))


def test_the_summary_counts_the_live_files_folders_and_bytes_of_the_whole_subtree(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    member = api.sign_in(c, "member")
    folder = api.add_folder(c, admin.user_id, "Folder")
    sub = api.add_folder(c, admin.user_id, "Sub", folder)
    api.add_folder(c, admin.user_id, "Deeper", sub)
    _family(api, admin, sub, data=b"%PDF-1.4 ten")  # one file, two versions: 12 bytes each
    api.add_document(c, uploaded_by=admin.user_id, folder_id=folder, data=b"%PDF-1.4 x")
    api.add_document(
        c, uploaded_by=admin.user_id, folder_id=folder, deleted_at=api.clock(), deleted_by=admin.user_id
    )
    api.add_document(c, uploaded_by=admin.user_id)  # outside
    empty = api.add_folder(c, admin.user_id, "Empty")

    for signed in (admin, member):  # a member needs it too, to know before pressing Delete
        reply = signed.client.get(f"/api/folders/{folder}/summary")
        assert (reply.status_code, reply.json()) == (200, {"files": 2, "folders": 2, "bytes": 34})
    assert admin.client.get(f"/api/folders/{empty}/summary").json() == {"files": 0, "folders": 0, "bytes": 0}
    unknown = admin.client.get("/api/folders/7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e/summary")
    assert unknown.status_code == 404


def test_a_file_arriving_while_the_folder_is_deleted_is_409_and_changes_nothing(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    tree = _deep_tree(api, admin)
    folders, documents = _folders(db, c), _documents(db, c)
    real = trash.trash_in_folders

    def and_an_upload_lands(conn: Connection, customer_id: str, ids: list[str], **when: Any) -> int:
        trashed = real(conn, customer_id, ids, **when)
        insert_document(conn, customer_id, folder_id=tree["levels"][-1])  # no tree lock on an upload
        return trashed

    monkeypatch.setattr("kaagaz.db.trash.trash_in_folders", and_an_upload_lands)
    reply = _delete(admin, tree["folder"], "trash")

    assert (reply.status_code, reply.json()["error"]) == (
        409,
        {
            "code": "folder_changed",
            "message": "Something was added to this folder while it was being deleted."
            " Nothing was deleted. Try again.",
        },
    )
    assert (_folders(db, c), _documents(db, c)) == (folders, documents)


def test_a_failure_part_way_changes_nothing(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    tree = _deep_tree(api, admin)
    folders, documents = _folders(db, c), _documents(db, c)

    def broken(*_: object, **__: object) -> list[str]:  # the last step, after every write
        raise RuntimeError("storage fell over")

    monkeypatch.setattr("kaagaz.db.folders.prune_empty", broken)
    for contents in ("keep", "trash"):
        assert _delete(admin, tree["folder"], contents).status_code == 500

    assert (_folders(db, c), _documents(db, c)) == (folders, documents)
    assert _folder_audit(api, c) == []
