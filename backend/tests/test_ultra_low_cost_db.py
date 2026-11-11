"""Ultra low cost with the database (#488, Tushar, 4 Nov 2026): the operator's API saves and shows
the reader and the ceiling (400 ``ceiling_out_of_range`` and 409 ``ulc_needs_gemini`` before a
code is used), an organisation that chose it reads with it and one that did not reads as before,
``kaagaz_op_ulc_cost`` measures what it really cost (OpenRouter's reported cost of the
``ulc_scan`` ledger rows, never cached, every limit hit counted), the estimate shows it, and the
catalogue's real cost counts its pages, and so do the Overview, the model board and the test model's
cost (0070's SPEND). Every test that saves the platform defaults puts them back
(the ``platform`` fixture)."""

import dataclasses
import logging
from decimal import Decimal

import pytest
from sqlalchemy import text

from kaagaz.db import models
from kaagaz.db import operator_insights as insights
from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.settings import Settings
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api
from tests.test_isolation_ai_settings import NO_DEFAULTS, platform, set_defaults, set_org
from tests.test_migration_0023_db import as_owner
from tests.test_operator_insights_db import at, of, one, read, usage, window_day
from tests.test_operator_models_db import FOLLOWS_DEFAULTS, _save, _signed_in
from tests.test_scan_cost_settings_db import SETTINGS, reading_of, scan_model

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name
ORG_BODY = {**FOLLOWS_DEFAULTS, "ensemble": False, "ai_budget_usd": None}
NOT_IN_CATALOGUE = make_settings(ocr_model="google/not-in-the-catalogue")  # OCR_MODEL with no row
PLATFORM_BODY = {**FOLLOWS_DEFAULTS, "chat_levels": ["low"], "chat_default_level": "low", "dynamic": False}
CEILING = {
    "code": "ceiling_out_of_range",
    "message": "Choose from ₹0.20 to ₹0.55 a page, or leave it empty.",  # Changed on purpose (#488, 0073)
}
NEEDS_GEMINI = {
    "code": "ulc_needs_gemini",
    "message": "Ultra low cost needs a Gemini model with a known price that can read with minimal thinking.",
}
NO_PRICE = "Its price isn't known yet. Refresh the model list."
_LEDGER = (
    "INSERT INTO model_usage (customer_id, document_id, kind, model, cached, calls, pages, input_tokens,"
    " output_tokens, reasoning_tokens, cost_usd, cost_estimated, limit_hits, created_at)"
    " VALUES (:c, :d, :kind, :m, :cached, :pages, :pages, 2500, 1200, 0, :cost, :estimated, :hits,"
    " now() - make_interval(days => :days))"
)


def ledger(
    db: Database,
    customer: str,
    document: str | None,
    model: str,
    *,
    pages: int,
    cost: str | None,
    hits: int = 0,
    kind: str = "ulc_scan",
    cached: bool = False,
    estimated: bool = False,
    days: int = 0,
) -> None:
    params = {"c": customer, "d": document, "kind": kind, "m": model, "cached": cached, "pages": pages}
    as_owner(db, _LEDGER, {**params, "cost": cost, "estimated": estimated, "hits": hits, "days": days})


def _document(db: Database, customer: str) -> str:
    with db.customer(customer) as conn:
        return insert_document(conn, customer)


# --- the saves ----------------------------------------------------------------------------


def test_a_ceiling_outside_20_to_55_paise_is_refused_before_a_code_is_used(
    db: Database, platform: str
) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    customer = api.customer("ULC Ceiling")[0]
    for path, body in (
        (f"/api/operator/orgs/{customer}/ai", ORG_BODY),
        ("/api/operator/ai-defaults", PLATFORM_BODY),
    ):
        for bad in (0, 19, 56, 4000):  # Changed on purpose (#488, 0073): up to 55
            refused = client.put(
                path, json={**body, "ulc_ceiling_paise": bad, "code": "000000"}, headers=CSRF
            )
            assert (refused.status_code, refused.json()["error"]) == (400, CEILING), refused.text
        for wrong in (0.3, "30", True):  # paise, a whole number
            reply = client.put(
                path, json={**body, "ulc_ceiling_paise": wrong, "code": "000000"}, headers=CSRF
            )
            assert (reply.status_code, reply.json()["error"]["code"]) == (400, "bad_request")
    saved = _save(
        api, operator, client, f"/api/operator/orgs/{customer}/ai", {**ORG_BODY, "ulc_ceiling_paise": 25}
    )
    assert saved.status_code == 200, saved.text  # a ceiling may be kept while Gemini reads
    got = client.get(f"/api/operator/orgs/{customer}/ai").json()
    assert (got["own"]["ulc_ceiling_paise"], got["defaults"]["ulc_ceiling_paise"]) == (25, None)
    # #488 (0073): the highest, 55 paise, is taken.
    highest = {**ORG_BODY, "ulc_ceiling_paise": 55}
    saved = _save(api, operator, client, f"/api/operator/orgs/{customer}/ai", highest)
    assert saved.status_code == 200, saved.text
    assert client.get(f"/api/operator/orgs/{customer}/ai").json()["own"]["ulc_ceiling_paise"] == 55


def test_it_needs_a_gemini_with_a_price_that_takes_minimal(db: Database, platform: str) -> None:
    api = Api(db, settings=NOT_IN_CATALOGUE)
    operator, client = _signed_in(api)
    customer = api.customer("ULC Gemini")[0]
    path = f"/api/operator/orgs/{customer}/ai"
    no_minimal = scan_model(db, ("low", "high"))
    unknown_call = scan_model(db)  # #488 review (R3): no price a call known (no catalogue refresh yet)
    as_owner(db, "UPDATE model_catalog SET price_image_usd = NULL WHERE id = :m", {"m": unknown_call})
    minimal = scan_model(db)
    for lead, expected in ((no_minimal, 409), (unknown_call, 409), (minimal, 200)):
        body = {**ORG_BODY, "scan_models": [lead], "scan_default": lead, "scan_reader": "ultra_low_cost"}
        reply = _save(api, operator, client, path, body)
        assert reply.status_code == expected, reply.text
        if expected == 409:  # the default scan model's reason, after the rule
            name = next(
                m["name"] for m in client.get("/api/operator/models").json()["items"] if m["id"] == lead
            )
            why = "It can't read with minimal thinking." if lead == no_minimal else NO_PRICE
            message = f"{NEEDS_GEMINI['message']} The default scan model ({name}): {why}"
            assert reply.json()["error"] == {**NEEDS_GEMINI, "message": message}
            assert client.get(path).json()["own"]["scan_reader"] is None  # nothing saved
    got = client.get(path).json()["own"]
    assert (got["scan_reader"], got["ulc_ceiling_paise"]) == ("ultra_low_cost", None)
    # The catalogue says which can lead it, and why not, so the operator can choose.
    listed = {m["id"]: m for m in client.get("/api/operator/models").json()["items"]}
    assert [(listed[m]["ulc_ready"], listed[m]["ulc_why"]) for m in (no_minimal, unknown_call, minimal)] == [
        (False, "It can't read with minimal thinking."),
        (False, NO_PRICE),
        (True, None),
    ]
    # No Gemini of its own and OCR_MODEL not in the catalogue: refused, on the platform too.
    for where, body in ((path, ORG_BODY), ("/api/operator/ai-defaults", PLATFORM_BODY)):
        reply = _save(api, operator, client, where, {**body, "scan_reader": "ultra_low_cost"})
        assert (reply.status_code, reply.json()["error"]) == (409, NEEDS_GEMINI), where
    # Gemini or Mistral as before need nothing of it.
    assert _save(api, operator, client, path, {**ORG_BODY, "scan_reader": "openrouter"}).status_code == 200


def test_only_the_organisation_that_chose_it_reads_with_it(db: Database, platform: str) -> None:
    lead = scan_model(db)
    chosen, other = new_customer(db)[0], new_customer(db)[0]
    for customer in (chosen, other):
        assert set_org(db, platform, customer, ops.AiChoice([lead], lead, dynamic=True)).code == "done"
    before = reading_of(db, other, mode="dynamic")
    ulc = ops.AiChoice([lead], lead, dynamic=True, scan_reader="ultra_low_cost", ulc_ceiling_paise=22)
    assert set_org(db, platform, chosen, ulc).code == "done"
    reading = reading_of(db, chosen, mode="dynamic")
    assert (reading.mode, reading.lead.model, reading.thinking, reading.careful) == (
        "single",
        lead,
        "minimal",
        None,
    )
    assert reading.ulc is not None and reading.ulc.paise == 22
    assert reading_of(db, other, mode="dynamic") == before and before.ulc is None


# --- the measured cost ----------------------------------------------------------------------


def test_the_ulc_cost_is_the_reported_cost_of_its_pages_with_the_limit_hits(
    db: Database, platform: str
) -> None:
    lead = scan_model(db)
    customer, other = new_customer(db)[0], new_customer(db)[0]
    cheap, dear, elsewhere = _document(db, customer), _document(db, customer), _document(db, other)
    ledger(db, customer, cheap, lead, pages=10, cost="0.020", hits=1)
    ledger(db, customer, dear, lead, pages=2, cost="0.009")
    ledger(db, other, elsewhere, lead, pages=8, cost="0.024", hits=2)
    # Never counted: cached, a guessed cost, none reported, another kind or model, too old.
    ledger(db, customer, cheap, lead, pages=50, cost="0", cached=True)
    ledger(db, customer, cheap, lead, pages=50, cost="9", estimated=True)
    ledger(db, customer, cheap, lead, pages=50, cost=None, hits=50)
    ledger(db, customer, cheap, lead, pages=50, cost="9", kind="scan", hits=50)
    ledger(db, customer, cheap, "google/another", pages=50, cost="9", hits=50)
    ledger(db, customer, cheap, lead, pages=50, cost="9", days=31, hits=50)
    with OperatorDatabase(db.engine).operator() as conn:
        one = ops.ulc_cost(conn, platform, customer, lead)
        every = ops.ulc_cost(conn, platform, None, lead)
        stranger = ops.ulc_cost(conn, "00000000-0000-0000-0000-000000000000", None, lead)
    assert one is not None and (one["pages"], float(one["cost_usd"]), one["limit_hits"]) == (12, 0.029, 1)
    assert float(one["top_usd_per_page"]) == pytest.approx(0.0045)  # the dearer document
    assert every is not None and (every["pages"], float(every["cost_usd"]), every["limit_hits"]) == (
        20,
        0.053,
        3,
    )
    assert stranger is None  # no row for an operator who is not enabled
    with OperatorDatabase(db.engine).operator() as conn:
        none = ops.ulc_cost(conn, platform, new_customer(db)[0], lead)
    assert none is not None and (
        none["pages"],
        none["cost_usd"],
        none["limit_hits"],
        none["top_usd_per_page"],
    ) == (
        0,
        None,
        0,
        None,
    )


def test_the_app_cannot_run_the_ulc_cost(db: Database) -> None:
    customer, _ = new_customer(db)
    with pytest.raises(Exception) as refused, db.customer(customer) as conn:
        conn.execute(text("SELECT * FROM kaagaz_op_ulc_cost(gen_random_uuid(), NULL, 'google/x')"))
    assert "permission denied" in str(refused.value)


def test_the_estimate_shows_the_ceiling_and_what_it_measured(db: Database, platform: str) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("ULC Estimate")[0]
    ulc = ops.AiChoice([lead], lead, scan_reader="ultra_low_cost", ulc_ceiling_paise=35)
    assert set_org(db, platform, customer, ulc).code == "done"
    # The console sends the draft's own values (a null one is what the save would inherit).
    saved = {"customer_id": customer, "scan_reader": "ultra_low_cost", "ulc_ceiling_paise": 35}
    empty = client.post("/api/operator/scan-cost", json=saved, headers=CSRF)
    assert empty.status_code == 200, empty.text
    assert empty.json()["ulc"] == {
        "ceiling_paise": 35,
        "ceiling_usd": pytest.approx(0.35 / 88),
        "pages": 0,
        "usd_per_page": None,
        "top_usd_per_page": None,
        "limit_hits": 0,
    }
    document = _document(db, customer)
    ledger(db, customer, document, lead, pages=4, cost="0.012", hits=1)
    draft = {**saved, "ulc_ceiling_paise": 20}  # the draft's own ceiling
    measured = client.post("/api/operator/scan-cost", json=draft, headers=CSRF).json()["ulc"]
    assert measured == {
        "ceiling_paise": 20,
        "ceiling_usd": pytest.approx(0.20 / 88),
        "pages": 4,
        "usd_per_page": pytest.approx(0.003),
        "top_usd_per_page": pytest.approx(0.003),
        "limit_hits": 1,
    }
    off = {**saved, "scan_reader": "openrouter"}  # the draft switches it off
    assert client.post("/api/operator/scan-cost", json=off, headers=CSRF).json()["ulc"] is None
    bad = client.post("/api/operator/scan-cost", json={**draft, "ulc_ceiling_paise": 56}, headers=CSRF)
    assert (bad.status_code, bad.json()["error"]) == (400, CEILING)


def test_the_catalogues_real_cost_counts_its_pages(db: Database, platform: str) -> None:
    lead = scan_model(db)
    customer = new_customer(db)[0]
    ledger(db, customer, _document(db, customer), lead, pages=3, cost="0.009")
    with OperatorDatabase(db.engine).operator() as conn:
        [listed] = [row for row in ops.list_models(conn, platform) if row["id"] == lead]
    assert (listed["scan_pages"], float(listed["scan_cost_usd"])) == (3, 0.009)


def test_the_overview_the_model_board_and_the_test_models_cost_count_its_pages(
    db: Database, platform: str
) -> None:
    day, lead, customer = window_day(), scan_model(db), new_customer(db)[0]
    usage(db, customer, at(day), "ulc_scan", lead, 4, "0.012")
    usage(db, customer, at(day), "ulc_scan", lead, 2, "0", cached=True)
    [daily] = of(read(db, platform, insights.daily, day, day, "UTC"), customer)
    assert (daily["pages_read"], daily["pages_read_costed"], daily["pages_reread"]) == (4, 4, 0)
    assert (daily["scan_pages"], daily["cached_pages"], daily["scan_unknown_calls"]) == (4, 2, 0)
    spend = ("cost_scan_usd", "cost_documents_usd", "cost_scan_real_usd", "cost_real_usd")
    assert [daily[name] for name in spend] == [Decimal("0.012")] * 4
    board = one(read(db, platform, insights.model_board, day, day, "UTC"), "model", lead)
    assert (board["scan_calls"], board["cached_scan_calls"], board["scan_cost_usd"]) == (
        6,
        2,
        Decimal("0.012"),
    )
    as_owner(
        db,
        "INSERT INTO platform_model_override (id, scan_model, started_at) VALUES (true, :m, :t)"
        " ON CONFLICT (id) DO UPDATE SET scan_model = EXCLUDED.scan_model, chat_model = NULL,"
        " started_at = EXCLUDED.started_at",
        {"m": lead, "t": at(day, -1)},
    )
    try:
        with OperatorDatabase(db.engine).operator() as conn:
            tested = ops.current_test_model(conn, platform)
    finally:
        as_owner(db, "DELETE FROM platform_model_override")
    assert tested is not None and (tested["pages"], tested["cost_usd"]) == (4, Decimal("0.012"))


# --- promotion: production's .env switch (#564 [V]) -----------------------------------------


def test_promotion_the_env_switch_is_the_platform_default_and_a_saved_reader_wins(
    db: Database, platform: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Production turns Ultra low cost on with OCR_ULTRA_LOW_COST and OCR_PAGE_CEILING_PAISE
    (65ee12e3). Here, while neither the platform nor the organisation saved a reader, the switch
    reads Ultra low cost at that ceiling (a saved ceiling first), read at use and logged once; a
    reader the super admin saved always wins. Off, nothing reads differently."""
    caplog.set_level(logging.INFO, logger="kaagaz.db.models")
    models._env_ulc_noted.cache_clear()
    switched = dataclasses.replace(SETTINGS, ocr_ultra_low_cost=True, ocr_page_ceiling_paise=50)
    lead = scan_model(db)
    follows, chose, ceiling = new_customer(db)[0], new_customer(db)[0], new_customer(db)[0]
    for customer in (follows, chose, ceiling):
        assert set_org(db, platform, customer, ops.AiChoice([lead], lead, dynamic=True)).code == "done"
    gemini = ops.AiChoice([lead], lead, dynamic=True, scan_reader="openrouter")
    assert set_org(db, platform, chose, gemini).code == "done"
    assert set_org(db, platform, ceiling, ops.AiChoice([lead], lead, ulc_ceiling_paise=22)).code == "done"

    def resolved(customer: str, settings: Settings) -> models.Reading:
        with db.customer(customer) as conn:
            return models.resolve_scan(conn, customer, insert_document(conn, customer), settings=settings)

    assert resolved(follows, SETTINGS).ulc is None  # off: as before
    on = resolved(follows, switched)
    assert (on.mode, on.lead.model, on.thinking, on.notes, on.careful) == (
        "single",
        lead,
        "minimal",
        ("ultra_low_cost",),
        None,
    )
    assert on.ulc is not None and on.ulc.paise == 50
    saved_ceiling = resolved(ceiling, switched).ulc
    assert saved_ceiling is not None and saved_ceiling.paise == 22
    assert resolved(chose, switched).ulc is None  # the organisation's saved reader wins
    assert [r.getMessage() for r in caplog.records].count("ultra low cost from OCR_ULTRA_LOW_COST") == 1

    for settings, server in ((switched, ("ultra_low_cost", 40, 50)), (SETTINGS, ("openrouter", 40, None))):
        _, client = _signed_in(Api(db, settings=settings))
        env = client.get("/api/operator/ai-defaults").json()["env"]  # the super admin's "server's"
        assert (env["scan_reader"], env["ulc_ceiling_paise"], env["switch_ceiling_paise"]) == server
        # The platform draft's estimate: a null reader and ceiling are the server's.
        draft = {"scan_reader": None, "ulc_ceiling_paise": None}
        estimate = client.post("/api/operator/scan-cost", json=draft, headers=CSRF).json()["ulc"]
        expected = 50 if settings is switched else None
        assert (None if estimate is None else estimate["ceiling_paise"]) == expected

    platform_gemini = dataclasses.replace(NO_DEFAULTS, scan_reader="openrouter")
    assert set_defaults(db, platform, platform_gemini) == "done"
    assert resolved(follows, switched).ulc is None  # the platform's saved reader wins too


def test_review_564_a_saved_ultra_low_cost_with_no_ceiling_keeps_40_paise_with_the_switch_on(
    db: Database, platform: str
) -> None:
    """Review of #564: OCR_PAGE_CEILING_PAISE is the ceiling only where the switch supplies the
    reader. An Ultra low cost the super admin saved (an organisation's or the platform's) with no
    ceiling keeps the server's 40 paise (ULC_DEFAULT_PAISE), switch on or off."""
    switched = dataclasses.replace(SETTINGS, ocr_ultra_low_cost=True, ocr_page_ceiling_paise=50)
    lead = scan_model(db)
    org, follows = new_customer(db)[0], new_customer(db)[0]
    ulc = ops.AiChoice([lead], lead, scan_reader="ultra_low_cost")
    assert set_org(db, platform, org, ulc).code == "done"
    assert set_org(db, platform, follows, ops.AiChoice([lead], lead)).code == "done"

    def paise(customer: str) -> int | None:
        with db.customer(customer) as conn:
            read = models.resolve_scan(conn, customer, insert_document(conn, customer), settings=switched)
        return None if read.ulc is None else read.ulc.paise

    assert paise(org) == 40
    assert paise(follows) == 50  # nothing saved: the switch's reader at the switch's ceiling
    assert (
        set_defaults(db, platform, dataclasses.replace(NO_DEFAULTS, scan_reader="ultra_low_cost")) == "done"
    )
    assert paise(follows) == 40  # the platform's saved reader, with no ceiling saved
