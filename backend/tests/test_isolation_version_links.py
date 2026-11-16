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

import io
import json
import threading
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.api.routers import version_links as links_route
from kaagaz.cli import main
from kaagaz.db import audit, trash
from kaagaz.db import locks as locks_sql
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
    assert _link(management, org, ["y", "x"], "x")[0] == 200  # a version whose reading failed may be older


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


def test_an_admin_who_would_open_a_wall_to_themselves_types_their_password(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fin is given to the Admin: linking it into Sales opens nothing new to them (review A on #658:
    the count knows they were given it), so no password. Every file linked is one they see, so a
    real gain needs a wall the view and the count see differently: the count is made to say the
    Admin gains, and their password is asked (as a folder move asks it); with it the link is made."""
    api = Api(db)
    org = _org(api)
    admin = org.people["admin"]
    _give(db, org, "document_shares", "fin", admin.user_id)
    assert _link(admin, org, ["fin", "sales"], "sales")[0] == 200
    assert _state(db, org, "fin")["fin"]["family_id"] == "sales"
    monkeypatch.setattr(links_route, "_audience", lambda conn, user, moves: (set(), {user.user_id}))
    before = _everything(db, org)
    status, reply = _link(admin, org, ["x", "z"], "z")
    assert (status, reply["error"]["code"]) == (403, "password_required")
    assert _everything(db, org) == before
    assert _link(admin, org, ["x", "z"], "z", password=PASSWORD)[0] == 200


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


# --- review fixes (#658) --------------------------------------------------------------------------


def _undo(signed: Signed, org: Org, name: str, **body: Any) -> tuple[int, Any]:
    return _post(signed, f"/documents/{org.files[name]}/link-versions/undo", body or None)


def test_undo_works_after_a_tag_it_would_put_back_was_deleted(db: Database) -> None:
    """Review 1: g1's tag t2 deleted while linked; Undo puts back the tags still there."""
    org = _org(Api(db))
    management = org.people["management"]
    assert _link(management, org, ["g2", "x"], "x")[0] == 200
    _owner(db, "DELETE FROM tags WHERE id = :t", {"t": org.folders["t2"]})
    status, reply = _undo(management, org, "x")
    assert status == 200, reply
    assert _state(db, org, "g1")["g1"]["tags"] == []


@pytest.mark.parametrize("status", ["received", "clean"])
def test_a_file_still_being_read_is_never_linked(db: Database, status: str) -> None:
    """Review 2: a joining file, or another version of a joining family, still being read would be
    made current by its run, or its run would find its family changed: 409 not_ready, nothing written."""
    org = _org(Api(db))
    management = org.people["management"]
    for name, others in (("y", ["x", "y"]), ("g1", ["x", "g2"])):
        _owner(db, "UPDATE documents SET status = :s WHERE id = :d", {"s": status, "d": org.files[name]})
        before = _everything(db, org)
        status_code, reply = _link(management, org, others, "x")
        assert (status_code, reply["error"]["code"]) == (409, "not_ready"), name
        assert _everything(db, org) == before
        _owner(db, "UPDATE documents SET status = 'ready' WHERE id = :d", {"d": org.files[name]})


def test_undo_gives_each_version_to_exactly_whom_it_was_given_and_never_to_someone_switched_off(
    db: Database,
) -> None:
    """Review 6 and 12: g1 is given to the named person, g2 to no one (versions need not be given alike),
    x to the plain reviewer, held to the head; held is held for the member and owned by the head.
    Linked, every joining version is given to the plain reviewer (0100). The member and the head are
    switched off meanwhile. Undo: g1 to the named person alone, g2 to no one, x to the plain reviewer,
    held to no one (the head is switched off); held is held for the member and owned by the head again."""
    org = _org(Api(db))
    management, people = org.people["management"], org.people
    _give(db, org, "document_shares", "g1", people["named"].user_id)
    _give(db, org, "document_shares", "x", people["plain"].user_id)
    _give(db, org, "document_shares", "held", people["head"].user_id)
    _owner(
        db,
        "UPDATE documents SET owner_id = :u WHERE id = :d",
        {"u": people["head"].user_id, "d": org.files["held"]},
    )
    shares = (
        "SELECT document_id::text, user_id::text FROM document_shares"
        " WHERE document_id = ANY(CAST(:d AS uuid[]))"
    )
    names = ("g1", "g2", "x", "held")
    ids = {org.files[n]: n for n in names}

    def given() -> set[tuple[str, str]]:
        who = {p.user_id: n for n, p in people.items()}
        return {(ids[d], who[u]) for d, u in _owner(db, shares, {"d": list(ids)})}

    assert given() == {("g1", "named"), ("x", "plain"), ("held", "head")}
    assert _link(management, org, ["g2", "held", "x"], "x")[0] == 200
    assert {("g1", "plain"), ("g2", "plain"), ("held", "plain")} <= given()
    _owner(
        db,
        "UPDATE users SET disabled = true WHERE id IN (:a, :b)",
        {"a": people["member"].user_id, "b": people["head"].user_id},
    )
    status, reply = _undo(management, org, "x")
    assert status == 200, reply
    assert given() == {("g1", "named"), ("x", "plain")}
    held = _state(db, org, "held")["held"]
    assert (held["held_for"], held["owner_id"], held["folder_id"]) == (
        people["member"].user_id,
        people["head"].user_id,
        None,
    )


def test_the_confirm_step_counts_whom_the_files_are_given_to(db: Database) -> None:
    """Review 3 and A: ``given`` (Finance) is given to the plain reviewer. Linked into fin, it is no
    longer theirs: they lose it, though Finance is Finance. A new Finance file linked into ``given``
    is given to them by 0100's trigger: they gain it, and lose nothing."""
    api = Api(db)
    org = _org(api)
    management, plain = org.people["management"], org.people["plain"].user_id
    org.files["fin2"] = api.add_document(org.c, name="fin2.pdf", folder_id=org.folders["finance"])

    def preview(other: str, current: str) -> dict[str, Any]:
        ids = [org.files[other], org.files[current]]
        body = {"document_ids": ids, "order": ids, "current_id": ids[1]}
        status, reply = _post(management, "/documents/link-versions/preview", body)
        assert status == 200, reply
        return {key: {p["id"] for p in reply[key]} for key in ("losing", "gaining")}

    assert preview("given", "fin") == {"losing": {plain}, "gaining": set()}
    assert preview("fin2", "given") == {"losing": set(), "gaining": {plain}}


def test_undo_is_for_whoever_sees_every_version_not_only_the_one_asked(db: Database) -> None:
    """Review 4: x, given to the Admin, linked into fin (Finance, which the Admin can't see): the
    Admin still sees x through its gift, but Undo is the reply of a file that never was."""
    org = _org(Api(db))
    admin = org.people["admin"]
    _give(db, org, "document_shares", "x", admin.user_id)
    assert _link(org.people["management"], org, ["x", "fin"], "fin")[0] == 200
    before = _everything(db, org)
    hidden = _undo(admin, org, "x")
    assert hidden[0] == 404
    assert hidden == _post(admin, f"/documents/{uuid.uuid4()}/link-versions/undo")
    assert _everything(db, org) == before


def test_undo_of_a_file_going_back_where_the_person_cant_see_it_is_kept(db: Database) -> None:
    """Review 8: fin linked into Sales, where the Admin sees it; the Admin's Undo puts fin back in
    Finance, which they can't see: undone (no summary), and kept."""
    org = _org(Api(db))
    assert _link(org.people["management"], org, ["fin", "sales"], "sales")[0] == 200
    status, reply = _undo(org.people["admin"], org, "fin")
    assert (status, reply) == (200, {"id": org.files["fin"], "undone": True})
    fin = _state(db, org, "fin")["fin"]
    assert (fin["family_id"], fin["folder_id"]) == ("fin", org.folders["finance"])


def test_a_link_of_too_many_versions_is_refused(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 9: at most MAX_VERSIONS versions in one link (here 3: g1, g2, x and y are 4)."""
    org = _org(Api(db))
    management = org.people["management"]
    monkeypatch.setattr(links_sql, "MAX_VERSIONS", 3)
    before = _everything(db, org)
    status, reply = _link(management, org, ["g2", "x", "y"], "x")
    assert (status, reply["error"]["code"]) == (409, "too_many_versions")
    assert _everything(db, org) == before
    assert _link(management, org, ["x", "y"], "x")[0] == 200


def test_undo_never_puts_a_file_beside_one_of_the_same_name(db: Database) -> None:
    """Review 11: y linked into x; a new y.pdf uploaded where y was (the top level): Undo is 409
    name_taken, nothing changed; once it is renamed, Undo goes through."""
    api = Api(db)
    org = _org(api)
    management = org.people["management"]
    assert _link(management, org, ["y", "x"], "x")[0] == 200
    other = api.add_document(org.c, name="Y.pdf")
    before = _everything(db, org)
    status, reply = _undo(management, org, "x")
    assert (status, reply["error"]["code"]) == (409, "name_taken")
    assert _everything(db, org) == before
    _owner(db, "UPDATE documents SET display_name = 'y (2).pdf' WHERE id = :d", {"d": other})
    assert _undo(management, org, "x")[0] == 200


def test_undo_of_accept_takes_back_the_gifts_the_join_copied(db: Database) -> None:
    """Review 5 (lead decision): x is given to the plain reviewer, z to the named person. Accepted,
    z is given to the plain reviewer too (0100); Undo takes that back and keeps z's own."""
    org = _org(Api(db))
    management, people = org.people["management"], org.people
    _give(db, org, "document_shares", "x", people["plain"].user_id)
    _give(db, org, "document_shares", "z", people["named"].user_id)
    given = "SELECT user_id::text FROM document_shares WHERE document_id = :d ORDER BY user_id"
    suggestion = _owner(db, _SUGGEST, {"c": org.c, "d": org.files["z"], "f": org.files["x"]})[0][0]
    assert _post(management, f"/version-suggestions/{suggestion}/accept")[0] == 200
    plain, named = people["plain"].user_id, people["named"].user_id
    assert {r[0] for r in _owner(db, given, {"d": org.files["z"]})} == {plain, named}
    status, reply = _post(management, f"/documents/{org.files['z']}/version-undo")
    assert status == 200, reply
    assert [r[0] for r in _owner(db, given, {"d": org.files["z"]})] == [named]
    assert [r[0] for r in _owner(db, given, {"d": org.files["x"]})] == [plain]


def test_a_folder_kaagaz_made_that_a_link_empties_is_kept_for_its_undo(db: Database) -> None:
    """Review B: y is the only file of a folder Kaagaz made. Linked into x, the folder is empty, but
    ``prune-folders --apply`` keeps it while the link is not undone, and Undo puts y back in it."""
    api = Api(db)
    org = _org(api)
    management, first = org.people["management"], org.people["admin"].user_id
    made = api.add_folder(org.c, first, "Made by Kaagaz")
    with db.customer(org.c) as conn:
        audit.record(conn, org.c, actor_user_id=None, action="folder.create", target_type="folder",
                     target_id=made, details={"auto": True})  # fmt: skip
    _owner(db, "UPDATE documents SET folder_id = :f WHERE id = :d", {"f": made, "d": org.files["y"]})
    assert _link(management, org, ["y", "x"], "x")[0] == 200
    out = io.StringIO()
    assert main(["prune-folders", "--customer-id", org.c, "--apply"], db=db, environ={}, out=out) == 0
    assert _owner(db, "SELECT count(*) FROM folders WHERE id = :f", {"f": made})[0][0] == 1, out.getvalue()
    status, reply = _undo(management, org, "x")
    assert status == 200, reply
    assert _state(db, org, "y")["y"]["folder_id"] == made


def test_an_undo_that_opens_a_file_to_the_admin_asks_their_password(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review C: Undo asks the password of an Admin it opens a file to, as the link does. With every
    version seen (review 4), a real case needs a wall the view and the count see differently, so the
    count is made to say the Admin gains: refused, nothing written; with the password, done."""
    org = _org(Api(db))
    admin = org.people["admin"]
    assert _link(org.people["management"], org, ["x", "z"], "z")[0] == 200
    monkeypatch.setattr(links_route, "_audience", lambda conn, user, moves: (set(), {user.user_id}))
    before = _everything(db, org)
    status, reply = _undo(admin, org, "x")
    assert (status, reply["error"]["code"]) == (403, "password_required")
    assert _everything(db, org) == before
    assert _undo(admin, org, "x", password=PASSWORD)[0] == 200


# --- the per-person rule (PRD 7 PR 9, #606) ------------------------------------------------------


def _sql(db: Database, org: Org, who: str | None, statement: str, **params: Any) -> Any:
    """``statement``'s first column as the app role, as ``who`` (None: no person, as the worker)."""
    database = _app(db)
    with database.customer(org.c) if who is None else database.user(org.c, org.people[who].user_id) as conn:
        return conn.execute(text(statement), params).scalar()


def _refused_with(db: Database, org: Org, who: str | None, statement: str, **params: Any) -> str | None:
    with pytest.raises(DBAPIError) as refused:
        _sql(db, org, who, statement, **params)
    return violation(refused.value)[0]


_FAMILIES_OF = "SELECT array_agg(seen ORDER BY id) FROM kaagaz_link_families(CAST(:ids AS uuid[]), false)"
_LINK_FN = "SELECT kaagaz_link_versions(CAST(:order AS uuid[]), :current)"
_UNDO_FN = "SELECT kaagaz_link_undo(:link)"


def test_the_link_functions_check_their_caller_and_every_version_again(db: Database) -> None:
    """0100's owner-rights functions, called past the route: anyone but an Admin or Management (or no
    person) is refused; a link of versions that aren't their families' whole, of a version the caller
    can't see, locked or not ready, or of one family only, and an Undo of a link not the newest, of
    a family changed since, or by someone who can't see a version, are refused; nothing is written."""
    org = _org(Api(db))
    f = org.files
    for who in ("member", "head", None):
        assert _refused_with(db, org, who, _FAMILIES_OF, ids=[f["x"]]) == "42501", who
        assert _refused_with(db, org, who, _LINK_FN, order=[f["x"], f["z"]], current=f["x"]) == "42501", who
    before = _everything(db, org)
    _owner(
        db, "UPDATE documents SET folder_id = :f WHERE id = :d", {"f": org.folders["finance"], "d": f["g1"]}
    )
    seen = [name != "g1" for name in sorted(("g1", "g2"), key=f.__getitem__)]
    assert _sql(db, org, "admin", _FAMILIES_OF, ids=[f["g2"]]) == seen  # g1 is there, hidden from the Admin
    _owner(db, "UPDATE documents SET folder_id = :f WHERE id = :d", {"f": org.folders["old"], "d": f["g1"]})
    _owner(db, "UPDATE documents SET status = 'failed' WHERE id = :d", {"d": f["y"]})
    refusals = (
        ("management", [f["g2"], f["x"]], f["x"]),  # g1 left out
        ("admin", [f["x"], f["fin"]], f["x"]),  # fin is hidden from the Admin
        ("management", [f["x"], f["bin"]], f["x"]),  # in Trash
        ("management", [f["x"], f["y"]], f["y"]),  # the latest isn't ready
        ("management", [f["g1"], f["g2"]], f["g2"]),  # one family
        ("management", [f["x"], f["x"]], f["x"]),  # twice
    )
    for who, order, current in refusals:
        assert _refused_with(db, org, who, _LINK_FN, order=order, current=current) == "23514", (who, order)
    _owner(db, "UPDATE documents SET status = 'ready' WHERE id = :d", {"d": f["y"]})
    assert _everything(db, org) == before
    first = _sql(db, org, "management", _LINK_FN, order=[f["x"], f["z"]], current=f["z"])
    second = _sql(db, org, "management", _LINK_FN, order=[f["x"], f["z"], f["y"]], current=f["y"])
    assert _refused_with(db, org, "management", _UNDO_FN, link=first) == "23514"  # not the newest
    newer = Api(db).add_document(org.c, name="v.pdf", family_id=f["y"], version_no=4, is_current=False)
    assert _refused_with(db, org, "management", _UNDO_FN, link=second) == "23514"  # the family changed
    _owner(db, "DELETE FROM documents WHERE id = :d", {"d": newer})
    _owner(
        db, "UPDATE documents SET folder_id = :f WHERE id = :d", {"f": org.folders["finance"], "d": f["z"]}
    )
    assert _refused_with(db, org, "admin", _UNDO_FN, link=second) == "23514"  # z is hidden from the Admin
    assert _refused_with(db, org, "member", _UNDO_FN, link=second) == "42501"
    _sql(db, org, "management", _UNDO_FN, link=second)
    assert _state(db, org, "y")["y"]["family_id"] == "y"


def test_a_live_links_folder_is_kept_whoever_prunes_it(db: Database) -> None:
    """Why version_links has no person rule (#648, NEEDS VRUSHIT'S APPROVAL): y and k2 are in a folder
    Kaagaz made; y is linked into fin (Finance). The plain reviewer, who can't see fin or the link,
    moves k2 out, which prunes the folder if it is empty and no live link keeps it: it is kept, and
    Undo puts y back in it."""
    api = Api(db)
    org = _org(api)
    management, first = org.people["management"], org.people["admin"].user_id
    made = api.add_folder(org.c, first, "Made by Kaagaz")
    with db.customer(org.c) as conn:
        audit.record(conn, org.c, actor_user_id=None, action="folder.create", target_type="folder",
                     target_id=made, details={"auto": True})  # fmt: skip
    _owner(db, "UPDATE documents SET folder_id = :f WHERE id = :d", {"f": made, "d": org.files["y"]})
    k2 = api.add_document(org.c, name="k2.pdf", folder_id=made, uploaded_by=first)
    assert _link(management, org, ["y", "fin"], "fin")[0] == 200
    moved = org.people["plain"].client.patch(f"/api/documents/{k2}", json={"folder_id": None}, headers=CSRF)
    assert moved.status_code == 200, moved.text
    assert _owner(db, "SELECT count(*) FROM folders WHERE id = :f", {"f": made})[0][0] == 1
    status, reply = _undo(management, org, "y")
    assert status == 200, reply
    assert _state(db, org, "y")["y"]["folder_id"] == made
