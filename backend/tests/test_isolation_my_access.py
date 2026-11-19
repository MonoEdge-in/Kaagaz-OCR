"""#707: My access, GET /me/access: the signed-in person's own access and why, any role.

The organisation is test_isolation_access_views' ``_walled`` one (Finance Department only with its
member and Head, Salaries private inside it with a person named, Sales open with a locked file the
reviewer ``plain`` may open, a Finance file given to ``plain``). Each person gets their own access
only; a department, folder or file kept from them is never named, counted or pointed at (its id,
or its name in a path); another person's or another organisation's access never appears."""

from typing import Any

import pytest

from kaagaz.db.engine import Database
from tests.test_api_support import Api, Signed
from tests.test_isolation_access_api import _call, _ids
from tests.test_isolation_access_views import Org, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_KEYS = {"management", "departments", "private_folders", "files", "walled"}
_GIVE = "INSERT INTO document_shares (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :by)"


def _mine(signed: Signed) -> dict[str, Any]:
    status, body = _call(signed, "GET", "/api/me/access")
    assert status == 200, body
    assert set(body) == _KEYS
    return dict(body)


def _raw(signed: Signed) -> str:
    reply = signed.client.get("/api/me/access")
    assert reply.status_code == 200
    return reply.text


def _first(db: Database, org: Org) -> tuple[str, str]:
    """The organisation's first admin (who gave ``plain`` the Finance file): (id, name)."""
    row = _owner(
        db,
        "SELECT u.id::text, u.display_name FROM document_shares s JOIN users u ON u.id = s.given_by"
        " WHERE s.document_id = :d",
        {"d": org.files["given"]},
    )[0]
    return str(row[0]), str(row[1])


def test_each_person_sees_their_own_access_and_why(db: Database) -> None:
    org = _walled(Api(db))
    f, files = org.folders, org.files
    finance = {"id": f["finance"], "name": "Finance", "head": False, "department_only": True}
    member = _mine(org.people["member"])
    assert member == {
        "management": False,
        "departments": [finance],
        "private_folders": [],
        "files": [],
        "walled": True,
    }
    assert _mine(org.people["head"])["departments"] == [finance | {"head": True}]
    # Named on Salaries, inside Finance (Department only, not theirs): the folder, its path without Finance.
    named = _mine(org.people["named"])
    assert named["departments"] == [] and named["files"] == []
    assert named["private_folders"] == [{"id": f["salaries"], "name": "Salaries", "path": "Salaries"}]
    # Given the Finance file: by whom and when, no path (Finance is kept from them); the locked file
    # they may open is not "given".
    plain = _mine(org.people["plain"])
    assert plain["departments"] == [] and plain["private_folders"] == []
    [given] = plain["files"]
    assert set(given) == {"id", "name", "path", "given_at", "given_by"}
    assert (given["id"], given["name"], given["path"]) == (files["given"], "given.pdf", None)
    assert given["given_by"] == _first(db, org)[1] and given["given_at"]
    management = _mine(org.people["management"])
    assert management["management"] is True and management["departments"] == []
    # Any role: an Admin in an admin session too.
    assert _mine(org.people["admin"])["management"] is False


def test_a_file_given_then_trashed_or_locked_and_a_folder_opened_again_leave_the_lists(db: Database) -> None:
    org = _walled(Api(db))
    p, f, files = _ids(org), org.folders, org.files
    member = org.people["member"]
    for file in ("sales", "locked"):  # locked: never granted to the member, so they can't see it
        _owner(db, _GIVE, {"c": org.c, "d": files[file], "u": p["member"], "by": None})
    [given] = _mine(member)["files"]
    assert (given["id"], given["path"], given["given_by"]) == (
        files["sales"],
        "Sales",
        None,
    )  # Kaagaz gave it
    _owner(
        db,
        "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE id = :d",
        {"u": p["admin"], "d": files["sales"]},
    )
    assert _mine(member)["files"] == []
    _owner(db, "UPDATE folders SET private = false WHERE id = :f", {"f": f["salaries"]})
    assert _mine(org.people["named"])["private_folders"] == []  # the names are kept, unused


def test_another_persons_and_another_organisations_access_never_appear(db: Database) -> None:
    org, other = _walled(Api(db)), _walled(Api(db))
    strangers = {*other.folders.values(), *other.files.values(), *_ids(other).values()}
    for name, signed in org.people.items():
        if name == "off":
            continue
        raw = _raw(signed)
        assert [each for each in strangers if each in raw] == [], name
    # Each person's own only: the Finance file given to plain is in nobody else's reply.
    for name in ("member", "head", "named", "admin"):
        assert org.files["given"] not in _raw(org.people[name]), name
    assert org.folders["salaries"] not in _raw(org.people["member"])  # named only for "named"


def test_a_person_kept_from_a_place_gets_no_trace_of_it(db: Database) -> None:
    """Neither a department, folder or file's id nor its name, and no count of what is kept: one
    yes/no (``walled``) that some files are kept to other people."""
    org = _walled(Api(db))
    f, files = org.folders, org.files
    kept = {
        "plain": (f["finance"], "Finance", f["salaries"], "Salaries", files["fin"], files["sal"]),
        "named": (f["finance"], "Finance", files["fin"], files["given"]),
        "member": (f["salaries"], "Salaries", files["sal"], files["given"]),
    }
    for name, things in kept.items():
        raw = _raw(org.people[name])
        assert [thing for thing in things if thing in raw] == [], name
        assert "count" not in raw and "kept" not in raw, name
