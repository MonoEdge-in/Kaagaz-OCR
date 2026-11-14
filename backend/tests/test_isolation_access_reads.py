"""PRD 7 PR 10b (#601): the access API's reads.

The organisation is test_isolation_access_views' ``_walled`` one (Finance Department only with its
member and Head, Salaries private inside it with a person named, Sales open with a locked file the
reviewer ``plain`` may open, a Finance file given to ``plain``), plus a Head of Sales. Every read
is checked by role: an id the caller can't see answers byte for byte as a random one, then 403; an
Admin is answered about a file they can't open, names only (lead decision 8); nobody but an Admin
gets a kept-from-me count; Management's access activity names only the files Management can see;
a file in a folder the person can't see says ``shared_with_you`` and never the folder's id."""

import uuid
from typing import Any

import pytest

from kaagaz.api.routers import access as access_router
from kaagaz.db import audit
from kaagaz.db.engine import Database
from tests.db_support import insert_folder
from tests.test_api_support import CSRF, PASSWORD, Api, Signed
from tests.test_isolation_access_api import _call, _code, _ids
from tests.test_isolation_access_views import Org, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_HEAD = (
    "INSERT INTO department_members (customer_id, department_id, user_id, is_head, added_by)"
    " VALUES (:c, :d, :u, true, :u)"
)


def _org(api: Api) -> Org:
    """``_walled`` with ``sales_head``, the Head of Sales."""
    org = _walled(api)
    org.people["sales_head"] = api.sign_in(org.c, "member")
    _owner(api.db, _HEAD, {"c": org.c, "d": org.folders["sales"], "u": org.people["sales_head"].user_id})
    return org


def _raw(signed: Signed, url: str) -> tuple[int, bytes]:
    reply = signed.client.get(url, headers=CSRF)
    return reply.status_code, reply.content


def _get(signed: Signed, url: str) -> tuple[int, Any]:
    return _call(signed, "GET", url)


def _people(group: dict[str, Any]) -> set[str]:
    return {person["id"] for person in group["people"]}


def _groups(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {group["reason"]: group for group in body["groups"]}


# --- who can see this (route 17) ----------------------------------------------------------------


def test_who_can_see_a_file_and_why(db: Database) -> None:
    org = _org(Api(db))
    p, files, f = _ids(org), org.files, org.folders
    admin = org.people["admin"]
    active = len([name for name in p if name != "off"]) + 1  # and the organisation's first admin
    status, body = _get(admin, f"/api/documents/{files['fin']}/audience")  # one the Admin can't open
    assert status == 200 and set(body) == {"groups", "everyone_else"}  # names only (lead decision 8)
    groups = _groups(body)
    assert list(groups) == ["management", "member"]
    assert _people(groups["management"]) == {p["management"]}
    assert _people(groups["member"]) == {p["head"], p["member"]}
    assert groups["member"]["place"] == {"id": f["finance"], "name": "Finance"}
    assert body["everyone_else"] == active - 3
    groups = _groups(_get(admin, f"/api/documents/{files['sal']}/audience")[1])
    assert list(groups) == ["management", "head", "named"]
    assert (_people(groups["head"]), groups["head"]["place"]["name"]) == ({p["head"]}, "Finance")
    assert (_people(groups["named"]), groups["named"]["place"]["name"]) == ({p["named"]}, "Salaries")
    assert _people(_groups(_get(admin, f"/api/documents/{files['given']}/audience")[1])["given"]) == {
        p["plain"]
    }
    assert _people(_groups(_get(admin, f"/api/documents/{files['held']}/audience")[1])["held"]) == {
        p["member"]
    }
    locked = _groups(_get(admin, f"/api/documents/{files['locked']}/audience")[1])
    assert list(locked) == ["everyone", "may_open_locked"]  # Management can't open a lock
    assert _people(locked["everyone"]) == _people(locked["may_open_locked"]) == {p["plain"]}
    check = f"/api/documents/{files['fin']}/audience?user_id="
    assert _get(admin, check + p["member"]) == (200, {"can_see": True, "reasons": ["member"]})
    assert _get(admin, check + p["plain"]) == (200, {"can_see": False, "reasons": []})
    assert _get(admin, check + p["off"]) == (200, {"can_see": False, "reasons": []})
    other = Api(db).new_signed()
    for stranger in (str(uuid.uuid4()), other.user_id):
        assert _code(_get(admin, check + stranger)) == (404, "not_found")
    status, body = _get(org.people["head"], f"/api/folders/{f['salaries']}/audience")
    groups = _groups(body)
    assert status == 200 and list(groups) == ["management", "head", "named"]
    assert body["everyone_else"] == active - 3  # an Admin sees the folder's name, not its files
    assert _get(org.people["head"], f"/api/folders/{f['salaries']}/audience?user_id={p['admin']}") == (
        200,
        {"can_see": False, "reasons": []},
    )
    sales = _groups(_get(org.people["sales_head"], f"/api/folders/{f['sales']}/audience")[1])
    assert len(_people(sales["everyone"])) == active


# --- every read by role -------------------------------------------------------------------------


def _hidden_like_random(signed: Signed, url: str, random: str) -> None:
    """A hidden id answers exactly as an id that never was: the status and every byte."""
    assert _raw(signed, url) == _raw(signed, random), url
    assert _raw(signed, random)[0] == 404


def test_every_read_answers_by_role_and_a_hidden_id_as_a_random_one(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    who, f, files = org.people, org.folders, org.files
    stranger = api.new_admin()  # another organisation's Admin
    with db.customer(stranger.customer_id) as conn:
        foreign_folder = insert_folder(conn, stranger.customer_id, stranger.user_id, "Theirs")
    foreign_file = api.add_document(stranger.customer_id, uploaded_by=stranger.user_id)
    doc, folder = "/api/documents/{}/audience", "/api/folders/{}/audience"
    kept, preview = "/api/folders/{}/kept-from-me", "/api/folders/{}/private/preview?on=true"
    random_doc, random_folder = str(uuid.uuid4()), str(uuid.uuid4())
    # Who can see this: an Admin (any file, decision 8), the Head of its department.
    for name in ("admin", "head"):
        assert _get(who[name], doc.format(files["sal"]))[0] == 200, name
        assert _get(who[name], folder.format(f["salaries"]))[0] == 200, name
    assert _get(who["sales_head"], doc.format(files["sales"]))[0] == 200
    for name in ("management", "named"):  # they see it, and head nothing here
        assert _code(_get(who[name], doc.format(files["sal"]))) == (403, "forbidden"), name
        assert _code(_get(who[name], folder.format(f["salaries"]))) == (403, "forbidden"), name
    for name in ("head", "member", "plain", "management"):  # a Head of another department too
        assert _code(_get(who[name], doc.format(files["sales"]))) == (403, "forbidden"), name
    _owner(db, "INSERT INTO document_shares (customer_id, document_id, user_id) VALUES (:c, :d, :u)",
           {"c": org.c, "d": files["fin"], "u": who["sales_head"].user_id})  # fmt: skip
    assert _code(_get(who["sales_head"], doc.format(files["fin"]))) == (
        403,
        "forbidden",
    )  # given, not its Head
    for name in ("member", "plain", "sales_head"):  # hidden: Salaries, and Finance for two of them
        _hidden_like_random(who[name], doc.format(files["sal"]), doc.format(random_doc))
        _hidden_like_random(who[name], folder.format(f["salaries"]), folder.format(random_folder))
    _hidden_like_random(who["head"], doc.format(files["locked"]), doc.format(random_doc))  # a lock
    for name in ("admin", "head"):
        assert _get(who[name], doc.format(random_doc))[0] == 404
    for url in (doc.format(foreign_file), folder.format(foreign_folder), kept.format(foreign_folder)):
        assert _raw(who["admin"], url) == _raw(who["admin"], url.replace(url.split("/")[3], random_doc))
    # Kept from me: an Admin only; never a count to anyone else.
    status, body = _get(who["admin"], kept.format(f["finance"]))
    assert status == 200 and body["count"] == 2
    for name in ("head", "member", "management"):
        status, body = _get(who[name], kept.format(f["finance"]))
        assert (status, body["error"]["code"]) == (403, "forbidden") and "count" not in body, name
    for name in ("plain", "sales_head"):
        _hidden_like_random(who[name], kept.format(f["finance"]), kept.format(random_folder))
    # The private preview: an Admin's, after visibility.
    assert _get(who["admin"], preview.format(f["loose"]))[0] == 200
    assert _code(_get(who["plain"], preview.format(f["loose"]))) == (403, "forbidden")
    _hidden_like_random(who["member"], preview.format(f["salaries"]), preview.format(random_folder))
    # The /admin reads: an admin session, before any id is read.
    for url in (
        "/api/admin/departments",
        f"/api/admin/departments/{f['finance']}/members",
        f"/api/admin/departments/{f['finance']}/department-only/preview?on=false",
    ):
        assert _get(who["admin"], url)[0] == 200, url
        for name in ("management", "head", "member", "plain"):
            assert _code(_get(who[name], url)) == (403, "admin_session_required"), (name, url)
    for url in (
        f"/api/admin/departments/{f['finance']}/members",
        f"/api/admin/departments/{f['finance']}/department-only/preview?on=false",
    ):
        assert _code(_get(stranger, url)) == (404, "not_found"), url
    # The access activity: Admins and Management.
    for name in ("admin", "management"):
        assert _get(who[name], "/api/access/activity")[0] == 200, name
    for name in ("head", "member", "plain"):
        assert _code(_get(who[name], "/api/access/activity")) == (403, "forbidden"), name
    # Switched off: not signed in, whatever is asked.
    for url in (doc.format(files["top"]), kept.format(f["loose"]), "/api/access/activity"):
        assert _get(who["off"], url)[0] == 401, url


# --- departments and previews (routes 18 and 19) ------------------------------------------------


def test_the_departments_their_people_and_the_one_time_card(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin, p, f = org.people["admin"], _ids(org), org.folders
    status, body = _get(admin, "/api/admin/departments")
    assert status == 200 and body["keeps_files_to_some_people"] is True
    assert body["items"] == [
        {
            "id": f["finance"],
            "name": "Finance",
            "department_only": True,
            "members": 2,
            "heads": [{"id": p["head"], "name": "Test member"}],
            "warnings": [],
        },
        {
            "id": f["sales"],
            "name": "Sales",
            "department_only": False,
            "members": 0,
            "heads": [],
            "warnings": [],
        },
    ]
    _owner(db, "UPDATE folders SET department_only = true WHERE id = :f", {"f": f["sales"]})
    sales = _get(admin, "/api/admin/departments")[1]["items"][1]
    assert sales["warnings"] == ["no_members"]
    status, body = _get(admin, f"/api/admin/departments/{f['finance']}/members")
    assert status == 200 and body["department"] == {
        "id": f["finance"],
        "name": "Finance",
        "department_only": True,
    }
    assert {(item["id"], item["head"]) for item in body["items"]} == {(p["head"], True), (p["member"], False)}
    assert _code(_get(admin, f"/api/admin/departments/{f['loose']}/members")) == (409, "not_a_department")
    assert _code(_get(admin, f"/api/admin/departments/{uuid.uuid4()}/members")) == (404, "not_found")
    day_one = api.new_admin()
    with db.customer(day_one.customer_id) as conn:
        insert_folder(conn, day_one.customer_id, day_one.user_id, "Finance")
    assert _get(day_one, "/api/admin/departments")[1]["keeps_files_to_some_people"] is False


def _named(body: dict[str, Any], side: str) -> set[str]:
    return {person["id"] for person in body[side]}


def test_the_previews_name_who_loses_and_who_gains_as_the_write_does(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin, p, f = org.people["admin"], _ids(org), org.folders
    first = _owner(db, "SELECT id::text FROM users WHERE customer_id = :c AND id <> ALL(CAST(:u AS uuid[]))",
                   {"c": org.c, "u": list(p.values())})[0][0]  # fmt: skip
    url = f"/api/admin/departments/{f['sales']}/department-only/preview?on=true"
    status, body = _get(admin, url)
    everyone_but_management = {u for n, u in p.items() if n not in ("off", "management")} | {first}
    assert status == 200 and (_named(body, "losing"), body["gaining"], body["files"]) == (
        everyone_but_management,
        [],
        2,  # sales.pdf and the locked one
    )
    put = _call(admin, "PUT", f"/api/admin/departments/{f['sales']}/department-only", {"on": True})
    assert put[0] == 200
    [(losing,)] = _owner(db, "SELECT details->>'losing' FROM audit_log WHERE customer_id = :c"
                         " AND action = 'access.department_only_on'", {"c": org.c})  # fmt: skip
    assert int(losing) == len(body["losing"])  # the preview says what the write does
    body = _get(admin, f"/api/admin/departments/{f['finance']}/department-only/preview?on=false")[1]
    assert (_named(body, "gaining"), body["losing"], body["files"]) == (
        {p["plain"], p["named"], p["admin"], first},
        [],
        2,  # fin.pdf and given.pdf: Salaries' files are not Department only's
    )
    body = _get(admin, f"/api/folders/{f['salaries']}/private/preview?on=false")[1]
    assert (_named(body, "gaining"), _named(body, "losing"), body["files"]) == (
        {p["member"]},
        {p["named"]},
        1,
    )
    assert _get(admin, f"/api/folders/{f['salaries']}/private/preview?on=true")[1] == {
        "losing": [],
        "gaining": [],
        "files": 1,
    }
    body = _get(admin, f"/api/folders/{f['loose']}/private/preview?on=true")[1]
    assert _named(body, "losing") == everyone_but_management and body["files"] == 1
    loose = f"/api/folders/{f['loose']}/private/preview"
    body = _get(admin, f"{loose}?on=true&named={p['member']}&named={p['plain']}")[1]
    assert _named(body, "losing") == everyone_but_management - {p["member"], p["plain"]}
    assert _code(_get(admin, f"{loose}?on=true&named={p['off']}")) == (409, "disabled")
    assert _code(_get(admin, f"{loose}?on=false&named={p['member']}")) == (400, "bad_request")
    with db.customer(org.c) as conn:
        inside = insert_folder(conn, org.c, first, "Payslips", f["salaries"])
    assert _code(_get(admin, f"/api/folders/{inside}/private/preview?on=true")) == (409, "inside_private")
    assert _code(_get(admin, f"/api/folders/{f['finance']}/private/preview?on=true")) == (409, "department")


# --- the names list (route 20) ------------------------------------------------------------------


def test_kept_from_me_names_the_files_and_nothing_inside_them(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = _walled(Api(db))
    admin, f = org.people["admin"], org.folders
    status, body = _get(admin, f"/api/folders/{f['finance']}/kept-from-me")
    assert status == 200 and body["count"] == 2
    assert sorted(item["name"] for item in body["items"]) == ["fin.pdf", "given.pdf"]
    assert {key for item in body["items"] for key in item} == {
        "id",
        "name",
        "kind",
        "uploader",
        "created_at",
        "version_no",
        "locked",
    }
    [locked] = _get(admin, f"/api/folders/{f['sales']}/kept-from-me")[1]["items"]
    assert (locked["name"], locked["locked"]) == ("locked.pdf", True)
    assert _get(admin, f"/api/folders/{f['loose']}/kept-from-me")[1] == {"count": 0, "items": []}
    monkeypatch.setattr(access_router, "MAX_KEPT", 1)
    body = _get(admin, f"/api/folders/{f['finance']}/kept-from-me")[1]
    assert (body["count"], [item["name"] for item in body["items"]]) == (2, ["fin.pdf"])


# --- activity (routes 21 and 22) ----------------------------------------------------------------


def test_the_admin_log_filters_by_whom_it_is_about_and_shows_self_given_first(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin, p, files = org.people["admin"], _ids(org), org.files
    assert _call(admin, "PUT", f"/api/documents/{files['fin']}/shares/{p['plain']}", {})[0] == 200
    # A file the Admin can't open filters by its whole family (the #595 residual), checked
    # before the Admin makes themselves Management and so sees it.
    later = api.add_document(
        org.c, name="fin v2.pdf", folder_id=org.folders["finance"], family_id=files["fin"], version_no=2,
        is_current=False, uploaded_by=p["admin"],
    )  # fmt: skip
    body = _get(admin, f"/api/admin/audit?document_id={later}")[1]
    assert "access.file_give" in [item["action"] for item in body["items"]]
    assert _call(admin, "PUT", f"/api/admin/users/{p['named']}/management", {"on": True})[0] == 200
    mine = {"on": True, "password": PASSWORD}
    assert _call(admin, "PUT", f"/api/admin/users/{p['admin']}/management", mine)[0] == 200
    status, body = _get(admin, f"/api/admin/audit?subject_id={p['plain']}")
    assert status == 200 and [item["action"] for item in body["items"]] == ["access.file_give"]
    [given] = body["items"]
    [(name,)] = _owner(db, "SELECT display_name FROM users WHERE id = :u", {"u": p["plain"]})
    assert (given["subject_id"], given["subject_name"], given["document"]["name"]) == (
        p["plain"],
        name,
        "fin.pdf",  # an Admin's log: true names (decision 20)
    )
    assert body["total"] == 1
    body = _get(admin, "/api/admin/audit?limit=1")[1]
    [self_given] = body["self_given"]
    assert (self_given["action"], self_given["details"], self_given["subject_id"]) == (
        "access.management_on",
        {"self": True},
        p["admin"],
    )
    assert "self_given" not in _get(admin, f"/api/admin/audit?limit=1&cursor={body['next_cursor']}")[1]


def test_management_sees_access_activity_with_a_file_it_cant_see_unnamed(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin, p, files, f = org.people["admin"], _ids(org), org.files, org.folders
    older = files["locked"]  # Management can't open the lock: its newer version is locked too
    _owner(db, "UPDATE documents SET is_current = false WHERE id = :d", {"d": older})
    newer = api.add_document(
        org.c, name="locked v2.pdf", folder_id=f["sales"], family_id=older, version_no=2,
        uploaded_by=p["admin"],
    )  # fmt: skip
    _owner(
        db,
        "UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d",
        {"u": p["admin"], "d": newer},
    )
    for file in (older, files["sales"]):  # an older version's id; a file Management sees
        assert _call(admin, "PUT", f"/api/documents/{file}/shares/{p['member']}", {})[0] == 200
    _owner(db, "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE family_id = :f",
           {"u": p["admin"], "f": older})  # fmt: skip
    assert _call(admin, "DELETE", f"/api/documents/{older}/shares/{p['member']}")[0] == 204  # in Trash
    with db.customer(org.c) as conn:
        audit.record(conn, org.c, actor_user_id=p["admin"], action="document.view", target_type="document",
                     target_id=files["sales"])  # fmt: skip
    status, body = _get(org.people["management"], "/api/access/activity")
    actions = [item["action"] for item in body["items"]]
    assert status == 200 and all(action.startswith("access.") for action in actions)  # not the view
    taken, seen, given = [item for item in body["items"] if item["action"].startswith("access.file_")]
    assert (taken["action"], seen["action"], given["action"]) == (
        "access.file_take",
        "access.file_give",
        "access.file_give",
    )
    for hidden in (taken, given):
        assert hidden["document"] == {
            "id": None,
            "name": "a file you can't see",
            "version_no": None,
            "deleted": False,
        }
        assert (hidden["target_id"], hidden["family_id"], hidden["folder"], hidden["details"]) == (
            None,
            None,
            None,
            {},
        )
        assert f["sales"] not in str(hidden)
    for leak in (older, newer, "locked.pdf", "locked v2.pdf"):
        assert leak not in str(body), leak
    assert (seen["document"]["name"], seen["subject_id"], seen["folder"]) == (
        "sales.pdf",
        p["member"],
        {"id": f["sales"], "path": "Sales"},  # a file Management sees keeps its place
    )
    items = _get(admin, "/api/access/activity")[1]["items"]
    names = [item["document"]["name"] for item in items if item["document"] is not None]
    assert names == ["locked.pdf", "sales.pdf", "locked.pdf"]  # an Admin: every name (decision 20)


# --- Shared with me and "Shared with you" (route 23, lead decision 3) ---------------------------


def test_shared_with_me_and_a_hidden_folder_never_named(db: Database) -> None:
    org = _walled(Api(db))
    plain, member, admin = org.people["plain"], org.people["member"], org.people["admin"]
    files, f = org.files, org.folders
    assert _get(member, "/api/documents?shared=true")[1] == {"items": [], "total": 0}
    status, body = _get(plain, "/api/documents?shared=true")
    assert status == 200 and [item["id"] for item in body["items"]] == [files["given"]]  # not the lock
    [given] = body["items"]
    assert (given["folder_id"], given["shared_with_you"]) == (None, True)
    detail = _get(plain, f"/api/documents/{files['given']}")[1]
    assert (detail["folder_id"], detail["shared_with_you"], detail["folder"]) == (None, True, None)
    assert f["finance"] not in str(_get(plain, "/api/documents?limit=100")[1])
    listed = {item["id"]: item for item in _get(member, "/api/documents?limit=100")[1]["items"]}
    assert (listed[files["fin"]]["folder_id"], listed[files["fin"]]["shared_with_you"]) == (
        f["finance"],
        False,
    )
    assert (listed[files["top"]]["folder_id"], listed[files["top"]]["shared_with_you"]) == (None, False)
    assert _call(admin, "PUT", f"/api/documents/{files['sal']}/shares/{member.user_id}", {})[0] == 200
    [sal] = _get(member, "/api/documents?shared=true")[1]["items"]
    # Changed on purpose (#601 review): a hidden folder names no department either.
    assert (sal["folder_id"], sal["shared_with_you"], sal["department"]) == (None, True, None)
    for url in (f"department_id={f['finance']}", f"folder_id={f['finance']}&include_subfolders=true"):
        ids = [item["id"] for item in _get(member, f"/api/documents?limit=100&{url}")[1]["items"]]
        assert files["fin"] in ids and files["sal"] not in ids, url  # never placed below Finance
    _owner(db, "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE id = :d",
           {"u": admin.user_id, "d": files["given"]})  # fmt: skip
    assert _get(plain, "/api/documents?shared=true")[1] == {"items": [], "total": 0}
    trashed = _get(plain, "/api/documents?shared=true&trashed=true")[1]["items"]
    assert [item["id"] for item in trashed] == [files["given"]]
