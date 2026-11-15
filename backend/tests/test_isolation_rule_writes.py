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

from kaagaz.db.engine import Database
from kaagaz.db.sqlstate import CHECK_VIOLATION, INSUFFICIENT_PRIVILEGE, sqlstate
from tests.db_support import name_session, wait_until_blocked
from tests.test_api_support import CSRF, Api
from tests.test_isolation_access_views import Org, _app, _seen, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

# Each function, called so that a caller it lets in changes nothing with random ids (``:ids`` a
# list, ``:a`` and ``:b`` single ids).
CALLS = {
    "kaagaz_admin_trash_in_folders": "SELECT kaagaz_admin_trash_in_folders(CAST(:ids AS uuid[]), :a, now())",
    "kaagaz_detach_trashed": "SELECT kaagaz_detach_trashed(CAST(:ids AS uuid[]))",
    "kaagaz_admin_move_folder_documents": "SELECT kaagaz_admin_move_folder_documents(:a, :b)",
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
    _trash_folder(org, org.folders["finance"])
    before = _documents(db, org.c)
    hidden = (org.folders["finance"], org.folders["salaries"], org.files["fin"], org.files["sal"])
    for function in sorted(set(CALLS) - ADMINS_ONLY - {"kaagaz_detach_trashed"}):
        random = _call(db, org, plain, function)
        for one in hidden:
            assert _call(db, org, plain, function, a=one) == random, (function, one)
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
