"""The Numbers tab's reader pages and spend estimate (PRD 5 U9, V5): this month's pages by
who read them (the reading model, the backup reader, the page cache) and read again, and
the tokens billed; the dollar figure is null while MODEL_PRICE_* are 0 (not known).
Another customer's runs: ``test_isolation_admin_reader_numbers.py``."""

from datetime import UTC, datetime

import pytest

from kaagaz.api.routers.admin import spend_usd
from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from kaagaz.pipeline import FinishedRun
from tests.settings_factory import make_settings
from tests.test_api_support import Api, MovingClock
from tests.test_runs_db import totals


def test_v5_the_spend_is_an_estimate_from_tokens_and_null_without_prices() -> None:
    priced = make_settings(model_price_input_usd_per_m=2.0, model_price_output_usd_per_m=12.0)
    assert spend_usd(priced, input_tokens=1_500_000, output_tokens=250_000) == 6.0
    assert spend_usd(priced, input_tokens=0, output_tokens=0) == 0.0
    assert spend_usd(make_settings(), input_tokens=1_500_000, output_tokens=250_000) is None
    half = make_settings(model_price_input_usd_per_m=2.0)
    assert spend_usd(half, input_tokens=1, output_tokens=1) is None  # one price not known


def _run(api: Api, customer_id: str, started: datetime, **counts: int) -> None:
    document_id = api.add_document(customer_id)
    run = PgRuns(api.db).start(customer_id, document_id, now=started)
    PgRuns(api.db).finish(customer_id, FinishedRun(run, totals("ready", **counts), started))


@pytest.mark.db
def test_u9_the_reader_pages_and_tokens_are_this_months(db: Database) -> None:
    settings = make_settings(model_price_input_usd_per_m=2.0, model_price_output_usd_per_m=12.0)
    api = Api(db, settings=settings, clock=MovingClock(datetime(2026, 10, 20, 6, 0, tzinfo=UTC)))
    admin = api.new_admin()
    c = admin.customer_id
    this_month = datetime(2026, 10, 2, tzinfo=UTC)
    _run(api, c, this_month, pages_gemini=4, pages_cached=2, input_tokens=1_000_000, output_tokens=100_000)
    _run(api, c, this_month, pages_local=3, pages_read_again=1, pages_gemini=1, input_tokens=500_000)
    _run(api, c, datetime(2026, 9, 29, tzinfo=UTC), pages_gemini=50, input_tokens=9_000_000)  # last month

    stats = admin.client.get("/api/admin/stats").json()

    assert stats["reader_pages"] == {"gemini": 5, "local": 3, "cached": 2, "read_again": 1}
    assert stats["spend_estimate"] == {"input_tokens": 1_500_000, "output_tokens": 100_000, "usd": 4.2}
