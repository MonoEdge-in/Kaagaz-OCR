"""PRD 7 PR 13 (#605): the reads behind an Admin's locked-file pop-ups. The lock dialog's people,
the locked files a department or private folder gives (the give pop-up and the Access page's
[Change]), and the Access page's "Locked files".

Land is a Department only department with a sub-folder Daregaon and a private folder Deeds; Sales
is open. Ravi and Priya are members of Land, Hema heads it, Naveen is named on Deeds, Mona is
Management and Sam is in no department. Another organisation has its own Land."""

import uuid
from dataclasses import dataclass

import httpx2 as httpx
import pytest

from kaagaz.db.engine import Database
from tests.db_support import insert_folder
from tests.test_api_support import CSRF, Api, Signed
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_MEMBER = (
    "INSERT INTO department_members (customer_id, department_id, user_id, is_head, added_by)"
    " VALUES (:c, :d, :u, :head, :by)"
)
_GRANT = "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :by)"


@dataclass
class Org:
    c: str
    first: str
    admin: Signed
    people: dict[str, Signed]
    folders: dict[str, str]
    files: dict[str, str]


def _org(api: Api) -> Org:
    db = api.db
    c, first = api.customer()
    admin = api.admin_sign_in(c)
    people = {name: api.sign_in(c, "member") for name in ("ravi", "priya", "hema", "naveen", "mona", "sam")}
    with db.customer(c) as conn:
        land = insert_folder(conn, c, first, "Land")
        sales = insert_folder(conn, c, first, "Sales")
        daregaon = insert_folder(conn, c, first, "Daregaon", land)
        deeds = insert_folder(conn, c, first, "Deeds", land)
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id IN (:l, :s)", {"l": land, "s": sales})
    _owner(db, "UPDATE folders SET department_only = true WHERE id = :l", {"l": land})
    _owner(db, "UPDATE folders SET private = true WHERE id = :d", {"d": deeds})
    folders = {"land": land, "sales": sales, "daregaon": daregaon, "deeds": deeds}
    files = {
        "open": api.add_document(c, name="Rent.pdf", folder_id=land, uploaded_by=first),
        "deed": api.add_document(c, name="Sale deed.pdf", folder_id=daregaon, uploaded_by=first),
        "loan": api.add_document(c, name="bank loan.pdf", folder_id=land, uploaded_by=first),
        "will": api.add_document(c, name="Will.pdf", folder_id=deeds, uploaded_by=first),
        "note": api.add_document(c, name="Note.pdf", folder_id=deeds, uploaded_by=first),
        "sales": api.add_document(c, name="Sales deal.pdf", folder_id=sales, uploaded_by=first),
        "trashed": api.add_document(c, name="Old deed.pdf", folder_id=land, uploaded_by=first),
    }
    ids = {name: signed.user_id for name, signed in people.items()}
    for name, head in (("ravi", False), ("priya", False), ("hema", True)):
        _owner(db, _MEMBER, {"c": c, "d": land, "u": ids[name], "head": head, "by": first})
    _owner(
        db,
        "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
        " VALUES (:c, :f, :u, :by)",
        {"c": c, "f": deeds, "u": ids["naveen"], "by": first},
    )
    _owner(db, "UPDATE users SET management = true WHERE id = :u", {"u": ids["mona"]})
    for name in ("deed", "loan", "will", "sales", "trashed"):
        _owner(
            db,
            "UPDATE documents SET locked_at = now(), locked_by = :u, status = 'locked' WHERE id = :d",
            {"u": first, "d": files[name]},
        )
    _owner(
        db,
        "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE id = :d",
        {"u": first, "d": files["trashed"]},
    )
    return Org(c, first, admin, people, folders, files)


def _get(signed: Signed, path: str) -> httpx.Response:
    return signed.client.get(path)


def _code(reply: httpx.Response) -> tuple[int, str | None]:
    body = reply.json()
    return reply.status_code, body["error"]["code"] if isinstance(body, dict) and "error" in body else None


def _same_as_missing(reply: httpx.Response, missing: httpx.Response) -> None:
    assert missing.status_code == 404
    assert (reply.status_code, reply.content) == (missing.status_code, missing.content)


# --- the give pop-up: a department's and a private folder's locked files -----------------------


def test_a_department_lists_the_files_it_gives_and_which_are_locked(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    ravi = org.people["ravi"].user_id
    _owner(db, _GRANT, {"c": org.c, "d": org.files["loan"], "u": ravi, "by": org.first})
    path = f"/api/admin/departments/{org.folders['land']}/locked-files"
    reply = _get(org.admin, f"{path}?user={ravi}")
    assert reply.status_code == 200, reply.text
    assert reply.json() == {
        "files": 3,  # Rent, Sale deed, bank loan; not Deeds' (a Head's), not the one in Trash
        "locked": [
            {"id": org.files["loan"], "name": "bank loan.pdf", "path": "Land", "open": True},
            {"id": org.files["deed"], "name": "Sale deed.pdf", "path": "Land / Daregaon", "open": False},
        ],
    }
    headed = _get(org.admin, f"{path}?head=true").json()
    assert headed["files"] == 5
    assert [(item["name"], item["open"]) for item in headed["locked"]] == [
        ("bank loan.pdf", False),
        ("Sale deed.pdf", False),
        ("Will.pdf", False),
    ]


def test_a_private_folder_lists_its_own_locked_files(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    reply = _get(org.admin, f"/api/folders/{org.folders['deeds']}/locked-files")
    assert reply.json() == {
        "files": 2,
        "locked": [{"id": org.files["will"], "name": "Will.pdf", "path": "Land / Deeds", "open": False}],
    }
    assert _code(_get(org.admin, f"/api/folders/{org.folders['daregaon']}/locked-files")) == (
        409,
        "not_private",
    )


def test_only_an_admin_session_asks_and_another_organisations_place_is_not_found(db: Database) -> None:
    """Anyone else gets the same 403 for a place they can see, can't see, or that never was."""
    api = Api(db)
    org, other = _org(api), _org(api)
    for signed in (org.people["ravi"], org.people["hema"], org.people["sam"]):
        for place in ("land", "deeds"):
            for path in (
                f"/api/admin/departments/{org.folders[place]}/locked-files",
                f"/api/folders/{org.folders[place]}/locked-files",
                f"/api/folders/{uuid.uuid4()}/locked-files",
            ):
                assert _code(_get(signed, path)) == (403, "admin_session_required")
    for path in (
        f"/api/admin/departments/{other.folders['land']}/locked-files",
        f"/api/folders/{other.folders['deeds']}/locked-files",
    ):
        assert _code(_get(org.admin, path)) == (404, "not_found")
    assert _code(_get(org.admin, f"/api/admin/departments/{org.folders['daregaon']}/locked-files")) == (
        409,
        "not_a_department",
    )


# --- the lock dialog's people ----------------------------------------------------------------


def _names(reply: httpx.Response) -> list[tuple[str, bool]]:
    assert reply.status_code == 200, reply.text
    return [(item["id"], item["you"]) for item in reply.json()["people"]]


def test_the_lock_dialog_lists_who_can_see_the_file_today_and_the_admin(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    ids = {name: signed.user_id for name, signed in org.people.items()}
    seen = {ids["ravi"], ids["priya"], ids["hema"], ids["mona"]}  # Land's people and Management
    people = _names(_get(org.admin, f"/api/documents/{org.files['open']}/lock-preview"))
    assert {person for person, _ in people} == seen | {org.admin.user_id}
    assert [you for person, you in people if person == org.admin.user_id] == [True]
    assert sum(you for _, you in people) == 1
    deeds = _names(_get(org.admin, f"/api/documents/{org.files['note']}/lock-preview"))
    assert {person for person, _ in deeds} == {ids["hema"], ids["naveen"], ids["mona"], org.admin.user_id}
    many = org.admin.client.post(
        "/api/documents/lock-preview",
        json={"document_ids": [org.files["open"], org.files["note"]]},
        headers=CSRF,
    )
    assert {person for person, _ in _names(many)} == seen | {ids["naveen"], org.admin.user_id}
    by_name = [item["name"] for item in many.json()["people"]]
    assert by_name == sorted(by_name, key=str.lower)


def test_the_lock_dialog_is_an_admins_and_a_hidden_file_is_one_that_never_was(db: Database) -> None:
    api = Api(db)
    org, other = _org(api), _org(api)
    sam, ravi = org.people["sam"], org.people["ravi"]
    missing = _get(sam, f"/api/documents/{uuid.uuid4()}/lock-preview")
    _same_as_missing(_get(sam, f"/api/documents/{org.files['open']}/lock-preview"), missing)  # Land is closed
    assert _code(_get(ravi, f"/api/documents/{org.files['open']}/lock-preview")) == (403, "forbidden")
    assert _code(_get(org.admin, f"/api/documents/{other.files['open']}/lock-preview")) == (404, "not_found")
    many = org.admin.client.post(
        "/api/documents/lock-preview",
        json={"document_ids": [org.files["open"], other.files["open"]]},
        headers=CSRF,
    )
    assert _code(many) == (404, "not_found")
    by_member = ravi.client.post(
        "/api/documents/lock-preview", json={"document_ids": [org.files["open"]]}, headers=CSRF
    )
    assert _code(by_member) == (403, "admin_session_required")


# --- the Access page -------------------------------------------------------------------------------


def test_the_access_page_lists_the_locked_files_a_person_may_open(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    ravi = org.people["ravi"].user_id
    for name in ("deed", "trashed", "open"):  # in Trash, and one no longer locked: not listed
        _owner(db, _GRANT, {"c": org.c, "d": org.files[name], "u": ravi, "by": org.first})
    reply = _get(org.admin, f"/api/admin/users/{ravi}/access")
    assert reply.status_code == 200, reply.text
    [item] = reply.json()["locked"]
    assert {key: item[key] for key in ("id", "name", "path", "given_by")} == {
        "id": org.files["deed"],
        "name": "Sale deed.pdf",
        "path": "Land / Daregaon",
        "given_by": org.first,
    }
    assert _get(org.admin, f"/api/admin/users/{org.people['sam'].user_id}/access").json()["locked"] == []


# --- the give pop-up's ticks, written with the place (needs PR 12's kaagaz_lock_grant) ----------------


def _grants(db: Database, document: str) -> set[str]:
    rows = _owner(db, "SELECT user_id::text FROM lock_grants WHERE document_id = :d", {"d": document})
    return {str(row[0]) for row in rows}


def _member(db: Database, org: Org, person: str) -> bool:
    found = _owner(
        db,
        "SELECT count(*) FROM department_members WHERE department_id = :d AND user_id = :u",
        {"d": org.folders["land"], "u": person},
    )
    return bool(found[0][0])


def test_giving_a_department_gives_the_ticked_locked_files_in_the_same_step(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    sam = org.people["sam"].user_id
    path = f"/api/admin/users/{sam}/departments/{org.folders['land']}"
    wrong = org.admin.client.put(
        path, json={"locked_files": [org.files["loan"], org.files["sales"]]}, headers=CSRF
    )
    assert _code(wrong) == (409, "not_in_place")
    assert not _member(db, org, sam) and sam not in _grants(db, org.files["loan"])  # nothing written
    given = org.admin.client.put(path, json={"locked_files": [org.files["loan"]]}, headers=CSRF)
    assert given.status_code == 200, given.text
    assert _member(db, org, sam)
    assert sam in _grants(db, org.files["loan"]) and sam not in _grants(db, org.files["deed"])
    rows = _owner(
        db,
        "SELECT target_id, subject_user_id::text, details FROM audit_log"
        " WHERE customer_id = :c AND action = 'lock.grant'",
        {"c": org.c},
    )
    assert [tuple(row) for row in rows] == [(org.files["loan"], sam, {"via": "member"})]
    listed = _get(org.admin, f"/api/admin/departments/{org.folders['land']}/locked-files?user={sam}").json()
    assert [(item["name"], item["open"]) for item in listed["locked"]] == [
        ("bank loan.pdf", True),
        ("Sale deed.pdf", False),
    ]


def test_naming_on_a_private_folder_gives_its_ticked_locked_files(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    sam, hema = org.people["sam"].user_id, org.people["hema"]
    path = f"/api/folders/{org.folders['deeds']}/named/{sam}"
    by_head = hema.client.put(path, json={"locked_files": [org.files["will"]]}, headers=CSRF)
    assert _code(by_head) == (403, "admin_session_required")  # a Head names people; only an Admin gives locks
    named = _owner(db, "SELECT count(*) FROM private_folder_members WHERE user_id = :u", {"u": sam})
    assert named == [(0,)]
    assert hema.client.put(path, json={}, headers=CSRF).status_code == 200  # naming alone is the Head's
    given = org.admin.client.put(path, json={"locked_files": [org.files["will"]]}, headers=CSRF)
    assert given.status_code == 200, given.text
    assert sam in _grants(db, org.files["will"])
    assert _code(org.admin.client.put(path, json={"locked_files": [org.files["loan"]]}, headers=CSRF)) == (
        409,
        "not_in_place",
    )


def test_an_admin_ticking_themselves_needs_the_company_sign_in(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    me = org.admin.user_id
    _owner(db, _MEMBER, {"c": org.c, "d": org.folders["land"], "u": me, "head": False, "by": org.first})
    path = f"/api/admin/users/{me}/departments/{org.folders['land']}"  # a member already: only the tick
    reply = org.admin.client.put(path, json={"locked_files": [org.files["loan"]]}, headers=CSRF)
    assert _code(reply) == (403, "keycloak_reauth_required")
    assert me not in _grants(db, org.files["loan"])
    api.prove(org.admin, "access")
    body = {"locked_files": [org.files["loan"]]}
    assert org.admin.client.put(path, json=body, headers=CSRF).status_code == 200
    assert me in _grants(db, org.files["loan"])
