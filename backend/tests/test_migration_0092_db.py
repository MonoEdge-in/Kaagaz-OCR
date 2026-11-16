"""Migration 0092 (PRD 7 PR 9b, #606): the writes over rows the person can't see, as owner-rights
functions. Up, down (the schema as at 0091) and up again on a fresh database. What they do:
test_isolation_rule_writes; their rights: test_isolation_schema."""

import re

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
fresh = base.fresh
M0092 = _load("0092_database_rule_writes.py")
_NAMES = sorted(name.split("(")[0] for name in M0092.FUNCTIONS)
# Each one: owner-rights, an empty search path, PUBLIC may not run it, the app and the worker role may.
_OURS = (
    "SELECT p.proname, p.prosecdef, p.proconfig = ARRAY['search_path=\"\"'],"
    " has_function_privilege('public', p.oid, 'EXECUTE'),"
    " has_function_privilege('kaagaz_app', p.oid, 'EXECUTE'),"
    " has_function_privilege('kaagaz_worker', p.oid, 'EXECUTE')"
    " FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace AND p.proname = ANY (:names)"
    " ORDER BY p.proname"
)


def test_the_revision_follows_0091() -> None:
    assert (M0092.revision, M0092.down_revision) == ("0092", "0091")


def test_every_write_checks_the_caller_then_takes_the_tree_lock_then_the_family_locks() -> None:
    """The house order (the caller's check, folders._lock_tree, then trash._LOCK_FAMILY's family
    order) and the explicit customer filter, in every function's text; delete forever's family
    takes its family lock only, and the reads take none."""
    for name, body in M0092.FUNCTIONS.items():
        assert "public.kaagaz_current_customer()" in body, name
        assert "SECURITY DEFINER SET search_path = ''" in body, name
        check = body.find("RAISE EXCEPTION 'only")
        tree = body.find("FROM public.customers c WHERE c.id")
        families = [m.start() for m in re.finditer(r"\((?:x|d)\.id <> (?:x|d)\.family_id\)", body)]
        if "STABLE" in body:
            assert tree == -1 and "FOR NO KEY UPDATE" not in body, name
        elif name.startswith("kaagaz_purge_family"):
            assert tree == -1 and families and 0 < check < min(families), name
        else:
            assert families and 0 < check < tree < min(families), name


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0091")
    at_0091 = _state(db)
    migrate(url, "0092")
    at_0092 = _state(db)
    with db.system() as conn:
        found = conn.execute(text(_OURS), {"names": _NAMES}).all()
    assert [tuple(row) for row in found] == [(name, True, True, False, True, True) for name in _NAMES]
    assert len(_NAMES) == 10

    base._downgrade(url, "0091")
    assert _state(db) == at_0091
    with db.system() as conn:
        assert conn.execute(text(_OURS), {"names": _NAMES}).all() == []

    migrate(url, "0092")
    assert _state(db) == at_0092
