"""PRD 7 PR 10a (#601): the access API's writes, 0090's functions and switching a person off.

The organisation is test_isolation_access_views' ``_walled`` one: Finance (Department only) with
its member and Head, Salaries (private, inside Finance) with a person named on it, Sales (open),
Management, a plain reviewer given one Finance file and allowed to open Sales' locked one, an Admin
and a person switched off. Every write is checked for its reply, its audit row (with the person it
is about), the access version (+1, or +0 for a no-op or a refusal) and another organisation left
as it was; a folder or file the caller can't see answers exactly as an id that never was."""

import threading
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.adapters.email import MemoryMailer
from kaagaz.api.routers import access as access_router
from kaagaz.db import folders as folders_sql
from kaagaz.db import trash
from kaagaz.db.documents import add_version
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord
from tests.db_support import insert_folder
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PASSWORD, Api, Signed
from tests.test_isolation_access_views import Org, _app, _expected, _seen, _walled
from tests.test_operator_admins_db import ORGS, _signed_in
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_AUDIENCE = "SELECT * FROM kaagaz_audience(:k, :i)"
_FLAGS = ("management", "member", "head", "named", "given", "held", "everyone")


def _version(db: Database, c: str) -> int:
    return int(_owner(db, "SELECT access_version FROM customers WHERE id = :c", {"c": c})[0][0])


def _audit(db: Database, c: str, action: str) -> list[tuple[Any, ...]]:
    """(actor, target_type, target_id, subject, details) of each ``action`` row, oldest first."""
    rows = _owner(
        db,
        "SELECT actor_user_id::text, target_type, target_id, subject_user_id::text, details FROM audit_log"
        " WHERE customer_id = :c AND action = :a ORDER BY id",
        {"c": c, "a": action},
    )
    return [tuple(row) for row in rows]


def _as(db: Database, c: str, u: str, statement: str, **params: Any) -> list[Any]:
    with _app(db).user(c, u) as conn:
        return [row._mapping for row in conn.execute(text(statement), params)]


def _as_owner(db: Database, c: str, statement: str, **params: Any) -> list[Any]:
    """As the owner (no role may run kaagaz_access_of), with the organisation set."""
    with db.engine.begin() as conn:
        conn.execute(text("SELECT set_config('kaagaz.customer_id', :c, true)"), {"c": c})
        return [tuple(row) for row in conn.execute(text(statement), params)]


def _refused(db: Database, c: str, u: str, statement: str, **params: Any) -> str | None:
    """The SQLSTATE a statement run as ``u`` fails with (None: it ran)."""
    try:
        _as(db, c, u, statement, **params)
    except DBAPIError as error:
        return getattr(error.orig, "sqlstate", None)
    return None


def _call(signed: Signed, method: str, url: str, body: Any = None) -> tuple[int, Any]:
    reply = signed.client.request(method, url, json=body, headers=CSRF)
    try:
        return reply.status_code, reply.json()
    except ValueError:
        return reply.status_code, None


def _code(reply: tuple[int, Any]) -> tuple[int, str | None]:
    status, body = reply
    return status, None if not isinstance(body, dict) or "error" not in body else body["error"]["code"]


def _ids(org: Org) -> dict[str, str]:
    return {name: signed.user_id for name, signed in org.people.items()}


# --- 0090's functions --------------------------------------------------------------------------


def test_access_of_is_kaagaz_access_for_the_person_named(db: Database) -> None:
    org, other = _walled(Api(db)), _walled(Api(db))
    for name, signed in org.people.items():
        with _app(db).user(org.c, signed.user_id) as conn:
            mine = tuple(conn.execute(text("SELECT * FROM kaagaz_access()")).one())
        [named] = _as_owner(db, org.c, "SELECT * FROM kaagaz_access_of(:u)", u=signed.user_id)
        assert named == mine, name
    for stranger in (org.people["off"].user_id, other.people["management"].user_id, str(uuid.uuid4())):
        [row] = _as_owner(
            db, org.c, "SELECT person, management, departments FROM kaagaz_access_of(:u)", u=stranger
        )
        assert row == (None, False, []), stranger


def test_the_python_rule_the_views_the_audience_and_the_place_audience_agree(db: Database) -> None:
    """The four-way rule test: for every person and file (and folder), the written rule, the view
    and kaagaz_audience's sees agree, and sees is R0 and any reason; for each plain file, so does
    kaagaz_place_audience of its place."""
    org = _walled(Api(db))
    admin, app, people = org.people["admin"].user_id, _app(db), _ids(org)
    seen = {name: _seen(app, org.c, user) for name, user in people.items()}
    for name, user in people.items():
        assert seen[name] == _expected(db, org, user), name
        folders = _expected(db, org, user, folders=True)
        assert _seen(app, org.c, user, "visible_folders") == folders, name
    every = _owner(db, "SELECT id FROM documents WHERE customer_id = :c", {"c": org.c})
    for (document,) in every:
        rows = _as(db, org.c, admin, _AUDIENCE, k="document", i=str(document))
        by_person = {str(row["user_id"]): row for row in rows}
        assert people["off"] not in by_person  # every active person (and the organisation's first admin)
        assert set(by_person) >= {u for n, u in people.items() if n != "off"}
        for name, user in people.items():
            if name == "off":
                continue
            row = by_person[user]
            assert row["sees"] == (str(document) in seen[name]), (name, document)
            locked = _owner(db, "SELECT locked_at IS NOT NULL FROM documents WHERE id = :d", {"d": document})[
                0
            ][0]
            reasons = any(row[flag] for flag in _FLAGS)
            assert row["sees"] == ((not locked or row["may_open_locked"]) and reasons), (name, document)
    for folder in org.folders.values():
        rows = _as(db, org.c, admin, _AUDIENCE, k="folder", i=folder)
        for row in rows:
            user = str(row["user_id"])
            assert row["sees"] == (folder in _expected(db, org, user, folders=True)), user
    f = org.folders
    places = {
        "fin": (f["finance"], None, True),
        "sal": (f["finance"], f["salaries"], True),
        "sales": (f["sales"], None, False),
        "top": (None, None, False),
        "loose": (None, None, False),
    }
    for file, (department, private, closed) in places.items():
        rows = _as(
            db,
            org.c,
            admin,
            "SELECT * FROM kaagaz_place_audience(:d, :p, :o)",
            d=department,
            p=private,
            o=closed,
        )
        sees = {str(row["user_id"]) for row in rows if row["sees"]} & set(people.values())
        assert sees == {u for n, u in people.items() if org.files[file] in seen[n]}, file


def test_who_may_ask_who_sees_what(db: Database) -> None:
    """An Admin; the Head of its department who can see it; nobody else (insufficient_privilege).
    Another organisation's file gives no row, even to an Admin."""
    org, other = _walled(Api(db)), _walled(Api(db))
    p, files = _ids(org), org.files
    assert _refused(db, org.c, p["head"], _AUDIENCE, k="document", i=files["sal"]) is None
    assert _refused(db, org.c, p["head"], _AUDIENCE, k="folder", i=org.folders["salaries"]) is None
    for who, file in (("head", "sales"), ("member", "fin"), ("management", "fin"), ("plain", "given")):
        assert _refused(db, org.c, p[who], _AUDIENCE, k="document", i=files[file]) == "42501", who
    assert _as(db, org.c, p["admin"], _AUDIENCE, k="document", i=other.files["top"]) == []
    ids = {"folder": org.folders["finance"], "file": files["given"], "person": p["plain"]}
    # Changed on purpose (#601, 0090b): Management, and a person about a place they can see (the
    # move previews: a Head inside their department, a holder filing their own file), may ask about
    # a place; nobody about a place they can't see.
    place = "SELECT * FROM kaagaz_place_audience(:d, :p, true)"
    finance, salaries = ids["folder"], org.folders["salaries"]
    for who, department, private in (
        ("management", finance, salaries),
        ("head", finance, None),
        ("head", finance, salaries),
        ("member", finance, None),
        ("member", None, None),  # Loose: open, at the top level
    ):
        assert _refused(db, org.c, p[who], place, d=department, p=private) is None, (who, private)
    for who, department, private in (("member", finance, salaries), ("plain", finance, None)):
        assert _refused(db, org.c, p[who], place, d=department, p=private) == "42501", who
    for statement in (
        "SELECT * FROM kaagaz_kept_from_me(:folder)",
        "SELECT * FROM kaagaz_admin_document(:file)",
        "SELECT * FROM kaagaz_give_file(:file, ARRAY[CAST(:person AS uuid)])",
        "SELECT kaagaz_take_file(:file, :person)",
    ):
        for who in ("head", "management", "member"):
            assert _refused(db, org.c, p[who], statement, **ids) == "42501", (who, statement)
    assert _as(db, org.c, p["admin"], "SELECT * FROM kaagaz_admin_document(:d)", d=other.files["top"]) == []


def test_kept_from_me_names_only_what_the_admin_cant_see(db: Database) -> None:
    org = _walled(Api(db))
    admin, kept = org.people["admin"].user_id, "SELECT * FROM kaagaz_kept_from_me(:f)"
    rows = _as(db, org.c, admin, kept, f=org.folders["finance"])
    assert sorted(row["display_name"] for row in rows) == ["fin.pdf", "given.pdf"]
    [locked] = _as(db, org.c, admin, kept, f=org.folders["sales"])  # a lock nobody was given
    assert (locked["display_name"], locked["locked"]) == ("locked.pdf", True)
    assert _as(db, org.c, admin, kept, f=org.folders["loose"]) == []


# --- switching a person off ---------------------------------------------------------------------


def _rows_of(db: Database, c: str, user: str) -> tuple[int, ...]:
    return tuple(
        int(
            _owner(
                db,
                f"SELECT count(*) FROM {table} WHERE customer_id = :c AND user_id = :u",  # noqa: S608
                {"c": c, "u": user},
            )[0][0]
        )
        for table in ("department_members", "private_folder_members", "document_shares", "lock_grants")
    )


def test_switching_a_person_off_clears_their_access_and_on_again_starts_with_none(db: Database) -> None:
    org = _walled(Api(db))
    admin, plain, head = org.people["admin"], org.people["plain"].user_id, org.people["head"].user_id
    _owner(db, "UPDATE users SET management = true WHERE id = :u", {"u": head})
    for user, rows in ((plain, (0, 0, 1, 1)), (head, (1, 0, 0, 0))):
        assert _rows_of(db, org.c, user) == rows
        before = _version(db, org.c)
        reply = admin.client.patch(f"/api/admin/users/{user}", json={"disabled": True}, headers=CSRF)
        assert reply.status_code == 200, reply.text
        assert _rows_of(db, org.c, user) == (0, 0, 0, 0)
        assert _version(db, org.c) == before + 1
        assert _owner(db, "SELECT management FROM users WHERE id = :u", {"u": user}) == [(False,)]
        actor, kind, target, subject, details = _audit(db, org.c, "access.switched_off")[-1]
        assert (actor, kind, target, subject) == (admin.user_id, "user", user, user)
        assert details["files"] == rows[2] and details["departments"] == rows[0]
    _owner(db, "INSERT INTO document_shares (customer_id, document_id, user_id) VALUES (:c, :d, :u)",
           {"c": org.c, "d": org.files["top"], "u": plain})  # fmt: skip
    reply = admin.client.patch(f"/api/admin/users/{plain}", json={"disabled": False}, headers=CSRF)
    assert reply.status_code == 200
    assert _rows_of(db, org.c, plain) == (0, 0, 0, 0)  # a leftover goes too: they start with none
    [(_, _, target, subject, details)] = _audit(db, org.c, "access.switched_on")  # and it is on record
    assert (target, subject, details["files"]) == (plain, plain, 1)


def test_the_operator_switching_an_admin_off_clears_their_access_too(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    second = api.admin_sign_in(org.c)
    _owner(db, "UPDATE users SET management = true WHERE id = :u", {"u": second.user_id})
    _owner(
        db,
        "INSERT INTO department_members (customer_id, department_id, user_id, added_by)"
        " VALUES (:c, :d, :u, :u)",
        {"c": org.c, "d": org.folders["finance"], "u": second.user_id},
    )
    _, client = _signed_in(api)
    reply = client.post(f"{ORGS}/{org.c}/admins/{second.user_id}/disable", headers=CSRF)
    assert reply.status_code == 200, reply.text
    assert _rows_of(db, org.c, second.user_id) == (0, 0, 0, 0)
    assert _owner(db, "SELECT management FROM users WHERE id = :u", {"u": second.user_id}) == [(False,)]
    actor, *_ = _audit(db, org.c, "access.switched_off")[-1]
    assert actor is None


# --- the self-grant step ------------------------------------------------------------------------


def _email_api(db: Database) -> tuple[Api, MemoryMailer]:
    mailer = MemoryMailer()
    settings = make_settings(email_enabled=True, email_from="no-reply@example.com")
    return Api(db, settings=settings, mailer=mailer), mailer


def test_an_admin_giving_themselves_management_types_the_password_and_the_others_are_told(
    db: Database,
) -> None:
    api, mailer = _email_api(db)
    org = _walled(api)
    admin, other_admin = org.people["admin"], api.admin_sign_in(org.c)
    url = f"/api/admin/users/{admin.user_id}/management"
    before = _version(db, org.c)
    assert _code(_call(admin, "PUT", url, {"on": True})) == (403, "password_required")
    failed = api.user_state(org.c, admin.user_id)["failed_logins"]
    assert _code(_call(admin, "PUT", url, {"on": True, "password": "not it at all"})) == (
        400,
        "wrong_password",
    )
    assert api.user_state(org.c, admin.user_id)["failed_logins"] == failed + 1  # on the sign-in lock
    assert _version(db, org.c) == before and _audit(db, org.c, "access.management_on") == []
    status, body = _call(admin, "PUT", url, {"on": True, "password": PASSWORD})
    assert status == 200 and body["management"] is True
    assert api.user_state(org.c, admin.user_id)["failed_logins"] == 0
    assert _version(db, org.c) == before + 1
    [(actor, _, target, subject, details)] = _audit(db, org.c, "access.management_on")
    assert (actor, target, subject, details) == (admin.user_id, admin.user_id, admin.user_id, {"self": True})
    told = {sent.to for sent in mailer.sent if sent.subject == "Access was given in Kaagaz"}
    watchers = _owner(
        db,
        "SELECT email FROM users WHERE customer_id = :c AND (management OR role = 'admin') AND NOT disabled"
        " AND id <> :u",
        {"c": org.c, "u": admin.user_id},
    )
    assert told == {row[0] for row in watchers}  # never the actor, never anyone else
    assert {org.people["management"].email, other_admin.email} <= told
    text_sent = next(sent.text for sent in mailer.sent if sent.to == other_admin.email)
    assert "gave themselves Management" in text_sent and "Finance" not in text_sent


def test_a_locked_account_is_told_so(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin = org.people["admin"]
    _owner(
        db, "UPDATE users SET locked_until = now() + interval '1 hour' WHERE id = :u", {"u": admin.user_id}
    )
    url = f"/api/admin/users/{admin.user_id}/management"
    assert _code(_call(admin, "PUT", url, {"on": True, "password": PASSWORD})) == (400, "password_locked")


def test_every_management_grant_is_emailed_and_a_no_op_changes_nothing(db: Database) -> None:
    api, mailer = _email_api(db)
    org = _walled(api)
    admin, plain = org.people["admin"], org.people["plain"]
    url = f"/api/admin/users/{plain.user_id}/management"
    assert _call(admin, "PUT", url, {"on": True})[0] == 200
    assert any(sent.to == org.people["management"].email for sent in mailer.sent)
    before, sent = _version(db, org.c), len(mailer.sent)
    assert _call(admin, "PUT", url, {"on": True})[0] == 200  # already on
    assert (_version(db, org.c), len(mailer.sent), len(_audit(db, org.c, "access.management_on"))) == (
        before,
        sent,
        1,
    )
    off = org.people["off"].user_id
    assert _code(_call(admin, "PUT", f"/api/admin/users/{off}/management", {"on": True})) == (409, "disabled")
    other = _walled(api)
    stranger = other.people["plain"].user_id
    assert _code(_call(admin, "PUT", f"/api/admin/users/{stranger}/management", {"on": True})) == (
        404,
        "not_found",
    )
    assert _owner(db, "SELECT management FROM users WHERE id = :u", {"u": stranger}) == [(False,)]
    assert _code(_call(org.people["head"], "PUT", url, {"on": False})) == (403, "admin_session_required")


# --- departments --------------------------------------------------------------------------------


def test_members_and_the_head_tick(db: Database) -> None:
    org = _walled(Api(db))
    admin, plain = org.people["admin"], org.people["plain"].user_id
    finance = org.folders["finance"]
    url = f"/api/admin/users/{plain}/departments/{finance}"
    before = _version(db, org.c)
    assert _call(admin, "PUT", url, {}) == (200, {"head": False, "changed": True})
    assert _version(db, org.c) == before + 1
    assert _call(admin, "PUT", url, {}) == (200, {"head": False, "changed": False})  # ON CONFLICT
    assert _version(db, org.c) == before + 1
    assert _call(admin, "PUT", url, {"head": True}) == (200, {"head": True, "changed": True})
    assert _call(admin, "PUT", url, {}) == (200, {"head": True, "changed": False})  # left out: kept
    assert [row[1:4] for row in _audit(db, org.c, "access.member_add")] == [("folder", finance, plain)]
    assert [row[3] for row in _audit(db, org.c, "access.head_on")] == [plain]
    assert _call(admin, "DELETE", url)[0] == 204
    assert _audit(db, org.c, "access.member_remove")[-1][4] == {"was_head": True}
    assert _code(_call(admin, "DELETE", url)) == (404, "not_found")
    assert org.files["given"] in _seen(_app(db), org.c, plain)  # a file given directly stays
    salaries, off = org.folders["salaries"], org.people["off"].user_id
    assert _code(_call(admin, "PUT", f"/api/admin/users/{plain}/departments/{salaries}", {})) == (
        409,
        "not_a_department",
    )
    assert _code(_call(admin, "PUT", f"/api/admin/users/{off}/departments/{finance}", {})) == (
        409,
        "disabled",
    )
    other = _walled(Api(db))
    for user, department in ((plain, other.folders["sales"]), (other.people["plain"].user_id, finance)):
        assert _code(_call(admin, "PUT", f"/api/admin/users/{user}/departments/{department}", {})) == (
            404,
            "not_found",
        )
    assert _owner(db, "SELECT count(*) FROM department_members WHERE customer_id = :c", {"c": other.c}) == [
        (2,)
    ]


def test_an_admin_adding_themselves_or_ticking_head_types_the_password(db: Database) -> None:
    org = _walled(Api(db))
    admin = org.people["admin"]
    url = f"/api/admin/users/{admin.user_id}/departments/{org.folders['finance']}"
    assert _code(_call(admin, "PUT", url, {})) == (403, "password_required")
    assert _call(admin, "PUT", url, {"password": PASSWORD})[0] == 200
    assert _code(_call(admin, "PUT", url, {"head": True})) == (403, "password_required")
    assert _call(admin, "PUT", url, {"head": True, "password": PASSWORD})[0] == 200
    assert [
        row[4]["self"] for row in _audit(db, org.c, "access.member_add") + _audit(db, org.c, "access.head_on")
    ] == [True, True]
    assert _call(admin, "PUT", url, {"head": False}) == (
        200,
        {"head": False, "changed": True},
    )  # less: no password


def test_department_only_on_and_off(db: Database) -> None:
    api, mailer = _email_api(db)
    org = _walled(api)
    admin = org.people["admin"]
    with db.customer(org.c) as conn:
        legal = insert_folder(conn, org.c, admin.user_id, "Legal")
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id = :f", {"f": legal})
    status, body = _call(admin, "PUT", f"/api/admin/departments/{legal}/department-only", {"on": True})
    assert (status, body["warnings"], body["members"]) == (200, ["no_members"], 0)
    sales = org.folders["sales"]
    before = _version(db, org.c)
    status, body = _call(admin, "PUT", f"/api/admin/departments/{sales}/department-only", {"on": True})
    assert (status, body["department_only"]) == (200, True)
    assert _version(db, org.c) == before + 1
    active = "SELECT count(*) FROM users WHERE customer_id = :c AND NOT disabled AND NOT management"
    [(losing,)] = _owner(db, active, {"c": org.c})
    assert _audit(db, org.c, "access.department_only_on")[-1][4] == {"members": 0, "losing": losing}
    url = f"/api/admin/departments/{org.folders['finance']}/department-only"
    assert _code(_call(admin, "PUT", url, {"on": False})) == (403, "password_required")  # the Admin gains it
    assert _call(admin, "PUT", url, {"on": False, "password": PASSWORD})[0] == 200
    assert _audit(db, org.c, "access.department_only_off")[-1][4]["self"] is True
    assert any("by opening them to more people" in sent.text for sent in mailer.sent)
    salaries = org.folders["salaries"]
    assert _code(_call(admin, "PUT", f"/api/admin/departments/{salaries}/department-only", {"on": True})) == (
        409,
        "not_a_department",
    )


# --- private folders and their list ------------------------------------------------------------


def test_private_on_and_off_and_what_it_refuses(db: Database) -> None:
    org = _walled(Api(db))
    admin, plain, member = org.people["admin"], org.people["plain"], org.people["member"]
    with db.customer(org.c) as conn:
        bills = insert_folder(conn, org.c, admin.user_id, "Bills", org.folders["loose"])
        inner = insert_folder(conn, org.c, admin.user_id, "Inner", bills)
        under = insert_folder(conn, org.c, admin.user_id, "Under", org.folders["salaries"])
    named = [plain.user_id]
    before = _version(db, org.c)
    status, body = _call(admin, "PUT", f"/api/folders/{bills}/private", {"on": True, "named": named})
    assert status == 200, body
    assert (body["private"], [p["id"] for p in body["named"]], body["you_lose_access"]) == (True, named, True)
    assert _version(db, org.c) > before
    assert _audit(db, org.c, "access.named_add")[-1][3] == plain.user_id
    assert _audit(db, org.c, "access.private_on")[-1][4]["named"] == 1
    for folder, code in (
        (inner, "inside_private"),
        (org.folders["loose"], "holds_private"),
        (org.folders["finance"], "department"),
        (under, "inside_private"),
    ):
        assert _code(_call(admin, "PUT", f"/api/folders/{folder}/private", {"on": True})) == (409, code), code
    off = {"on": False}  # the Admin, not named, gains it: the self-grant step (lead decision Q2)
    assert _code(_call(admin, "PUT", f"/api/folders/{bills}/private", off)) == (403, "password_required")
    status, body = _call(admin, "PUT", f"/api/folders/{bills}/private", {**off, "password": PASSWORD})
    assert (status, body["private"], body["named"]) == (200, False, [])
    assert _audit(db, org.c, "access.private_off")[-1][4] == {"named": 0, "losing": 0, "self": True}
    assert _owner(db, "SELECT count(*) FROM private_folder_members WHERE folder_id = :f", {"f": bills}) == [
        (1,)
    ]
    assert _code(_call(member, "PUT", f"/api/folders/{bills}/private", {"on": True})) == (403, "forbidden")
    random = _call(plain, "PUT", f"/api/folders/{uuid.uuid4()}/private", {"on": True})
    assert random[0] == 404
    assert _call(plain, "PUT", f"/api/folders/{org.folders['salaries']}/private", {"on": False}) == random
    status, body = _call(
        admin, "PUT", f"/api/folders/{bills}/private", {"on": True, "named": [admin.user_id]}
    )
    assert _code((status, body)) == (403, "password_required")


def _named_url(org: Org, who: str | None = None) -> str:
    base = f"/api/folders/{org.folders['salaries']}/named"
    return base if who is None else f"{base}/{who}"


def test_the_named_list_answers_its_admin_and_its_head_only(db: Database) -> None:
    """Hidden: 404 exactly as a random id (visibility first). Visible to Management or a person
    named on it, or the Head of another department: 403. Its Head and an Admin: the list."""
    api = Api(db)
    org = _walled(api)
    with db.customer(org.c) as conn:
        conn.execute(
            text("INSERT INTO department_members (customer_id, department_id, user_id, is_head, added_by)"
                 " VALUES (:c, :d, :u, true, :u)"),
            {"c": org.c, "d": org.folders["sales"], "u": org.people["named"].user_id},
        )  # fmt: skip
    random = f"/api/folders/{uuid.uuid4()}/named"
    for who in ("plain", "member"):
        assert _call(org.people[who], "GET", _named_url(org)) == _call(org.people[who], "GET", random), who
        assert _call(org.people[who], "GET", random)[0] == 404
    for who in ("management", "named"):  # sees it; heads Sales, not Finance
        assert _code(_call(org.people[who], "GET", _named_url(org))) == (403, "forbidden"), who
    for who in ("head", "admin"):
        status, body = _call(org.people[who], "GET", _named_url(org))
        assert status == 200, (who, body)
        assert [p["id"] for p in body["named"]] == [org.people["named"].user_id]
        assert [p["id"] for p in body["heads"]] == [org.people["head"].user_id]
        assert body["folder"]["path"] == "Finance / Salaries" and body["can_edit"] is True
    finance = f"/api/folders/{org.folders['finance']}/named"
    assert _code(_call(org.people["head"], "GET", finance)) == (409, "not_private")


def test_a_head_names_and_unnames_people(db: Database) -> None:
    org = _walled(Api(db))
    head, plain = org.people["head"], org.people["plain"].user_id
    before = _version(db, org.c)
    assert _call(head, "PUT", _named_url(org, plain), {}) == (200, {"changed": True})
    assert _call(head, "PUT", _named_url(org, plain), {}) == (200, {"changed": False})
    assert _version(db, org.c) == before + 1
    assert org.files["sal"] in _seen(_app(db), org.c, plain)
    assert _audit(db, org.c, "access.named_add")[-1][:4] == (
        head.user_id,
        "folder",
        org.folders["salaries"],
        plain,
    )
    assert _call(head, "DELETE", _named_url(org, plain))[0] == 204
    assert _code(_call(head, "DELETE", _named_url(org, plain))) == (404, "not_found")
    assert _call(head, "PUT", _named_url(org, head.user_id), {}) == (200, {"changed": True})  # no password
    assert _audit(db, org.c, "access.named_add")[-1][3:] == (head.user_id, {"self": True})
    for who in (org.people["off"].user_id, str(uuid.uuid4()), _walled(Api(db)).people["plain"].user_id):
        assert _code(_call(head, "PUT", _named_url(org, who), {})) == (404, "not_found")
    admin = org.people["admin"]
    assert _code(_call(admin, "PUT", _named_url(org, admin.user_id), {})) == (403, "password_required")
    assert _call(admin, "PUT", _named_url(org, admin.user_id), {"password": PASSWORD}) == (
        200,
        {"changed": True},
    )


# --- files given directly -----------------------------------------------------------------------


def _three_versions(api: Api, org: Org) -> tuple[str, list[str]]:
    first = org.files["sales"]
    later = [
        api.add_document(
            org.c, name=f"sales v{n}.pdf", folder_id=org.folders["sales"], family_id=first, version_no=n,
            is_current=False, uploaded_by=org.people["admin"].user_id,
        )
        for n in (2, 3)
    ]  # fmt: skip
    return first, [first, *later]


def _given(db: Database, user: str, documents: list[str]) -> int:
    rows = _owner(
        db,
        "SELECT count(*) FROM document_shares WHERE user_id = :u AND document_id = ANY(CAST(:d AS uuid[]))",
        {"u": user, "d": documents},
    )
    return int(rows[0][0])


def test_a_file_is_given_and_taken_back_on_every_version(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin, member = org.people["admin"], org.people["member"].user_id
    first, versions = _three_versions(api, org)
    _owner(db, "UPDATE folders SET department_only = true WHERE id = :f", {"f": org.folders["sales"]})
    assert first not in _seen(_app(db), org.c, member)
    before = _version(db, org.c)
    assert _call(admin, "PUT", f"/api/documents/{first}/shares/{member}", {}) == (
        200,
        {"versions": 3, "changed": True},
    )
    assert _version(db, org.c) == before + 1 and _given(db, member, versions) == 3
    assert first in _seen(_app(db), org.c, member)
    [(actor, kind, target, subject, details)] = _audit(db, org.c, "access.file_give")
    assert (actor, kind, target, subject, details) == (
        admin.user_id,
        "document",
        first,
        member,
        {"versions": 3},
    )
    assert _call(admin, "PUT", f"/api/documents/{first}/shares/{member}", {}) == (
        200,
        {"versions": 3, "changed": False},
    )
    status, body = _call(admin, "GET", f"/api/documents/{first}/shares")
    assert status == 200 and [item["user"]["id"] for item in body["items"]] == [member]
    record = DocumentRecord(org.c, str(uuid.uuid4()), uuid.uuid4().hex * 2, 10, "pdf")
    with db.customer(org.c) as conn:
        added = add_version(conn, record, of_document_id=first)
    assert added is not None and _given(db, member, [record.document_id]) == 1  # a later version too
    _owner(db, "UPDATE documents SET deleted_at = now(), deleted_by = :u WHERE family_id = :f",
           {"u": admin.user_id, "f": first})  # fmt: skip
    assert _call(admin, "DELETE", f"/api/documents/{first}/shares/{member}")[0] == 204  # Trash too
    assert _given(db, member, [*versions, record.document_id]) == 0
    assert _code(_call(admin, "DELETE", f"/api/documents/{first}/shares/{member}")) == (404, "not_found")
    assert _code(_call(admin, "PUT", f"/api/documents/{first}/shares/{member}", {})) == (409, "in_trash")


def test_giving_a_file_answers_a_hidden_one_as_one_that_never_was(db: Database) -> None:
    org = _walled(Api(db))
    plain, admin = org.people["plain"], org.people["admin"]
    hidden, visible, user = org.files["fin"], org.files["sales"], org.people["member"].user_id
    for method, path, body in (
        ("GET", "/api/documents/{d}/shares", None),
        ("PUT", "/api/documents/{d}/shares/{u}", {}),
        ("DELETE", "/api/documents/{d}/shares/{u}", None),
    ):
        random = _call(plain, method, path.format(d=uuid.uuid4(), u=user), body)
        assert random[0] == 404
        assert _call(plain, method, path.format(d=hidden, u=user), body) == random, path
        assert _code(_call(plain, method, path.format(d=visible, u=user), body)) == (403, "forbidden"), path
    # The Admin can't see Finance's file, and still finds it (decision 20).
    assert _call(admin, "PUT", f"/api/documents/{hidden}/shares/{plain.user_id}", {})[0] == 200
    other = _walled(Api(db))
    for document, person in (
        (other.files["top"], plain.user_id),
        (org.files["top"], other.people["plain"].user_id),
    ):
        assert _call(admin, "PUT", f"/api/documents/{document}/shares/{person}", {})[0] == 404
    assert _owner(db, "SELECT count(*) FROM document_shares WHERE customer_id = :c", {"c": other.c}) == [(1,)]
    off = org.people["off"].user_id
    assert _code(_call(admin, "PUT", f"/api/documents/{visible}/shares/{off}", {})) == (409, "disabled")
    assert _code(_call(admin, "PUT", f"/api/documents/{visible}/shares/{admin.user_id}", {})) == (
        403,
        "password_required",
    )


def test_bulk_giving_gives_every_pair_or_nothing(db: Database) -> None:
    org = _walled(Api(db))
    admin = org.people["admin"]
    people = [org.people[n].user_id for n in ("member", "named")]
    files = [org.files[n] for n in ("sales", "top", "loose")]
    body = {"document_ids": [*files, str(uuid.uuid4())], "user_ids": people}
    assert _code(_call(admin, "POST", "/api/documents/shares", body)) == (404, "not_found")
    assert sum(_given(db, person, files) for person in people) == 0
    assert _call(admin, "POST", "/api/documents/shares", {**body, "document_ids": files}) == (
        200,
        {"given": 6},
    )
    assert len(_audit(db, org.c, "access.file_give")) == 6
    assert _call(admin, "POST", "/api/documents/shares", {**body, "document_ids": files}) == (
        200,
        {"given": 0},
    )
    too_many = {"document_ids": [str(uuid.uuid4()) for _ in range(101)], "user_ids": people}
    assert _call(admin, "POST", "/api/documents/shares", too_many)[0] == 400
    assert _code(_call(org.people["head"], "POST", "/api/documents/shares", body)) == (403, "forbidden")


# --- what the screens read ----------------------------------------------------------------------


def test_me_says_management_and_heads_and_the_folders_say_their_walls(db: Database) -> None:
    org = _walled(Api(db))
    me = org.people["head"].client.get("/api/auth/me").json()
    # Changed on purpose (#601, PR 10c): in_department.
    assert me["access"] == {"management": False, "heads": [org.folders["finance"]], "in_department": True}
    assert org.people["management"].client.get("/api/auth/me").json()["access"]["management"] is True
    folders = {f["id"]: f for f in org.people["admin"].client.get("/api/folders").json()["items"]}
    assert (
        folders[org.folders["finance"]]["department_only"],
        folders[org.folders["finance"]]["private"],
    ) == (True, False)
    assert folders[org.folders["salaries"]]["private"] is True
    users = {u["id"]: u for u in org.people["admin"].client.get("/api/admin/users").json()["items"]}
    assert users[org.people["head"].user_id]["departments"] == [
        {"id": org.folders["finance"], "name": "Finance", "head": True}
    ]
    assert users[org.people["management"].user_id]["management"] is True
    status, body = _call(org.people["admin"], "GET", f"/api/admin/users/{org.people['plain'].user_id}/access")
    assert status == 200
    assert [f["id"] for f in body["files"]] == [org.files["given"]] and body["files"][0]["path"] == "Finance"
    named = _call(org.people["admin"], "GET", f"/api/admin/users/{org.people['named'].user_id}/access")[1]
    assert named["private_folders"] == [
        {"id": org.folders["salaries"], "name": "Salaries", "path": "Finance / Salaries"}
    ]


# --- lock order and two sessions ----------------------------------------------------------------


def _in_thread(task: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    out: list[Any] = []
    thread = threading.Thread(target=lambda: out.append(task()))
    thread.start()
    return thread, out


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize("held", ["tree", "family"])
def test_giving_and_taking_wait_for_the_tree_lock_and_the_family_lock(
    db: Database, method: str, held: str
) -> None:
    """Auto-filing holds the tree lock, saving a reading the family lock: each write waits for
    either, then succeeds (no 503). Holding the tree lock, auto-filing then takes the family lock:
    the write waits holding nothing of the family, so that never deadlocks."""
    org = _walled(Api(db))
    admin, member, file = org.people["admin"], org.people["member"].user_id, org.files["sales"]
    if method == "DELETE":
        assert _call(admin, "PUT", f"/api/documents/{file}/shares/{member}", {})[0] == 200
    with db.customer(org.c) as conn:
        if held == "tree":
            folders_sql._lock_tree(conn, org.c)
        else:
            assert trash.lock_family(conn, org.c, file) == file
        thread, out = _in_thread(lambda: _call(admin, method, f"/api/documents/{file}/shares/{member}", {}))
        thread.join(timeout=1.5)
        assert thread.is_alive(), held
        if held == "tree":
            assert trash.lock_family(conn, org.c, file) == file  # as auto-filing, tree then family
    thread.join(timeout=20)
    assert out[0][0] in (200, 204), out


def test_two_admins_add_the_same_member_at_once(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admins = [org.people["admin"], api.admin_sign_in(org.c)]
    url = f"/api/admin/users/{org.people['plain'].user_id}/departments/{org.folders['sales']}"
    with db.customer(org.c) as conn:
        folders_sql._lock_tree(conn, org.c)
        threads = [_in_thread(lambda a=a: _call(a, "PUT", url, {})) for a in admins]
        threads[0][0].join(timeout=1)
        assert all(thread.is_alive() for thread, _ in threads)
    for thread, _ in threads:
        thread.join(timeout=20)
    assert sorted(out[0][1]["changed"] for _, out in threads) == [False, True]


def test_switching_a_person_off_while_an_admin_gives_them_a_file(db: Database) -> None:
    """The switch waits for the give (both take the tree lock first); then nothing is left."""
    api = Api(db)
    org = _walled(api)
    admin, other_admin, member = org.people["admin"], api.admin_sign_in(org.c), org.people["member"].user_id
    with db.customer(org.c) as conn:
        folders_sql._lock_tree(conn, org.c)
        giving = _in_thread(
            lambda: _call(admin, "PUT", f"/api/documents/{org.files['top']}/shares/{member}", {})
        )
        giving[0].join(timeout=1)
        switching = _in_thread(
            lambda: other_admin.client.patch(
                f"/api/admin/users/{member}", json={"disabled": True}, headers=CSRF
            )
        )
        switching[0].join(timeout=1)
        assert giving[0].is_alive() and switching[0].is_alive()
    for thread, _ in (giving, switching):
        thread.join(timeout=20)
    assert giving[1][0][0] in (200, 409) and switching[1][0].status_code == 200
    assert _rows_of(db, org.c, member) == (0, 0, 0, 0)


def test_the_operator_switching_an_admin_off_waits_for_an_admin_giving_them_a_file(db: Database) -> None:
    api = Api(db)
    org = _walled(api)
    admin, second = org.people["admin"], api.admin_sign_in(org.c)
    _, operator = _signed_in(api)
    with db.customer(org.c) as conn:
        folders_sql._lock_tree(conn, org.c)
        giving = _in_thread(
            lambda: _call(admin, "PUT", f"/api/documents/{org.files['top']}/shares/{second.user_id}", {})
        )
        giving[0].join(timeout=1)
        switching = _in_thread(
            lambda: operator.post(f"{ORGS}/{org.c}/admins/{second.user_id}/disable", headers=CSRF)
        )
        switching[0].join(timeout=1)
        assert giving[0].is_alive() and switching[0].is_alive()
    for thread, _ in (giving, switching):
        thread.join(timeout=20)
    assert switching[1][0].status_code == 200, switching[1][0].text
    assert _rows_of(db, org.c, second.user_id) == (0, 0, 0, 0)


def test_two_operator_switches_in_one_organisation_queue_on_the_tree_lock(db: Database) -> None:
    """0090: kaagaz_op_change_admin takes the organisation's row FOR NO KEY UPDATE (the tree lock),
    not FOR SHARE. With FOR SHARE, two switches would each hold a share and then both need the row
    for the access version: a deadlock. Here a reader's share holds them both at the start."""
    api = Api(db)
    org = _walled(api)
    admins = [api.admin_sign_in(org.c), api.admin_sign_in(org.c)]
    _, operator = _signed_in(api)
    with db.engine.begin() as conn:
        conn.execute(text("SELECT 1 FROM customers WHERE id = :c FOR SHARE"), {"c": org.c})
        switching = [
            _in_thread(lambda a=a: operator.post(f"{ORGS}/{org.c}/admins/{a.user_id}/disable", headers=CSRF))
            for a in admins
        ]
        switching[0][0].join(timeout=1.5)
        assert all(thread.is_alive() for thread, _ in switching)
    for thread, _ in switching:
        thread.join(timeout=20)
    assert [out[0].status_code for _, out in switching] == [200, 200]


# --- review fixes (#601) ------------------------------------------------------------------------


def test_the_password_is_proved_with_no_connection_held(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The password check opens its own short transactions: a request holding a pooled connection
    meanwhile would starve the pool for every organisation under parallel self-grants."""
    api = Api(db)
    org = _walled(api)
    admin, me = org.people["admin"], org.people["admin"].user_id
    held: list[int] = []
    proved = access_router.prove_password

    def watched(context: Any, user: Any, password: str) -> None:
        held.append(db.engine.pool.checkedout())
        proved(context, user, password)

    monkeypatch.setattr(access_router, "prove_password", watched)
    with db.customer(org.c) as conn:
        bills = insert_folder(conn, org.c, me, "Bills", org.folders["loose"])
    for method, url, body in (
        ("PUT", f"/api/admin/users/{me}/management", {"on": True}),
        ("PUT", f"/api/admin/users/{me}/departments/{org.folders['sales']}", {}),
        ("PUT", f"/api/admin/departments/{org.folders['finance']}/department-only", {"on": False}),
        ("PUT", f"/api/folders/{bills}/private", {"on": True, "named": [me]}),
        ("PUT", f"/api/folders/{org.folders['salaries']}/named/{me}", {}),
        ("PUT", f"/api/documents/{org.files['top']}/shares/{me}", {}),
        ("POST", "/api/documents/shares", {"document_ids": [org.files["loose"]], "user_ids": [me]}),
    ):
        status, reply = _call(admin, method, url, {**body, "password": PASSWORD})
        assert status == 200, (url, reply)
    assert held == [0] * 7


def test_a_head_whose_tick_goes_while_their_naming_waits_is_refused(db: Database) -> None:
    org = _walled(Api(db))
    head, plain = org.people["head"], org.people["plain"].user_id
    _owner(
        db,
        "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
        " VALUES (:c, :f, :u, :u)",
        {"c": org.c, "f": org.folders["salaries"], "u": head.user_id},
    )  # still sees Salaries once no longer Head
    with db.customer(org.c) as conn:
        folders_sql._lock_tree(conn, org.c)
        thread, out = _in_thread(lambda: _call(head, "PUT", _named_url(org, plain), {}))
        thread.join(timeout=1.5)
        assert thread.is_alive()
        conn.execute(
            text("UPDATE department_members SET is_head = false WHERE customer_id = :c AND user_id = :u"),
            {"c": org.c, "u": head.user_id},
        )
    thread.join(timeout=20)
    assert _code(out[0]) == (403, "forbidden")
    assert _owner(db, "SELECT count(*) FROM private_folder_members WHERE user_id = :u", {"u": plain}) == [
        (0,)
    ]


def test_a_folder_opened_while_a_naming_waits_is_not_private(db: Database) -> None:
    org = _walled(Api(db))
    head, plain = org.people["head"], org.people["plain"].user_id
    with db.customer(org.c) as conn:
        folders_sql._lock_tree(conn, org.c)
        thread, out = _in_thread(lambda: _call(head, "PUT", _named_url(org, plain), {}))
        thread.join(timeout=1.5)
        assert thread.is_alive()
        conn.execute(text("UPDATE folders SET private = false WHERE id = :f"), {"f": org.folders["salaries"]})
    thread.join(timeout=20)
    assert _code(out[0]) == (409, "not_private")
    assert _owner(db, "SELECT count(*) FROM private_folder_members WHERE user_id = :u", {"u": plain}) == [
        (0,)
    ]


def test_a_folder_moved_into_a_private_one_while_private_waits_is_refused(db: Database) -> None:
    org = _walled(Api(db))
    admin = org.people["admin"]
    with db.customer(org.c) as conn:
        bills = insert_folder(conn, org.c, admin.user_id, "Bills", org.folders["loose"])
    with db.customer(org.c) as conn:
        folders_sql._lock_tree(conn, org.c)
        thread, out = _in_thread(lambda: _call(admin, "PUT", f"/api/folders/{bills}/private", {"on": True}))
        thread.join(timeout=1.5)
        assert thread.is_alive()
        conn.execute(
            text("UPDATE folders SET parent_id = :p WHERE id = :f"),
            {"p": org.folders["salaries"], "f": bills},
        )
    thread.join(timeout=20)
    assert _code(out[0]) == (409, "inside_private")
    assert _owner(db, "SELECT private FROM folders WHERE id = :f", {"f": bills}) == [(False,)]


def test_a_file_taken_back_while_a_new_version_is_added_leaves_no_version_given(db: Database) -> None:
    org = _walled(Api(db))
    admin, member, file = org.people["admin"], org.people["member"].user_id, org.files["sales"]
    assert _call(admin, "PUT", f"/api/documents/{file}/shares/{member}", {})[0] == 200
    record = DocumentRecord(org.c, str(uuid.uuid4()), uuid.uuid4().hex * 2, 10, "pdf")
    with db.customer(org.c) as conn:
        assert add_version(conn, record, of_document_id=file) is not None  # holds tree and family
        thread, out = _in_thread(lambda: _call(admin, "DELETE", f"/api/documents/{file}/shares/{member}"))
        thread.join(timeout=1.5)
        assert thread.is_alive()
    thread.join(timeout=20)
    assert out[0][0] == 204
    assert _owner(db, "SELECT count(*) FROM document_shares WHERE user_id = :u", {"u": member}) == [(0,)]


def test_bulk_giving_to_the_admin_themselves_takes_the_password(db: Database) -> None:
    api, mailer = _email_api(db)
    org = _walled(api)
    admin = org.people["admin"]
    files = [org.files["top"], org.files["loose"]]
    body = {"document_ids": files, "user_ids": [admin.user_id]}
    before = _version(db, org.c)
    assert _code(_call(admin, "POST", "/api/documents/shares", body)) == (403, "password_required")
    assert (_given(db, admin.user_id, files), _version(db, org.c)) == (0, before)
    assert _call(admin, "POST", "/api/documents/shares", {**body, "password": PASSWORD}) == (
        200,
        {"given": 2},
    )
    assert [row[4] for row in _audit(db, org.c, "access.file_give")] == [{"versions": 1, "self": True}] * 2
    assert any("gave themselves a file" in sent.text for sent in mailer.sent)


def test_every_kind_of_access_an_admin_gets_is_told_to_management_and_the_other_admins(db: Database) -> None:
    """Each self-grant kind, and access given to another Admin (lead decision: an Admin's second
    account): Management and the other Admins are emailed, never the actor."""
    api, mailer = _email_api(db)
    org = _walled(api)
    admin, other = org.people["admin"], api.admin_sign_in(org.c)
    me, sales = admin.user_id, org.folders["sales"]
    with db.customer(org.c) as conn:
        bills = insert_folder(conn, org.c, me, "Bills", org.folders["loose"])
    watchers = {
        row[0]
        for row in _owner(
            db,
            "SELECT email FROM users WHERE customer_id = :c AND (management OR role = 'admin')"
            " AND NOT disabled",
            {"c": org.c},
        )
    } - {admin.email}
    steps = (
        (  # first: once the Admin is Management, opening a wall gains them nothing
            "themselves access to files",
            f"/api/admin/departments/{org.folders['finance']}/department-only",
            {"on": False},
        ),
        ("themselves Management", f"/api/admin/users/{me}/management", {"on": True}),
        ("themselves a place in a department", f"/api/admin/users/{me}/departments/{sales}", {}),
        ("themselves the Head tick", f"/api/admin/users/{me}/departments/{sales}", {"head": True}),
        ("themselves a place on a private", f"/api/folders/{org.folders['salaries']}/named/{me}", {}),
        ("themselves a file", f"/api/documents/{org.files['top']}/shares/{me}", {}),
        ("themselves a place on a private", f"/api/folders/{bills}/private", {"on": True, "named": [me]}),
    )
    for words, url, body in steps:
        sent = len(mailer.sent)
        assert _call(admin, "PUT", url, {**body, "password": PASSWORD})[0] == 200, url
        new = [m for m in mailer.sent[sent:] if m.subject == "Access was given in Kaagaz"]
        assert {m.to for m in new} == watchers, words
        assert all(words in m.text for m in new), words
    for person, told in ((other.user_id, True), (org.people["plain"].user_id, False)):
        sent = len(mailer.sent)
        assert _call(admin, "PUT", f"/api/documents/{org.files['loose']}/shares/{person}", {})[0] == 200
        new = [m for m in mailer.sent[sent:] if m.subject == "Access was given in Kaagaz"]
        assert {m.to for m in new} == (watchers if told else set()), person
        assert all("a file, given directly" in m.text for m in new)


def test_a_department_in_use_keeps_its_kind(db: Database) -> None:
    """Lead decision 6: with members, Department only, or a private folder inside, 409
    ``department_in_use`` before anything is written; an unused one may change."""
    org = _walled(Api(db))
    admin = org.people["admin"]
    with db.customer(org.c) as conn:
        legal = insert_folder(conn, org.c, admin.user_id, "Legal")
        deeds = insert_folder(conn, org.c, admin.user_id, "Deeds", legal)
        spare = insert_folder(conn, org.c, admin.user_id, "Spare")
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id IN (:l, :s)", {"l": legal, "s": spare})
    _owner(db, "UPDATE folders SET private = true WHERE id = :f", {"f": deeds})
    _owner(db, "DELETE FROM department_members WHERE department_id = :f", {"f": org.folders["finance"]})
    _owner(
        db,
        "INSERT INTO department_members (customer_id, department_id, user_id, added_by)"
        " VALUES (:c, :d, :u, :u)",
        {"c": org.c, "d": org.folders["sales"], "u": admin.user_id},
    )
    for department in (
        org.folders["finance"],
        org.folders["sales"],
        legal,
    ):  # Department only, member, private
        reply = _call(admin, "PATCH", f"/api/folders/{department}", {"kind": "custom", "name": "Renamed"})
        assert _code(reply) == (409, "department_in_use"), department
        state = _owner(db, "SELECT kind, name FROM folders WHERE id = :f", {"f": department})
        assert state[0][0] == "department" and state[0][1] != "Renamed"
    assert _call(admin, "PATCH", f"/api/folders/{spare}", {"kind": "custom"})[0] == 200
    with pytest.raises(folders_sql.DepartmentInUse), db.customer(org.c) as conn:  # the CHECK, named
        folders_sql.set_kind(conn, org.c, org.folders["finance"], kind="custom", sort_order=None, order=False)


def test_a_file_is_never_given_to_a_person_switched_off_even_by_sql(db: Database) -> None:
    org = _walled(Api(db))
    statement = "SELECT * FROM kaagaz_give_file(:file, ARRAY[CAST(:person AS uuid)])"
    params = {"file": org.files["top"], "person": org.people["off"].user_id}
    assert _refused(db, org.c, org.people["admin"].user_id, statement, **params) == "23514"
