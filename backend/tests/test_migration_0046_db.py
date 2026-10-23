"""The shared OpenRouter scheduler and RPM settings migration, up and down."""

import pytest

from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate

pytestmark = pytest.mark.db
fresh = base.fresh


def test_0046_up_down_up(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0045")
    at_0045 = base._schema(db)
    migrate(url, "head")
    at_head = base._schema(db)
    assert {
        "model_rate_scheduler",
        "model_rate_waiters",
        "model_rate_overrides",
        "user_model_rate_overrides",
        "model_rate_state",
    } <= base._names(at_head, "relations")
    assert {"kaagaz_model_rate_slot", "kaagaz_op_model_rates", "kaagaz_op_set_model_rate"} <= base._names(
        at_head, "functions"
    )
    base._downgrade(url, "0045")
    assert base._schema(db) == at_0045
    migrate(url, "head")
    assert base._schema(db) == at_head
