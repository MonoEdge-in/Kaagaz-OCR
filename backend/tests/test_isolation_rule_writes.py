"""PRD 7 PR 9b (#606): the writes over rows the person can't see, 0092's owner-rights functions, with
the database rule on (the test session's default).

* Who may run each one: an Admin (or the worker role with no person) for an Admin's; any active
  person for the rest; never the API role with no person or a switched-off person.
* Another organisation's ids, and for a person an id hidden from them, do exactly what a random id
  does (each organisation and person its own session).
* What each writes over the rows the caller can't see, read back as the owner.
* Each one that writes waits for the tree lock, and delete forever for its family's, so it never
  acts on what it read before a change it then meets.

The routes that call them run with the rule on in test_isolation_folders_trash_views,
test_isolation_open_paths, test_isolation_pr7_review and test_isolation_access_views."""

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import trashed_folders as trashed_sql
from kaagaz.db.engine import Database
from kaagaz.db.folders import FolderNotFound
from kaagaz.db.sqlstate import CHECK_VIOLATION, INSUFFICIENT_PRIVILEGE, sqlstate
from tests.db_support import insert_document, insert_folder, name_session, rule_off, wait_until_blocked
from tests.test_api_support import CSRF, Api
from tests.test_isolation_access_views import Org, _app, _seen, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

# Each function, called so that a caller it lets in changes nothing with random ids (``:ids`` a
# list, ``:a`` and ``:b`` single ids).
CALLS = {
    "kaagaz_admin_trash_in_folders": "SELECT kaagaz_admin_trash_in_folders(CAST(:ids AS uuid[]), :a, now())",
    "kaagaz_detach_trashed": "SELECT kaagaz_detach_trashed(CAST(:ids AS uuid[]))",
    "kaagaz_admin_move_folder_documents": "SELECT kaagaz_admin_move_folder_documents(:a, :b, false)",
    "kaagaz_restore_folder": "SELECT count(*) FROM kaagaz_restore_folder(:a)",
    "kaagaz_restore_with_folders": "SELECT count(*) FROM kaagaz_restore_with_folders(:a)",
    "kaagaz_trash_uploaders": "SELECT kaagaz_trash_uploaders(:a)",
    "kaagaz_trash_families": "SELECT count(*) FROM kaagaz_trash_families(:a)",
    "kaagaz_purge_family": "SELECT count(*) FROM kaagaz_purge_family(:a)",
    "kaagaz_admin_move_documents": "SELECT count(*) FROM kaagaz_admin_move_documents(CAST(:ids AS uuid[]),"
    " NULL)",
    "kaagaz_admin_trash_documents": "SELECT count(*) FROM kaagaz_admin_trash_documents(CAST(:ids AS uuid[]),"
    " now())",
}
ADMINS_ONLY = frozenset(
    {
        "kaagaz_admin_trash_in_folders",
        "kaagaz_admin_move_folder_documents",
        "kaagaz_admin_move_documents",
        "kaagaz_admin_trash_documents",
    }
)
_GATES = ("only an Admin", "only an active person of this organisation")
_MOVE = "SELECT * FROM kaagaz_admin_move_documents(CAST(:ids AS uuid[]), :to)"


@contextmanager
def _as(db: Database, org: Org, who: str) -> Iterator[Connection]:
    """A session of ``who``: a person's name (the API role, them set), "nobody" (the API role, no
    person) or "worker" (the worker role, no person)."""
    if who == "worker":
        with db.customer(org.c) as conn:
            yield conn
    elif who == "nobody":
        with _app(db).customer(org.c) as conn:
            yield conn
    else:
        with _app(db).user(org.c, org.people[who].user_id) as conn:
            yield conn


def _call(db: Database, org: Org, who: str, function: str, **ids: Any) -> Any:
    """The function's answer for ``who`` (random ids unless given); "refused" when its caller check
    refused them; a refusal after it, as (SQLSTATE, message)."""
    params = {"ids": [str(uuid.uuid4())], "a": str(uuid.uuid4()), "b": str(uuid.uuid4()), **ids}
    try:
        with _as(db, org, who) as conn:
            return conn.execute(text(CALLS[function]), params).scalar()
    except DBAPIError as error:
        said = str(getattr(getattr(error.orig, "diag", None), "message_primary", None) or "")
        if sqlstate(error) == INSUFFICIENT_PRIVILEGE and said in _GATES:
            return "refused"
        return sqlstate(error), said


def _documents(db: Database, customer: str) -> list[tuple[Any, ...]]:
    query = (
        "SELECT id, folder_id, deleted_at, trashed_folder_id FROM documents WHERE customer_id = :c ORDER BY 1"
    )
    return [tuple(row) for row in _owner(db, query, {"c": customer})]


def _trash_folder(org: Org, folder: str) -> None:
    reply = org.people["admin"].client.delete(f"/api/folders/{folder}?contents=trash", headers=CSRF)
    assert reply.status_code == 200, reply.text


# --- who may run each one ------------------------------------------------------------------------


@pytest.mark.parametrize("function", sorted(CALLS))
def test_each_function_lets_in_only_its_callers(db: Database, function: str) -> None:
    org = _walled(Api(db))
    admins_only = function in ADMINS_ONLY
    expected = {
        "admin": False,
        "worker": False,
        "management": admins_only,
        "member": admins_only,
        "plain": admins_only,
        "nobody": True,
        "off": True,
    }
    found = {who: _call(db, org, who, function, ids=[]) == "refused" for who in expected}
    assert found == expected


def test_another_organisations_ids_are_random_ids(db: Database) -> None:
    """Organisation A's Admin, with B's folders, trashed folder and files: each answer is a random
    id's, and B's rows are as they were."""
    api = Api(db)
    a, b = _walled(api), _walled(api)
    _trash_folder(b, b.folders["finance"])
    before = _documents(db, b.c)
    for function in CALLS:
        random = _call(db, a, "admin", function)
        for one in (b.folders["finance"], b.folders["loose"], b.files["sal"], b.files["top"]):
            theirs = {
                "ids": [b.folders["loose"], b.files["top"], b.files["sal"]],
                "a": one,
                "b": b.folders["sales"],
            }
            assert _call(db, a, "admin", function, **theirs) == random, (function, one)
    assert _documents(db, b.c) == before


def test_a_hidden_id_is_a_random_id_for_a_person(db: Database) -> None:
    """The plain reviewer can't see Finance, live or in Trash, nor its files: with each of those ids,
    every function a person may run answers as with a random id, and nothing changes."""
    org = _walled(Api(db))
    plain = "plain"
    _owner(
        db, "UPDATE documents SET deleted_at = now() WHERE id = :d", {"d": org.files["fin"]}
    )  # in Trash, filed
    before = _documents(db, org.c)
    assert _call(db, org, plain, "kaagaz_detach_trashed", ids=[org.folders["finance"]]) == _call(
        db, org, plain, "kaagaz_detach_trashed"
    )
    assert _documents(db, org.c) == before  # fin is still filed in Finance
    _owner(db, "UPDATE documents SET deleted_at = NULL WHERE id = :d", {"d": org.files["fin"]})
    # 0091's F6, made to see only the folders the person sees (0092, the lead's decision): Finance, live,
    # holds files hidden from them, and answers as a random id.
    live = "SELECT * FROM kaagaz_hidden_inside(ARRAY[CAST(:a AS uuid)], NULL)"
    with _as(db, org, plain) as conn:
        for one in (org.folders["finance"], org.folders["salaries"], str(uuid.uuid4())):
            assert tuple(conn.execute(text(live), {"a": one}).one()) == (0, 0, False), one
    _trash_folder(org, org.folders["finance"])
    before = _documents(db, org.c)
    hidden = (org.folders["finance"], org.folders["salaries"], org.files["fin"], org.files["sal"])
    for function in sorted(set(CALLS) - ADMINS_ONLY - {"kaagaz_detach_trashed"}):
        random = _call(db, org, plain, function)
        for one in hidden:
            assert _call(db, org, plain, function, a=one) == random, (function, one)
    # And in Trash.
    inside = "SELECT * FROM kaagaz_hidden_inside(NULL, ARRAY[CAST(:a AS uuid)])"
    with _as(db, org, plain) as conn:
        answers = {
            one: tuple(conn.execute(text(inside), {"a": one}).one()) for one in (*hidden, str(uuid.uuid4()))
        }
    assert set(answers.values()) == {(0, 0, False)}, answers
    with _as(db, org, "admin") as conn:  # who sees it is still told what is hidden inside
        assert conn.execute(text(inside), {"a": org.folders["finance"]}).one()[2] is True
    assert _documents(db, org.c) == before


# --- what each one writes ------------------------------------------------------------------------


def test_an_admins_folder_delete_sends_every_file_to_trash_and_counts_them(db: Database) -> None:
    """Finance holds fin and given, and Salaries in it sal: the Admin sees none of them, and all
    three go to Trash, counted, deleted by them, with the folders."""
    org = _walled(Api(db))
    admin = org.people["admin"]
    files = {org.files["fin"], org.files["given"], org.files["sal"]}
    assert files & _seen(_app(db), org.c, admin.user_id) == set()
    reply = admin.client.delete(f"/api/folders/{org.folders['finance']}?contents=trash", headers=CSRF)
    assert (reply.status_code, reply.json()) == (200, {"files": 3, "folders": 1}), reply.text
    rows = _owner(
        db, "SELECT id, deleted_by FROM documents WHERE id = ANY(CAST(:d AS uuid[]))", {"d": list(files)}
    )
    assert sorted(str(by) for _, by in rows) == [admin.user_id] * 3
    assert _owner(db, "SELECT count(*) FROM folders WHERE id = :f", {"f": org.folders["finance"]}) == [(0,)]


def test_an_admins_delete_forever_deletes_the_files_they_cant_see(db: Database) -> None:
    org = _walled(Api(db))
    _trash_folder(org, org.folders["finance"])
    reply = org.people["admin"].client.delete(
        f"/api/documents/trash/folders/{org.folders['finance']}/permanent", headers=CSRF
    )
    assert reply.status_code == 204, reply.text
    files = [org.files["fin"], org.files["given"], org.files["sal"]]
    assert _owner(db, "SELECT count(*) FROM documents WHERE id = ANY(CAST(:d AS uuid[]))", {"d": files}) == [
        (0,)
    ]
    assert _owner(db, "SELECT count(*) FROM trashed_folders WHERE customer_id = :c", {"c": org.c}) == [(0,)]


def test_a_member_may_not_restore_a_folder_holding_a_hidden_file_someone_else_uploaded(db: Database) -> None:
    """Finance's member uploaded every file of Plans they can see; a locked one, uploaded by the
    Admin, is hidden from them. The item can't be restored by them (who uploaded is read whole),
    restoring it is refused, and nothing changes."""
    api = Api(db)
    org = _walled(api)
    member, admin = org.people["member"], org.people["admin"]
    plans = api.add_folder(org.c, admin.user_id, "Plans", org.folders["finance"])
    api.add_document(org.c, name="mine.pdf", folder_id=plans, uploaded_by=member.user_id)
    theirs = api.add_document(org.c, name="theirs.pdf", folder_id=plans, uploaded_by=admin.user_id)
    _owner(
        db,
        "UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d",
        {"u": admin.user_id, "d": theirs},
    )
    _trash_folder(org, plans)
    items = {i["id"]: i for i in member.client.get("/api/documents/trash/folders").json()["items"]}
    assert (items[plans]["files"], items[plans]["can_restore"]) == (1, False)
    before = _documents(db, org.c)
    back = member.client.post(f"/api/documents/trash/folders/{plans}/restore", headers=CSRF)
    assert back.status_code == 403, back.text
    assert _call(db, org, "member", "kaagaz_restore_folder", a=plans) == (
        INSUFFICIENT_PRIVILEGE,
        "someone else uploaded a file in it: an Admin restores it",
    )
    assert _call(db, org, "member", "kaagaz_trash_families", a=plans) == (  # delete forever: an Admin's
        INSUFFICIENT_PRIVILEGE,
        "something inside is hidden from them: an Admin deletes it",
    )
    assert _documents(db, org.c) == before


def test_each_function_holds_its_rule_without_the_routes_check(db: Database) -> None:
    """The routes refuse these first; each function refuses them on its own too, and writes
    nothing: Salaries' contents into Finance would show them to Finance's members (#594), and
    Salaries' restore would bring Finance, a department, back for a member (F1)."""
    org = _walled(Api(db))
    salaries, finance = org.folders["salaries"], org.folders["finance"]
    before = _documents(db, org.c)
    assert _call(db, org, "admin", "kaagaz_admin_move_folder_documents", a=salaries, b=finance) == (
        INSUFFICIENT_PRIVILEGE,
        "other people would see them there",
    )
    assert _documents(db, org.c) == before
    _owner(
        db,
        "UPDATE documents SET uploaded_by = :u WHERE id = :d",
        {"u": org.people["named"].user_id, "d": org.files["sal"]},
    )
    _trash_folder(org, finance)
    before = _documents(db, org.c)
    assert _call(db, org, "named", "kaagaz_restore_folder", a=salaries) == (
        INSUFFICIENT_PRIVILEGE,
        "a department comes back by an Admin only",
    )
    assert _documents(db, org.c) == before


def test_detach_tells_only_an_admin_how_many_files_left_a_folder(db: Database) -> None:
    """Sales holds locked.pdf in Trash, hidden from Finance's member. Their detach of Sales (a folder
    they see) takes it out of Sales, as with the rule off, but says no count, which would tell of a
    file they can't see; the Admin's says how many."""
    org = _walled(Api(db))
    _owner(
        db,
        "UPDATE documents SET deleted_at = now() WHERE id IN (:l, :s)",
        {"l": org.files["locked"], "s": org.files["loose"]},
    )
    assert _call(db, org, "member", "kaagaz_detach_trashed", ids=[org.folders["sales"]]) is None
    assert _owner(db, "SELECT folder_id FROM documents WHERE id = :d", {"d": org.files["locked"]}) == [
        (None,)
    ]
    assert _call(db, org, "admin", "kaagaz_detach_trashed", ids=[org.folders["loose"]]) == 1


def test_a_confirmed_keep_moves_what_its_warning_was_about(db: Database) -> None:
    """10c's warn-then-confirm keep-delete (#601) through 0092: Salaries' files (hidden from the
    Admin, sal and one in Trash) into Finance would show them to Finance's members. Unconfirmed it
    is refused and nothing moves; confirmed, both move."""
    org = _walled(Api(db))
    salaries, finance = org.folders["salaries"], org.folders["finance"]
    move = "SELECT kaagaz_admin_move_folder_documents(:a, :b, :confirmed)"
    before = _documents(db, org.c)
    with pytest.raises(DBAPIError) as refused, _as(db, org, "admin") as conn:
        conn.execute(text(move), {"a": salaries, "b": finance, "confirmed": False})
    assert sqlstate(refused.value) == INSUFFICIENT_PRIVILEGE
    assert _documents(db, org.c) == before
    with _as(db, org, "admin") as conn:
        assert conn.execute(text(move), {"a": salaries, "b": finance, "confirmed": True}).scalar() == 2
    moved = _owner(
        db,
        "SELECT folder_id::text FROM documents WHERE id IN (:s, :t)",
        {"s": org.files["sal"], "t": org.files["trashed"]},
    )
    assert moved == [(finance,), (finance,)]


def test_a_person_deletes_forever_no_family_with_a_version_they_cant_see(db: Database) -> None:
    """Finance's member sees version 1 of a family in Trash; version 2 is locked, hidden from them.
    Their delete forever of that family deletes nothing (an Admin's would delete both)."""
    org = _walled(Api(db))
    first = org.files["fin"]
    with db.customer(org.c) as conn:
        second = insert_document(
            conn, org.c, family_id=first, version_no=2, is_current=False, folder_id=org.folders["finance"]
        )
    _owner(
        db,
        "UPDATE documents SET locked_at = now(), locked_by = :u WHERE id = :d",
        {"u": org.people["admin"].user_id, "d": second},
    )
    _owner(db, "UPDATE documents SET deleted_at = now() WHERE family_id = :f", {"f": first})
    assert first in _seen(_app(db), org.c, org.people["member"].user_id)
    assert _call(db, org, "member", "kaagaz_purge_family", a=first) == 0
    assert _owner(db, "SELECT count(*) FROM documents WHERE family_id = :f", {"f": first}) == [(2,)]


def test_a_restore_names_no_folder_the_person_cant_see(db: Database) -> None:
    """Finance > Payroll > Inner (private, names ``named``, their file): Payroll goes to Trash, and
    ``named`` restores Inner. The topmost row (Payroll, hidden from them) carries no name; the
    asked one (Inner) its own."""
    api = Api(db)
    org = _walled(api)
    named = org.people["named"]
    with db.customer(org.c) as conn:
        payroll = insert_folder(conn, org.c, named.user_id, "Payroll", org.folders["finance"])
        inner = insert_folder(conn, org.c, named.user_id, "Inner", payroll)
    _owner(db, "UPDATE folders SET private = true WHERE id = :f", {"f": inner})
    _owner(
        db,
        "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
        " VALUES (:c, :f, :u, :u)",
        {"c": org.c, "f": inner, "u": named.user_id},
    )
    api.add_document(org.c, name="slip.pdf", folder_id=inner, uploaded_by=named.user_id)
    _trash_folder(org, payroll)
    with _as(db, org, "named") as conn:
        rows = conn.execute(
            text("SELECT id::text, name, is_top, is_asked FROM kaagaz_restore_folder(:f)"), {"f": inner}
        )
        found = sorted(tuple(row) for row in rows)
    assert found == sorted([(payroll, None, True, False), (inner, "Inner", False, True)])


def test_delete_forever_drops_no_trashed_folder_the_person_cant_see(db: Database) -> None:
    """Its last step (drop_folder) reads again that the person sees the folder: with the rule off
    (the views alone), the plain reviewer's drop of Finance in Trash, holding files hidden from
    them, is a folder that isn't there, and Finance stays whole."""
    org = _walled(Api(db))
    _trash_folder(org, org.folders["finance"])
    count = "SELECT count(*) FROM trashed_folders WHERE customer_id = :c"
    before = _owner(db, count, {"c": org.c})
    with rule_off(), pytest.raises(FolderNotFound), _app(db).user(org.c, org.people["plain"].user_id) as conn:
        trashed_sql.drop_folder(conn, org.c, org.folders["finance"], files=0)
    assert _owner(db, count, {"c": org.c}) == before


def test_the_names_lists_move_and_send_to_trash_act_on_whole_live_families(db: Database) -> None:
    """The Admin moves fin (hidden from them) into Loose and sends sal (hidden too) to Trash; a
    family in Trash doesn't move; a folder in Trash, another organisation's or none is no place to
    move to (nothing moves)."""
    api = Api(db)
    org, other = _walled(api), _walled(api)
    fin, sal, trashed = org.files["fin"], org.files["sal"], org.files["trashed"]
    with _as(db, org, "admin") as conn:
        moved = conn.execute(text(_MOVE), {"ids": [fin, trashed], "to": org.folders["loose"]}).all()
        sent = conn.execute(
            text("SELECT * FROM kaagaz_admin_trash_documents(CAST(:ids AS uuid[]), now())"), {"ids": [sal]}
        ).all()
    assert [tuple(str(v) for v in row) for row in moved] == [(fin, fin, org.folders["finance"])]
    assert _owner(db, "SELECT folder_id::text FROM documents WHERE id = :d", {"d": fin}) == [
        (org.folders["loose"],)
    ]
    assert [tuple(str(v) for v in row) for row in sent] == [(sal, sal)]
    deleted = _owner(
        db, "SELECT deleted_at IS NOT NULL, deleted_by::text FROM documents WHERE id = :d", {"d": sal}
    )
    assert deleted == [(True, org.people["admin"].user_id)]
    _trash_folder(org, org.folders["sales"])
    before = _documents(db, org.c)
    for place in (str(uuid.uuid4()), other.folders["loose"], org.folders["sales"]):
        with pytest.raises(DBAPIError) as refused, _as(db, org, "admin") as conn:
            conn.execute(text(_MOVE), {"ids": [org.files["top"]], "to": place}).all()
        assert sqlstate(refused.value) == CHECK_VIOLATION, place
    assert _documents(db, org.c) == before


# --- the locks ------------------------------------------------------------------------------------


def test_delete_forever_leaves_a_family_restored_while_it_waits(db: Database) -> None:
    """A restore holds the family's lock and brings it back; delete forever of that family (the
    Admin's, who can't see it) waits for the lock, reads it again after and deletes nothing."""
    org = _walled(Api(db))
    family = org.files["trashed"]  # Salaries', in Trash
    session = f"purge-{uuid.uuid4().hex[:16]}"

    def purge() -> int:
        with _app(db).user(org.c, org.people["admin"].user_id) as conn:
            name_session(conn, session)
            return len(conn.execute(text("SELECT * FROM kaagaz_purge_family(:f)"), {"f": family}).all())

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(org.c) as conn:
            conn.execute(
                text(
                    "SELECT id FROM documents WHERE family_id = :f ORDER BY (id <> family_id), id"
                    " FOR NO KEY UPDATE"
                ),
                {"f": family},
            ).all()
            purging = pool.submit(purge)
            wait_until_blocked(db, application_name=session)
            conn.execute(
                text("UPDATE documents SET deleted_at = NULL, deleted_by = NULL WHERE id = :f"), {"f": family}
            )
        assert purging.result(timeout=10) == 0
    assert _owner(db, "SELECT count(*) FROM documents WHERE id = :f", {"f": family}) == [(1,)]


_WRITES = sorted(set(CALLS) - {"kaagaz_trash_uploaders", "kaagaz_trash_families", "kaagaz_purge_family"})


@pytest.mark.parametrize("function", _WRITES)
def test_each_write_waits_for_the_tree_lock(db: Database, function: str) -> None:
    """Another transaction holds the organisation's tree lock (folders._lock_tree): the write waits
    for it before it reads anything."""
    org = _walled(Api(db))
    session = f"write-{uuid.uuid4().hex[:16]}"
    params = {"ids": [], "a": str(uuid.uuid4()), "b": str(uuid.uuid4())}

    def call() -> None:
        with _app(db).user(org.c, org.people["admin"].user_id) as conn:
            name_session(conn, session)
            conn.execute(text(CALLS[function]), params).all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.customer(org.c) as conn:
            conn.execute(text("SELECT 1 FROM customers WHERE id = :c FOR NO KEY UPDATE"), {"c": org.c})
            calling = pool.submit(call)
            wait_until_blocked(db, application_name=session)
        calling.result(timeout=10)


# --- the restore's branches, written in SQL by 0092 (as trashed_folders._make_live was) ---------------


def _tree_state(db: Database, customer: str) -> list[Any]:
    return [
        _owner(
            db,
            f"SELECT id, parent_id, name, autofile_key FROM {table} WHERE customer_id = :c ORDER BY 1",  # noqa: S608
            {"c": customer},
        )
        for table in ("folders", "trashed_folders")
    ] + [_documents(db, customer)]


def test_a_restore_101_levels_deep_is_refused_and_changes_nothing(db: Database) -> None:
    """Live folders 99 deep; X (with Y in it, holding a file) went to Trash from the deepest. Back,
    X would be at 100 and Y at 101: both the folder's restore (its height, 2) and the file's with its
    chain (X and Y, 2) are 409 folder_too_deep, and nothing changes."""
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    parent = None
    for level in range(99):
        parent = api.add_folder(c, admin.user_id, f"L{level}", parent)
    x = api.add_folder(c, admin.user_id, "X", parent)
    y = api.add_folder(c, admin.user_id, "Y", x)
    document = api.add_document(c, name="deep.pdf", folder_id=y, uploaded_by=admin.user_id)
    assert admin.client.delete(f"/api/folders/{x}?contents=trash", headers=CSRF).status_code == 200
    before = _tree_state(db, c)
    for path in (f"/api/documents/trash/folders/{x}/restore", f"/api/documents/{document}/restore"):
        reply = admin.client.post(path, headers=CSRF)
        assert (reply.status_code, reply.json()["error"]["code"]) == (409, "folder_too_deep"), path
        assert _tree_state(db, c) == before, path


def test_a_restored_folder_whose_auto_filing_key_was_taken_comes_back_without_it(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    folder = api.add_folder(c, admin.user_id, "Invoices")
    _owner(db, "UPDATE folders SET autofile_key = 'type:invoice' WHERE id = :f", {"f": folder})
    assert admin.client.delete(f"/api/folders/{folder}?contents=trash", headers=CSRF).status_code == 200
    taker = api.add_folder(c, admin.user_id, "Invoices 2026")
    _owner(db, "UPDATE folders SET autofile_key = 'type:invoice' WHERE id = :f", {"f": taker})
    reply = admin.client.post(f"/api/documents/trash/folders/{folder}/restore", headers=CSRF)
    assert reply.status_code == 200, reply.text
    keys = _owner(
        db,
        "SELECT id::text, autofile_key FROM folders WHERE id IN (:f, :t) ORDER BY name",
        {"f": folder, "t": taker},
    )
    assert keys == [(folder, None), (taker, "type:invoice")]


def test_a_full_length_name_beside_a_clash_comes_back_cut_with_no_trailing_space(db: Database) -> None:
    """A 100-character name whose 96th character is a space: beside a live folder of that name it
    comes back as its first 95 characters and " (2)", as folders._numbered cuts it."""
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    name = "a" * 95 + " " + "b" * 4
    folder = api.add_folder(c, admin.user_id, name)
    assert admin.client.delete(f"/api/folders/{folder}?contents=trash", headers=CSRF).status_code == 200
    api.add_folder(c, admin.user_id, name)
    reply = admin.client.post(f"/api/documents/trash/folders/{folder}/restore", headers=CSRF)
    assert reply.status_code == 200, reply.text
    assert (reply.json()["folder"]["name"], reply.json()["renamed"]) == ("a" * 95 + " (2)", True)


# --- the names list's Move and Send to Trash (PRD 7 section 3; PR 11 adds the buttons) --------------

_MOVE_KEPT = "/api/documents/{d}/kept/move"
_TRASH_KEPT = "/api/documents/{d}/kept/trash"


def _post(signed: Any, path: str, body: Any = None) -> tuple[int, Any]:
    reply = signed.client.post(path, json=body, headers=CSRF)
    return reply.status_code, (reply.json() if reply.content else None)


def _audited(db: Database, customer: str, action: str, document: str) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in _owner(
            db,
            "SELECT actor_user_id::text, family_id::text, details FROM audit_log"
            " WHERE customer_id = :c AND action = :a AND target_id = :d",
            {"c": customer, "a": action, "d": document},
        )
    ]


def test_an_admin_moves_and_trashes_a_file_they_cant_see_from_the_names_list(db: Database) -> None:
    """fin and sal are hidden from the Admin: Move puts fin's family in Loose, Send to Trash puts
    sal's in Trash, each audited with its family; the replies name no file."""
    org = _walled(Api(db))
    admin, fin, sal = org.people["admin"], org.files["fin"], org.files["sal"]
    loose = org.folders["loose"]
    assert _post(admin, _MOVE_KEPT.format(d=fin), {"folder_id": loose}) == (
        200,
        {"id": fin, "folder_id": loose},
    )
    assert _owner(db, "SELECT folder_id::text FROM documents WHERE id = :d", {"d": fin}) == [(loose,)]
    [(actor, family, details)] = _audited(db, org.c, "document.move", fin)
    assert (actor, family, details["folder_id"]) == (admin.user_id, fin, loose)
    assert _post(admin, _TRASH_KEPT.format(d=sal)) == (204, None)
    deleted = _owner(
        db, "SELECT deleted_at IS NOT NULL, deleted_by::text FROM documents WHERE id = :d", {"d": sal}
    )
    assert deleted == [(True, admin.user_id)]
    assert [(a, f) for a, f, _ in _audited(db, org.c, "document.trash", sal)] == [(admin.user_id, sal)]


def test_the_names_lists_actions_find_no_other_organisations_file_or_folder(db: Database) -> None:
    """Another organisation's file, or its folder as the place, answers as a random id (404), and
    changes nothing on either side; a file in Trash can't move (409)."""
    api = Api(db)
    org, other = _walled(api), _walled(api)
    admin = org.people["admin"]
    before = (_documents(db, org.c), _documents(db, other.c))
    for path in (_MOVE_KEPT, _TRASH_KEPT):
        body = {"folder_id": None} if path == _MOVE_KEPT else None
        random = _post(admin, path.format(d=uuid.uuid4()), body)
        assert random[0] == 404, path
        assert _post(admin, path.format(d=other.files["fin"]), body) == random, path
    to_random = _post(admin, _MOVE_KEPT.format(d=org.files["fin"]), {"folder_id": str(uuid.uuid4())})
    assert to_random[0] == 404
    assert (
        _post(admin, _MOVE_KEPT.format(d=org.files["fin"]), {"folder_id": other.folders["loose"]})
        == to_random
    )
    assert (_documents(db, org.c), _documents(db, other.c)) == before
    trashed = _post(admin, _MOVE_KEPT.format(d=org.files["trashed"]), {"folder_id": None})
    assert (trashed[0], trashed[1]["error"]["code"]) == (409, "in_trash")


@pytest.mark.parametrize("who", ["management", "member", "plain"])
def test_only_an_admin_uses_the_names_lists_actions(db: Database, who: str) -> None:
    """Anyone else: a file they can see is 403; one hidden from them is a random id (404); nothing
    changes."""
    org = _walled(Api(db))
    signed = org.people[who]
    before = _documents(db, org.c)
    seen = _seen(_app(db), org.c, signed.user_id)
    for path in (_MOVE_KEPT, _TRASH_KEPT):
        body = {"folder_id": None} if path == _MOVE_KEPT else None
        random = _post(signed, path.format(d=uuid.uuid4()), body)
        for name in ("fin", "sal", "sales"):
            document = org.files[name]
            got = _post(signed, path.format(d=document), body)
            if document in seen:
                assert got[0] == 403, (path, name)
            else:
                assert got == random, (path, name)
    assert _documents(db, org.c) == before
