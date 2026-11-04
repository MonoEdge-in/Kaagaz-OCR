"""An admin's own two-step off (0061b, Tushar, 30 Oct 2026) on a fresh database: up, down and
up again. One nullable timestamptz on ``users`` with its comment, no row written; the
operator's reset (``kaagaz_op_change_admin``) clears it too and changes nothing else, its
owner, comment, grants, SECURITY DEFINER and search path kept."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0061B = _load("0061b_admin_two_step_off.py")
_DEFINITION = text(
    "SELECT pg_get_functiondef(p.oid), pg_get_userbyid(p.proowner), obj_description(p.oid, 'pg_proc')"
    " FROM pg_proc p"
    " WHERE p.oid = 'public.kaagaz_op_change_admin(uuid, uuid, uuid, text, bigint)'::regprocedure"
)
_COMMENT = text(
    "SELECT col_description(a.attrelid, a.attnum) FROM pg_attribute a"
    " WHERE a.attrelid = 'users'::regclass AND a.attname = 'totp_off_at'"
)


def _definition(db: Database) -> tuple[str, str, str]:
    with db.system() as conn:
        body, owner, comment = conn.execute(_DEFINITION).one()
    return body, owner, comment


def test_0061b_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, M0061B.down_revision)
    before, at_0060 = base._schema(db), _definition(db)
    customer_id, user_id = new_customer(db)  # an admin from before 0061b
    assert at_0060[0].count(M0061B.OLD) == 1

    # Changed on purpose (#484, 3 Nov 2026): up to 0061b itself, not head; 0061c follows it.
    migrate(url, M0061B.revision)
    after = base._schema(db)
    added = [row for row in after["columns"] if row[:2] == ("users", "totp_off_at")]
    assert added == [("users", "totp_off_at", "timestamp with time zone", False, None)]
    assert {key: value for key, value in after.items() if key != "columns"} == {
        key: value for key, value in before.items() if key != "columns"
    }
    at_head = _definition(db)
    assert at_head == (at_0060[0].replace(M0061B.OLD, M0061B.NEW), *at_0060[1:])  # only that change
    with db.system() as conn:
        assert "turned their own two-step sign-in off" in conn.execute(_COMMENT).scalar_one()
    with db.customer(customer_id) as conn:
        kept = conn.execute(text("SELECT totp_off_at FROM users WHERE id = :u"), {"u": user_id})
        assert kept.scalar() is None  # no row is written

    base._downgrade(url, M0061B.down_revision)
    assert base._schema(db) == before
    assert _definition(db) == at_0060
    migrate(url, M0061B.revision)
    assert base._schema(db) == after
    assert _definition(db) == at_head
