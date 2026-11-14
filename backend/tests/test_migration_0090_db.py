"""Migration 0090 (PRD 7 PR 10a, #601): the access API's owner-rights functions, switching a person
off, and the operator's tree lock. Up, down (the schema as at 0089) and up again on a fresh
database. What the functions and the trigger do: test_isolation_access_api; their rights:
test_isolation_schema."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0090 = _load("0090_access_api.py")
_CHANGE_ADMIN = (
    "SELECT pg_get_functiondef('public.kaagaz_op_change_admin(uuid, uuid, uuid, text, bigint)'::regprocedure)"
)
_OURS = (
    "SELECT count(*) FROM pg_proc WHERE proname IN ('kaagaz_access_of', 'kaagaz_audience',"
    " 'kaagaz_place_audience', 'kaagaz_kept_from_me', 'kaagaz_admin_document', 'kaagaz_give_file',"
    " 'kaagaz_take_file', 'kaagaz_user_switched')"
)


def test_the_revision_follows_0089() -> None:
    assert (M0090.revision, M0090.down_revision) == ("0090", "0089")


def test_the_rule_is_0089s_access_and_0087s_views_with_the_person_named() -> None:
    """Each generated text holds the last rule (0089's access, its walls_set; 0087's views), never
    the current person's own lookups."""
    assert "kaagaz.user_id" not in M0090.ACCESS_OF and "u.id = p_user AND NOT u.disabled" in M0090.ACCESS_OF
    assert "walls_set" in M0090.ACCESS_OF
    for rule in (M0090.DOCUMENT_RULE, M0090.FOLDER_RULE, M0090.PLACE_RULE):
        assert "current_user" not in rule and "kaagaz_shared_with_me" not in rule
    assert "p_department_only" in M0090.PLACE_RULE and "a.admin" not in M0090.PLACE_RULE


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0089")
    at_0089 = _state(db)
    with db.system() as conn:
        before = conn.execute(text(_CHANGE_ADMIN)).scalar_one()
    migrate(url, "0090")
    at_0090 = _state(db)
    with db.system() as conn:
        assert conn.execute(text(_OURS)).scalar_one() == 8
        changed = conn.execute(text(_CHANGE_ADMIN)).scalar_one()
    assert changed == before.replace(M0090.SHARE_LINE, M0090.TREE_LINE) != before

    base._downgrade(url, "0089")
    assert _state(db) == at_0089
    with db.system() as conn:
        assert conn.execute(text(_OURS)).scalar_one() == 0
        assert conn.execute(text(_CHANGE_ADMIN)).scalar_one() == before

    migrate(url, "0090")
    assert _state(db) == at_0090
