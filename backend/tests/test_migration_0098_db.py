"""Migration 0098 (#603 W6): the operator's daily spend counts WhatsApp's voice notes
(``transcribe``) as question spend. Up, down (0097's body exactly) and up again, on a fresh
database; and a day's ``transcribe`` dollars land in the question spend."""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db import operator_insights as insights
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_operator_insights_db import at, new_model, of, operator, read, usage, window_day

pytestmark = pytest.mark.db
fresh = base.fresh

_DAILY = "pg_get_functiondef('public.kaagaz_op_daily(uuid, date, date, text)'::regprocedure)"
OLD = "'chat', 'memory', 'search', 'ensemble_chat'"
NEW = "'chat', 'memory', 'search', 'ensemble_chat', 'transcribe'"


def _state(db: Database) -> dict[str, Any]:
    """The schema, plus the daily function's body (``base._schema`` leaves bodies out)."""
    with db.system() as conn:
        daily = conn.execute(text(f"SELECT {_DAILY}")).scalar_one()
    return {**base._schema(db), "daily": daily}


def test_0098_up_down_up_counts_transcribe_as_question_spend(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0097")
    at_0097 = _state(db)
    assert at_0097["daily"].count(OLD) == 1 and "transcribe" not in at_0097["daily"]
    migrate(url, "0098")
    at_0098 = _state(db)
    assert at_0098["daily"].count(NEW) == 1
    assert at_0098["daily"].replace(NEW, OLD, 1) == at_0097["daily"]
    assert {k: v for k, v in at_0098.items() if k != "daily"} == {
        k: v for k, v in at_0097.items() if k != "daily"
    }
    base._downgrade(url, "0097")
    assert _state(db) == at_0097
    migrate(url, "0098")
    assert _state(db) == at_0098


def test_a_days_voice_notes_are_question_spend(db: Database) -> None:
    """The head's daily numbers: a voice note's ``transcribe`` dollars are question spend, with the
    answer's, as 0026's ``ensemble_chat`` became (tests/test_migration_0026_db.py)."""
    op, day = operator(db), window_day()
    customer, _ = new_customer(db)
    chat = new_model()
    usage(db, customer, at(day), "chat", chat, 1, "0.02")
    usage(db, customer, at(day), "transcribe", chat, 1, "0.0007")
    [daily] = of(read(db, op, insights.daily, day, day, "UTC"), customer)
    assert daily["cost_questions_usd"] == Decimal("0.0207")
    assert daily["cost_usd"] == Decimal("0.0207")
