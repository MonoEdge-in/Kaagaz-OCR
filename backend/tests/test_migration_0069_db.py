"""Migration 0069 (product QA, 4 Nov 2026) on a fresh database: up, down and up again. Two
functions change in place: ``kaagaz_op_set_admin_two_step`` signs out the organisation's admins
without two-step when it turns the rule on (U1), and ``kaagaz_op_mistral_status`` leaves out
documents in the Trash (P9). The downgrade puts 0068's and 0065's bodies and comments back."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load

pytestmark = pytest.mark.db
fresh = base.fresh
M0069 = _load("0069_qa_fixes.py")
PREVIOUS = "0068"
CHANGED = ("kaagaz_op_set_admin_two_step(uuid, uuid, boolean, bigint)", "kaagaz_op_mistral_status(uuid)")


def _definitions(db: Database) -> list[tuple[str, str | None, str]]:
    """Each changed function's definition, comment and grants."""
    with db.system() as conn:
        return [
            tuple(
                conn.execute(
                    text(
                        "SELECT pg_get_functiondef(p.oid), obj_description(p.oid, 'pg_proc'), p.proacl::text"
                        " FROM pg_proc p WHERE p.oid = CAST(:f AS regprocedure)"
                    ),
                    {"f": f"public.{signature}"},
                ).one()
            )
            for signature in CHANGED
        ]


def test_0069_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    assert (M0069.revision, M0069.down_revision) == ("0069", PREVIOUS)
    migrate(url, PREVIOUS)
    before, functions_before = base._schema(db), _definitions(db)

    migrate(url, M0069.revision)
    after = _definitions(db)
    set_two_step, mistral = (definition for definition, _, _ in after)
    assert "SET session_version = u.session_version + 1" in set_two_step
    assert "d.deleted_at IS NULL" in mistral
    assert [acl for _, _, acl in after] == [acl for _, _, acl in functions_before]  # the same grants
    assert base._schema(db) == before  # no table, column or index changes

    base._downgrade(url, PREVIOUS)
    assert _definitions(db) == functions_before
    migrate(url, M0069.revision)
    assert _definitions(db) == after
