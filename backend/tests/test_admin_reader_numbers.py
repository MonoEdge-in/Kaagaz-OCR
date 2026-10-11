"""The Numbers tab's reader pages and spend (PRD 5 U9; model choice, council 11 Oct 2026,
SP1): this month's pages by who read them (the reading model, the backup reader, the page
cache) and read again, the scan tokens billed, and this month's dollars from the AI spend
ledger (every kind of work), no longer a price set in .env.
Another customer's runs and spend: ``test_isolation_admin_reader_numbers.py``."""

from datetime import UTC, datetime

import pytest

from kaagaz.db.engine import Database
from kaagaz.db.runs import PgRuns
from kaagaz.pipeline import FinishedRun
from tests.test_api_support import Api, MovingClock
from tests.test_api_usage import add_spend
from tests.test_runs_db import totals


def _run(api: Api, customer_id: str, started: datetime, **counts: int) -> None:
    document_id = api.add_document(customer_id)
    run = PgRuns(api.db).start(customer_id, document_id, now=started)
    PgRuns(api.db).finish(customer_id, FinishedRun(run, totals("ready", **counts), started))


@pytest.mark.db
def test_u9_sp1_the_reader_pages_tokens_and_spend_are_this_months(db: Database) -> None:
    api = Api(db, clock=MovingClock(datetime(2026, 10, 20, 6, 0, tzinfo=UTC)))
    admin = api.new_admin()
    c = admin.customer_id
    this_month = datetime(2026, 10, 2, tzinfo=UTC)
    _run(api, c, this_month, pages_gemini=4, pages_cached=2, input_tokens=1_000_000, output_tokens=100_000)
    _run(api, c, this_month, pages_local=3, pages_read_again=1, pages_gemini=1, input_tokens=500_000)
    _run(api, c, datetime(2026, 9, 29, tzinfo=UTC), pages_gemini=50, input_tokens=9_000_000)  # last month
    add_spend(db, c, at=this_month, cost=4.0)
    add_spend(db, c, at=this_month, cost=0.204, kind="chat")
    add_spend(db, c, at=this_month, cost=None, kind="embed")  # not known: adds nothing
    add_spend(db, c, at=datetime(2026, 9, 29, tzinfo=UTC), cost=90.0)  # last month

    stats = admin.client.get("/api/admin/stats").json()

    assert stats["reader_pages"] == {"gemini": 5, "local": 3, "cached": 2, "read_again": 1}
    assert stats["spend_estimate"] == {"input_tokens": 1_500_000, "output_tokens": 100_000, "usd": 4.2}
