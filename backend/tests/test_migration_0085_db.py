"""Migration 0085 (PRD 7, #593, 14 Nov 2026): the triggers that keep each folder's and file's stored
department and private folder right, hand a new version its family's access, take a removal from
every version, and raise ``customers.access_version`` once per statement, without moving
``updated_at``. After every create, move, Private and open switch, kind change, Trash, restore and
version step below, check-access's differences for the organisation are none; the other
organisation is never touched. Writes run as the app role, which may run none of the trigger
functions (test_isolation_schema). Up (rows written between 0084 and 0085 worked out again), down
(the schema as at 0084) and up again on a fresh database."""

import io
import subprocess
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.cli import _ACCESS_DIFFERENCES, main
from kaagaz.db import versions
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, insert_folder, migrate, new_customer
from tests.test_api_support import CSRF, Api
from tests.test_api_trashed_folders import _restore, _trash, _tree
from tests.test_isolation_logins import WORKER
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0085 = _load("0085_access_triggers.py")
NOW = datetime(2026, 11, 1, tzinfo=UTC)
_VERSION = "SELECT access_version FROM customers WHERE id = :c"
_TOUCH = (
    "SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger"
    " WHERE tgname IN ('documents_touch', 'learned_corrections_touch') ORDER BY 1"
)


def _run(db: Database, customer: str, statement: str, **params: Any) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        result = conn.execute(text(statement), {"c": customer, **params})
        return [tuple(row) for row in result] if result.returns_rows else []


def _differences(db: Database, customer: str) -> list[tuple[Any, ...]]:
    """check-access's differences for one organisation, read as the workers' role."""
    with Database(db.engine, WORKER).customer(customer) as conn:
        return [tuple(row) for row in conn.execute(_ACCESS_DIFFERENCES, {"c": customer})]


def _values(
    db: Database, customer: str, table: str = "documents"
) -> dict[str, tuple[str | None, str | None]]:
    """Each row's (department_id, private_folder_id); ``table`` is one of three fixed names."""
    statement = f"SELECT id, department_id, private_folder_id FROM {table} WHERE customer_id = :c"  # noqa: S608
    rows = _run(db, customer, statement)
    return {str(i): (None if d is None else str(d), None if p is None else str(p)) for i, d, p in rows}


def _version(db: Database, customer: str) -> int:
    return int(_run(db, customer, _VERSION)[0][0])


def _given(db: Database, customer: str) -> set[tuple[str, str, str]]:
    """Every file given and lock grant: (table, document, user)."""
    rows = _run(
        db,
        customer,
        "SELECT 'share', document_id, user_id FROM document_shares WHERE customer_id = :c"
        " UNION ALL SELECT 'grant', document_id, user_id FROM lock_grants WHERE customer_id = :c",
    )
    return {(str(kind), str(d), str(u)) for kind, d, u in rows}


def _department(db: Database, customer: str, user: str, name: str) -> str:
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, name)
        conn.execute(text("UPDATE folders SET kind = 'department' WHERE id = :f"), {"f": folder})
    return folder


def test_the_revision_follows_0084() -> None:
    assert (M0085.revision, M0085.down_revision) == ("0085", "0084")


def test_folder_create_move_private_open_and_kind_change_keep_every_value_right(db: Database) -> None:
    c, u = new_customer(db)
    other, other_user = new_customer(db)
    finance, ops = _department(db, c, u, "Finance"), _department(db, c, u, "Ops")
    _department(db, other, other_user, "Theirs")
    with db.customer(c) as conn:
        bills = insert_folder(conn, c, u, "Bills", finance)
        old = insert_folder(conn, c, u, "Old", bills)
        bill = insert_document(conn, c, folder_id=bills)
        kept = insert_document(conn, c, folder_id=old)
        trashed = insert_document(conn, c, folder_id=bills)
        conn.execute(
            text("UPDATE documents SET deleted_at = :t, deleted_by = :u WHERE id = :d"),
            {"t": NOW, "u": u, "d": trashed},
        )
    theirs = (_values(db, other, "folders"), _values(db, other), _version(db, other))

    def step(statement: str, **params: Any) -> None:
        _run(db, c, statement, **params)
        assert _differences(db, c) == []

    step("SELECT 1")  # created: the department's own id below it
    assert _values(db, c, "folders")[old] == (finance, None)
    assert _values(db, c)[bill] == (finance, None)
    assert _values(db, c)[trashed] == (finance, None)

    step("UPDATE folders SET private = true WHERE id = :f", f=bills)
    assert _values(db, c, "folders")[old] == (finance, bills)
    assert {_values(db, c)[d] for d in (bill, kept)} == {(finance, bills)}

    step("UPDATE folders SET parent_id = :p WHERE id = :f", p=ops, f=bills)
    assert {_values(db, c)[d] for d in (bill, kept)} == {(ops, bills)}
    assert _values(db, c)[trashed] == (finance, None)  # a file in Trash keeps what it had

    step("UPDATE folders SET private = false WHERE id = :f", f=bills)
    step("UPDATE folders SET kind = 'custom' WHERE id = :f", f=ops)
    assert {_values(db, c)[d] for d in (bill, kept)} == {(None, None)}

    # Two changed folders, one under the other, in one statement: the higher one's walk counts.
    step(
        "UPDATE folders SET parent_id = CASE WHEN id = :b THEN CAST(:p AS uuid) ELSE parent_id END,"
        " private = (id = :o) WHERE id IN (:b, :o)",
        b=bills,
        o=old,
        p=finance,
    )
    assert _values(db, c, "folders")[old] == (finance, old)
    assert _values(db, c)[bill] == (finance, None)
    assert _values(db, c)[kept] == (finance, old)
    step("UPDATE folders SET name = 'Renamed' WHERE id = :f", f=bills)
    assert (_values(db, other, "folders"), _values(db, other), _version(db, other)) == theirs


def test_a_file_takes_its_folders_values_keeps_them_in_trash_and_gets_them_again_on_restore(
    db: Database,
) -> None:
    c, u = new_customer(db)
    finance, ops = _department(db, c, u, "Finance"), _department(db, c, u, "Ops")
    with db.customer(c) as conn:
        bills = insert_folder(conn, c, u, "Bills", finance)
        conn.execute(text("UPDATE folders SET private = true WHERE id = :f"), {"f": bills})
        uploaded = insert_document(conn, c, folder_id=bills)
        held = insert_document(conn, c)
        conn.execute(text("UPDATE documents SET held_for = :u WHERE id = :d"), {"u": u, "d": held})
        alone = insert_document(conn, c, folder_id=bills)
        with_folder = insert_document(conn, c, folder_id=bills)
    assert _values(db, c)[uploaded] == (finance, bills)
    assert _values(db, c)[held] == (None, None)

    _run(db, c, "UPDATE documents SET folder_id = :f WHERE id = :d", f=ops, d=held)  # filed
    assert _run(db, c, "SELECT held_for FROM documents WHERE id = :d", d=held) == [(None,)]
    assert _values(db, c)[held] == (ops, None)
    assert _differences(db, c) == []

    _run(db, c, "UPDATE documents SET deleted_at = :t, deleted_by = :u WHERE id = :d", t=NOW, u=u, d=alone)
    # As trash._TRASH_IN_FOLDERS: the folder link emptied, the trashed folder kept.
    _run(
        db,
        c,
        "UPDATE documents SET deleted_at = :t, deleted_by = :u, trashed_folder_id = folder_id,"
        " folder_id = NULL WHERE id = :d",
        t=NOW,
        u=u,
        d=with_folder,
    )
    _run(db, c, "UPDATE folders SET parent_id = :p WHERE id = :f", p=ops, f=bills)
    assert {_values(db, c)[d] for d in (alone, with_folder)} == {(finance, bills)}
    assert _differences(db, c) == []

    _run(db, c, "UPDATE documents SET deleted_at = NULL, deleted_by = NULL WHERE id = :d", d=alone)
    assert _values(db, c)[alone] == (ops, bills)
    assert _differences(db, c) == []


def test_a_folder_restored_where_its_department_is_gone_comes_back_in_no_department(db: Database) -> None:
    """The restore's rows are inserted with the values they had in Trash; the trigger works them
    out again where they land (here the top level: their department was deleted meanwhile)."""
    api = Api(db)
    admin = api.new_admin()
    c = admin.customer_id
    t = _tree(api, admin)
    _run(db, c, "UPDATE folders SET kind = 'department' WHERE id = :f", f=t["accounts"])
    year_file = t["year_file"]
    assert _values(db, c)[year_file] == (t["accounts"], None)
    _trash(admin, t["year"])
    assert _values(db, c, "trashed_folders")[t["bills"]] == (t["accounts"], None)  # kept in Trash
    admin.client.delete(f"/api/documents/{t['outside']}", headers=CSRF)
    assert admin.client.delete(f"/api/folders/{t['accounts']}", headers=CSRF).status_code == 204

    assert _restore(admin, t["year"]).status_code == 200

    folders = _values(db, c, "folders")
    assert {folders[t[name]] for name in ("year", "bills", "old", "bank")} == {(None, None)}
    assert _values(db, c)[year_file] == (None, None)
    assert _differences(db, c) == []


def test_a_new_version_takes_its_familys_access_and_join_undo_and_separate_change_nobodys(
    db: Database,
) -> None:
    api = Api(db)
    c, u = new_customer(db)
    b, _ = api.add_user(c, "member")
    lock_holder, _ = api.add_user(c, "member")
    finance, ops = _department(db, c, u, "Finance"), _department(db, c, u, "Ops")
    with db.customer(c) as conn:
        first = insert_document(conn, c, folder_id=finance)
        loose = insert_document(conn, c, folder_id=ops)
    given = "INSERT INTO {} (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :by)"
    _run(db, c, given.format("document_shares"), d=first, u=b, by=u)
    _run(db, c, given.format("document_shares"), d=loose, u=b, by=u)
    _run(db, c, given.format("lock_grants"), d=first, u=lock_holder, by=u)
    _run(db, c, "UPDATE documents SET locked_at = :t, locked_by = :u WHERE id = :d", t=NOW, u=u, d=first)

    family = [first]
    for number in (2, 3):
        with db.customer(c) as conn:
            family.append(
                insert_document(
                    conn, c, folder_id=finance, family_id=first, version_no=number, is_current=False
                )
            )
    assert _given(db, c) == (
        {("share", d, b) for d in family}
        | {("share", loose, b)}
        | {("grant", d, lock_holder) for d in family}
    )
    locks = _run(db, c, "SELECT locked_by FROM documents WHERE family_id = :f", f=first)
    assert sorted(str(row[0]) for row in locks) == [u] * 3
    assert _differences(db, c) == []

    # A share and a lock grant removed from one version go from all three, in that statement.
    before = _version(db, c)
    _run(db, c, "DELETE FROM document_shares WHERE document_id = :d AND user_id = :u", d=first, u=b)
    _run(db, c, "DELETE FROM lock_grants WHERE document_id = :d AND user_id = :u", d=family[2], u=lock_holder)
    assert _given(db, c) == {("share", loose, b)}
    assert _version(db, c) == before + 2
    _run(db, c, "UPDATE documents SET locked_at = NULL, locked_by = NULL WHERE family_id = :f", f=first)

    # Join (versions._JOIN takes the family's folder), Undo, Separate: no access row moves.
    rows = _given(db, c)
    with db.customer(c) as conn:
        conn.execute(versions._FAMILY_NOT_CURRENT, {"c": c, "f": first})
        conn.execute(versions._JOIN, {"c": c, "f": first, "v": 4, "n": loose, "cur": family[0]})
    assert _values(db, c)[loose] == (finance, None)
    assert (_given(db, c), _differences(db, c)) == (rows, [])
    with db.customer(c) as conn:
        conn.execute(versions._ALONE_AGAIN, {"c": c, "d": loose, "folder": ops})
    assert _values(db, c)[loose] == (ops, None)
    assert (_given(db, c), _differences(db, c)) == (rows, [])
    with db.customer(c) as conn:
        conn.execute(versions._ON_ITS_OWN, {"c": c, "d": family[2]})
    assert (_given(db, c), _differences(db, c)) == (rows, [])


def test_access_updates_never_move_last_changed_and_a_statements_own_update_still_does(db: Database) -> None:
    c, u = new_customer(db)
    finance = _department(db, c, u, "Finance")
    with db.customer(c) as conn:
        bills = insert_folder(conn, c, u, "Bills")
        files = [insert_document(conn, c, folder_id=bills) for _ in range(2)]
        gone = insert_document(conn, c, deleted_at=NOW, deleted_by=u)
        conn.execute(
            text(
                "INSERT INTO learned_corrections (customer_id, field, raw_value, corrected_value, created_by,"
                " source_document_id) VALUES (:c, 'gstin', 'a', 'b', :u, :d)"
            ),
            {"c": c, "u": u, "d": gone},
        )
    changed = "SELECT id, updated_at FROM documents WHERE customer_id = :c"
    corrected = "SELECT source_document_id, updated_at FROM learned_corrections WHERE customer_id = :c"
    before, correction = sorted(_run(db, c, changed)), _run(db, c, corrected)

    _run(db, c, "UPDATE folders SET parent_id = :p, private = true WHERE id = :f", p=finance, f=bills)
    _run(db, c, "DELETE FROM documents WHERE id = :d", d=gone)  # forever: the key is SET NULL

    assert {_values(db, c)[d] for d in files} == {(finance, bills)}
    assert sorted(_run(db, c, changed)) == [row for row in before if str(row[0]) != gone]
    assert _run(db, c, corrected) == [(None, correction[0][1])]
    _run(db, c, "UPDATE documents SET display_name = 'renamed.pdf' WHERE id = :d", d=files[0])
    assert dict(_run(db, c, changed))[uuid.UUID(files[0])] > dict(before)[uuid.UUID(files[0])]


def test_the_access_version_goes_up_once_per_statement_that_changes_access(db: Database) -> None:
    api = Api(db)
    c, u = new_customer(db)
    other, _ = new_customer(db)
    people = [api.add_user(c, "member")[0] for _ in range(3)]
    finance, ops = _department(db, c, u, "Finance"), _department(db, c, u, "Ops")
    with db.customer(c) as conn:
        bills = insert_folder(conn, c, u, "Bills", finance)
        first = insert_document(conn, c, folder_id=bills)
        second = insert_document(conn, c, folder_id=bills, family_id=first, version_no=2, is_current=False)
    theirs = _version(db, other)
    members = "SELECT :c, CAST(:f AS uuid), p, false, CAST(:u AS uuid) FROM unnest(CAST(:p AS uuid[])) AS p"
    steps = [
        (
            "INSERT INTO department_members (customer_id, department_id, user_id, is_head, added_by)"
            f" {members}",
            1,
        ),
        ("UPDATE department_members SET is_head = true WHERE customer_id = :c", 1),
        ("UPDATE department_members SET is_head = true WHERE customer_id = :c", 0),
        ("DELETE FROM department_members WHERE customer_id = :c", 1),
        (
            "INSERT INTO private_folder_members (customer_id, folder_id, user_id, added_by)"
            " SELECT :c, CAST(:b AS uuid), p, CAST(:u AS uuid) FROM unnest(CAST(:p AS uuid[])) AS p",
            1,
        ),
        ("DELETE FROM private_folder_members WHERE customer_id = :c", 1),
        ("UPDATE folders SET name = 'Bills 2025' WHERE id = :b", 0),
        ("UPDATE folders SET department_only = true WHERE id = :f", 1),
        ("UPDATE folders SET parent_id = :o WHERE id = :b", 1),
        ("UPDATE documents SET status = 'failed' WHERE id = :d", 0),
        ("UPDATE documents SET locked_at = now(), locked_by = :u WHERE family_id = :d", 1),
        (
            "INSERT INTO document_shares (customer_id, document_id, user_id, given_by)"
            " SELECT :c, d, p, CAST(:u AS uuid) FROM unnest(CAST(:p AS uuid[])) AS p,"
            " unnest(ARRAY[CAST(:d AS uuid), CAST(:s AS uuid)]) AS d",
            1,
        ),
        ("DELETE FROM document_shares WHERE document_id = :d", 1),
        ("UPDATE users SET management = true WHERE id = ANY(CAST(:p AS uuid[]))", 1),
        ("UPDATE users SET display_name = 'Someone' WHERE id = ANY(CAST(:p AS uuid[]))", 0),
    ]
    params = {"f": finance, "o": ops, "b": bills, "d": first, "s": second, "u": u, "p": people}
    for statement, raised in steps:
        before = _version(db, c)
        _run(db, c, statement, **params)
        assert _version(db, c) == before + raised, statement
    assert _run(db, c, "SELECT count(*) FROM document_shares WHERE customer_id = :c") == [(0,)]
    assert _version(db, other) == theirs
    assert _differences(db, c) == []


def _between(db: Database) -> dict[str, str]:
    """At 0084, rows no trigger kept right: a department Finance with Bills (made private) inside,
    a plain top-level Misc holding a stale department, files with NULL or stale values, a file
    trashed alone in Bills, and a folder trashed from Finance with its file."""
    customer, user = new_customer(db)
    t: dict[str, str] = {"c": customer}
    with db.customer(customer) as conn:
        t["finance"] = insert_folder(conn, customer, user, "Finance")
        conn.execute(text("UPDATE folders SET kind = 'department' WHERE id = :f"), {"f": t["finance"]})
        t["bills"] = insert_folder(conn, customer, user, "Bills", t["finance"])
        t["misc"] = insert_folder(conn, customer, user, "Misc")
        conn.execute(text("UPDATE folders SET private = true WHERE id = :f"), {"f": t["bills"]})
        stale = "UPDATE {} SET department_id = :f WHERE id = :d"
        conn.execute(text(stale.format("folders")), {"f": t["finance"], "d": t["misc"]})
        t["bill"] = insert_document(conn, customer, folder_id=t["bills"])
        t["note"] = insert_document(conn, customer, folder_id=t["misc"])
        t["loose"] = insert_document(conn, customer)
        for name in ("note", "loose"):
            conn.execute(text(stale.format("documents")), {"f": t["finance"], "d": t[name]})
        t["alone"] = insert_document(conn, customer, folder_id=t["bills"], deleted_at=NOW, deleted_by=user)
        t["old"] = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO trashed_folders (id, customer_id, parent_id, name, kind, created_by, created_at,"
                " trash_id, root, deleted_at, deleted_by) VALUES (:f, :c, :p, 'Old', 'custom', :u, :t,"
                " :trash, true, :t, :u)"
            ),
            {
                "f": t["old"],
                "c": customer,
                "p": t["finance"],
                "u": user,
                "t": NOW,
                "trash": str(uuid.uuid4()),
            },
        )
        t["old_file"] = insert_document(conn, customer, deleted_at=NOW, deleted_by=user)
        conn.execute(
            text("UPDATE documents SET trashed_folder_id = :f WHERE id = :d"),
            {"f": t["old"], "d": t["old_file"]},
        )
    return t


def _check(db: Database) -> tuple[int, list[str]]:
    out = io.StringIO()
    code = main(["check-access"], db=Database(db.engine, WORKER), out=out)
    return code, out.getvalue().splitlines()


def test_up_works_out_every_value_again_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0084")
    t = _between(db)
    touched = "SELECT id, updated_at FROM documents WHERE customer_id = :c"
    before = sorted(_run(db, t["c"], touched))
    at_0084 = (_state(db), _run(db, t["c"], _TOUCH))

    migrate(url, "0085")
    at_0085 = _state(db)
    assert _check(db) == (0, ["0 differences"])
    finance, bills = t["finance"], t["bills"]
    assert _values(db, t["c"], "folders") == {
        finance: (finance, None),
        bills: (finance, bills),
        t["misc"]: (None, None),
    }
    assert _values(db, t["c"], "trashed_folders") == {t["old"]: (finance, None)}
    documents = _values(db, t["c"])
    assert {name: documents[t[name]] for name in ("bill", "note", "loose", "alone", "old_file")} == {
        "bill": (finance, bills),
        "note": (None, None),
        "loose": (None, None),
        "alone": (finance, bills),
        "old_file": (finance, None),
    }
    assert sorted(_run(db, t["c"], touched)) == before  # the touch trigger was off
    assert _version(db, t["c"]) == 1
    assert all("pg_trigger_depth() < 1" in definition for _, definition in _run(db, t["c"], _TOUCH))

    base._downgrade(url, "0084")
    assert (_state(db), _run(db, t["c"], _TOUCH)) == at_0084

    # A folder 101 deep: 0085 stops rather than leave its values unknown.
    with db.customer(t["c"]) as conn:
        parent, user = None, _run(db, t["c"], "SELECT id FROM users WHERE customer_id = :c")[0][0]
        for depth in range(101):
            parent = insert_folder(conn, t["c"], str(user), f"Level {depth}", parent)
    with pytest.raises(subprocess.CalledProcessError) as stopped:
        migrate(url, "0085")
    assert b"deeper than 100 levels" in stopped.value.stderr
    _run(db, t["c"], "DELETE FROM folders WHERE id = :f", f=parent)

    migrate(url, "0085")
    assert _state(db) == at_0085
    assert _check(db) == (0, ["0 differences"])
