"""Migration 0076 (#520, Tushar, 6 Nov 2026): Ensemble held by the reading pause. Only the body and
comment of ``kaagaz_customer_ids_held()`` change: it gives the organisations with a held queued
Ensemble run too, still owner rights, the empty search path and the app's alone; down puts 0066's
back exactly; up, down, up."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate
from tests.test_ensemble_job_db import org, start
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0066_db import _bodies

pytestmark = pytest.mark.db
fresh = base.fresh
PREVIOUS = "0075"
_HELD = text("SELECT t.id::text FROM kaagaz_customer_ids_held() AS t (id)")


def _held(db: Database) -> list[str]:
    with db.system() as conn:
        return [str(row[0]) for row in conn.execute(_HELD)]


def test_a_held_ensemble_run_brings_its_organisation_to_the_release_sweep(
    fresh: tuple[str, Database],
) -> None:
    url, db = fresh
    migrate(url, "head")
    with org(db) as o:
        run_id = start(db, o, scope="document")
        assert o["c"] not in _held(db)
        as_owner(db, "UPDATE ensemble_runs SET error_code = 'paused_budget' WHERE id = :r", {"r": run_id})
        assert _held(db) == [o["c"]]
        as_owner(db, "UPDATE ensemble_runs SET error_code = NULL WHERE id = :r", {"r": run_id})
        assert _held(db) == []


def test_down_up_restores_0075_exactly(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, PREVIOUS)
    at_0075, bodies_0075 = base._schema(db), _bodies(db)
    migrate(url, "0076")
    at_0076, bodies_0076 = base._schema(db), _bodies(db)
    [(_, body, comment, acl)] = [row for row in bodies_0076 if row[0] == "kaagaz_customer_ids_held"]
    assert "ensemble_runs" in body and "SECURITY DEFINER" in body and "SET search_path TO ''" in body
    assert "Ensemble" in comment and "kaagaz_app=X" in acl and "kaagaz_operator" not in acl
    assert [row for row in bodies_0076 if row[0] != "kaagaz_customer_ids_held"] == [
        row for row in bodies_0075 if row[0] != "kaagaz_customer_ids_held"
    ]
    base._downgrade(url, PREVIOUS)
    assert base._schema(db) == at_0075
    assert _bodies(db) == bodies_0075
    migrate(url, "0076")
    assert base._schema(db) == at_0076
    assert _bodies(db) == bodies_0076
