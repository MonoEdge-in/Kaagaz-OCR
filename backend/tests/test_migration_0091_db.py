"""Migration 0091 (PRD 7 PR 9a, #606): the per-person database rule, dormant behind its switch. Up,
down (the schema as at 0090) and up again on a fresh database. What the rule does:
test_isolation_person_rule and test_isolation_rule_three_way; its rights: test_isolation_schema."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0091 = _load("0091_database_rule.py")
_OURS = (
    "SELECT (SELECT count(*) FROM pg_policy WHERE polname = 'person_only'),"
    " (SELECT count(*) FROM pg_proc WHERE proname IN ('kaagaz_person_rule_on', 'kaagaz_sees_everything',"
    " 'kaagaz_family_visible')),"
    " to_regclass('public.kaagaz_person_rule') IS NOT NULL,"
    " (SELECT count(*) FROM pg_attribute"
    "  WHERE attrelid = 'public.reader_status'::regclass AND attname = 'role')"
)
_INDEX = (
    "SELECT i.indisvalid,"
    " pg_get_indexdef(i.indexrelid) LIKE '%(customer_id) WHERE ((private_folder_id IS NOT NULL)%'"
    " FROM pg_index i WHERE i.indexrelid = to_regclass('public.documents_kept')"
)


def test_the_revision_follows_0090() -> None:
    assert (M0091.revision, M0091.down_revision) == ("0091", "0090")


def test_the_rule_is_0087s_views_for_the_app_role_only() -> None:
    """The generated predicates hold the views' rule on the table's own columns, never the worker's
    whole-organisation case nor an alias the policy doesn't have; 30 tables, documents first."""
    for rule in (M0091.DOCUMENT, M0091.FOLDER, M0091.TRASHED):
        assert "current_user" not in rule and "kaagaz_access()" in rule
        assert " d." not in rule and " f." not in rule and " t." not in rule
    assert "a.admin" in M0091.FOLDER and "a.admin" not in M0091.DOCUMENT
    assert list(M0091.POLICIES)[:3] == ["documents", "folders", "trashed_folders"]
    assert len(M0091.POLICIES) == 30 and len(M0091.CONTENT) == 27


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0090")
    at_0090 = _state(db)
    migrate(url, "0091")
    at_0091 = _state(db)
    with db.system() as conn:
        assert tuple(conn.execute(text(_OURS)).one()) == (30, 3, True, 1)
        assert tuple(conn.execute(text(_INDEX)).one()) == (True, True)  # built CONCURRENTLY, valid

    base._downgrade(url, "0090")
    assert _state(db) == at_0090
    with db.system() as conn:
        assert tuple(conn.execute(text(_OURS)).one()) == (0, 0, False, 0)
        assert conn.execute(text(_INDEX)).first() is None

    migrate(url, "0091")
    assert _state(db) == at_0091
