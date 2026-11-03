"""Held runs left out of the operator's insights (0069a, QA P3, 4 Nov 2026): up, down and up again
on a fresh database, each of the three functions differing from 0069's only by the one condition,
its owner, comment, grants, SECURITY DEFINER and search path kept; and a held run is never a
failed one."""

import pytest
from sqlalchemy import text

from kaagaz.db import operator_insights as insights
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_migration_0037 import _load
from tests.test_operator_insights_db import at, document, one, operator, read, run, window_day

pytestmark = pytest.mark.db
fresh = base.fresh
M0069A = _load("0069a_paused_runs.py")
_DEFINITION = text(
    "SELECT pg_get_functiondef(p.oid), pg_get_userbyid(p.proowner), obj_description(p.oid, 'pg_proc')"
    " FROM pg_proc p WHERE p.oid = CAST(:f AS regprocedure)"
)


def _definitions(db: Database) -> list[tuple[str, str, str]]:
    with db.system() as conn:
        return [tuple(conn.execute(_DEFINITION, {"f": f"public.{each}"}).one()) for each in M0069A.FUNCTIONS]


def test_0069a_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    assert (M0069A.revision, M0069A.down_revision) == ("0069a", "0069")
    migrate(url, M0069A.down_revision)
    before, at_0069 = base._schema(db), _definitions(db)
    assert [body.count(M0069A.OLD) for body, _, _ in at_0069] == [1, 1, 1]

    migrate(url, "0069a")
    at_0069a = _definitions(db)
    assert at_0069a == [(body.replace(M0069A.OLD, M0069A.NEW), *rest) for body, *rest in at_0069]
    assert base._schema(db) == before  # SECURITY DEFINER, search path and grants kept

    base._downgrade(url, M0069A.down_revision)
    assert _definitions(db) == at_0069
    assert base._schema(db) == before
    migrate(url, "0069a")
    assert _definitions(db) == at_0069a


def test_a_held_run_is_never_counted_as_a_run_or_a_failed_one(db: Database) -> None:
    op, day = operator(db), window_day()
    customer, _ = new_customer(db)
    for status, code in (
        ("failed", "paused_daily_limit"),
        ("failed", "paused_budget"),
        ("failed", "corrupt_file"),
        ("ready", None),
        ("ready", "pausedxbudget"),  # the underscore is a plain one, never a wildcard
    ):
        run(db, customer, document(db, customer, at(day)), at(day), status=status, code=code)
    quality = one(read(db, op, insights.quality, day, day, "UTC"), "customer_id", customer)
    assert (quality["runs"], quality["runs_failed"]) == (3, 1)
    [daily] = [
        row for row in read(db, op, insights.daily, day, day, "UTC") if str(row["customer_id"]) == customer
    ]
    assert (daily["runs"], daily["runs_failed"]) == (3, 1)
