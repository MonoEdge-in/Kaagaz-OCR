"""Migration 0068 (#482, #481, Tushar, 3 Nov 2026) on a fresh database: up, down and up again.
The kept audit table (no grant to anyone), the partial view index, the admin two-step column
(false for every existing organisation), the three functions, and the finish that keeps a
deleted organisation's audit rows; the downgrade puts 0047's finish back exactly."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0068 = _load("0068_audit_keeping.py")
PREVIOUS = "0067"  # M0068.PREVIOUS
FINISH = "public.kaagaz_finish_customer_delete(uuid)"
NEW_FUNCTIONS = {"kaagaz_forget_kept_audit", "kaagaz_op_admin_two_step", "kaagaz_op_set_admin_two_step"}


def _finish(db: Database) -> str:
    with db.system() as conn:
        return str(
            conn.execute(
                text("SELECT pg_get_functiondef(CAST(:f AS regprocedure))"), {"f": FINISH}
            ).scalar_one()
        )


def test_0068_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    assert M0068.down_revision == PREVIOUS
    migrate(url, PREVIOUS)
    before, finish_before = base._schema(db), _finish(db)
    customer, _ = new_customer(db)  # an organisation from before 0068

    migrate(url, M0068.revision)
    after = base._schema(db)
    assert base._names(after, "relations") - base._names(before, "relations") == {
        "audit_log_kept",
        "audit_log_kept_pkey",
        "audit_log_kept_deleted",
        "audit_log_views",
    }
    assert base._names(after, "functions") - base._names(before, "functions") == NEW_FUNCTIONS
    added = [row for row in after["columns"] if row[:2] == ("customers", "admin_two_step_required")]
    assert added == [("customers", "admin_two_step_required", "boolean", True, "false")]
    assert as_owner(db, "SELECT admin_two_step_required FROM customers WHERE id = :c", {"c": customer}) == [
        (False,)
    ]  # nothing changes on release day
    views = [row for row in after["indexes"] if row[1] == "audit_log_views"]
    assert "WHERE (action = 'document.view'::text)" in views[0][2]
    finish = _finish(db)
    assert "INSERT INTO public.audit_log_kept" in finish
    assert finish.replace(M0068._KEEP, "") == finish_before  # the one statement added

    base._downgrade(url, PREVIOUS)
    assert base._schema(db) == before
    assert _finish(db) == finish_before
    migrate(url, M0068.revision)
    assert base._schema(db) == after
    assert _finish(db) == finish
