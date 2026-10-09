"""The Numbers tab's reader pages and spend estimate across customers (PRD 5 U9, V5): each
admin's figures come from its own customer's runs only, whatever another customer read in
the same month; each sees its own (the control)."""

from datetime import UTC, datetime

import pytest

from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from kaagaz.pipeline import FinishedRun
from tests.settings_factory import make_settings
from tests.test_api_support import Api, MovingClock
from tests.test_runs_db import totals

pytestmark = pytest.mark.db


def _run(api: Api, customer_id: str, started: datetime, **counts: int) -> None:
    document_id = api.add_document(customer_id)
    run = PgRuns(api.db).start(customer_id, document_id, now=started)
    PgRuns(api.db).finish(customer_id, FinishedRun(run, totals("ready", **counts), started))


def test_u9_reader_pages_and_spend_never_cross_customers(db: Database) -> None:
    settings = make_settings(model_price_input_usd_per_m=2.0, model_price_output_usd_per_m=12.0)
    api = Api(db, settings=settings, clock=MovingClock(datetime(2026, 10, 20, 6, 0, tzinfo=UTC)))
    a, b = api.new_admin(), api.new_admin()
    this_month = datetime(2026, 10, 2, tzinfo=UTC)
    _run(api, a.customer_id, this_month, pages_gemini=4, pages_local=1, input_tokens=1_000_000)
    _run(api, b.customer_id, this_month, pages_gemini=70, pages_cached=9, output_tokens=7_000_000)

    a_stats = a.client.get("/api/admin/stats").json()
    b_stats = b.client.get("/api/admin/stats").json()

    assert a_stats["reader_pages"] == {"gemini": 4, "local": 1, "cached": 0, "read_again": 0}
    assert a_stats["spend_estimate"] == {"input_tokens": 1_000_000, "output_tokens": 0, "usd": 2.0}
    assert b_stats["reader_pages"] == {"gemini": 70, "local": 0, "cached": 9, "read_again": 0}
    assert b_stats["spend_estimate"] == {"input_tokens": 0, "output_tokens": 7_000_000, "usd": 84.0}
