"""PRD 7 PR 7 (#599): the folder tree, folder counts, Trash and duplicates follow who can see what.

PR 6's walled organisation (``test_isolation_access_views._walled``): Finance is Department only,
"Salaries" inside it is private and names one person, Sales is open. Each person sees in the tree,
the folder counts and Trash only what PRD 7's rule lets them see; a folder they can't see, live or
in Trash, answers every route exactly as a folder that never was, before any lock; and uploading
a copy of a file they can't see says only that it is in Kaagaz."""

import hashlib
import json
import threading
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.routers.documents import HIDDEN_COPY
from kaagaz.api.routers.folders import HIDDEN_INSIDE
from kaagaz.db.engine import Database
from kaagaz.search.scope import ScopeBody, resolve
from tests.db_support import insert_folder
from tests.test_api_delete_forever_grant import _grant
from tests.test_api_folder_upload_files import ResumableStorage, _fingerprint
from tests.test_api_folder_uploads import item, plan, register
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed
from tests.test_isolation_access_views import Org, _app, _expected, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_FOLDER_ROWS = "SELECT row_to_json(f)::text FROM folders f WHERE customer_id = :c ORDER BY id"
_TRASHED_ROWS = "SELECT row_to_json(t)::text FROM trashed_folders t WHERE customer_id = :c ORDER BY id"


def _reply(signed: Signed, method: str, path: str, body: Any = None) -> tuple[int, Any]:
    reply = signed.client.request(method, path, json=body, headers=CSRF)
    try:
        return reply.status_code, reply.json()
    except ValueError:
        return reply.status_code, reply.text


def _fill(value: Any, **ids: str) -> Any:
    return json.loads(json.dumps(value).replace("{h}", ids["h"]).replace("{v}", ids["v"]))


def _trash(org: Org, folder: str) -> None:
    # Changed on purpose (PR 9b, #606): an Admin deletes a folder with files they can't see, with the rule on.
    reply = org.people["admin"].client.delete(f"/api/folders/{folder}?contents=trash", headers=CSRF)
    assert reply.status_code == 200, reply.text


# --- the tree and its counts ----------------------------------------------------------------


def test_the_tree_holds_only_what_each_person_may_see(db: Database) -> None:
    org = _walled(Api(db))
    for name, signed in org.people.items():
        if name == "off":
            continue
        rows = {row["id"]: row for row in signed.client.get("/api/folders").json()["items"]}
        assert set(rows) == _expected(db, org, signed.user_id, folders=True), name
        for row in rows.values():  # a hidden parent never reaches the tree
            assert row["parent_id"] is None or row["parent_id"] in rows, name
    salaries, finance = org.folders["salaries"], org.folders["finance"]
    named = {r["id"]: r for r in org.people["named"].client.get("/api/folders").json()["items"]}
    assert named[salaries]["parent_id"] is None  # Finance is hidden: Salaries is at the top for them
    head = {r["id"]: r for r in org.people["head"].client.get("/api/folders").json()["items"]}
    assert head[salaries]["parent_id"] == finance
    plain = org.people["plain"].client.get("/api/folders").text
    assert "Finance" not in plain and "Salaries" not in plain


def test_a_folders_counts_are_what_the_person_sees_and_an_admins_the_true_totals(db: Database) -> None:
    org = _walled(Api(db))
    size = len(PDF_BYTES)
    finance = org.folders["finance"]
    summaries = {
        name: org.people[name].client.get(f"/api/folders/{finance}/summary").json()
        for name in ("member", "head", "admin")
    }
    # fin and given in Finance; sal in Salaries (hidden from the member); "trashed" is in Trash.
    assert summaries["member"] == {"files": 2, "folders": 0, "bytes": 2 * size}
    assert summaries["head"] == {"files": 3, "folders": 1, "bytes": 3 * size}
    assert summaries["admin"] == {"files": 3, "folders": 1, "bytes": 3 * size}  # true totals


# --- a hidden folder answers as one that never was ---------------------------------------------

_FOLDER_ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/api/folders/{h}/summary", None),
    ("GET", "/api/folders/{h}/activity", None),
    ("PATCH", "/api/folders/{h}", {"name": "Renamed"}),
    ("PATCH", "/api/folders/{h}", {"parent_id": None}),
    ("PATCH", "/api/folders/{h}", {"kind": "custom"}),
    ("PATCH", "/api/folders/{v}", {"parent_id": "{h}"}),
    ("POST", "/api/folders", {"name": "Inside", "parent_id": "{h}"}),
    ("DELETE", "/api/folders/{h}", None),
    ("GET", "/api/documents?folder_id={h}", None),
]


@pytest.mark.parametrize(
    ("method", "path", "body"), _FOLDER_ROUTES, ids=[f"{m} {p} {b}" for m, p, b in _FOLDER_ROUTES]
)
def test_every_folder_route_answers_a_hidden_folder_as_one_that_never_was(
    db: Database, method: str, path: str, body: Any
) -> None:
    """For a folder the plain reviewer can't see (Finance, Salaries inside it): the status and
    body of a random id, and every folder row as it was."""
    org = _walled(Api(db))
    plain, visible = org.people["plain"], org.folders["loose"]
    for hidden in (org.folders["finance"], org.folders["salaries"]):
        before = _owner(db, _FOLDER_ROWS, {"c": org.c})
        missing = _reply(plain, method, *_fill([path, body], h=str(uuid.uuid4()), v=visible))
        assert missing[0] == 404, missing
        assert _reply(plain, method, *_fill([path, body], h=hidden, v=visible)) == missing
        assert _owner(db, _FOLDER_ROWS, {"c": org.c}) == before


_TRASHED_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/documents/trash/folders/{h}"),
    ("POST", "/api/documents/trash/folders/{h}/restore"),
    ("DELETE", "/api/documents/trash/folders/{h}/permanent"),
    ("GET", "/api/documents?trashed=true&trashed_folder_id={h}"),
]


@pytest.mark.parametrize(("method", "path"), _TRASHED_ROUTES, ids=[f"{m} {p}" for m, p in _TRASHED_ROUTES])
def test_every_trashed_folder_route_answers_a_hidden_one_as_one_that_never_was(
    db: Database, method: str, path: str
) -> None:
    """Finance, with Salaries, in Trash: the plain reviewer (allowed to delete forever, password
    typed) gets a random id's reply for both, and nothing in Trash changes."""
    org = _walled(Api(db))
    plain = org.people["plain"]
    assert _grant(org.people["admin"], plain.user_id) == (200, True)
    _trash(org, org.folders["finance"])
    before = (_owner(db, _TRASHED_ROWS, {"c": org.c}), _owner(db, _FOLDER_ROWS, {"c": org.c}))
    for hidden in (org.folders["finance"], org.folders["salaries"]):
        missing = _reply(plain, method, path.format(h=uuid.uuid4()))
        assert missing[0] == 404, missing
        assert _reply(plain, method, path.format(h=hidden)) == missing
    assert (_owner(db, _TRASHED_ROWS, {"c": org.c}), _owner(db, _FOLDER_ROWS, {"c": org.c})) == before


def test_a_hidden_folder_is_refused_before_the_tree_lock(db: Database) -> None:
    """Another transaction holds this organisation's tree lock: a move of a hidden folder and a
    restore of a hidden one in Trash still answer at once (the check is a plain read first)."""
    org = _walled(Api(db))
    plain = org.people["plain"]
    with db.customer(org.c) as conn:
        trashed = insert_folder(conn, org.c, plain.user_id, "Old", org.folders["salaries"])
    reply = org.people["admin"].client.delete(f"/api/folders/{trashed}?contents=trash", headers=CSRF)
    assert reply.status_code == 200, reply.text
    calls = [
        ("PATCH", f"/api/folders/{org.folders['salaries']}", {"parent_id": None}),
        ("POST", f"/api/documents/trash/folders/{trashed}/restore", None),
    ]
    with db.engine.connect() as holder:
        holder.execute(text("SELECT 1 FROM customers WHERE id = :c FOR NO KEY UPDATE"), {"c": org.c})
        replies: list[tuple[int, Any]] = []
        worker = threading.Thread(target=lambda: replies.extend(_reply(plain, *call) for call in calls))
        worker.start()
        worker.join(timeout=20)
        blocked = worker.is_alive()
        holder.rollback()
    worker.join()
    assert not blocked, "a hidden folder waited for the tree lock"
    assert [status for status, _ in replies] == [404, 404]


def test_uploads_into_a_hidden_folder_answer_as_for_one_that_never_was(db: Database) -> None:
    """A file into Finance or as Finance's department, and a folder upload into Salaries."""
    api = Api(db)
    org = _walled(api)
    plain = org.people["plain"]
    stored = api.storage.count()
    for field, hidden in (("folder_id", org.folders["salaries"]), ("department_id", org.folders["finance"])):
        replies = [
            plain.client.post(
                "/api/documents",
                data={field: target},
                files={"file": ("bill.pdf", PDF_BYTES, "application/pdf")},
                headers=CSRF,
            )
            for target in (str(uuid.uuid4()), hidden)
        ]
        assert replies[0].status_code == 404
        assert (replies[1].status_code, replies[1].json()) == (replies[0].status_code, replies[0].json())
    planned = [
        plan(plain, parent_folder_id=target) for target in (str(uuid.uuid4()), org.folders["salaries"])
    ]
    assert planned[0].status_code == 404
    assert (planned[1].status_code, planned[1].json()) == (planned[0].status_code, planned[0].json())
    assert api.storage.count() == stored


def test_ask_scoped_to_a_hidden_folder_is_scoped_as_to_one_that_never_was(db: Database) -> None:
    org = _walled(Api(db))
    plain = org.people["plain"].user_id
    with _app(db).user(org.c, plain) as conn:
        scoped = [
            resolve(conn, org.c, ScopeBody(folder_id=uuid.UUID(f), include_subfolders=True), timezone="UTC")
            for f in (str(uuid.uuid4()), org.folders["finance"], org.folders["sales"])
        ]
    assert scoped[0].folder_ids == scoped[1].folder_ids == ()
    assert scoped[2].folder_ids == (org.folders["sales"],)


# --- Trash -----------------------------------------------------------------------------------


def test_trash_shows_each_person_only_what_they_could_see(db: Database) -> None:
    """Finance (with Salaries) goes to Trash. Each person's Trash items, their counts and Trash's
    loose files follow the rule; the file given to the plain reviewer alone, inside hidden
    Finance, is a loose file for them."""
    org = _walled(Api(db))
    _trash(org, org.folders["finance"])
    finance, salaries, f = org.folders["finance"], org.folders["salaries"], org.files
    expected = {  # items: (files, folders); loose files
        "management": ({finance: (3, 1)}, {f["trashed"]}),
        "head": ({finance: (3, 1)}, {f["trashed"]}),
        "member": ({finance: (2, 0)}, set()),
        "named": ({salaries: (1, 0)}, {f["trashed"]}),
        "plain": ({}, {f["given"]}),
        "admin": ({finance: (0, 1)}, set()),
    }
    for name, (items, loose) in expected.items():
        client = org.people[name].client
        shown = client.get("/api/documents/trash/folders").json()["items"]
        assert {i["id"]: (i["files"], i["folders"]) for i in shown} == items, name
        files = client.get("/api/documents?trashed=true&limit=100").json()["items"]
        assert {i["id"] for i in files} == loose, name
    plain = org.people["plain"].client
    assert "Finance" not in plain.get("/api/documents/trash/folders").text
    opened = org.people["named"].client.get(f"/api/documents/trash/folders/{salaries}").json()
    assert opened["path"] == [{"id": salaries, "name": "Salaries"}]  # not "Finance / Salaries"
    assert "Finance" not in json.dumps(opened)
    inside = org.people["member"].client.get(f"/api/documents?trashed=true&trashed_folder_id={finance}")
    assert {i["id"] for i in inside.json()["items"]} == {f["fin"], f["given"]}


def test_a_trashed_folder_under_a_hidden_live_folder_shows_no_place(db: Database) -> None:
    org = _walled(Api(db))
    _trash(org, org.folders["salaries"])
    salaries = org.folders["salaries"]
    for name, place in (("named", None), ("head", ["Finance"])):
        shown = org.people[name].client.get("/api/documents/trash/folders").json()["items"]
        assert [(i["id"], i["was_in"]) for i in shown] == [(salaries, place)], name


def test_only_an_admin_deletes_forever_a_trashed_folder_holding_what_they_cant_see(db: Database) -> None:
    """The member may delete forever, but Finance holds Salaries, hidden from them: 403 and
    nothing goes. The head sees all of it; an Admin may always."""
    org = _walled(Api(db))
    admin = org.people["admin"]
    for name in ("member", "head"):
        assert _grant(admin, org.people[name].user_id) == (200, True)
    _trash(org, org.folders["finance"])
    finance = org.folders["finance"]
    before = _owner(db, "SELECT count(*) FROM documents WHERE customer_id = :c", {"c": org.c})
    refused = org.people["member"].client.delete(
        f"/api/documents/trash/folders/{finance}/permanent", headers=CSRF
    )
    assert (refused.status_code, refused.json()["error"]) == (
        403,
        {"code": "hidden_inside", "message": HIDDEN_INSIDE},  # Changed on purpose (#601): the screens' code
    )
    assert _owner(db, "SELECT count(*) FROM documents WHERE customer_id = :c", {"c": org.c}) == before
    gone = org.people["head"].client.delete(f"/api/documents/trash/folders/{finance}/permanent", headers=CSRF)
    assert gone.status_code == 204, gone.text
    assert _owner(db, "SELECT count(*) FROM trashed_folders WHERE id = :f", {"f": finance}) == [(0,)]


def test_an_admin_deletes_forever_a_trashed_folder_holding_files_they_cant_open(db: Database) -> None:
    org = _walled(Api(db))
    admin = org.people["admin"]
    _trash(org, org.folders["finance"])
    # Changed on purpose (PR 9b, #606): an Admin deletes forever files they can't see, with the rule on.
    reply = admin.client.delete(
        f"/api/documents/trash/folders/{org.folders['finance']}/permanent", headers=CSRF
    )
    assert reply.status_code == 204, reply.text


def test_restore_replies_name_no_folder_the_person_cant_see(db: Database) -> None:
    """A file given to the plain reviewer alone, in private "Vault" (top level), comes back with
    Vault: the reply names no folder. A private "Notes" in Finance comes back for the person named
    on it: its place, Finance, is not named, and only what they see is counted."""
    api = Api(db)
    org = _walled(api)
    plain, named = org.people["plain"], org.people["named"]
    with db.customer(org.c) as conn:
        vault = insert_folder(conn, org.c, plain.user_id, "Vault")
        notes = insert_folder(conn, org.c, named.user_id, "Notes", org.folders["finance"])
    _owner(db, "UPDATE folders SET private = true WHERE id IN (:v, :n)", {"v": vault, "n": notes})
    _owner(
        db,
        "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
        " VALUES (:c, :n, :u, :u)",
        {"c": org.c, "n": notes, "u": named.user_id},
    )
    mine = api.add_document(org.c, name="mine.pdf", folder_id=vault, uploaded_by=plain.user_id)
    note = api.add_document(org.c, name="note.pdf", folder_id=notes, uploaded_by=named.user_id)
    _owner(
        db,
        "INSERT INTO document_shares (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :u)",
        {"c": org.c, "d": mine, "u": plain.user_id},
    )
    for folder in (vault, notes):
        _trash(org, folder)
    restored = plain.client.post(f"/api/documents/{mine}/restore", headers=CSRF)
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored_folder"] is None and "Vault" not in restored.text
    back = named.client.post(f"/api/documents/trash/folders/{notes}/restore", headers=CSRF)
    assert back.status_code == 200, back.text
    assert (back.json()["location"], back.json()["files"], back.json()["folders"]) == (None, 1, 0)
    assert "Finance" not in back.text
    assert note in {i["id"] for i in named.client.get("/api/documents?limit=100").json()["items"]}


def test_the_feeds_folder_trash_and_restore_counts_are_shown_to_no_one_but_the_admins_log(
    db: Database,
) -> None:
    """Decision 14 on #599: the counts in a folder's Trash and restore entries. The shown feeds
    (Home, a folder's Activity) carry no detail of these; the admin's log does."""
    org = _walled(Api(db))
    _trash(org, org.folders["sales"])
    restored = org.people["admin"].client.post(
        f"/api/documents/trash/folders/{org.folders['sales']}/restore", headers=CSRF
    )
    assert restored.status_code == 200, restored.text
    for name in ("management", "member", "plain"):
        feed = org.people[name].client.get("/api/dashboard").json()["activity"]
        entries = [e for e in feed if e["action"] in ("folder.trash", "folder.restore")]
        assert entries and all(e["details"] == {} for e in entries), name
    log = org.people["admin"].client.get("/api/admin/audit?limit=100").json()["items"]
    assert {e["details"]["files"] for e in log if e["action"] == "folder.trash"} == {2}


# --- duplicates ------------------------------------------------------------------------------

_COPY = b"%PDF-1.7\n" + b"1" * 200


def _as_copy(db: Database, document: str, data: bytes) -> None:
    """``document`` now holds ``data``: an upload of these bytes is a duplicate of it."""
    _owner(
        db,
        "UPDATE documents SET sha256 = :h, size_bytes = :s WHERE id = :d",
        {"h": hashlib.sha256(data).hexdigest(), "s": len(data), "d": document},
    )


def test_uploading_a_copy_of_a_hidden_file_says_only_that_it_is_in_kaagaz(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    plain = org.people["plain"].client
    _as_copy(db, org.files["fin"], _COPY)
    _as_copy(db, org.files["sales"], _COPY + b"2")
    stored = api.storage.count()

    def upload(data: bytes, path: str = "/api/documents") -> Any:
        return plain.post(path, files={"file": ("copy.pdf", data, "application/pdf")}, headers=CSRF)

    hidden = upload(_COPY)
    assert (hidden.status_code, hidden.json()) == (200, HIDDEN_COPY)
    as_version = upload(_COPY, f"/api/documents/{org.files['sales']}/versions")
    assert (as_version.status_code, as_version.json()) == (200, HIDDEN_COPY)
    seen = upload(_COPY + b"2")
    assert (seen.status_code, seen.json()["document"]["id"]) == (200, org.files["sales"])
    assert api.storage.count() == stored


def test_a_folder_uploads_copy_of_a_hidden_file_names_no_document(db: Database) -> None:
    """ "Add new files only": a file whose hash is Salaries' file is already in, and its item, the
    item list and the item's reply say only that it is hidden."""
    org = _walled(Api(db))
    plain = org.people["plain"]
    sha = hashlib.sha256(org.files["sal"].encode()).hexdigest()  # Api.add_document's hash
    planned = plan(plain, only_new=True).json()
    upload, folder = planned["id"], planned["folder_ids"][0]
    reply = register(plain, upload, [item("A/sal.pdf", folder, len(PDF_BYTES), sha256=sha)])
    assert reply.status_code == 200, reply.text
    (row,) = reply.json()["items"]
    assert (row["state"], row["document_id"], row["document"], row["hidden"]) == (
        "duplicate",
        None,
        None,
        True,
    )
    (listed,) = plain.client.get(f"/api/folder-uploads/{upload}/items").json()["items"]
    assert listed == row
    sent = plain.client.post(
        f"/api/documents?folder_upload_item_id={row['id']}",
        files={"file": ("sal.pdf", PDF_BYTES, "application/pdf")},
        headers=CSRF,
    )
    assert sent.status_code == 200, sent.text
    assert (sent.json()["document"], sent.json()["hidden"], sent.json()["item"]) == (None, True, row)
    assert org.files["sal"] not in sent.text and org.files["sal"] not in reply.text


def test_a_stored_reply_sent_again_names_no_file_the_person_can_no_longer_see(db: Database) -> None:
    """A resumable upload's reply is kept for a lost reply; the file is locked since (nobody given
    it), so the reply sent again, by complete or by Retry, names it no more."""
    api = Api(db, storage=ResumableStorage())
    org = _walled(api)
    plain = org.people["plain"]
    sessions = "/api/documents/upload-sessions"
    start = {
        "file_name": "big.pdf",
        "file_size": len(PDF_BYTES),
        "fingerprint": _fingerprint(PDF_BYTES),
        "user_id": plain.user_id,
        "folder_id": org.folders["loose"],
    }
    session = plain.client.post(sessions, json=start, headers=CSRF).json()["id"]
    assert (
        plain.client.put(f"{sessions}/{session}/parts/1", content=PDF_BYTES, headers=CSRF).status_code == 204
    )
    first = plain.client.post(f"{sessions}/{session}/complete", headers=CSRF)
    assert first.status_code == 201, first.text
    document = first.json()["document"]["id"]
    _owner(
        db,
        # Changed on purpose (#605, 0093): a locked version is never ready.
        "UPDATE documents SET locked_at = now(), locked_by = :u, status = 'locked' WHERE id = :d",
        {"u": org.people["admin"].user_id, "d": document},
    )
    again = plain.client.post(f"{sessions}/{session}/complete", headers=CSRF)
    retried = plain.client.post(sessions, json={**start, "resume_session_id": session}, headers=CSRF)
    for reply in (again, retried):
        assert reply.status_code == 200, reply.text
        assert (reply.json()["document"], reply.json()["hidden"]) == (None, True)
        assert document not in reply.text
