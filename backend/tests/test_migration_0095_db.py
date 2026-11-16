"""Migration 0095 (#647, 16 Nov 2026): ``model_catalog.parameters``, each model's supported
parameters (NULL: not read yet), with a CHECK on each name's shape, written by the app's refresh
and shown by the operator's ``kaagaz_op_models``. Up, down and up again with a row: the schema,
comments, grants and function bodies as before each time. The refresh and the client:
test_model_catalog_db.py, test_openrouter_client.py."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from tests import test_migration_0002_db as base
from tests.db_support import migrate, violation
from tests.test_isolation_ai_settings import platform
from tests.test_migration_0023_db import as_owner, catalog_model
from tests.test_migration_0037 import _load
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name
fresh = base.fresh
M0095 = _load("0095_model_parameters.py")
_SET = "UPDATE model_catalog SET parameters = CAST(:p AS text[]) WHERE id = :m"


def test_the_revision_follows_0094() -> None:  # Changed on purpose: rebased onto the chain's head
    assert (M0095.revision, M0095.down_revision) == ("0095", "0094")


def test_up_down_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0094")
    at_0094 = _state(db)
    model = catalog_model(db)

    migrate(url, "0095")
    at_0095 = _state(db)
    assert as_owner(db, "SELECT parameters FROM model_catalog WHERE id = :m", {"m": model}) == [(None,)]
    as_owner(db, _SET, {"p": ["max_tokens", "temperature"], "m": model})

    base._downgrade(url, "0094")
    assert _state(db) == at_0094

    migrate(url, "0095")
    assert _state(db) == at_0095
    # The column comes back empty: the next refresh writes it again.
    assert as_owner(db, "SELECT parameters FROM model_catalog WHERE id = :m", {"m": model}) == [(None,)]


@pytest.mark.parametrize(
    "parameters",
    [
        ["Temperature"],
        ["top p"],
        ["temperature", None],
        [""],
        ["temperature,top_p"],
        ["a" * 65],
        [f"p{n}" for n in range(65)],
    ],
)
def test_the_check_refuses_a_name_of_the_wrong_shape_or_too_many(
    db: Database, parameters: list[object]
) -> None:
    model = catalog_model(db)
    with pytest.raises(DBAPIError) as refused:
        as_owner(db, _SET, {"p": parameters, "m": model})
    assert violation(refused.value) == ("23514", "ai_model_catalog_parameters")


def test_the_app_writes_the_set_and_the_operator_sees_it(db: Database, platform: str) -> None:
    model = catalog_model(db)
    with db.system() as conn:  # the app role, as the refresh writes
        conn.execute(text(_SET), {"p": ["max_tokens", "reasoning", "temperature"], "m": model})
    with OperatorDatabase(db.engine).operator() as conn:
        [row] = [row for row in ops.list_models(conn, platform) if row["id"] == model]
    assert row["parameters"] == ["max_tokens", "reasoning", "temperature"]
    with db.system() as conn:
        conn.execute(text(_SET), {"p": [], "m": model})  # an empty set is a set: none listed
    assert as_owner(db, "SELECT parameters FROM model_catalog WHERE id = :m", {"m": model}) == [([],)]
