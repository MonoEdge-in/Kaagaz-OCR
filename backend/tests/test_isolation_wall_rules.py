"""PRD 7 PR 10c (#601): the rules that make walls safe.

* Moves (section 5, decision 18): a move that changes who can see a file or folder is an Admin's or
  Management's, or a Head's inside their department; anyone else gets 403 ``move_changes_access``.
  A move that changes nothing is anyone's. The same for a version join and, as a warning an Admin
  confirms, #594's keep-delete. The move previews name who loses and who gains, and the move's
  audit row counts the same.
* Uploads with no folder (decision 10): once the organisation keeps something to some people, into
  the uploader's one department, else held for them; on day one as before. Every path.
* Auto-filing gives the uploader the file when it files it where they can't see it, and its Undo
  holds it again.

The organisation is test_isolation_access_views' ``_walled`` one: Finance (Department only) with
its member and Head, Salaries (private, in Finance), Sales and Loose (open), Management, a reviewer
in no department (``plain``), an Admin and a person switched off."""

import hashlib
import threading
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.routers.documents import MOVE_REFUSED
from kaagaz.api.routers.folders import HIDDEN_INSIDE
from kaagaz.db import autofile, trash
from kaagaz.db import folders as folders_sql
from kaagaz.db import zip_unpacks as zip_sql
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord
from tests.db_support import insert_folder, name_session, wait_until_blocked
from tests.test_api_folder_upload_files import ResumableStorage, _fingerprint
from tests.test_api_support import CSRF, Api, Signed
from tests.test_isolation_access_api import _audit, _call, _code, _in_thread, _version
from tests.test_isolation_access_views import _SUGGEST, Org, _app, _expected, _seen, _walled
from tests.test_isolation_logins import WORKER
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_GRANT = "UPDATE customers SET autofile_granted = true, autofile_on = true WHERE id = :c"
_PLACE = "SELECT folder_id::text, held_for::text FROM documents WHERE id = :d"


def _pdf() -> bytes:
    return b"%PDF-1.7\n" + uuid.uuid4().hex.encode() * 8


def _place(db: Database, document: str) -> tuple[str | None, str | None]:
    folder, held = _owner(db, _PLACE, {"d": document})[0]
    return folder, held


def _ids(org: Org) -> dict[str, str]:
    return {name: signed.user_id for name, signed in org.people.items()}


def _target(org: Org, name: str | None) -> str | None:
    return None if name is None else org.folders[name]


def _moves(db: Database, org: Org, action: str) -> list[dict[str, Any]]:
    return [dict(row[4] or {}) for row in _audit(db, org.c, action)]


# --- moving a file -------------------------------------------------------------------------------

# (who, file, into, status): free moves, moves that change who can see it, a Head inside and outside
# their department, and a file or folder the person can't see (404 first).
DOCUMENT_MOVES = [
    ("member", "sales", "loose", 200),  # open to open: free, for anyone
    ("plain", "sales", "loose", 200),
    ("head", "sales", None, 200),  # open to the top level: free
    ("management", "fin", "sales", 200),  # Finance only to open: Management
    ("head", "fin", "sales", 403),  # out of the Head's department
    ("member", "fin", "sales", 403),
    ("member", "fin", None, 403),
    ("head", "fin", "salaries", 200),  # into a private folder of their own department
    ("management", "fin", "salaries", 200),
    ("member", "fin", "salaries", 404),  # a folder they can't see is one that isn't there
    ("admin", "sales", "finance", 200),  # open to Finance only: an Admin
    ("member", "sales", "finance", 403),
    ("head", "sales", "finance", 403),  # into their department, from another place
    ("plain", "sales", "finance", 404),
    ("plain", "fin", "loose", 404),  # a file they can't see
    ("member", "held", "sales", 200),  # held for them: theirs to file
    ("management", "held", "finance", 200),
]


@pytest.mark.parametrize(("who", "file", "into", "status"), DOCUMENT_MOVES)
def test_a_move_that_changes_who_sees_a_file_is_for_admins_management_and_heads(
    db: Database, who: str, file: str, into: str | None, status: int
) -> None:
    org = _walled(Api(db))
    document, folder, before = org.files[file], _target(org, into), _version(db, org.c)
    people = {name: _seen(_app(db), org.c, user) for name, user in _ids(org).items()}
    reply = _call(org.people[who], "PATCH", f"/api/documents/{document}", {"folder_id": folder})
    assert reply[0] == status, reply
    if status == 403:
        assert reply[1]["error"] == {"code": "move_changes_access", "message": MOVE_REFUSED}
    if status != 200:
        assert _place(db, document)[0] != folder and _version(db, org.c) == before
        assert _moves(db, org, "document.move") == []
        assert {name: _seen(_app(db), org.c, user) for name, user in _ids(org).items()} == people
        return
    assert _place(db, document)[0] == folder
    if who == "admin":  # moved where the Admin can't see it: kept, and said so
        assert reply[1] == {"id": document, "hidden": True}
    for name, user in _ids(org).items():  # the views follow the move
        assert _seen(_app(db), org.c, user) == _expected(db, org, user), name


def test_a_refused_move_is_the_same_reply_for_a_hidden_target_as_a_random_one(db: Database) -> None:
    org = _walled(Api(db))
    member, file = org.people["member"], org.files["fin"]
    hidden = _call(member, "PATCH", f"/api/documents/{file}", {"folder_id": org.folders["salaries"]})
    random = _call(member, "PATCH", f"/api/documents/{file}", {"folder_id": str(uuid.uuid4())})
    assert hidden == random and hidden[0] == 404


def test_rights_are_read_under_the_tree_lock(db: Database) -> None:
    """Management taken away while a move waits for the tree lock: the move is refused."""
    org = _walled(Api(db))
    management, file = org.people["management"], org.files["fin"]
    with db.customer(org.c) as conn:
        folders_sql._lock_tree(conn, org.c)
        thread, out = _in_thread(
            lambda: _call(management, "PATCH", f"/api/documents/{file}", {"folder_id": org.folders["sales"]})
        )
        thread.join(timeout=1.5)
        assert thread.is_alive()
        conn.execute(text("UPDATE users SET management = false WHERE id = :u"), {"u": management.user_id})
    thread.join(timeout=20)
    assert _code(out[0]) == (403, "move_changes_access"), out


# --- the previews and the audit row ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("who", "file", "into"), [("management", "fin", "sales"), ("admin", "sales", "finance")]
)
def test_the_file_preview_names_who_loses_and_gains_and_the_move_counts_the_same(
    db: Database, who: str, file: str, into: str
) -> None:
    org = _walled(Api(db))
    signed, document, folder = org.people[who], org.files[file], org.folders[into]
    before = {name: document in _seen(_app(db), org.c, user) for name, user in _ids(org).items()}
    status, preview = _call(signed, "GET", f"/api/documents/{document}/move-preview?folder_id={folder}")
    assert status == 200 and set(preview) == {"losing", "gaining", "files"} and preview["files"] == 1
    assert _call(signed, "PATCH", f"/api/documents/{document}", {"folder_id": folder})[0] == 200
    after = {name: _place_sees(db, org, name, document) for name in before}
    gaining = {org.people[n].user_id for n in before if after[n] and not before[n]}
    named = {each["id"] for each in preview["gaining"]}
    assert gaining <= named  # each person who now sees it was named (the switched-off one is never)
    [details] = _moves(db, org, "document.move")
    assert details == {
        "folder_id": folder,
        "losing": len(preview["losing"]),
        "gaining": len(preview["gaining"]),
    }


def _place_sees(db: Database, org: Org, name: str, document: str) -> bool:
    return document in _seen(_app(db), org.c, org.people[name].user_id)


def test_a_free_move_writes_no_counts(db: Database) -> None:
    org = _walled(Api(db))
    loose = org.folders["loose"]
    assert (
        _call(org.people["member"], "PATCH", f"/api/documents/{org.files['sales']}", {"folder_id": loose})[0]
        == 200
    )
    assert _moves(db, org, "document.move") == [{"folder_id": loose}]


@pytest.mark.parametrize(
    ("who", "file", "into", "status"),
    [
        ("admin", "sales", "finance", 200),
        ("admin", "fin", "sales", 404),  # an Admin who can't see the file: not found
        ("management", "fin", "salaries", 200),
        ("head", "fin", "salaries", 200),
        ("head", "fin", "sales", 403),  # out of their department
        ("member", "fin", "sales", 403),
        ("member", "sales", "loose", 403),  # previews are for those who may change who sees it
        ("plain", "fin", "sales", 404),
        ("off", "sales", "loose", 401),
    ],
)
def test_who_may_ask_for_a_file_preview(db: Database, who: str, file: str, into: str, status: int) -> None:
    """Visibility first (a hidden file is 404, as a random id), then an Admin, Management or a Head
    inside their department."""
    org = _walled(Api(db))
    url = f"/api/documents/{org.files[file]}/move-preview?folder_id={org.folders[into]}"
    assert _call(org.people[who], "GET", url)[0] == status


def test_a_hidden_file_or_folder_previews_as_a_random_id(db: Database) -> None:
    org, other = _walled(Api(db)), _walled(Api(db))
    plain, member = org.people["plain"], org.people["member"]
    sales = org.files["sales"]
    answers = [
        _call(plain, "GET", f"/api/documents/{org.files['fin']}/move-preview"),
        _call(plain, "GET", f"/api/documents/{uuid.uuid4()}/move-preview"),
        _call(plain, "GET", f"/api/documents/{other.files['sales']}/move-preview"),
        _call(member, "GET", f"/api/documents/{sales}/move-preview?folder_id={org.folders['salaries']}"),
        _call(member, "GET", f"/api/documents/{sales}/move-preview?folder_id={uuid.uuid4()}"),
        _call(member, "GET", f"/api/folders/{org.folders['salaries']}/move-preview"),
        _call(member, "GET", f"/api/folders/{uuid.uuid4()}/move-preview"),
    ]
    assert all(answer == answers[0] for answer in answers) and answers[0][0] == 404


# --- moving a folder -----------------------------------------------------------------------------


def _sub(db: Database, org: Org, name: str, parent: str | None) -> str:
    first = org.people["admin"].user_id
    with db.customer(org.c) as conn:
        made = insert_folder(conn, org.c, first, name, parent)
    org.folders[name] = made
    return made


# Who moves which folder where, and the status.
FOLDER_MOVES = [
    ("member", "box", "loose", 200),  # Sales to open Loose: free
    ("member", "box", "finance", 403),  # open to Finance only
    ("management", "box", "finance", 200),
    ("head", "box", "finance", 403),
    ("head", "salaries", "bills", 200),  # a private folder inside its own department: free
    ("member", "salaries", "bills", 404),
    ("head", "salaries", "sales", 403),  # its Head would change
    ("management", "salaries", "sales", 200),
    ("admin", "salaries", None, 200),
    # A private folder in open Sales, the member named on it, to open Loose: both places open, but
    # its Head would change (the department's), so not free.
    ("member", "vault", "loose", 403),
    ("management", "vault", "loose", 200),
]


@pytest.mark.parametrize(("who", "folder", "into", "status"), FOLDER_MOVES)
def test_a_folder_move_that_changes_who_sees_what_it_holds_needs_the_right(
    db: Database, who: str, folder: str, into: str | None, status: int
) -> None:
    org = _walled(Api(db))
    _sub(db, org, "box", org.folders["sales"])
    _sub(db, org, "bills", org.folders["finance"])
    vault = _sub(db, org, "vault", org.folders["sales"])
    _owner(db, "UPDATE folders SET private = true WHERE id = :f", {"f": vault})
    _owner(
        db,
        "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
        " VALUES (:c, :f, :u, :u)",
        {"c": org.c, "f": vault, "u": org.people["member"].user_id},
    )
    moved, target, before = org.folders[folder], _target(org, into), _version(db, org.c)
    reply = _call(org.people[who], "PATCH", f"/api/folders/{moved}", {"parent_id": target})
    assert reply[0] == status, reply
    if status == 403:
        assert reply[1]["error"] == {"code": "move_changes_access", "message": MOVE_REFUSED}
        assert _version(db, org.c) == before and _moves(db, org, "folder.move") == []
    if status == 200:
        for name, user in _ids(org).items():
            assert _seen(_app(db), org.c, user) == _expected(db, org, user), name


def test_the_folder_preview_counts_what_the_move_writes(db: Database) -> None:
    """Salaries (private) out of Finance into Sales: Finance's Head loses it, Sales' gains it; its
    named person keeps it."""
    org = _walled(Api(db))
    ids = _ids(org)
    _owner(
        db,
        "INSERT INTO department_members (customer_id, department_id, user_id, is_head, added_by)"
        " VALUES (:c, :d, :u, true, :u)",
        {"c": org.c, "d": org.folders["sales"], "u": ids["plain"]},
    )
    management, salaries, sales = org.people["management"], org.folders["salaries"], org.folders["sales"]
    status, preview = _call(management, "GET", f"/api/folders/{salaries}/move-preview?parent_id={sales}")
    assert status == 200
    assert [each["id"] for each in preview["losing"]] == [ids["head"]]
    assert [each["id"] for each in preview["gaining"]] == [ids["plain"]]
    assert preview["files"] == 1  # its live file (the other is in Trash)
    assert _call(management, "PATCH", f"/api/folders/{salaries}", {"parent_id": sales})[0] == 200
    assert _moves(db, org, "folder.move") == [{"parent_id": sales, "losing": 1, "gaining": 1}]
    assert org.files["sal"] in _seen(_app(db), org.c, ids["plain"])
    assert org.files["sal"] not in _seen(_app(db), org.c, ids["head"])
    assert org.files["sal"] in _seen(_app(db), org.c, ids["named"])


def test_a_private_folder_never_goes_into_another(db: Database) -> None:
    org = _walled(Api(db))
    _owner(db, "UPDATE folders SET private = true WHERE id = :f", {"f": org.folders["loose"]})
    reply = _call(
        org.people["admin"],
        "PATCH",
        f"/api/folders/{org.folders['loose']}",
        {"parent_id": org.folders["salaries"]},
    )
    assert _code(reply) == (409, "inside_private")


# --- a version join ------------------------------------------------------------------------------


@pytest.mark.parametrize(("who", "status"), [("member", 403), ("head", 403), ("management", 200)])
def test_a_join_that_changes_who_sees_the_file_needs_the_right(db: Database, who: str, status: int) -> None:
    """Sales' file joins Finance's family, so it moves into Finance (each of them sees both)."""
    org = _walled(Api(db))
    new, family = org.files["sales"], org.files["fin"]
    suggestion = str(_owner(db, _SUGGEST, {"c": org.c, "d": new, "f": family})[0][0])
    reply = _call(org.people[who], "POST", f"/api/version-suggestions/{suggestion}/accept", {})
    assert reply[0] == status, reply
    if status == 403:
        assert reply[1]["error"]["code"] == "move_changes_access"
        assert _place(db, new)[0] == org.folders["sales"]
        assert (
            _owner(db, "SELECT status FROM version_suggestions WHERE id = :s", {"s": suggestion})[0][0]
            == "pending"
        )
    else:
        assert _place(db, new)[0] == org.folders["finance"]


def test_a_join_inside_the_same_place_is_anyones(db: Database) -> None:
    org = _walled(Api(db))
    new = Api(db).add_document(
        org.c, name="sales2.pdf", folder_id=org.folders["sales"], uploaded_by=org.people["admin"].user_id
    )
    suggestion = str(_owner(db, _SUGGEST, {"c": org.c, "d": new, "f": org.files["sales"]})[0][0])
    reply = _call(org.people["plain"], "POST", f"/api/version-suggestions/{suggestion}/accept", {})
    assert reply[0] == 200, reply


# --- #594's keep-delete: a warning, then confirm --------------------------------------------------


def test_a_keep_delete_that_changes_who_sees_it_warns_then_goes_ahead_when_confirmed(db: Database) -> None:
    org = _walled(Api(db))
    box = _sub(db, org, "box", org.folders["sales"])
    inside = Api(db).add_document(
        org.c, name="box.pdf", folder_id=box, uploaded_by=org.people["admin"].user_id
    )
    admin, finance = org.people["admin"], org.folders["finance"]
    url = f"/api/folders/{box}?contents=keep&to={finance}"
    assert _code(_call(admin, "DELETE", url)) == (409, "access_would_change")
    assert _place(db, inside)[0] == box
    assert _call(admin, "DELETE", url + "&confirm=true")[0] == 200
    assert _place(db, inside)[0] == finance
    [details] = _moves(db, org, "folder.delete")
    assert details["confirmed"] is True and details["to"] == finance
    assert _code(_call(admin, "DELETE", f"/api/folders/{org.folders['loose']}?confirm=true")) == (
        400,
        "bad_request",
    )


# --- a folder holding what the person can't see ---------------------------------------------------


def test_a_folder_holding_what_the_person_cant_see_is_deleted_by_an_admin_only(db: Database) -> None:
    """The screens' contract: 403 ``hidden_inside`` (and ``move_changes_access`` above), exactly."""
    org = _walled(Api(db))
    box = _sub(db, org, "box", org.folders["sales"])
    secret = _sub(db, org, "secret", box)
    _owner(db, "UPDATE folders SET private = true WHERE id = :f", {"f": secret})
    reply = _call(org.people["member"], "DELETE", f"/api/folders/{box}")
    assert reply[0] == 403 and reply[1]["error"] == {"code": "hidden_inside", "message": HIDDEN_INSIDE}
    assert _code(_call(org.people["admin"], "DELETE", f"/api/folders/{box}")) == (409, "folder_not_empty")
    # A file in Trash they can't see counts too (Salaries' trashed file, its place kept).
    _owner(db, "UPDATE folders SET private = false WHERE id = :f", {"f": secret})
    assert _code(_call(org.people["member"], "DELETE", f"/api/folders/{secret}")) == (204, None)
    _owner(db, "UPDATE documents SET folder_id = :f WHERE id = :d", {"f": box, "d": org.files["trashed"]})
    assert _code(_call(org.people["member"], "DELETE", f"/api/folders/{box}")) == (403, "hidden_inside")
    assert _call(org.people["head"], "DELETE", f"/api/folders/{box}")[0] == 204  # the Head sees it


def test_the_refusal_codes_the_screens_map_are_exact() -> None:
    assert folders_sql.MoveChangesAccess().code == "move_changes_access"
    assert folders_sql.HiddenInside().code == "hidden_inside"
    assert MOVE_REFUSED == "You can't move this here, because it would change who can see it. Ask an admin."
    assert HIDDEN_INSIDE == "You can't delete this folder. Ask an admin."


# --- uploads with no folder ----------------------------------------------------------------------


def _browser(signed: Signed) -> str:
    reply = signed.client.post(
        "/api/documents", files={"file": ("a.pdf", _pdf(), "application/pdf")}, headers=CSRF
    )
    assert reply.status_code == 201, reply.text
    return str(reply.json()["document"]["id"])


def _resumable(signed: Signed) -> str:
    data = _pdf()
    start = {
        "file_name": "big.pdf",
        "file_size": len(data),
        "fingerprint": _fingerprint(data),
        "user_id": signed.user_id,
    }
    session = signed.client.post("/api/documents/upload-sessions", json=start, headers=CSRF).json()["id"]
    sent = signed.client.put(f"/api/documents/upload-sessions/{session}/parts/1", content=data, headers=CSRF)
    assert sent.status_code == 204
    done = signed.client.post(f"/api/documents/upload-sessions/{session}/complete", headers=CSRF)
    assert done.status_code == 201, done.text
    return str(done.json()["document"]["id"])


def _worker(db: Database, c: str, user: str) -> str:
    """A file the worker stores (a zip's, through the same insert), with no folder."""
    record = DocumentRecord(
        customer_id=c,
        document_id=str(uuid.uuid4()),
        sha256=hashlib.sha256(_pdf()).hexdigest(),
        size=10,
        detected_type="pdf",
        uploaded_by=user,
    )
    stored, created = PgDocuments(Database(db.engine, WORKER)).add_if_absent(record)
    assert created
    return stored.document_id


UPLOADS = {"browser": _browser, "resumable": _resumable}


@pytest.mark.parametrize("path", ["browser", "resumable", "worker"])
def test_an_upload_with_no_folder_goes_to_the_one_department_or_is_held(db: Database, path: str) -> None:
    org = _walled(Api(db, storage=ResumableStorage()))
    ids = _ids(org)

    def upload(who: str) -> str:
        if path == "worker":
            return _worker(db, org.c, ids[who])
        return UPLOADS[path](org.people[who])

    in_finance, held = upload("member"), upload("plain")
    assert _place(db, in_finance) == (org.folders["finance"], None)  # the member's one department
    assert _place(db, held) == (None, ids["plain"])  # in no department: held for them
    _owner(
        db,
        "INSERT INTO department_members (customer_id, department_id, user_id, is_head, added_by)"
        " VALUES (:c, :d, :u, false, :u)",
        {"c": org.c, "d": org.folders["sales"], "u": ids["member"]},
    )
    assert _place(db, upload("member")) == (None, ids["member"])  # two departments: held
    for name, user in ids.items():
        seen = _seen(_app(db), org.c, user)
        assert (held in seen) == (name in ("plain", "management")), name


@pytest.mark.parametrize("path", ["browser", "resumable", "worker"])
def test_on_day_one_an_upload_with_no_folder_stays_open(db: Database, path: str) -> None:
    api = Api(db, storage=ResumableStorage())
    c, _ = api.customer()
    member, other = api.sign_in(c), api.sign_in(c)
    with db.customer(c) as conn:
        finance = insert_folder(conn, c, member.user_id, "Finance")
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id = :f", {"f": finance})
    _owner(
        db,
        "INSERT INTO department_members (customer_id, department_id, user_id, is_head, added_by)"
        " VALUES (:c, :d, :u, false, :u)",
        {"c": c, "d": finance, "u": member.user_id},
    )
    document = _worker(db, c, member.user_id) if path == "worker" else UPLOADS[path](member)
    assert _place(db, document) == (None, None)
    assert document in _seen(_app(db), c, other.user_id)


def test_a_new_version_takes_its_familys_place(db: Database) -> None:
    """A version upload goes where its family is: held with a held family, open with an open one."""
    org = _walled(Api(db))
    member = org.people["member"]
    for family, held in ((org.files["held"], member.user_id), (org.files["top"], None)):
        reply = member.client.post(
            f"/api/documents/{family}/versions",
            files={"file": ("v2.pdf", _pdf(), "application/pdf")},
            headers=CSRF,
        )
        assert reply.status_code == 201, reply.text
        assert _place(db, reply.json()["document"]["id"]) == (None, held)


def test_a_held_zip_is_kept_as_a_file_not_unpacked_into_an_open_folder(db: Database) -> None:
    org = _walled(Api(db))
    held, filed = (
        _worker(db, org.c, org.people["plain"].user_id),
        _worker(db, org.c, org.people["member"].user_id),
    )
    with db.customer(org.c) as conn:
        found = {d: zip_sql.place(conn, org.c, d) for d in (held, filed)}
    kept, unpacked = found[held], found[filed]
    assert kept is not None and not kept.unpacks
    assert unpacked is not None and unpacked.unpacks


# --- auto-filing ---------------------------------------------------------------------------------


def _filed_invoice(db: Database, org: Org, who: str) -> str:
    """An invoice uploaded with no folder by ``who`` and read: auto-filing files it under Finance
    (an alias of Accounts and HR), as the worker does."""
    _owner(db, _GRANT, {"c": org.c})
    document = _worker(db, org.c, org.people[who].user_id)
    worker = PgDocuments(Database(db.engine, WORKER))
    _owner(db, "UPDATE documents SET doc_type = 'invoice' WHERE id = :d", {"d": document})
    worker.mark_ready(org.c, document, 1)
    worker.file_document(org.c, document)
    return document


def test_auto_filing_where_the_uploader_cant_see_gives_them_the_file_and_undo_holds_it_again(
    db: Database,
) -> None:
    org = _walled(Api(db))
    plain = org.people["plain"]
    document = _filed_invoice(db, org, "plain")
    folder, held = _place(db, document)
    assert held is None and folder is not None and folder != org.folders["finance"]
    assert (
        _owner(db, "SELECT department_id::text FROM documents WHERE id = :d", {"d": document})[0][0]
        == org.folders["finance"]
    )
    given = _owner(
        db, "SELECT user_id::text, given_by FROM document_shares WHERE document_id = :d", {"d": document}
    )
    assert [tuple(row) for row in given] == [(plain.user_id, None)]  # Kaagaz gave it
    assert document in _seen(_app(db), org.c, plain.user_id)
    [details] = _moves(db, org, "document.autofile")
    assert details["given"] == 1 and details["held_for"] == plain.user_id
    # Undo by someone it would not be held for: refused; by its holder: held again, not open.
    refused = _call(org.people["head"], "POST", f"/api/documents/{document}/autofile/undo", {})
    assert refused[0] == 403
    assert _call(plain, "POST", f"/api/documents/{document}/autofile/undo", {})[0] == 200
    assert _place(db, document) == (None, plain.user_id)
    assert document not in _seen(_app(db), org.c, org.people["member"].user_id)


def test_auto_filing_where_the_uploader_sees_gives_nothing(db: Database) -> None:
    org = _walled(Api(db))
    document = _filed_invoice(db, org, "member")  # into Finance, the member's department
    assert (
        _owner(db, "SELECT count(*) FROM document_shares WHERE document_id = :d", {"d": document})[0][0] == 0
    )
    assert "given" not in _moves(db, org, "document.autofile")[0]


def test_only_kaagaz_itself_gives_the_uploader_a_file(db: Database) -> None:
    """A person's transaction can't run kaagaz_give_uploader."""
    org = _walled(Api(db))
    with (
        pytest.raises(Exception, match="only Kaagaz itself"),
        _app(db).user(org.c, org.people["admin"].user_id) as conn,
    ):
        conn.execute(text("SELECT kaagaz_give_uploader(:d)"), {"d": org.files["fin"]})


def test_auto_filing_waits_for_a_move_holding_the_tree_lock_and_never_deadlocks(db: Database) -> None:
    """Two sessions: a person's move holds the tree lock and the family's; auto-filing (tree, then
    family, then the give's family lock again) waits, then files and gives, with no deadlock."""
    org = _walled(Api(db))
    _owner(db, _GRANT, {"c": org.c})
    document = _worker(db, org.c, org.people["plain"].user_id)
    worker = PgDocuments(Database(db.engine, WORKER))
    _owner(db, "UPDATE documents SET doc_type = 'invoice' WHERE id = :d", {"d": document})
    worker.mark_ready(org.c, document, 1)
    errors: list[BaseException] = []

    def filing() -> None:
        try:
            with Database(db.engine, WORKER).customer(org.c) as conn:
                name_session(conn, "p10c-filing")
                autofile.file_document(conn, org.c, document)
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    with db.customer(org.c) as conn:
        folders_sql._lock_tree(conn, org.c)
        assert trash.lock_family(conn, org.c, document) == document
        thread = threading.Thread(target=filing)
        thread.start()
        wait_until_blocked(db, application_name="p10c-filing")
    thread.join(timeout=20)
    assert not thread.is_alive() and errors == []
    assert (
        _owner(db, "SELECT count(*) FROM document_shares WHERE document_id = :d", {"d": document})[0][0] == 1
    )


# --- people, and Private's people dropped -----------------------------------------------------------


def test_a_file_held_for_a_person_switched_off_is_seen_by_management_only(db: Database) -> None:
    org = _walled(Api(db))
    ids = _ids(org)
    _owner(db, "UPDATE users SET disabled = true WHERE id = :u", {"u": ids["member"]})
    for name, user in ids.items():
        assert (org.files["held"] in _seen(_app(db), org.c, user)) == (name == "management"), name


def test_me_says_whether_the_person_is_in_a_live_department(db: Database) -> None:
    org = _walled(Api(db))
    expected = {"member": True, "head": True, "plain": False, "management": False, "admin": False}
    for name, inside in expected.items():
        me = org.people[name].client.get("/api/auth/me").json()
        assert me["access"]["in_department"] is inside, name


def test_making_a_folder_private_again_can_drop_people_named_before(db: Database) -> None:
    """The kept names: the Finance member, named before, dropped in the same step never sees the
    folder once it is private; one kept sees it again; an id not on the list is ignored."""
    org = _walled(Api(db))
    admin, ids, salaries = org.people["admin"], _ids(org), org.folders["salaries"]
    _owner(
        db,
        "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
        " VALUES (:c, :f, :u, :u)",
        {"c": org.c, "f": salaries, "u": ids["member"]},
    )
    assert _call(admin, "PUT", f"/api/folders/{salaries}/private", {"on": False})[0] == 200
    assert org.files["sal"] in _seen(_app(db), org.c, ids["member"])  # open: the member sees it
    body = {"on": True, "drop_named": [ids["member"], ids["management"], str(uuid.uuid4())]}
    status, reply = _call(admin, "PUT", f"/api/folders/{salaries}/private", body)
    assert status == 200, reply
    assert [each["id"] for each in reply["named"]] == [ids["named"]]
    assert org.files["sal"] not in _seen(_app(db), org.c, ids["member"])
    assert salaries not in _seen(_app(db), org.c, ids["member"], "visible_folders")
    assert org.files["sal"] in _seen(_app(db), org.c, ids["named"])
    removed = [row[3] for row in _audit(db, org.c, "access.named_remove")]
    assert removed == [ids["member"]]
    assert _code(
        _call(admin, "PUT", f"/api/folders/{salaries}/private", {"on": False, "drop_named": [ids["named"]]})
    ) == (400, "bad_request")
