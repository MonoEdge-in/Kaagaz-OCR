"""Possible versions (V17, #648, V-B): who sees the version finder's groups, and the decisions.

The organisation is test_isolation_access_views' ``_walled`` one, with five groups written as the
finder leaves them (members and pairs, as the owner):

* ``open``: sales (open), top (no folder), loose (open) and gone (Sales, in Trash); sales~top,
  top~loose, sales~gone.
* ``walled``: fin (Finance, Department only), sal (Salaries, private), given (Finance, given to the
  plain reviewer) and trashed (Salaries, in Trash); fin~sal, sal~given, fin~trashed.
* ``chain``: a~c, c~b, b~e, e~f, where a, c, e and f are in Sales and b in Finance.
* ``locked``: the locked file (open to the Admin here), x and y (Sales); locked~x, x~y, locked~y.
* ``duo``: p~q (Sales).

Only Admins and Management see them (Tushar, 16 Nov 2026), and a group is all or nothing (review,
16 Nov 2026): one file of it the viewer can't see, and the group doesn't exist for them, so a hidden
file never shows, nor the gap it leaves. Files they can see in Trash or locked are left out, as
they aren't secret. Management sees everything but the lock nobody gave them; the Admin, who can't
see Finance or Salaries, sees open, locked (its locked file left out) and duo. A hidden group or
file answers exactly as an id that never was."""

import json
import uuid
from typing import Any

import pytest

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed
from tests.test_isolation_access_views import Org, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_PAIR = (
    "INSERT INTO version_pairs (customer_id, first_id, second_id, text_overlap, text_overlap_exact,"
    " name_similarity) SELECT :c, least(a, b), greatest(a, b), :t, true, :n"
    " FROM (SELECT CAST(:a AS uuid) a, CAST(:b AS uuid) b) x"
)
_MEMBER = (
    "INSERT INTO version_group_members (customer_id, document_id, group_id, position) VALUES (:c, :d, :g, :p)"
)
_STATES = (
    "SELECT document_id::text, state, decided_by::text FROM version_group_members WHERE customer_id = :c"
)
GROUPS = {
    "open": (["sales", "top", "loose", "gone"],
             [("sales", "top", 0.9), ("top", "loose", 0.85), ("sales", "gone", 0.99)]),
    "walled": (["fin", "sal", "given", "trashed"],
               [("fin", "sal", 0.95), ("sal", "given", 0.9), ("fin", "trashed", 0.99)]),
    "chain": (["a", "c", "b", "e", "f"], [("a", "c", 0.9), ("c", "b", 0.9), ("b", "e", 0.9), ("e", "f", 0.9)]),
    "locked": (["locked", "x", "y"], [("locked", "x", 0.9), ("x", "y", 0.9), ("locked", "y", 0.9)]),
    "duo": (["p", "q"], [("p", "q", 0.9)]),
}  # fmt: skip
EXTRA = {"gone": "sales", "a": "sales", "b": "finance", "c": "sales", "e": "sales", "f": "sales"} | {
    name: "sales" for name in ("x", "y", "p", "q")
}


def _org(api: Api) -> tuple[Org, dict[str, str]]:
    """The walled organisation with its five groups; (org, group ids by name)."""
    org = _walled(api)
    first = org.people["admin"].user_id
    for name, folder in EXTRA.items():
        org.files[name] = api.add_document(
            org.c, name=f"{name}.pdf", folder_id=org.folders[folder], uploaded_by=first
        )
    _owner(
        api.db,
        "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE id = :d",
        {"u": first, "d": org.files["gone"]},
    )
    _owner(  # the Admin may open the locked file: only Ready's rule keeps it out
        api.db,
        "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :u)",
        {"c": org.c, "d": org.files["locked"], "u": first},
    )
    ids = {}
    for name, (members, pairs) in GROUPS.items():
        ids[name] = group = str(uuid.uuid4())
        for position, file in enumerate(members, 1):
            _owner(api.db, _MEMBER, {"c": org.c, "d": org.files[file], "g": group, "p": position})
        for a, b, overlap in pairs:
            params = {"c": org.c, "a": org.files[a], "b": org.files[b], "t": overlap, "n": 0.5}
            _owner(api.db, _PAIR, params)
    return org, ids


def _get(signed: Signed, path: str) -> tuple[int, Any]:
    reply = signed.client.get(f"/api{path}")
    return reply.status_code, reply.json()


def _post(signed: Signed, path: str, files: list[str]) -> tuple[int, Any]:
    reply = signed.client.post(f"/api{path}", json={"document_ids": files}, headers=CSRF)
    return reply.status_code, reply.json()


def _shown(org: Org, group: dict[str, Any]) -> list[str]:
    names = {v: k for k, v in org.files.items()}
    return [names[file["id"]] for file in group["files"]]


def _listed(org: Org, signed: Signed) -> dict[str, list[str]]:
    status, reply = _get(signed, "/version-groups")
    assert status == 200, reply
    return {group["id"]: _shown(org, group) for group in reply["items"]}


def _states(db: Database, org: Org) -> dict[str, tuple[str, str | None]]:
    names = {v: k for k, v in org.files.items()}
    return {names[d]: (s, by) for d, s, by in _owner(db, _STATES, {"c": org.c})}


def test_each_role_sees_only_what_it_may(db: Database) -> None:
    """The matrix: Management every group but the one with the lock nobody gave them, each without
    its files in Trash; the Admin only the groups whose every file they can see, without the locked
    file they may open; Heads, members, reviewers and named people 403, in plain words."""
    org, ids = _org(Api(db))
    assert _listed(org, org.people["management"]) == {
        ids["open"]: ["sales", "top", "loose"],
        ids["walled"]: ["fin", "sal", "given"],
        ids["chain"]: ["a", "c", "b", "e", "f"],
        ids["duo"]: ["p", "q"],
    }
    assert _listed(org, org.people["admin"]) == {
        ids["open"]: ["sales", "top", "loose"],
        ids["locked"]: ["x", "y"],
        ids["duo"]: ["p", "q"],
    }
    for role in ("head", "member", "plain", "named"):
        status, reply = _get(org.people[role], "/version-groups")
        assert (status, reply["error"]["code"]) == (403, "forbidden"), role
        assert reply["error"]["message"] == "Only Admins and Management can see possible versions.", role


def test_a_hidden_file_bridging_a_chain_hides_the_whole_group(db: Database) -> None:
    """a~c, c~b, b~e, e~f with b hidden from the Admin: the group doesn't exist for them (no a, c, e
    or f anywhere in their list, and its id is a random id's 404), rather than two halves with a
    gap; Management, who can see b, sees all five."""
    org, ids = _org(Api(db))
    admin = org.people["admin"]
    listed = json.dumps(_get(admin, "/version-groups")[1])
    for name in ("a", "b", "c", "e", "f"):
        assert org.files[name] not in listed, name
    assert _get(admin, f"/version-groups/{ids['chain']}") == _get(admin, f"/version-groups/{uuid.uuid4()}")
    shown = _get(org.people["management"], f"/version-groups/{ids['chain']}")[1]
    assert _shown(org, shown) == ["a", "c", "b", "e", "f"]


def test_a_group_never_names_a_file_in_trash_and_says_why_in_plain_words(db: Database) -> None:
    """The Admin's open group: sales, top and loose in the finder's order, renumbered, loose the
    suggested latest, each with its reasons in plain words; the file in Trash nowhere in the reply."""
    org, ids = _org(Api(db))
    status, group = _get(org.people["admin"], f"/version-groups/{ids['open']}")
    assert status == 200, group
    sales, top, loose = org.files["sales"], org.files["top"], org.files["loose"]
    assert [(f["id"], f["position"], f["state"]) for f in group["files"]] == [
        (sales, 1, "open"),
        (top, 2, "open"),
        (loose, 3, "open"),
    ]
    assert group["suggested_latest_id"] == loose and group["kept_apart"] == []
    assert group["files"][0]["reasons"] == [
        {
            "with": top,
            "text_overlap": pytest.approx(0.9),
            "text_overlap_estimated": False,
            "name_similarity": pytest.approx(0.5),
            "shared_identifier": None,
            "words": "90% the same text",
        }
    ]
    said = json.dumps(group)
    assert org.files["gone"] not in said and "gone.pdf" not in said
    walled = _get(org.people["management"], f"/version-groups/{ids['walled']}")[1]
    sal = next(f for f in walled["files"] if f["name"] == "sal.pdf")
    assert sorted(r["with"] for r in sal["reasons"]) == sorted([org.files["fin"], org.files["given"]])


def test_a_hidden_group_or_file_answers_as_one_that_never_was(db: Database) -> None:
    """For the Admin: a group with a file they can't see, a file not in the group or hidden, a
    locked one, another organisation's group, all exactly the reply of a random id; nothing written.
    For a Head, the same 403 for any id."""
    api = Api(db)
    org, ids = _org(api)
    other, other_ids = _org(api)
    admin, before = org.people["admin"], _states(db, org)
    random_group, random_file = str(uuid.uuid4()), str(uuid.uuid4())
    missing = _get(admin, f"/version-groups/{random_group}")
    assert missing[0] == 404
    for group in (ids["walled"], ids["chain"], other_ids["open"]):
        assert _get(admin, f"/version-groups/{group}") == missing
    for action in ("keep-apart", "keep-apart/undo", "later"):
        refused = _post(admin, f"/version-groups/{random_group}/{action}", [random_file])
        assert refused[0] == 404, action
        for group, file in (
            (ids["open"], org.files["fin"]),  # hidden from the Admin
            (ids["open"], random_file),
            (ids["open"], other.files["sales"]),
            (ids["chain"], org.files["a"]),  # visible, in a group with a file hidden from them
            (ids["walled"], org.files["trashed"]),
            (ids["locked"], org.files["locked"]),  # may open it, but locked
            (ids["duo"], org.files["gone"]),  # in Trash, and not in the group
            (other_ids["open"], other.files["sales"]),
        ):
            assert _post(admin, f"/version-groups/{group}/{action}", [file]) == refused, (action, file)
        both = _post(admin, f"/version-groups/{ids['open']}/{action}", [org.files["sales"], org.files["fin"]])
        assert both == refused, action  # all or none
    assert _post(admin, f"/version-groups/{ids['duo']}/keep-apart/undo", [org.files["p"]]) == refused
    assert _states(db, org) == before
    head = org.people["head"]
    forbidden = _get(head, f"/version-groups/{random_group}")
    assert forbidden[0] == 403 and _get(head, f"/version-groups/{ids['open']}") == forbidden
    assert _post(head, f"/version-groups/{ids['open']}/keep-apart", [org.files["sales"]])[0] == 403
    assert _states(db, org) == before


def test_not_versions_is_kept_until_undone_and_audited(db: Database) -> None:
    """Management keeps fin apart: walled shows sal and given (sal~given), with fin under kept
    apart; the Admin still can't see the group; Undo puts it back. Each is one audit row about the
    file, by Management, naming the group."""
    api = Api(db)
    org, ids = _org(api)
    management, fin = org.people["management"], org.files["fin"]
    status, reply = _post(management, f"/version-groups/{ids['walled']}/keep-apart", [fin])
    assert status == 200, reply
    assert _shown(org, reply["group"]) == ["sal", "given"]
    assert reply["group"]["kept_apart"] == [{"id": fin, "name": "fin.pdf"}]
    assert _states(db, org)["fin"] == ("kept_apart", management.user_id)
    assert _get(org.people["admin"], f"/version-groups/{ids['walled']}")[0] == 404
    for group in _listed(org, management).values():
        assert "fin" not in group  # never suggested again while kept apart
    again = _post(management, f"/version-groups/{ids['walled']}/keep-apart", [fin])
    assert again[0] == 404  # kept apart already: no longer a file the group shows
    status, reply = _post(management, f"/version-groups/{ids['walled']}/keep-apart/undo", [fin])
    assert status == 200, reply
    assert _shown(org, reply["group"]) == ["fin", "sal", "given"] and reply["group"]["kept_apart"] == []
    rows = _owner(
        db,
        "SELECT action, actor_user_id::text, target_id, details->>'group_id' FROM audit_log"
        " WHERE customer_id = :c AND action LIKE 'version.kept_apart%' ORDER BY id",
        {"c": org.c},
    )
    assert [tuple(row) for row in rows] == [
        ("version.kept_apart", management.user_id, fin, ids["walled"]),
        ("version.kept_apart_undone", management.user_id, fin, ids["walled"]),
    ]


def test_a_group_with_under_two_files_left_leaves_the_list_but_keeps_its_undo(db: Database) -> None:
    """Keeping p apart leaves q alone: the duo group is off the list, but after a reload its page
    still shows p kept apart (review, #648), so Undo works; Undo brings the pair back."""
    org, ids = _org(Api(db))
    management, p = org.people["management"], org.files["p"]
    status, reply = _post(management, f"/version-groups/{ids['duo']}/keep-apart", [p])
    assert status == 200, reply
    assert (reply["group"]["files"], reply["group"]["kept_apart"]) == ([], [{"id": p, "name": "p.pdf"}])
    assert reply["group"]["suggested_latest_id"] is None
    assert ids["duo"] not in _listed(org, management)
    assert _get(management, f"/version-groups/{ids['duo']}") == (200, reply["group"])
    status, reply = _post(management, f"/version-groups/{ids['duo']}/keep-apart/undo", [p])
    assert status == 200 and _shown(org, reply["group"]) == ["p", "q"]
    _post(management, f"/version-groups/{ids['duo']}/keep-apart", [p, org.files["q"]])  # both apart
    assert _get(management, f"/version-groups/{ids['duo']}")[1]["kept_apart"] == [
        {"id": p, "name": "p.pdf"},
        {"id": org.files["q"], "name": "q.pdf"},
    ]


def test_the_decisions_show_in_the_admin_log_only(db: Database) -> None:
    """Review (#648): Not versions, its Undo and Decide later are Admins' and Management's
    decisions: never in a file's timeline or the dashboard's feed, which members see; all three in
    the admin's log."""
    org, ids = _org(Api(db))
    management, p, q = org.people["management"], org.files["p"], org.files["q"]
    _post(management, f"/version-groups/{ids['duo']}/later", [q])
    _post(management, f"/version-groups/{ids['duo']}/keep-apart", [p])
    _post(management, f"/version-groups/{ids['duo']}/keep-apart/undo", [p])
    decisions = {"version.kept_apart", "version.kept_apart_undone", "version.later"}
    for signed in (management, org.people["member"]):
        for path in (f"/documents/{p}/activity", f"/documents/{q}/activity"):
            status, reply = _get(signed, path)
            assert status == 200 and not decisions & {i["action"] for i in reply["items"]}, path
        feed = _get(signed, "/dashboard")[1]["activity"]
        assert not decisions & {i["action"] for i in feed}
    status, log = _get(org.people["admin"], "/admin/audit?limit=100")
    assert status == 200 and decisions <= {i["action"] for i in log["items"]}


def test_decide_later_keeps_the_files_and_puts_the_group_last(db: Database) -> None:
    org, _ = _org(Api(db))
    management = org.people["management"]
    first = list(_listed(org, management))
    status, reply = _post(
        management, f"/version-groups/{first[0]}/later", [f["id"] for f in _files(management, first[0])]
    )
    assert status == 200, reply
    assert {f["state"] for f in reply["group"]["files"]} == {"later"}
    assert list(_listed(org, management)) == [*first[1:], first[0]]
    rows = _owner(
        db, "SELECT count(*) FROM audit_log WHERE customer_id = :c AND action = 'version.later'", {"c": org.c}
    )
    assert rows[0][0] == len(reply["group"]["files"])
    kept = _post(management, f"/version-groups/{first[0]}/keep-apart", [reply["group"]["files"][0]["id"]])
    assert kept[0] == 200  # a file left for later can still be kept apart


def _files(signed: Signed, group: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = _get(signed, f"/version-groups/{group}")[1]["files"]
    return files
