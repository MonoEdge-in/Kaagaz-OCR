"""Migration 0084 (PRD 7, #590, 14 Nov 2026): who-can-see-what's tables and columns, schema only
(the backfill is 0085's). On a fresh database with folders and documents: 0084 writes no row
(every stored value NULL, ``updated_at`` unchanged, every new setting today's behaviour);
``check-access``, run as the workers' role, names the values the tree gives that are not stored,
is clean once they are stored by hand, then names each value made wrong and a member row whose
folder is gone, but no file in Trash. Up, down (the schema, comments and grants as at 0083, the
rows kept) and up again. The keys and CHECKs on documents and learned_corrections are added NOT
VALID (0085 validates them) and still refuse what they should (shared database). The
tables' locks and grants: test_isolation_schema; another organisation's rows:
test_isolation_access_tables; the organisation delete: test_isolation_customer_purge."""

import io
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.cli import main
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import APP_ROLE, insert_document, insert_folder, migrate, new_customer, violation
from tests.test_isolation_logins import WORKER
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state
from tests.test_migration_0083_db import _rights

pytestmark = pytest.mark.db
fresh = base.fresh
M0084 = _load("0084_access_tables.py")
NOW = datetime(2026, 11, 1, tzinfo=UTC)
# Added NOT VALID, so 0084 reads neither table; 0085 validates them outside its transaction.
NOT_VALID = (
    "documents_held_for_fk",
    "documents_locked",
    "documents_locked_by_fk",
    "documents_status_check",
    "learned_corrections_source_fk",
)
_VALIDATED = "SELECT conname, convalidated FROM pg_constraint WHERE conname = ANY(:n) ORDER BY 1"
# How many rows hold anything but the new columns' defaults: none after 0084.
_SET = (
    "SELECT (SELECT count(*) FROM folders WHERE customer_id = :c"
    " AND (department_id IS NOT NULL OR private_folder_id IS NOT NULL OR private OR department_only))"
    " + (SELECT count(*) FROM trashed_folders WHERE customer_id = :c"
    " AND (department_id IS NOT NULL OR private_folder_id IS NOT NULL OR private OR department_only))"
    " + (SELECT count(*) FROM documents WHERE customer_id = :c AND (department_id IS NOT NULL"
    " OR private_folder_id IS NOT NULL OR held_for IS NOT NULL OR locked_at IS NOT NULL))"
)
_TRASHED_FOLDER = (
    "INSERT INTO trashed_folders (id, customer_id, parent_id, name, kind, created_by, created_at, trash_id,"
    " root, deleted_at, deleted_by) VALUES (:f, :c, NULL, 'Old', 'custom', :u, :t, :trash, true, :t, :u)"
)
_MEMBER = "INSERT INTO {} (customer_id, {}, user_id, added_by) VALUES (:c, :f, :u, :u)"
_GIVEN = "INSERT INTO {} (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :u)"


def _run(statement: str, db: Database, customer: str, **params: Any) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        result = conn.execute(text(statement), {"c": customer, **params})
        return [tuple(row) for row in result] if result.returns_rows else []


def _check(db: Database) -> tuple[int, list[str]]:
    """check-access as the workers' role: its exit code and lines."""
    out = io.StringIO()
    code = main(["check-access"], db=Database(db.engine, WORKER), out=out)
    return code, out.getvalue().splitlines()


def _organisation(db: Database) -> dict[str, str]:
    """The department Finance with Bills inside, a plain top-level folder, files live, in Trash
    alone and in no folder, and a folder in Trash."""
    customer, user = new_customer(db)
    t: dict[str, str] = {"c": customer, "user": user, "old": str(uuid.uuid4())}
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
        params = {"f": t["old"], "c": customer, "u": user, "t": NOW, "trash": str(uuid.uuid4())}
        conn.execute(text(_TRASHED_FOLDER), params)
    return t


def _missing(t: dict[str, str]) -> list[str]:
    """check-access's lines for an ``_organisation`` with nothing stored: Finance's live rows."""
    return [
        f"customer {t['c']}: {kind} {t[name]} stores department none private folder none;"
        f" the tree gives department {t['finance']} private folder none"
        for kind, name in (
            ("document", "bill"),
            ("document", "bill_v2"),
            ("document", "ledger"),
            ("folder", "finance"),
            ("folder", "bills"),
        )
    ]


def _store_by_hand(db: Database, t: dict[str, str]) -> None:
    """What 0085's backfill is to store: Finance on its live folders and files."""
    folders = "UPDATE folders SET department_id = :f WHERE customer_id = :c AND id IN (:f, :b)"
    _run(folders, db, t["c"], f=t["finance"], b=t["bills"])
    documents = "UPDATE documents SET department_id = :f WHERE customer_id = :c AND id IN (:d1, :d2, :d3)"
    _run(documents, db, t["c"], f=t["finance"], d1=t["bill"], d2=t["bill_v2"], d3=t["ledger"])


def test_the_revision_follows_0083() -> None:
    assert (M0084.revision, M0084.down_revision) == ("0084", "0083")


def test_up_writes_no_row_the_check_finds_what_is_wrong_and_down_and_up_again(
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
        assert sorted(_run(touched, db, t["c"])) == before[t["c"]]  # no row written
        assert _run(_SET, db, t["c"]) == [(0,)]  # nothing stored, private, held or locked
        settings = "SELECT access_version, department_pointer FROM customers WHERE id = :c"
        assert _run(settings, db, t["c"]) == [(1, False)]
        assert _run("SELECT bool_or(management) FROM users WHERE customer_id = :c", db, t["c"]) == [(False,)]
    assert _rights(db, WORKER) == _rights(db, APP_ROLE)
    assert _run(_VALIDATED, db, a["c"], n=list(NOT_VALID)) == [(name, False) for name in NOT_VALID]
    code, lines = _check(db)
    assert (code, sorted(lines)) == (1, sorted([*_missing(a), *_missing(b), "10 differences"]))

    for t in (a, b):
        _store_by_hand(db, t)
    assert _check(db) == (0, ["0 differences"])

    _run("UPDATE folders SET private = true WHERE id = :f", db, a["c"], f=a["bills"])
    _run("UPDATE documents SET department_id = NULL WHERE id = :d", db, a["c"], d=a["bill"])
    in_trash = "UPDATE documents SET department_id = :f WHERE id = :d"
    _run(in_trash, db, a["c"], f=a["misc"], d=a["alone"])
    gone, user = str(uuid.uuid4()), a["user"]
    _run(_MEMBER.format("department_members", "department_id"), db, a["c"], f=gone, u=user)
    # A folder in Trash is still its folder.
    _run(_MEMBER.format("private_folder_members", "folder_id"), db, a["c"], f=a["old"], u=user)
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
    assert _run(_SET, db, a["c"]) == [(0,)]


def test_a_correction_keeps_its_row_and_a_versions_access_rows_go_with_it(db: Database) -> None:
    """0084: SET NULL (source_document_id) on learned_corrections, so the correction stays as one made
    before PRD 7; CASCADE on document_shares and lock_grants."""
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user)
        conn.execute(
            text(
                "INSERT INTO learned_corrections (customer_id, field, raw_value, corrected_value, created_by,"
                " source_document_id) VALUES (:c, 'total', '1O0', '100', :u, :d)"
            ),
            {"c": customer, "u": user, "d": document},
        )
        for table in ("document_shares", "lock_grants"):
            conn.execute(text(_GIVEN.format(table)), {"c": customer, "d": document, "u": user})
        conn.execute(
            text("DELETE FROM documents WHERE customer_id = :c AND id = :d"), {"c": customer, "d": document}
        )
    kept = "SELECT corrected_value, source_document_id FROM learned_corrections WHERE customer_id = :c"
    assert _run(kept, db, customer) == [("100", None)]
    left = (
        "SELECT (SELECT count(*) FROM document_shares WHERE customer_id = :c)"
        " + (SELECT count(*) FROM lock_grants WHERE customer_id = :c)"
    )
    assert _run(left, db, customer) == [(0,)]


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE documents SET locked_at = now() WHERE customer_id = :c AND id = :d", "documents_locked"),
        ("UPDATE documents SET locked_by = :u WHERE customer_id = :c AND id = :d", "documents_locked"),
        (
            "UPDATE folders SET department_only = true WHERE customer_id = :c AND id = :f",
            "folders_department_only",
        ),
    ],
)
def test_half_a_lock_and_department_only_off_a_department_are_refused(
    db: Database, statement: str, constraint: str
) -> None:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        folder = insert_folder(conn, customer, user, "Bills")  # custom, not a department
        document = insert_document(conn, customer, folder_id=folder)
    with pytest.raises(DBAPIError) as refused, db.customer(customer) as conn:
        conn.execute(text(statement), {"c": customer, "d": document, "u": user, "f": folder})
    assert violation(refused.value) == ("23514", constraint)
