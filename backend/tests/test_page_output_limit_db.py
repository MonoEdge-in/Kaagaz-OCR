"""A page read's output limit against PostgreSQL (#463, Tushar, 31 Oct 2026; 0064): it
inherits organisation > platform > PAGE_MAX_OUTPUT_TOKENS, is saved only by the operator with a
fresh code and audited before and after, is refused outside 1024 to 65536, reaches every model
call of a scan through the pipeline and an accuracy check's draft reads (never its max reads),
and the estimate's ``tokens`` block is measured from the ledger.

Every test that saves the platform defaults puts them back (the ``platform`` fixture)."""

from typing import Any

import pytest

from kaagaz.ai.openrouter import PAGE_MAX_OUTPUT_TOKENS
from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database
from kaagaz.pipeline import JobReading
from tests.db_support import new_customer
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_accuracy_check_db import PageImages, Reads, model_page, old_checks, run, start
from tests.test_api_support import CSRF, Api, wrong_code
from tests.test_isolation_ai_settings import platform, set_defaults, set_org
from tests.test_migration_0023_db import as_owner
from tests.test_operator_auth_db import _owner
from tests.test_operator_models_db import FOLLOWS_DEFAULTS, _save, _signed_in
from tests.test_pipeline_gemini import SHARP, FakeChecker, rig
from tests.test_scan_cost_settings_db import SendsClient, _engine, reading_of, scan_model

pytestmark = pytest.mark.db
__all__ = ["old_checks", "platform"]  # the fixtures, used by name
GEMINI = make_settings(ocr_engine="gemini")
_USAGE = (
    "INSERT INTO model_usage (customer_id, kind, model, cached, calls, pages, input_tokens, output_tokens,"
    " reasoning_tokens, cost_usd, cost_estimated, thinking, resolution, limit_hits)"
    " VALUES (:c, 'scan', :m, :cached, :calls, :calls, :tin, :tout, 0, NULL, false, :t, 'high', :hits)"
)


class LimitedSends(SendsClient):
    """``SendsClient``, noting each call's output limit too."""

    def __init__(self) -> None:
        super().__init__()
        self.limits: list[int | None] = []

    def read_page(self, *, max_output_tokens: int | None = None, **request: Any) -> Any:
        self.limits.append(max_output_tokens)
        return super().read_page(**request)


def test_the_limit_inherits_and_is_saved_with_a_fresh_code_and_audited(db: Database, platform: str) -> None:
    lead = scan_model(db)
    own, inherits = new_customer(db)[0], new_customer(db)[0]
    assert reading_of(db, inherits).page_max_output_tokens is None  # PAGE_MAX_OUTPUT_TOKENS
    assert set_org(db, platform, own, ops.AiChoice([lead], lead, page_max_output_tokens=8192)).code == "done"
    for refused in (512, 70000):
        choice = ops.AiChoice([lead], lead, page_max_output_tokens=refused)
        assert set_org(db, platform, own, choice).code == "limit_out_of_range"
        on_platform = ops.AiChoice(
            chat_levels=["low"], chat_default_level="low", dynamic=False, page_max_output_tokens=refused
        )
        assert set_defaults(db, platform, on_platform) == "limit_out_of_range"
    defaults = ops.AiChoice(
        chat_levels=["low"], chat_default_level="low", dynamic=False, page_max_output_tokens=16384
    )
    assert set_defaults(db, platform, defaults) == "done"
    assert reading_of(db, inherits).page_max_output_tokens == 16384  # the platform's
    assert reading_of(db, own).page_max_output_tokens == 8192  # its own
    # Changed on purpose (#463 review, X1): only the operator's own audit holds the limit.
    [(details,)] = as_owner(
        db,
        "SELECT details FROM audit_log WHERE customer_id = :c AND action = 'org.ai_settings_set'"
        " ORDER BY created_at DESC LIMIT 1",
        {"c": own},
    )
    assert "page_max_output_tokens" not in details
    [(operator_details,)] = as_owner(
        db,
        "SELECT details FROM platform_audit WHERE subject_customer_id = :c AND action = 'org.ai_settings_set'"
        " ORDER BY id DESC LIMIT 1",
        {"c": own},
    )
    assert operator_details["page_max_output_tokens"] == 8192
    [(saved,)] = as_owner(
        db,
        "SELECT details FROM platform_audit WHERE operator_id = :o AND action = 'platform.ai_defaults_set'"
        " ORDER BY id DESC LIMIT 1",
        {"o": platform},
    )
    assert (saved["before"]["page_max_output_tokens"], saved["after"]["page_max_output_tokens"]) == (
        None,
        16384,
    )
    assert saved["orgs_changed"]["page_max_output_tokens"] >= 1


def test_the_route_saves_it_only_with_a_fresh_code_and_refuses_a_limit_out_of_range(db: Database) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    customer = api.customer("Limit Route Org")[0]
    path = f"/api/operator/orgs/{customer}/ai"
    body = {**FOLLOWS_DEFAULTS, "page_max_output_tokens": 8192, "ensemble": False, "ai_budget_usd": None}
    stale = client.put(path, json={**body, "code": wrong_code(api, operator.secret)}, headers=CSRF)
    assert (stale.status_code, stale.json()["error"]["code"]) == (403, "step_up_failed")
    out = _save(api, operator, client, path, {**body, "page_max_output_tokens": 100})
    assert (out.status_code, out.json()["error"]["code"]) == (400, "limit_out_of_range")
    saved = _save(api, operator, client, path, body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["own"]["page_max_output_tokens"] == 8192
    assert saved.json()["env"]["page_max_output_tokens"] == PAGE_MAX_OUTPUT_TOKENS
    for bad in ({"page_max_output_tokens": 70000}, {"customer_id": customer, "page_max_output_tokens": 1000}):
        refused = client.post("/api/operator/scan-cost", json=bad, headers=CSRF)
        assert (refused.status_code, refused.json()["error"]["code"]) == (400, "limit_out_of_range")


def test_a_scan_sends_the_organisations_limit_on_every_read(db: Database, platform: str) -> None:
    lead = scan_model(db)
    customer = new_customer(db)[0]
    choice = ops.AiChoice([lead], lead, scan_thinking="minimal", dynamic=True, page_max_output_tokens=4096)
    assert set_org(db, platform, customer, choice).code == "done"
    reading = reading_of(db, customer, mode="dynamic")
    sends = LimitedSends()
    system = rig(
        _engine(sends, GEMINI),
        checker=FakeChecker("Total: 1,180.00"),
        ocr_model=lead,
        settings=GEMINI,
        job_reading=lambda customer, document: JobReading(None, lead, "google-vertex", reading=reading),
    )
    system.run(make_pdf([SHARP]))
    assert len(sends.limits) == 2 and set(sends.limits) == {4096}  # the first read and Careful's


def test_a_checks_draft_reads_take_its_limit_and_its_max_reads_the_most(
    db: Database, platform: str, old_checks: None
) -> None:
    api = Api(db, settings=GEMINI)
    operator, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("Limit Check Org")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead, scan_thinking="minimal")).code == "done"
    images = PageImages()
    model_page(db, images, customer, read_by=lead)
    started = start(api, operator, client, customer, scan_thinking="minimal", page_max_output_tokens=2048)
    assert started.status_code == 202, started.text
    reads = LimitedReads()
    run(db, api, images, reads)
    assert sorted(zip(reads.levels, reads.limits, strict=True)) == [("high", None), ("minimal", 2048)]
    done = client.get(f"/api/operator/orgs/{customer}/accuracy-check/latest").json()
    assert done["settings"]["page_max_output_tokens"] == 2048


class LimitedReads(Reads):
    """``Reads``, noting each call's thinking level and output limit (None: the most)."""

    def __init__(self) -> None:
        super().__init__()
        self.levels: list[str] = []
        self.limits: list[int | None] = []

    def read_page(self, *, max_output_tokens: int | None = None, **request: Any) -> Any:
        self.levels.append(request["thinking_level"])
        self.limits.append(max_output_tokens)
        return super().read_page(**request)


def test_the_estimates_tokens_are_measured(db: Database, platform: str) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    lead = scan_model(db)
    customer, other = api.customer("Tokens Org")[0], api.customer("Tokens Other")[0]
    assert (
        set_org(db, platform, customer, ops.AiChoice([lead], lead, page_max_output_tokens=8192)).code
        == "done"
    )
    # Changed on purpose (#463 review, P2): every figure is the organisation's own, a page once.
    rows = (
        (customer, 4, 4000, 2000, 1, "low", False),
        (other, 6, 9000, 6000, 2, "low", False),  # another organisation's: never
        (customer, 50, 1, 1, 0, "low", True),  # cached: never
        (customer, 50, 1, 1, 9, "high", False),  # another level: never
    )
    for owner, calls, tokens_in, tokens_out, hits, level, cached in rows:
        params = {
            "c": owner,
            "m": lead,
            "cached": cached,
            "calls": calls,
            "tin": tokens_in,
            "tout": tokens_out,
        }
        _owner(db, _USAGE, {**params, "t": level, "hits": hits})
    body = {"customer_id": customer, "scan_thinking": "low", "page_max_output_tokens": 8192}
    tokens = client.post("/api/operator/scan-cost", json=body, headers=CSRF).json()["tokens"]
    assert tokens == {
        "input_per_page": 1000,
        "output_per_page": 500,
        "output_p99": None,
        "limit": 8192,
        "limit_hits": 1,
        "pages": 4,
    }
    empty = client.post("/api/operator/scan-cost", json={**body, "scan_resolution": "low"}, headers=CSRF)
    assert empty.json()["tokens"] == {
        "input_per_page": None,
        "output_per_page": None,
        "output_p99": None,
        "limit": 8192,
        "limit_hits": 0,
        "pages": 0,
    }
    inherited = client.post("/api/operator/scan-cost", json={"customer_id": customer}, headers=CSRF).json()
    assert inherited["tokens"]["limit"] == PAGE_MAX_OUTPUT_TOKENS  # null: what the save would inherit
