"""Possible versions (V17, #648, V-B): who sees the version finder's groups, and the decisions.

The organisation is test_isolation_access_views' ``_walled`` one, with four groups written as the
finder leaves them (members and pairs, as the owner):

* ``main``: sales (open), top (no folder), fin (Finance, Department only), sal (Salaries, private)
  and trashed (in Trash); sales~top, sales~fin, fin~sal, top~sal, sales~trashed.
* ``pair``: loose (open) and given (Finance, given to the plain reviewer).
* ``bridge``: a and c (Sales) joined only through b (Finance): a~b, b~c.
* ``locked``: held (held for the Finance member), the locked file (open to the Admin here) and x
  (Sales): held~locked, locked~x, held~x.

Only Admins and Management see them (Tushar, 16 Nov 2026); each sees only the files they can see
(PRD 7's views), and only those with a reason against another such file, so nothing they can't see
is ever named: Management everything but Trash and the lock nobody gave them; the Admin, who can't
see Finance, Salaries or the held file, only main's sales and top. A hidden group or file answers
exactly as an id that never was."""

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
    "main": (["sales", "top", "fin", "sal", "trashed"], [
        ("sales", "top", 0.9), ("sales", "fin", 0.85), ("fin", "sal", 0.95), ("top", "sal", 0.82),
        ("sales", "trashed", 0.99),
    ]),
    "pair": (["loose", "given"], [("loose", "given", 0.9)]),
    "bridge": (["a", "b", "c"], [("a", "b", 0.9), ("b", "c", 0.9)]),
    "locked": (["held", "locked", "x"], [("held", "locked", 0.9), ("locked", "x", 0.9), ("held", "x", 0.88)]),
}  # fmt: skip
ROLES = ("admin", "management", "head", "member", "plain", "named")


def _org(api: Api) -> tuple[Org, dict[str, str]]:
    """The walled organisation with its four groups; (org, group ids by name)."""
    org = _walled(api)
    first = org.people["admin"].user_id
    for name, folder in (("a", "sales"), ("b", "finance"), ("c", "sales"), ("x", "sales")):
        org.files[name] = api.add_document(
            org.c, name=f"{name}.pdf", folder_id=org.folders[folder], uploaded_by=first
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
    """The matrix: Management every live file but the lock nobody gave them; the Admin main's sales
    and top only (the bridge, pair and locked groups have under 2 files with a reason they can
    see); Heads, members, reviewers and named people 403, in plain words."""
    org, ids = _org(Api(db))
    assert _listed(org, org.people["management"]) == {
        ids["main"]: ["sales", "top", "fin", "sal"],
        ids["pair"]: ["loose", "given"],
        ids["bridge"]: ["a", "b", "c"],
        ids["locked"]: ["held", "x"],
    }
    assert _listed(org, org.people["admin"]) == {ids["main"]: ["sales", "top"]}
    for role in ("head", "member", "plain", "named"):
        status, reply = _get(org.people[role], "/version-groups")
        assert (status, reply["error"]["code"]) == (403, "forbidden"), role
        assert reply["error"]["message"] == "Only Admins and Management can see possible versions.", role


def test_a_group_never_names_a_file_the_viewer_cant_see(db: Database) -> None:
    """The Admin's main group: sales and top in the finder's order, renumbered, top the suggested
    latest, each with its one reason against the other in plain words; no id, name or reason of
    fin, sal or the file in Trash anywhere in the reply."""
    org, ids = _org(Api(db))
    status, group = _get(org.people["admin"], f"/version-groups/{ids['main']}")
    assert status == 200, group
    sales, top = org.files["sales"], org.files["top"]
    assert [(f["id"], f["position"], f["state"]) for f in group["files"]] == [
        (sales, 1, "open"),
        (top, 2, "open"),
    ]
    assert group["suggested_latest_id"] == top and group["kept_apart"] == []
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
    for hidden in ("fin", "sal", "trashed"):
        assert org.files[hidden] not in said and f"{hidden}.pdf" not in said, hidden
    management = _get(org.people["management"], f"/version-groups/{ids['main']}")[1]
    sal = next(f for f in management["files"] if f["id"] == org.files["sal"])
    assert sorted(r["with"] for r in sal["reasons"]) == sorted([org.files["fin"], org.files["top"]])


def test_a_hidden_group_or_file_answers_as_one_that_never_was(db: Database) -> None:
    """For the Admin: a group with one file they can see, a hidden file of a group they see, a
    group of another organisation, all exactly the reply of a random id; nothing written. For a
    Head, the same 403 for any id."""
    api = Api(db)
    org, ids = _org(api)
    other, other_ids = _org(api)
    admin, before = org.people["admin"], _states(db, org)
    random_group, random_file = str(uuid.uuid4()), str(uuid.uuid4())
    missing = _get(admin, f"/version-groups/{random_group}")
    assert missing[0] == 404
    for group in (ids["pair"], ids["bridge"], ids["locked"], other_ids["main"]):
        assert _get(admin, f"/version-groups/{group}") == missing
    for action in ("keep-apart", "keep-apart/undo", "later"):
        refused = _post(admin, f"/version-groups/{random_group}/{action}", [random_file])
        assert refused[0] == 404, action
        for group, file in (
            (ids["main"], org.files["fin"]),  # hidden from the Admin
            (ids["main"], random_file),
            (ids["main"], other.files["sales"]),
            (ids["pair"], org.files["loose"]),  # visible, in a group not shown to them
            (ids["locked"], org.files["locked"]),  # may open it, but locked
            (other_ids["main"], other.files["sales"]),
        ):
            assert _post(admin, f"/version-groups/{group}/{action}", [file]) == refused, (action, file)
        both = _post(admin, f"/version-groups/{ids['main']}/{action}", [org.files["sales"], org.files["fin"]])
        assert both == refused, action  # all or none
    assert _states(db, org) == before
    head = org.people["head"]
    forbidden = _get(head, f"/version-groups/{random_group}")
    assert forbidden[0] == 403 and _get(head, f"/version-groups/{ids['main']}") == forbidden
    assert _post(head, f"/version-groups/{ids['main']}/keep-apart", [org.files["sales"]])[0] == 403
    assert _states(db, org) == before


def test_not_versions_is_kept_until_undone_and_audited(db: Database) -> None:
    """Management keeps fin apart: main shows sales, top and sal (sal still has top), with fin
    under kept apart, which the Admin (who can't see fin) is never told; Undo puts it back. Each is
    one audit row about the file, by Management, naming the group."""
    api = Api(db)
    org, ids = _org(api)
    management, fin = org.people["management"], org.files["fin"]
    status, reply = _post(management, f"/version-groups/{ids['main']}/keep-apart", [fin])
    assert status == 200, reply
    assert _shown(org, reply["group"]) == ["sales", "top", "sal"]
    assert reply["group"]["kept_apart"] == [{"id": fin, "name": "fin.pdf"}]
    assert _states(db, org)["fin"] == ("kept_apart", management.user_id)
    assert _get(org.people["admin"], f"/version-groups/{ids['main']}")[1]["kept_apart"] == []
    for group in _listed(org, management).values():
        assert "fin" not in group  # never suggested again while kept apart
    again = _post(management, f"/version-groups/{ids['main']}/keep-apart", [fin])
    assert again[0] == 404  # kept apart already: no longer a file the group shows
    status, reply = _post(management, f"/version-groups/{ids['main']}/keep-apart/undo", [fin])
    assert status == 200, reply
    assert (
        _shown(org, reply["group"]) == ["sales", "top", "fin", "sal"] and reply["group"]["kept_apart"] == []
    )
    rows = _owner(
        db,
        "SELECT action, actor_user_id::text, target_id, details->>'group_id' FROM audit_log"
        " WHERE customer_id = :c AND action LIKE 'version.kept_apart%' ORDER BY id",
        {"c": org.c},
    )
    assert [tuple(row) for row in rows] == [
        ("version.kept_apart", management.user_id, fin, ids["main"]),
        ("version.kept_apart_undone", management.user_id, fin, ids["main"]),
    ]


def test_a_group_with_under_two_files_left_is_gone_until_undone(db: Database) -> None:
    """Keeping loose apart leaves given alone: the pair group is gone (null, and off the list);
    Undo brings it back."""
    org, ids = _org(Api(db))
    management = org.people["management"]
    status, reply = _post(management, f"/version-groups/{ids['pair']}/keep-apart", [org.files["loose"]])
    assert (status, reply) == (200, {"group": None})
    assert ids["pair"] not in _listed(org, management)
    assert _get(management, f"/version-groups/{ids['pair']}")[0] == 404
    status, reply = _post(management, f"/version-groups/{ids['pair']}/keep-apart/undo", [org.files["loose"]])
    assert status == 200 and _shown(org, reply["group"]) == ["loose", "given"]


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
