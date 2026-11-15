"""PRD 7 PR 7 (#599), its two reviews: each place they found that named, counted or changed what a
person can't see, and the tests their mutation runs showed were missing.

PR 6's walled organisation (``test_isolation_access_views._walled``): Finance is Department only,
"Salaries" inside it is private and names one person (``named``), Sales is open. Restores name no
hidden folder and count only what the person sees; a word suggestion on a hidden file, a move into
a hidden folder and a hidden department answer as if they were not there; a deleted Department only
department's Trash stays closed (fail closed); and the checks each mutation run broke are pinned."""

import hashlib
import uuid

import pytest

from kaagaz.api.routers.documents import ASK_AN_ADMIN
from kaagaz.api.routers.folders import HIDDEN_INSIDE
from kaagaz.db.engine import Database
from kaagaz.search.scope import EVERYTHING, FileFilters, narrow
from tests.db_support import insert_folder
from tests.test_api_delete_forever_grant import _confirm, _grant
from tests.test_api_folder_upload_files import ResumableStorage, _fingerprint
from tests.test_api_folder_uploads import TREE, item, plan, register
from tests.test_api_support import CSRF, PDF_BYTES, Api
from tests.test_isolation_access_views import Org, _app, _seen, _walled
from tests.test_isolation_logins import WORKER
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_SESSIONS = "/api/documents/upload-sessions"
_DATA = b"%PDF-1.7\n" + b"7" * 300


def _trash(org: Org, folder: str) -> None:
    reply = org.people["admin"].client.delete(f"/api/folders/{folder}?contents=trash", headers=CSRF)
    assert reply.status_code == 200, reply.text


def _private(db: Database, org: Org, folder: str, *named: str) -> None:
    _owner(db, "UPDATE folders SET private = true WHERE id = :f", {"f": folder})
    for user in named:
        _owner(
            db,
            "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
            " VALUES (:c, :f, :u, :u)",
            {"c": org.c, "f": folder, "u": user},
        )


def _lock(db: Database, org: Org, document: str) -> None:
    _owner(
        db,
        # Changed on purpose (#605, 0093): a locked version is never ready.
        "UPDATE documents SET locked_at = now(), locked_by = :u, status = 'locked' WHERE id = :d",
        {"u": org.people["admin"].user_id, "d": document},
    )


# --- restores ----------------------------------------------------------------------------------


def test_restoring_a_folder_under_a_hidden_one_names_neither_nor_counts_the_hidden(db: Database) -> None:
    """Finance > Payroll > Salaries2 (private, names ``named``, holding their file and one locked
    since). Payroll goes to Trash; ``named`` sees Salaries2 as their own item and restores it: the
    reply is Salaries2, no place, no rename or move of the folder above, one file."""
    api = Api(db)
    org = _walled(api)
    named = org.people["named"]
    with db.customer(org.c) as conn:
        payroll = insert_folder(conn, org.c, named.user_id, "Payroll", org.folders["finance"])
        inner = insert_folder(conn, org.c, named.user_id, "Salaries2", payroll)
    _private(db, org, inner, named.user_id)
    api.add_document(org.c, name="slip.pdf", folder_id=inner, uploaded_by=named.user_id)
    _lock(db, org, api.add_document(org.c, name="sealed.pdf", folder_id=inner, uploaded_by=named.user_id))
    _trash(org, payroll)
    items = named.client.get("/api/documents/trash/folders").json()["items"]
    assert [(i["id"], i["can_restore"]) for i in items] == [(inner, True)], items
    back = named.client.post(f"/api/documents/trash/folders/{inner}/restore", headers=CSRF)
    assert back.status_code == 200, back.text
    body = back.json()
    assert (body["folder"]["id"], body["folder"]["parent_id"], body["location"]) == (inner, None, None)
    assert (body["renamed"], body["relocated"], body["files"], body["folders"]) == (False, False, 1, 0)
    for hidden in ("Payroll", "Finance", payroll, org.folders["finance"]):
        assert hidden not in back.text


def test_a_restore_that_brings_back_a_hidden_department_says_only_ask_an_admin(db: Database) -> None:
    """Finance (with Salaries) is in Trash; ``named``, who uploaded Salaries' files, may not
    restore Salaries (Finance comes back with it, an Admin's), and is not told Finance is a
    department, nor its name; the item says it can't be restored by them."""
    api = Api(db)
    org = _walled(api)
    named = org.people["named"]
    salaries = org.folders["salaries"]
    _owner(
        db, "UPDATE documents SET uploaded_by = :u WHERE folder_id = :f", {"u": named.user_id, "f": salaries}
    )
    head = org.people["head"]
    theirs = api.add_document(
        org.c, name="plan.pdf", folder_id=org.folders["finance"], uploaded_by=head.user_id
    )
    _trash(org, org.folders["finance"])
    items = named.client.get("/api/documents/trash/folders").json()["items"]
    assert [(i["id"], i["can_restore"]) for i in items] == [(salaries, False)], items
    back = named.client.post(f"/api/documents/trash/folders/{salaries}/restore", headers=CSRF)
    assert (back.status_code, back.json()["error"]) == (403, {"code": "forbidden", "message": ASK_AN_ADMIN})
    sal = org.files["sal"]
    one = named.client.post(f"/api/documents/{sal}/restore", headers=CSRF)
    assert (one.status_code, one.json()["error"]["message"]) == (403, ASK_AN_ADMIN)
    for reply in (back, one):
        assert "Finance" not in reply.text and "department" not in reply.text
    seen = head.client.post(f"/api/documents/{theirs}/restore", headers=CSRF)  # who sees Finance is told
    assert seen.json()["error"]["message"] == "Its folder is a department, which only an admin can restore."


def test_no_reply_carries_a_hidden_parents_id(db: Database) -> None:
    """``GET /folders`` gives Salaries a null parent for ``named`` (Finance is hidden); so do the
    replies to a rename, a new folder inside it and a restore."""
    org = _walled(Api(db))
    named, salaries, finance = org.people["named"], org.folders["salaries"], org.folders["finance"]
    renamed = named.client.patch(f"/api/folders/{salaries}", json={"name": "Payslips"}, headers=CSRF)
    made = named.client.post("/api/folders", json={"name": "Inside", "parent_id": salaries}, headers=CSRF)
    assert (renamed.status_code, renamed.json()["parent_id"]) == (200, None)
    assert (made.status_code, made.json()["parent_id"]) == (201, salaries)  # one they see is named
    _trash(org, salaries)
    _owner(
        db,
        "UPDATE documents SET uploaded_by = :u WHERE trashed_folder_id = :f",
        {"u": named.user_id, "f": salaries},
    )
    back = named.client.post(f"/api/documents/trash/folders/{salaries}/restore", headers=CSRF)
    assert back.status_code == 200, back.text
    assert back.json()["folder"]["parent_id"] is None
    for reply in (renamed, made, back):
        assert finance not in reply.text


# --- answers as for one that never was ----------------------------------------------------------


def test_deciding_a_word_suggestion_of_a_hidden_file_answers_as_a_random_id(db: Database) -> None:
    org = _walled(Api(db))
    plain = org.people["plain"]  # a reviewer who can't see Finance's file
    suggestion = _owner(
        db,
        "INSERT INTO organization_word_suggestions (customer_id, submitted_by, word, word_fold,"
        " meaning, meaning_fold, document_id, page_no, status)"
        " VALUES (:c, :u, 'secretword', 'secretword', 'meaning', 'meaning', :d, 1, 'pending') RETURNING id",
        {"c": org.c, "u": org.people["member"].user_id, "d": org.files["fin"]},
    )[0][0]
    assert "secretword" not in plain.client.get("/api/organization/words").text
    body = {"decision": "reject"}
    missing = plain.client.post(f"/api/organization/words/{uuid.uuid4()}/decision", json=body, headers=CSRF)
    hidden = plain.client.post(f"/api/organization/words/{suggestion}/decision", json=body, headers=CSRF)
    assert (hidden.status_code, hidden.json()) == (missing.status_code, missing.json())
    assert _owner(
        db, "SELECT status FROM organization_word_suggestions WHERE id = :s", {"s": suggestion}
    ) == [("pending",)]


def test_moving_a_file_into_a_hidden_folder_answers_as_a_random_folder(db: Database) -> None:
    """The plain reviewer sees given.pdf (given to them) but neither Salaries nor Finance."""
    org = _walled(Api(db))
    plain, given = org.people["plain"], org.files["given"]
    before = _owner(db, "SELECT row_to_json(d)::text FROM documents d WHERE id = :d", {"d": given})
    missing = plain.client.patch(
        f"/api/documents/{given}", json={"folder_id": str(uuid.uuid4())}, headers=CSRF
    )
    hidden = plain.client.patch(
        f"/api/documents/{given}", json={"folder_id": org.folders["salaries"]}, headers=CSRF
    )
    assert missing.status_code == 404
    assert (hidden.status_code, hidden.json()) == (missing.status_code, missing.json())
    assert _owner(db, "SELECT row_to_json(d)::text FROM documents d WHERE id = :d", {"d": given}) == before


def test_a_folder_upload_item_whose_folder_became_hidden_is_gone(db: Database) -> None:
    """The plain reviewer's folder upload; its folder is made private since (nobody named): its
    waiting file answers as for a folder deleted since, folder_gone, and is never stored."""
    api = Api(db, storage=ResumableStorage())
    org = _walled(api)
    plain = org.people["plain"]
    planned = plan(plain).json()
    folder = planned["folder_ids"][0]
    (row,) = register(plain, planned["id"], [item("A/bill.pdf", folder, len(PDF_BYTES))]).json()["items"]
    _private(db, org, folder)
    start = {
        "file_name": "bill.pdf",
        "file_size": len(PDF_BYTES),
        "fingerprint": _fingerprint(PDF_BYTES),
        "user_id": plain.user_id,
        "folder_upload_item_id": row["id"],
    }
    reply = plain.client.post(_SESSIONS, json=start, headers=CSRF)
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "folder_gone"), reply.text


def test_the_previous_folder_upload_names_no_hidden_parent(db: Database) -> None:
    """The member uploaded folder A into Finance; since, they left Finance and were named on A
    (made private): A is still theirs to continue, Finance is not named."""
    org = _walled(Api(db))
    member = org.people["member"]
    planned = plan(member, parent_folder_id=org.folders["finance"])
    assert planned.status_code == 200, planned.text
    root = planned.json()["root_folder_id"]
    _owner(db, "DELETE FROM department_members WHERE user_id = :u", {"u": member.user_id})
    _private(db, org, root, member.user_id)
    found = member.client.get("/api/folder-uploads/previous", params={"source_name": TREE[0]["name"]}).json()
    assert (found["previous"]["root_folder_id"], found["previous"]["parent_folder_id"]) == (root, None)
    assert org.folders["finance"] not in str(found)
    _owner(db, "DELETE FROM private_folder_members WHERE user_id = :u", {"u": member.user_id})
    gone = member.client.get("/api/folder-uploads/previous", params={"source_name": TREE[0]["name"]}).json()
    assert gone == {"previous": None}  # a root they can't see now is passed over


def test_ask_filtered_to_a_hidden_department_matches_as_an_unknown_one(db: Database) -> None:
    """The file given to the plain reviewer sits in Finance, which they can't see."""
    org = _walled(Api(db))
    with _app(db).user(org.c, org.people["plain"].user_id) as conn:
        found = [
            narrow(conn, org.c, EVERYTHING, FileFilters(department_id=uuid.UUID(d))).document_ids
            for d in (str(uuid.uuid4()), org.folders["finance"], org.folders["sales"])
        ]
    assert found[0] == found[1] and org.files["given"] not in (found[1] or ())
    assert org.files["sales"] in (found[2] or ())


# --- the review queue by document and Verify all ------------------------------------------------


def _reviewable(api: Api, org: Org) -> dict[str, str]:
    values = {}
    for name in ("fin", "sales"):
        api.add_page(org.c, org.files[name])
        values[name] = api.add_value(org.c, org.files[name])
    return values


def test_the_review_queue_of_a_hidden_document_is_empty_as_for_a_random_one(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    _reviewable(api, org)
    plain = org.people["plain"].client
    missing = plain.get(f"/api/review?document_id={uuid.uuid4()}").json()
    hidden = plain.get(f"/api/review?document_id={org.files['fin']}").json()
    assert hidden == missing == {"items": [], "total": 0}
    assert plain.get(f"/api/review?document_id={org.files['sales']}").json()["total"] == 1


def test_verify_all_never_verifies_a_value_of_a_hidden_file(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    values = _reviewable(api, org)
    reply = org.people["plain"].client.post("/api/review/verify-all", json={}, headers=CSRF)
    # The hidden value is never even picked (nor locked): not one left out, as for a queue of one.
    assert (reply.status_code, reply.json()) == (200, {"verified": 1, "skipped": [], "left": 0}), reply.text
    label = "SELECT label FROM extracted_values WHERE id = :v"
    assert _owner(db, label, {"v": values["fin"]}) == [("needs_review",)]
    assert _owner(db, label, {"v": values["sales"]}) != [("needs_review",)]  # the visible one was


# --- delete forever ------------------------------------------------------------------------------


def test_a_member_cannot_delete_forever_a_trashed_folder_holding_only_a_hidden_file(db: Database) -> None:
    """Sales is open; its locked file is hidden from the member. No hidden folder inside."""
    org = _walled(Api(db))
    member = org.people["member"]
    assert _grant(org.people["admin"], member.user_id) == (200, True)
    assert _confirm(member) == 204
    _trash(org, org.folders["sales"])
    shown = member.client.get("/api/documents/trash/folders").json()["items"]
    assert [(i["id"], i["files"]) for i in shown] == [(org.folders["sales"], 1)], shown
    refused = member.client.delete(
        f"/api/documents/trash/folders/{org.folders['sales']}/permanent", headers=CSRF
    )
    assert (refused.status_code, refused.json()["error"]["message"]) == (403, HIDDEN_INSIDE), refused.text
    assert _owner(db, "SELECT count(*) FROM documents WHERE id = :d", {"d": org.files["locked"]}) == [(1,)]


def test_a_member_cannot_delete_forever_a_trashed_folder_holding_only_a_hidden_folder(db: Database) -> None:
    """Loose is open and holds the member's visible file; inside it, an empty private folder nobody
    is named on, hidden from the member."""
    org = _walled(Api(db))
    member = org.people["member"]
    with db.customer(org.c) as conn:
        vault = insert_folder(conn, org.c, member.user_id, "Vault", org.folders["loose"])
    _private(db, org, vault)
    assert _grant(org.people["admin"], member.user_id) == (200, True)
    assert _confirm(member) == 204
    _trash(org, org.folders["loose"])
    refused = member.client.delete(
        f"/api/documents/trash/folders/{org.folders['loose']}/permanent", headers=CSRF
    )
    assert (refused.status_code, refused.json()["error"]["message"]) == (403, HIDDEN_INSIDE), refused.text
    assert _owner(db, "SELECT count(*) FROM trashed_folders WHERE id = :f", {"f": vault}) == [(1,)]


# --- Trash of a department deleted since: fail closed (0087) -------------------------------------


def test_trash_of_a_department_only_department_deleted_since_stays_closed(db: Database) -> None:
    """Budgets (in Finance, Department only) goes to Trash with its file; then Finance itself is
    deleted, emptied first (#594: a keep-delete of Finance is refused now). The organisation still
    keeps something to some people (Legal is Department only too), so the plain reviewer sees neither
    then; Management still does, and the worker with no person sees its whole organisation. (#594
    closed the known gap: had Finance been its last Department only department, its stranded Trash
    stays closed too, test_isolation_open_paths.)"""
    api = Api(db)
    org = _walled(api)
    admin, plain = org.people["admin"], org.people["plain"]
    with db.customer(org.c) as conn:
        budgets = insert_folder(conn, org.c, admin.user_id, "Budgets", org.folders["finance"])
        legal = insert_folder(conn, org.c, admin.user_id, "Legal")
    _owner(db, "UPDATE folders SET kind = 'department', department_only = true WHERE id = :f", {"f": legal})
    budget = api.add_document(org.c, name="budget.pdf", folder_id=budgets, uploaded_by=admin.user_id)
    _trash(org, budgets)
    assert "Budgets" not in plain.client.get("/api/documents/trash/folders").text
    finance, sales = org.folders["finance"], org.folders["sales"]
    _owner(db, "UPDATE folders SET parent_id = :s WHERE parent_id = :f", {"s": sales, "f": finance})
    _owner(db, "UPDATE documents SET folder_id = :s WHERE folder_id = :f", {"s": sales, "f": finance})
    gone = admin.client.delete(f"/api/folders/{finance}", headers=CSRF)
    assert gone.status_code == 204, gone.text
    assert "Budgets" not in plain.client.get("/api/documents/trash/folders").text
    files = plain.client.get(f"/api/documents?trashed=true&trashed_folder_id={budgets}")
    assert files.status_code == 404 and budget not in files.text
    assert budget not in _seen(_app(db), org.c, plain.user_id)
    management = org.people["management"]
    assert "Budgets" in management.client.get("/api/documents/trash/folders").text
    worker = Database(db.engine, WORKER)
    assert budget in _seen(worker, org.c, None) and budgets in _seen(
        worker, org.c, None, "visible_trashed_folders"
    )


def _gone_department(api: Api, org: Org, name: str) -> tuple[str, str]:
    """A department ``name`` (open) with a file the member uploaded and sent to Trash; then the
    department itself is deleted for good (its Trash file goes to the top level, keeping the
    department it had). Returns (department, file)."""
    db, admin, member = api.db, org.people["admin"], org.people["member"]
    with db.customer(org.c) as conn:
        department = insert_folder(conn, org.c, admin.user_id, name)
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id = :f", {"f": department})
    document = api.add_document(org.c, name=f"{name}.pdf", folder_id=department, uploaded_by=member.user_id)
    assert member.client.delete(f"/api/documents/{document}", headers=CSRF).status_code == 204
    assert admin.client.delete(f"/api/folders/{department}", headers=CSRF).status_code == 204
    assert _owner(db, "SELECT department_id FROM documents WHERE id = :d", {"d": document}) == [
        (uuid.UUID(department),)
    ]
    return department, document


def test_day_one_a_file_whose_department_is_gone_stays_its_uploaders_in_trash(db: Database) -> None:
    """Day one changes nothing (PRD 7): nothing is Department only, so a department deleted since
    is open as every department is; the member sees their file in Trash and restores it."""
    api = Api(db)
    c, _ = api.customer()  # no wall: nothing is Department only
    org = Org(c, {"admin": api.admin_sign_in(c), "member": api.sign_in(c, "member")}, {}, {})
    _, document = _gone_department(api, org, "Ops")
    member = org.people["member"].client
    assert document in {i["id"] for i in member.get("/api/documents?trashed=true&limit=100").json()["items"]}
    assert member.post(f"/api/documents/{document}/restore", headers=CSRF).status_code == 200


def test_once_walled_a_file_whose_department_is_gone_is_closed_but_to_management(db: Database) -> None:
    """The walled organisation has a Department only department, so a department deleted since is
    closed (fail closed): the member who uploaded the file can't see it in Trash; Management can."""
    api = Api(db)
    org = _walled(api)
    _, document = _gone_department(api, org, "Ops")
    trash = "/api/documents?trashed=true&limit=100"
    assert document not in {i["id"] for i in org.people["member"].client.get(trash).json()["items"]}
    assert document not in {i["id"] for i in org.people["plain"].client.get(trash).json()["items"]}
    assert document in {i["id"] for i in org.people["management"].client.get(trash).json()["items"]}


# --- gaps the mutation runs found ------------------------------------------------------------------


def test_a_folders_activity_names_nothing_of_a_hidden_subfolder(db: Database) -> None:
    org = _walled(Api(db))
    admin, member = org.people["admin"], org.people["member"]
    salaries, finance = org.folders["salaries"], org.folders["finance"]
    assert (
        admin.client.patch(f"/api/folders/{salaries}", json={"name": "Payslips"}, headers=CSRF).status_code
        == 200
    )
    with db.customer(org.c) as conn:
        inner = insert_folder(conn, org.c, admin.user_id, "Bonus", salaries)
    assert (
        admin.client.patch(f"/api/folders/{inner}", json={"name": "Bonuses"}, headers=CSRF).status_code == 200
    )
    feed = member.client.get(f"/api/folders/{finance}/activity")
    assert feed.status_code == 200
    for text in (feed.text, member.client.get("/api/dashboard").text):
        assert "Payslips" not in text and salaries not in text and inner not in text and "Bonus" not in text


def test_a_visible_trashed_folder_opened_lists_no_hidden_child(db: Database) -> None:
    org = _walled(Api(db))
    _trash(org, org.folders["finance"])
    opened = org.people["member"].client.get(f"/api/documents/trash/folders/{org.folders['finance']}")
    assert opened.status_code == 200
    assert "Salaries" not in opened.text and org.folders["salaries"] not in opened.text, opened.text


def test_a_folder_upload_file_that_copies_a_hidden_file_names_no_document(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    plain, fin = org.people["plain"], org.files["fin"]
    _owner(
        db,
        "UPDATE documents SET sha256 = :h, size_bytes = :s WHERE id = :d",
        {"h": hashlib.sha256(_DATA).hexdigest(), "s": len(_DATA), "d": fin},
    )
    planned = plan(plain).json()
    upload, folder = planned["id"], planned["folder_ids"][0]
    (row,) = register(plain, upload, [item("A/copy.pdf", folder, len(_DATA))]).json()["items"]
    assert row["state"] == "waiting", row
    for _ in range(2):  # sent, and sent again
        sent = plain.client.post(
            f"/api/documents?folder_upload_item_id={row['id']}",
            files={"file": ("copy.pdf", _DATA, "application/pdf")},
            headers=CSRF,
        )
        assert sent.status_code == 200, sent.text
        assert (sent.json()["document"], sent.json()["hidden"]) == (None, True)
        assert fin not in sent.text
    assert fin not in plain.client.get(f"/api/folder-uploads/{upload}/items").text


def test_a_resumable_upload_into_a_hidden_folder_answers_as_for_a_random_one(db: Database) -> None:
    org = _walled(Api(db, storage=ResumableStorage()))
    plain = org.people["plain"]
    replies = []
    for target in (str(uuid.uuid4()), org.folders["salaries"]):
        start = {
            "file_name": "big.pdf",
            "file_size": len(PDF_BYTES),
            "fingerprint": _fingerprint(PDF_BYTES),
            "user_id": plain.user_id,
            "folder_id": target,
        }
        replies.append(plain.client.post(_SESSIONS, json=start, headers=CSRF))
    assert replies[0].status_code == 404
    assert (replies[1].status_code, replies[1].json()) == (replies[0].status_code, replies[0].json())


def test_a_summary_leaves_out_a_hidden_file_inside_a_visible_folder(db: Database) -> None:
    """Sales holds sales.pdf and locked.pdf; the member can't see the locked one."""
    org = _walled(Api(db))
    summary = org.people["member"].client.get(f"/api/folders/{org.folders['sales']}/summary").json()
    assert summary == {"files": 1, "folders": 0, "bytes": len(PDF_BYTES)}, summary


def test_restore_counts_only_what_the_person_sees(db: Database) -> None:
    """Notes (private, names ``named``) holds their note, and Inner with a file locked since."""
    api = Api(db)
    org = _walled(api)
    named = org.people["named"]
    with db.customer(org.c) as conn:
        notes = insert_folder(conn, org.c, named.user_id, "Notes")
        inner = insert_folder(conn, org.c, named.user_id, "Inner", notes)
    _private(db, org, notes, named.user_id)
    api.add_document(org.c, name="note.pdf", folder_id=notes, uploaded_by=named.user_id)
    _lock(db, org, api.add_document(org.c, name="sealed.pdf", folder_id=inner, uploaded_by=named.user_id))
    _trash(org, notes)
    back = named.client.post(f"/api/documents/trash/folders/{notes}/restore", headers=CSRF)
    assert back.status_code == 200, back.text
    assert (back.json()["files"], back.json()["folders"]) == (1, 1), back.text
