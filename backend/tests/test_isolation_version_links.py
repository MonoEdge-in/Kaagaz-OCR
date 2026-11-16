"""Linking files already uploaded as versions of each other, and its Undo (V13 to V16, V18 to V22,
#648, V-A).

The organisation is test_isolation_access_views' ``_walled`` one (Finance Department only, Salaries
private inside it, Sales open; the Admin can't see Finance or Salaries; Management sees all but the
lock nobody gave them), with, in Sales unless said: ``x`` (owner Management, Confidential, tag t1,
in a possible-versions group with g2), ``y`` (no folder), the family ``g`` of two versions g1 and g2
(in Old, tag t2 on g1; g2 paired with x), ``z``, and ``bin`` (in Trash).

Only Admins and Management link; a file, or a version of its family, the person can't see is a file
that never was; locked and Trash files are never linked. Each chosen file brings its family, in the
chosen order, into the latest's place with its owner, label and tags; the family's people and the
stories follow (0100); Undo puts everything back while nothing newer joined. Two sessions at once
wait for each other on the tree lock and never deadlock."""

import json
import threading
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import locks as locks_sql
from kaagaz.db import trash
from kaagaz.db import version_links as links_sql
from kaagaz.db.engine import Database
from tests.db_support import name_session, violation, wait_until_blocked
from tests.test_api_support import CSRF, PASSWORD, Api, Signed
from tests.test_isolation_access_views import _SUGGEST, Org, _app, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_STATE = (
    "SELECT d.id::text, d.family_id::text, d.version_no, d.is_current, d.folder_id::text, d.held_for::text,"
    " d.owner_id::text, d.sensitivity,"
    " ARRAY(SELECT t.tag_id::text FROM document_tags t WHERE t.document_id = d.id ORDER BY t.tag_id) AS tags,"
    " (SELECT g.state FROM version_group_members g WHERE g.document_id = d.id) AS member"
    " FROM documents d WHERE d.id = ANY(CAST(:ids AS uuid[]))"
)
_MEMBER = (
    "INSERT INTO version_group_members (customer_id, document_id, group_id, position) VALUES (:c, :d, :g, :p)"
)
_PAIR = (
    "INSERT INTO version_pairs (customer_id, first_id, second_id, text_overlap, text_overlap_exact)"
    " SELECT :c, least(a, b), greatest(a, b), 0.9, true"
    " FROM (SELECT CAST(:a AS uuid) a, CAST(:b AS uuid) b) x"
)
_STORY = (
    "INSERT INTO document_stories (customer_id, user_id, family_id, story, story_latin)"
    " VALUES (:c, :u, :f, :s, '')"
)
LINKED = {"version.linked", "version.unlinked"}


def _give(db: Database, org: Org, table: str, name: str, user: str) -> None:
    """One file given (``document_shares``) or a lock granted (``lock_grants``) to ``user``."""
    _owner(
        db,
        f"INSERT INTO {table} (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :u)",  # noqa: S608
        {"c": org.c, "d": org.files[name], "u": user},
    )


def _org(api: Api) -> Org:
    org = _walled(api)
    c, first, f = org.c, org.people["admin"].user_id, org.folders
    f["old"] = api.add_folder(c, first, "Old")
    files = org.files
    files["x"] = api.add_document(
        c, name="x.pdf", folder_id=f["sales"], uploaded_by=first, owner_id=org.people["management"].user_id,
        sensitivity="confidential", sensitivity_set_by_person=True,
    )  # fmt: skip
    files["y"] = api.add_document(c, name="y.pdf", uploaded_by=first)
    files["z"] = api.add_document(c, name="z.pdf", folder_id=f["sales"], uploaded_by=first)
    files["g1"] = api.add_document(c, name="g.pdf", folder_id=f["old"], uploaded_by=first, is_current=False)
    files["g2"] = api.add_document(
        c, name="g v2.pdf", folder_id=f["old"], uploaded_by=first, family_id=files["g1"], version_no=2
    )
    files["bin"] = api.add_document(c, name="bin.pdf", folder_id=f["sales"], uploaded_by=first)
    _owner(
        api.db,
        "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE id = :d",
        {"u": first, "d": files["bin"]},
    )
    org.folders["t1"] = api.add_tag(c, first, "t1")
    org.folders["t2"] = api.add_tag(c, first, "t2")
    api.tag_document(c, files["x"], org.folders["t1"])
    api.tag_document(c, files["g1"], org.folders["t2"])
    group = str(uuid.uuid4())
    for position, name in enumerate(("x", "g2"), 1):
        _owner(api.db, _MEMBER, {"c": c, "d": files[name], "g": group, "p": position})
    _owner(api.db, _PAIR, {"c": c, "a": files["x"], "b": files["g2"]})
    return org


def _post(signed: Signed, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    reply = signed.client.post(f"/api{path}", json=body or {}, headers=CSRF)
    return reply.status_code, reply.json()


def _link(signed: Signed, org: Org, names: list[str], current: str, **more: Any) -> tuple[int, Any]:
    ids = [org.files[n] for n in names]  # in the order chosen; the files sent in another
    body = {"document_ids": ids[::-1], "order": ids, "current_id": org.files[current], **more}
    return _post(signed, "/documents/link-versions", body)


def _state(db: Database, org: Org, *names: str) -> dict[str, dict[str, Any]]:
    rows = _owner(db, _STATE, {"ids": [org.files[n] for n in names]})
    back = {v: k for k, v in org.files.items()}
    return {back[row[0]]: dict(row._mapping) | {"family_id": back.get(row[1], row[1])} for row in rows}


def _everything(db: Database, org: Org) -> Any:
    """What a refused request must leave as it was."""
    return (
        _state(db, org, *org.files),
        _owner(db, "SELECT count(*) FROM version_links WHERE customer_id = :c", {"c": org.c}),
        _owner(
            db,
            "SELECT count(*) FROM audit_log WHERE customer_id = :c AND action LIKE 'version.%'",
            {"c": org.c},
        ),
    )


# --- who may link ---------------------------------------------------------------------------------


def test_only_admins_and_management_may_link_and_anyone_else_is_told_to_ask(db: Database) -> None:
    org = _org(Api(db))
    before = _everything(db, org)
    ids = [org.files["x"], org.files["y"]]
    body = {"document_ids": ids, "order": ids, "current_id": ids[0]}
    for role in ("head", "member", "plain", "named"):
        signed = org.people[role]
        for status, reply in (
            _link(signed, org, ["x", "y"], "x"),
            _post(signed, "/documents/link-versions/preview", body),
            _post(signed, f"/documents/{org.files['x']}/link-versions/undo"),
        ):
            assert (status, reply["error"]["code"]) == (403, "forbidden"), role
            assert (
                reply["error"]["message"]
                == "Only Admins and Management can link versions. Ask an admin to link these."
            )
        assert signed.client.get("/api/version-kept-apart").status_code == 403, role
    assert _everything(db, org) == before


# --- the link -------------------------------------------------------------------------------------


def test_families_merge_in_the_chosen_order_into_the_latests_place(db: Database) -> None:
    """V13 to V15: g (two versions, in Loose), x (Sales) and y (no folder), x the latest: g1, g2, x
    and y numbered 1 to 4 in x's family and folder, x current, with x's owner, label and tags; the
    group's files linked; one version_links row and one admin-only audit row per version; the list
    shows x alone (old versions leave it at once)."""
    api = Api(db)
    org = _org(api)
    management = org.people["management"]
    status, reply = _link(management, org, ["g2", "x", "y"], "x")
    assert status == 200, reply
    assert reply["document"]["id"] == org.files["x"]
    state = _state(db, org, "g1", "g2", "x", "y")
    assert {n: (s["family_id"], s["version_no"], s["is_current"]) for n, s in state.items()} == {
        "g1": ("x", 1, False),
        "g2": ("x", 2, False),
        "x": ("x", 3, True),
        "y": ("x", 4, False),
    }
    for name in ("g1", "g2", "y"):
        assert state[name]["folder_id"] == org.folders["sales"], name
        assert (state[name]["owner_id"], state[name]["sensitivity"]) == (management.user_id, "confidential")
        assert state[name]["tags"] == [org.folders["t1"]], name
    assert (state["x"]["member"], state["g2"]["member"]) == ("linked", "linked")
    links = _owner(
        db,
        "SELECT family_id::text, document_ids::text[], linked_by::text FROM version_links"
        " WHERE customer_id = :c",
        {"c": org.c},
    )
    assert [tuple(r) for r in links] == [
        (org.files["x"], [org.files[n] for n in ("g1", "g2", "x", "y")], management.user_id)
    ]
    rows = _owner(
        db,
        "SELECT target_id, details->>'from_family_id', (details->>'version_no')::int FROM audit_log"
        " WHERE customer_id = :c AND action = 'version.linked' ORDER BY (details->>'version_no')::int",
        {"c": org.c},
    )
    assert [tuple(r) for r in rows] == [
        (org.files["g1"], org.files["g1"], 1),
        (org.files["g2"], org.files["g1"], 2),
        (org.files["x"], org.files["x"], 3),
        (org.files["y"], org.files["y"], 4),
    ]
    listed = {i["id"] for i in management.client.get("/api/documents?limit=100").json()["items"]}
    assert org.files["x"] in listed and not {org.files[n] for n in ("g1", "g2", "y")} & listed
    detail = management.client.get(f"/api/documents/{org.files['y']}").json()
    assert [v["id"] for v in detail["versions"]] == [org.files[n] for n in ("g1", "g2", "x", "y")]
    assert detail["version_link"]["can_undo"] is True
    assert org.people["member"].client.get(f"/api/documents/{org.files['x']}").json()["version_link"] is None


def test_a_file_or_version_the_person_cant_see_is_a_file_that_never_was(db: Database) -> None:
    """For the Admin, who can't see Finance: fin, another organisation's file, a random id, and a
    family with one version moved into Finance (each version is checked, not only the one chosen)
    are all one 404; nothing is written."""
    api = Api(db)
    org, other = _org(api), _org(api)
    admin = org.people["admin"]
    before = _everything(db, org)

    def link(second: str) -> tuple[int, Any]:
        ids = [org.files["x"], second]
        return _post(
            admin, "/documents/link-versions", {"document_ids": ids, "order": ids, "current_id": ids[0]}
        )

    missing = link(str(uuid.uuid4()))
    assert missing[0] == 404
    assert link(org.files["fin"]) == missing
    assert link(other.files["sales"]) == missing
    _owner(
        db,
        "UPDATE documents SET folder_id = :f WHERE id = :d",
        {"f": org.folders["finance"], "d": org.files["g1"]},
    )
    assert link(org.files["g2"]) == missing  # g2 is seen, its first version isn't
    _owner(
        db,
        "UPDATE documents SET folder_id = :f WHERE id = :d",
        {"f": org.folders["old"], "d": org.files["g1"]},
    )
    assert _everything(db, org) == before


def test_locked_and_trash_files_are_never_linked(db: Database) -> None:
    """The Admin may open the locked file: 409 locked all the same; a file in Trash 409 in_trash;
    Management, never given the lock, is told the locked file isn't there. Nothing is written."""
    api = Api(db)
    org = _org(api)
    admin, management = org.people["admin"], org.people["management"]
    _give(db, org, "lock_grants", "locked", admin.user_id)
    before = _everything(db, org)
    status, reply = _link(admin, org, ["x", "locked"], "x")
    assert (status, reply["error"]["code"], reply["error"]["message"]) == (
        409,
        "locked",
        "This file is locked. Unlock it first.",
    )
    assert _link(management, org, ["x", "locked"], "x")[0] == 404
    status, reply = _link(management, org, ["x", "bin"], "x")
    assert (status, reply["error"]["code"]) == (409, "in_trash")
    assert _everything(db, org) == before


def test_the_order_the_latest_and_files_already_versions(db: Database) -> None:
    org = _org(Api(db))
    management = org.people["management"]
    x, y, g1, g2 = (org.files[n] for n in ("x", "y", "g1", "g2"))
    for body in (
        {"document_ids": [x, y], "order": [x, x], "current_id": x},
        {"document_ids": [x, y], "order": [x, y], "current_id": g1},
        {"document_ids": [x, x], "order": [x, x], "current_id": x},
    ):
        assert _post(management, "/documents/link-versions", body)[0] in (400, 422), body
    status, reply = _post(
        management,
        "/documents/link-versions",
        {"document_ids": [g1, g2, x], "order": [g1, g2, x], "current_id": x},
    )
    assert (status, reply["error"]["code"]) == (409, "already_versions")
    _owner(db, "UPDATE documents SET status = 'failed' WHERE id = :d", {"d": y})
    status, reply = _link(management, org, ["x", "y"], "y")
    assert (status, reply["error"]["code"]) == (409, "not_ready")
    assert _link(management, org, ["y", "x"], "x")[0] == 200  # a version still read may be an older one


def test_the_confirm_step_names_the_latest_its_folder_and_who_loses_access(db: Database) -> None:
    """V13 step 3, V15: fin (Finance, Department only) into Sales, which everyone sees: nobody loses,
    the Admin and the rest gain; sales into Finance: everyone outside Finance loses. Nothing is
    written or locked."""
    org = _org(Api(db))
    management = org.people["management"]
    before = _everything(db, org)

    def preview(current: str, other: str) -> dict[str, Any]:
        ids = [org.files[other], org.files[current]]
        status, reply = _post(
            management,
            "/documents/link-versions/preview",
            {"document_ids": ids, "order": ids, "current_id": ids[1]},
        )
        assert status == 200, reply
        return dict(reply)

    into_sales = preview("sales", "fin")
    assert into_sales["latest"] == {"id": org.files["sales"], "name": "sales.pdf"}
    assert (
        into_sales["folder"] == {"id": org.folders["sales"], "path": "Sales"} and into_sales["held"] is False
    )
    assert into_sales["versions"] == 2 and into_sales["losing"] == []
    assert org.people["admin"].user_id in {p["id"] for p in into_sales["gaining"]}
    into_finance = preview("fin", "sales")
    losing = {p["id"] for p in into_finance["losing"]}
    assert {org.people[n].user_id for n in ("admin", "plain", "named")} <= losing
    assert not {org.people[n].user_id for n in ("management", "head", "member")} & losing
    assert _everything(db, org) == before


def test_an_admin_who_would_open_a_wall_to_themselves_types_their_password(db: Database) -> None:
    """fin is given to the Admin: linking it into Sales opens Finance's file to them by its place,
    so their password is asked (as a folder move asks it), and with it the link is made."""
    api = Api(db)
    org = _org(api)
    admin = org.people["admin"]
    _give(db, org, "document_shares", "fin", admin.user_id)
    before = _everything(db, org)
    status, reply = _link(admin, org, ["fin", "sales"], "sales")
    assert (status, reply["error"]["code"]) == (403, "password_required")
    assert _everything(db, org) == before
    assert _link(admin, org, ["fin", "sales"], "sales", password=PASSWORD)[0] == 200
    assert _state(db, org, "fin")["fin"]["family_id"] == "sales"


# --- what follows a link, and Undo ---------------------------------------------------------------


def test_the_familys_people_and_stories_follow_the_link_and_undo_puts_everything_back(db: Database) -> None:
    """V16, V19, V20: x is given to the plain reviewer; the member has a story on g and on x, the
    head on g alone. Linked: g's versions are given to the plain reviewer too, the head's story is
    x's, the member keeps x's. Undo: every version's family, number, current, folder, owner, label,
    tags and group decision back, the gift taken back, the head's story back on g; audited."""
    api = Api(db)
    org = _org(api)
    management, plain = org.people["management"], org.people["plain"]
    _give(db, org, "document_shares", "x", plain.user_id)
    for person, family, story in (
        ("member", "g1", "member g"),
        ("member", "x", "member x"),
        ("head", "g1", "head g"),
    ):
        _owner(db, _STORY, {"c": org.c, "u": org.people[person].user_id, "f": org.files[family], "s": story})
    stories = (
        "SELECT u.id::text, s.family_id::text, s.story FROM document_stories s"
        " JOIN users u ON u.id = s.user_id WHERE s.customer_id = :c ORDER BY s.story"
    )
    before = _state(db, org, "g1", "g2", "x", "y")
    assert _link(management, org, ["g2", "x", "y"], "x")[0] == 200
    shared = _owner(
        db, "SELECT document_id::text FROM document_shares WHERE user_id = :u", {"u": plain.user_id}
    )
    assert {r[0] for r in shared} == {org.files[n] for n in ("given", "g1", "g2", "x", "y")}
    assert [tuple(r) for r in _owner(db, stories, {"c": org.c})] == [
        (org.people["head"].user_id, org.files["x"], "head g"),
        (org.people["member"].user_id, org.files["g1"], "member g"),
        (org.people["member"].user_id, org.files["x"], "member x"),
    ]
    status, reply = _post(management, f"/documents/{org.files['g1']}/link-versions/undo")
    assert status == 200, reply
    assert _state(db, org, "g1", "g2", "x", "y") == before
    left = {
        r[0]
        for r in _owner(
            db, "SELECT document_id::text FROM document_shares WHERE user_id = :u", {"u": plain.user_id}
        )
    }
    assert left == {org.files["given"], org.files["x"]}
    assert [tuple(r) for r in _owner(db, stories, {"c": org.c})] == [
        (org.people["head"].user_id, org.files["g1"], "head g"),
        (org.people["member"].user_id, org.files["g1"], "member g"),
        (org.people["member"].user_id, org.files["x"], "member x"),
    ]
    undone = _owner(
        db,
        "SELECT count(*) FROM audit_log WHERE customer_id = :c AND action = 'version.unlinked'",
        {"c": org.c},
    )
    assert undone[0][0] == 4
    assert (
        _owner(db, "SELECT undone_by::text FROM version_links WHERE customer_id = :c", {"c": org.c})[0][0]
        == management.user_id
    )
    again = _post(management, f"/documents/{org.files['g1']}/link-versions/undo")
    assert (again[0], again[1]["error"]["code"]) == (409, "not_linked")


def test_undo_only_while_the_family_is_as_the_link_left_it_and_its_folders_are_there(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    management = org.people["management"]
    assert _link(management, org, ["g2", "x"], "x")[0] == 200
    newer = api.add_document(org.c, name="x v4.pdf", folder_id=org.folders["sales"], family_id=org.files["x"],
                             version_no=4, is_current=False)  # fmt: skip
    status, reply = _post(management, f"/documents/{org.files['x']}/link-versions/undo")
    assert (status, reply["error"]["code"]) == (409, "not_newest")
    _owner(db, "DELETE FROM documents WHERE id = :d", {"d": newer})
    _owner(db, "DELETE FROM folders WHERE id = :f", {"f": org.folders["old"]})
    status, reply = _post(management, f"/documents/{org.files['x']}/link-versions/undo")
    assert (status, reply["error"]["code"]) == (409, "folder_gone")
    status, reply = _post(management, f"/documents/{org.files['y']}/link-versions/undo")
    assert (status, reply["error"]["code"]) == (409, "not_linked")
    admin = org.people["admin"]  # the family locked since, opened to the Admin: Unlock it first
    _owner(
        db,
        "UPDATE documents SET locked_at = now(), locked_by = :u, status = 'locked', text_minhash = NULL,"
        " text_bands = NULL, doc_type = NULL WHERE family_id = :f",
        {"u": admin.user_id, "f": org.files["x"]},
    )
    for name in ("g1", "g2", "x"):
        _give(db, org, "lock_grants", name, admin.user_id)
    status, reply = _post(admin, f"/documents/{org.files['x']}/link-versions/undo")
    assert (status, reply["error"]["code"]) == (409, "locked")


def test_links_and_their_undo_show_in_the_admin_log_only(db: Database) -> None:
    org = _org(Api(db))
    management, member = org.people["management"], org.people["member"]
    assert _link(management, org, ["x", "z"], "z")[0] == 200
    assert _post(management, f"/documents/{org.files['x']}/link-versions/undo")[0] == 200
    for signed in (management, member):
        for name in ("x", "z"):
            reply = signed.client.get(f"/api/documents/{org.files[name]}/activity").json()
            assert not LINKED & {i["action"] for i in reply["items"]}, name
        assert not LINKED & {i["action"] for i in signed.client.get("/api/dashboard").json()["activity"]}
    log = org.people["admin"].client.get("/api/admin/audit?limit=100").json()
    assert {i["action"] for i in log["items"]} >= LINKED


# --- the trigger fix (V19) ------------------------------------------------------------------------


def test_a_version_joined_by_accept_takes_its_familys_people_and_a_locked_family_takes_no_version(
    db: Database,
) -> None:
    """0100: Accept joins by an UPDATE; the joined version is given to whom the family is, as a
    version uploaded into it is. And a ready version joining a locked family fails 0093's CHECK."""
    api = Api(db)
    org = _org(api)
    management, plain = org.people["management"], org.people["plain"]
    _give(db, org, "document_shares", "x", plain.user_id)
    suggestion = _owner(db, _SUGGEST, {"c": org.c, "d": org.files["z"], "f": org.files["x"]})[0][0]
    status, reply = _post(management, f"/version-suggestions/{suggestion}/accept")
    assert status == 200, reply
    shared = _owner(db, "SELECT count(*) FROM document_shares WHERE document_id = :d AND user_id = :u",
                    {"d": org.files["z"], "u": plain.user_id})  # fmt: skip
    assert shared[0][0] == 1
    with pytest.raises(DBAPIError) as refused:
        _owner(
            db,
            "UPDATE documents SET family_id = :f WHERE id = :d",
            {"f": org.files["locked"], "d": org.files["y"]},
        )
    state, constraint = violation(refused.value)
    assert state == "23514" and constraint in ("documents_locked_state", "documents_locked_cleared")


# --- the banner, suggestions and Kept apart (V18, V22) -------------------------------------------


def test_the_linked_banner_comes_back_after_a_reload_and_a_linked_pairs_suggestion_is_not_asked(
    db: Database,
) -> None:
    org = _org(Api(db))
    management = org.people["management"]
    suggestion = _owner(db, _SUGGEST, {"c": org.c, "d": org.files["z"], "f": org.files["x"]})[0][0]
    assert _post(management, f"/version-suggestions/{suggestion}/accept")[0] == 200
    shown = management.client.get(f"/api/documents/{org.files['z']}").json()["version_suggestion"]
    assert (shown["id"], shown["status"], shown["role"]) == (str(suggestion), "accepted", "new")
    other = _owner(db, _SUGGEST, {"c": org.c, "d": org.files["y"], "f": org.files["g1"]})[0][0]
    assert management.client.get(f"/api/documents/{org.files['y']}").json()["version_suggestion"][
        "id"
    ] == str(other)
    assert _link(management, org, ["g2", "y"], "g2")[0] == 200
    assert management.client.get(f"/api/documents/{org.files['y']}").json()["version_suggestion"] is None


def test_kept_apart_pairs_are_listed_for_whoever_sees_both_and_only_a_pair_made_separate_joins_again(
    db: Database,
) -> None:
    """V18: kept separate (sales, z) and made separate (x v2 out of x) are listed with Not versions
    of a group; for the Admin, a pair with fin is not; linking a pair takes it off. Undo of a
    separate file joins only the pair made separate: a pair kept separate stays apart."""
    api = Api(db)
    org = _org(api)
    management, admin = org.people["management"], org.people["admin"]
    kept = _owner(db, _SUGGEST, {"c": org.c, "d": org.files["z"], "f": org.files["sales"]})[0][0]
    hidden = _owner(db, _SUGGEST, {"c": org.c, "d": org.files["y"], "f": org.files["fin"]})[0][0]
    for each in (kept, hidden):
        assert _post(management, f"/version-suggestions/{each}/reject")[0] == 200
    v2 = api.add_document(
        org.c,
        name="x2.pdf",
        folder_id=org.folders["sales"],
        family_id=org.files["x"],
        version_no=2,
        is_current=False,
    )
    assert _post(management, f"/documents/{v2}/separate", {"name": "x2 apart.pdf"})[0] == 200
    group = _owner(
        db, "SELECT group_id::text FROM version_group_members WHERE document_id = :d", {"d": org.files["x"]}
    )[0][0]
    assert (
        _post(management, f"/version-groups/{group}/keep-apart", {"document_ids": [org.files["g2"]]})[0]
        == 200
    )
    listed = management.client.get("/api/version-kept-apart").json()
    pairs = {(p["document"]["id"], p["other"]["id"], p["separated"]) for p in listed["pairs"]}
    assert pairs == {(org.files["z"], org.files["sales"], False), (org.files["y"], org.files["fin"], False),
                     (v2, org.files["x"], True)}  # fmt: skip
    assert listed["groups"] == [{"group_id": group, "id": org.files["g2"], "name": "g v2.pdf"}]
    for_admin = json.dumps(admin.client.get("/api/version-kept-apart").json())
    assert org.files["fin"] not in for_admin and org.files["z"] in for_admin
    status, _ = _post(
        management, f"/documents/{org.files['z']}/separate/undo", {"family_id": org.files["sales"]}
    )
    assert status == 404  # kept separate by a person: only linking joins it
    assert _post(management, f"/documents/{v2}/separate/undo", {"family_id": org.files["x"]})[0] == 200
    assert _link(management, org, ["z", "sales"], "sales")[0] == 200
    still = {p["document"]["id"] for p in management.client.get("/api/version-kept-apart").json()["pairs"]}
    assert still == {org.files["y"]}


# --- two sessions at once -------------------------------------------------------------------------


class _Undone(Exception):
    """Ends a transaction a test holds open without committing it."""


def _in_thread(work: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    out: list[Any] = []

    def run() -> None:
        try:
            out.append(work())
        except Exception as error:  # noqa: BLE001 - the test reads what the session ended with
            out.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def _linking(db: Database, org: Org, names: list[str], current: str, session: str | None = None) -> str:
    ids = [org.files[n] for n in names]
    with _app(db).user(org.c, org.people["management"].user_id) as conn:
        if session is not None:
            name_session(conn, session)
        plan = links_sql.prepare(conn, org.c, ids, ids, org.files[current])
        return links_sql.apply(conn, org.c, plan, by=org.people["management"].user_id)


def test_two_links_of_the_same_files_at_once_make_one_and_refuse_the_other(db: Database) -> None:
    org = _org(Api(db))
    with _app(db).user(org.c, org.people["management"].user_id) as conn:
        ids = [org.files["x"], org.files["z"]]
        links_sql.apply(
            conn, org.c, links_sql.prepare(conn, org.c, ids, ids, ids[0]), by=org.people["management"].user_id
        )
        thread, out = _in_thread(lambda: _linking(db, org, ["z", "x"], "z", "second"))
        wait_until_blocked(db, application_name="second")  # on the tree lock
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert isinstance(out[0], links_sql.Refused) and out[0].code == "already_versions"
    assert _owner(db, "SELECT count(*) FROM version_links WHERE customer_id = :c", {"c": org.c})[0][0] == 1


def test_a_link_never_waits_on_a_group_decision_under_way(db: Database) -> None:
    """V-B's NOWAIT: a decision holding x's group member, the link is refused at once ("changed",
    try again), writing nothing; once the decision is gone, the link is made."""
    org = _org(Api(db))
    before = _everything(db, org)
    with pytest.raises(_Undone), db.customer(org.c) as conn:  # a decision on x's member, never committed
        conn.execute(
            text("UPDATE version_group_members SET state = 'later' WHERE document_id = :d"),
            {"d": org.files["x"]},
        )
        thread, out = _in_thread(lambda: _linking(db, org, ["x", "z"], "x"))
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert isinstance(out[0], links_sql.Refused) and out[0].code == "changed"
        raise _Undone
    assert _everything(db, org) == before
    status, reply = _link(org.people["management"], org, ["x", "z"], "x")
    assert status == 200, reply


def test_a_lock_waiting_on_a_link_locks_every_version_it_left(db: Database) -> None:
    api = Api(db)
    org = _org(api)
    admin = org.people["admin"].user_id
    api.add_page(org.c, org.files["x"])  # a lock needs the current version's page pictures

    def lock() -> locks_sql.Outcome:
        with _app(db).user(org.c, admin) as conn:
            name_session(conn, "locking")
            return locks_sql.lock(conn, [org.files["z"]], [])

    with _app(db).user(org.c, org.people["management"].user_id) as conn:
        ids = [org.files["x"], org.files["z"]]
        links_sql.apply(
            conn, org.c, links_sql.prepare(conn, org.c, ids, ids, ids[0]), by=org.people["management"].user_id
        )
        thread, out = _in_thread(lock)
        wait_until_blocked(db, application_name="locking")
    thread.join(timeout=10)
    assert not thread.is_alive() and not isinstance(out[0], Exception) and not out[0].refused
    locked = _owner(
        db,
        "SELECT count(*) FROM documents WHERE family_id = :f AND locked_at IS NOT NULL",
        {"f": org.files["x"]},
    )
    assert locked[0][0] == 2


def test_a_link_waiting_on_a_trash_is_refused_once_it_commits(db: Database) -> None:
    org = _org(Api(db))
    with db.customer(org.c) as conn:  # as the trash route: the family's lock, then the write
        assert trash.trash_document(
            conn, org.c, org.files["z"], by=org.people["admin"].user_id, now=_now(conn)
        )
        thread, out = _in_thread(lambda: _linking(db, org, ["x", "z"], "x", "linking"))
        wait_until_blocked(db, application_name="linking")
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert isinstance(out[0], links_sql.Refused) and out[0].code == "in_trash"


def _now(conn: Any) -> Any:
    return conn.execute(text("SELECT now()")).scalar_one()
