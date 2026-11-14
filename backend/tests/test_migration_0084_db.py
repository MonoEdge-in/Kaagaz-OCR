"""Migration 0084 (PRD 7, #590, 14 Nov 2026): who-can-see-what's tables and columns, backfilled.
On a fresh database with folders and documents live, in Trash alone and in Trash with their
folders: each folder's and document's department comes from its folder tree, no private folder,
``updated_at`` unchanged, every new setting today's behaviour; ``check-access``, run as the
workers' role, is clean, then names each value made wrong and a member row whose folder is gone.
Up, down (the schema, comments and grants as at 0083, the rows kept) and up again. The tables'
locks and grants: test_isolation_schema; the organisation delete: test_isolation_customer_purge."""

import io
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.cli import main
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import APP_ROLE, insert_document, insert_folder, migrate, new_customer
from tests.test_isolation_logins import WORKER
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state
from tests.test_migration_0083_db import _rights

pytestmark = pytest.mark.db
fresh = base.fresh
M0084 = _load("0084_access_tables.py")
NOW = datetime(2026, 11, 1, tzinfo=UTC)
_STORED = {
    "folders": "SELECT id, department_id FROM folders WHERE customer_id = :c",
    "trashed_folders": "SELECT id, department_id FROM trashed_folders WHERE customer_id = :c",
    "documents": "SELECT id, department_id FROM documents WHERE customer_id = :c",
}
_PRIVATE = (
    "SELECT (SELECT count(*) FROM folders WHERE customer_id = :c"
    " AND (private_folder_id IS NOT NULL OR private OR department_only))"
    " + (SELECT count(*) FROM trashed_folders WHERE customer_id = :c"
    " AND (private_folder_id IS NOT NULL OR private OR department_only))"
    " + (SELECT count(*) FROM documents WHERE customer_id = :c"
    " AND (private_folder_id IS NOT NULL OR held_for IS NOT NULL OR locked_at IS NOT NULL))"
)
_TRASHED_FOLDER = (
    "INSERT INTO trashed_folders (id, customer_id, parent_id, name, kind, created_by, created_at, trash_id,"
    " root, deleted_at, deleted_by) VALUES (:f, :c, :p, :n, :k, :u, :t, :trash, :root, :t, :u)"
)


def _run(statement: str, db: Database, customer: str, **params: Any) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        result = conn.execute(text(statement), {"c": customer, **params})
        return [tuple(row) for row in result] if result.returns_rows else []


def _departments(db: Database, customer: str) -> dict[str, dict[str, str | None]]:
    return {
        table: {str(row_id): None if department is None else str(department) for row_id, department in rows}
        for table, rows in ((table, _run(sql, db, customer)) for table, sql in _STORED.items())
    }


def _check(db: Database) -> tuple[int, list[str]]:
    """check-access as the workers' role: its exit code and lines."""
    out = io.StringIO()
    code = main(["check-access"], db=Database(db.engine, WORKER), out=out)
    return code, out.getvalue().splitlines()


def _organisation(db: Database) -> dict[str, str]:
    """At 0083: the department Finance with Bills inside, a plain top-level folder, files live, in
    Trash alone and in no folder; in Trash with their folders: a department with a folder inside,
    a folder Finance held, and a folder whose parent is gone; a file trashed alone whose folder
    went since."""
    customer, user = new_customer(db)
    t: dict[str, str] = {"c": customer}
    with db.customer(customer) as conn:
        t["finance"] = insert_folder(conn, customer, user, "Finance")
        conn.execute(text("UPDATE folders SET kind = 'department' WHERE id = :f"), {"f": t["finance"]})
        t["bills"] = insert_folder(conn, customer, user, "Bills", t["finance"])
        t["misc"] = insert_folder(conn, customer, user, "Misc")
        t["bill"] = insert_document(conn, customer, folder_id=t["bills"], uploaded_by=user)
        t["bill_v2"] = insert_document(
            conn, customer, folder_id=t["bills"], family_id=t["bill"], version_no=2, is_current=False
        )
        t["ledger"] = insert_document(conn, customer, folder_id=t["finance"])
        t["alone"] = insert_document(conn, customer, folder_id=t["bills"], deleted_at=NOW, deleted_by=user)
        t["note"] = insert_document(conn, customer, folder_id=t["misc"])
        t["loose"] = insert_document(conn, customer)
        t["lost"] = insert_document(conn, customer, deleted_at=NOW, deleted_by=user)  # its folder went since
        trash = str(uuid.uuid4())
        for name, parent, kind in (
            ("old_dept", None, "department"),
            ("old_sub", "old_dept", "custom"),
            ("gone_from_finance", "finance", "custom"),
            ("orphan", "nowhere", "custom"),
        ):
            t[name] = str(uuid.uuid4())
            conn.execute(
                text(_TRASHED_FOLDER),
                {
                    "f": t[name],
                    "c": customer,
                    "p": None if parent is None else t.get(parent, str(uuid.uuid4())),
                    "n": name,
                    "k": kind,
                    "u": user,
                    "t": NOW,
                    "trash": trash,
                    "root": parent != "old_dept",
                },
            )
            file = insert_document(conn, customer, deleted_at=NOW, deleted_by=user)
            conn.execute(
                text("UPDATE documents SET trashed_folder_id = :f, trash_id = :t WHERE id = :d"),
                {"f": t[name], "t": trash, "d": file},
            )
            t[f"{name}_file"] = file
    return t


def _expected(t: dict[str, str]) -> dict[str, dict[str, str | None]]:
    finance, old = t["finance"], t["old_dept"]
    return {
        "folders": {t["finance"]: finance, t["bills"]: finance, t["misc"]: None},
        "trashed_folders": {
            t["old_dept"]: old,
            t["old_sub"]: old,
            t["gone_from_finance"]: finance,
            t["orphan"]: None,
        },
        "documents": {
            **{t[name]: finance for name in ("bill", "bill_v2", "ledger", "alone", "gone_from_finance_file")},
            **{t[name]: old for name in ("old_dept_file", "old_sub_file")},
            **{t[name]: None for name in ("note", "loose", "lost", "orphan_file")},
        },
    }


def test_the_revision_follows_0083() -> None:
    assert (M0084.revision, M0084.down_revision) == ("0084", "0083")


def test_up_backfills_from_the_tree_the_check_finds_what_is_wrong_and_down_and_up_again(
    fresh: tuple[str, Database],
) -> None:
    url, db = fresh
    migrate(url, "0083")
    a, b = _organisation(db), _organisation(db)
    touched = "SELECT id, updated_at FROM documents WHERE customer_id = :c"
    before = {c: sorted(_run(touched, db, c)) for c in (a["c"], b["c"])}
    at_0083 = _state(db)

    migrate(url, "0084")
    at_0084 = _state(db)
    for t in (a, b):
        assert _departments(db, t["c"]) == _expected(t)
        assert sorted(_run(touched, db, t["c"])) == before[t["c"]]  # the touch trigger was off
        assert _run(_PRIVATE, db, t["c"]) == [(0,)]  # nothing private, held or locked
        settings = "SELECT access_version, department_pointer FROM customers WHERE id = :c"
        assert _run(settings, db, t["c"]) == [(1, False)]
        assert _run("SELECT bool_or(management) FROM users WHERE customer_id = :c", db, t["c"]) == [(False,)]
    assert _rights(db, WORKER) == _rights(db, APP_ROLE)
    assert _check(db) == (0, ["0 differences"])

    _run("UPDATE folders SET private = true WHERE id = :f", db, a["c"], f=a["bills"])
    _run("UPDATE documents SET department_id = NULL WHERE id = :d", db, a["c"], d=a["bill"])
    in_trash = "UPDATE documents SET department_id = :f WHERE id = :d"
    _run(in_trash, db, a["c"], f=a["misc"], d=a["alone"])
    gone, user = str(uuid.uuid4()), _run("SELECT id FROM users WHERE customer_id = :c", db, a["c"])[0][0]
    member = "INSERT INTO {} (customer_id, {}, user_id, added_by) VALUES (:c, :f, :u, :u)"
    _run(member.format("department_members", "department_id"), db, a["c"], f=gone, u=user)
    # A folder in Trash is still its folder.
    _run(member.format("private_folder_members", "folder_id"), db, a["c"], f=a["old_sub"], u=user)
    code, lines = _check(db)
    c, finance, bills = a["c"], a["finance"], a["bills"]
    assert code == 1
    assert sorted(lines) == sorted(
        [
            f"customer {c}: folder {bills} stores department {finance} private folder none;"
            f" the tree gives department {finance} private folder {bills}",
            f"customer {c}: document {a['bill']} stores department none private folder none;"
            f" the tree gives department {finance} private folder {bills}",
            f"customer {c}: document {a['bill_v2']} stores department {finance} private folder none;"
            f" the tree gives department {finance} private folder {bills}",
            f"customer {c}: department_members of user {user}: folder {gone} is in neither table",
            "4 differences",
        ]
    )  # b's rows are its own, and right; a file in Trash keeps what it had

    base._downgrade(url, "0083")
    assert _state(db) == at_0083
    for t in (a, b):
        assert {row[0] for row in _run(touched, db, t["c"])} == {row[0] for row in before[t["c"]]}  # kept

    migrate(url, "0084")
    assert _state(db) == at_0084
    assert _departments(db, a["c"]) == _expected(a)
    assert _check(db) == (0, ["0 differences"])
