"""Ultra low cost with the database (#488, Tushar, 4 Nov 2026): the operator's API saves and shows
the reader and the ceiling (400 ``ceiling_out_of_range`` and 409 ``ulc_needs_gemini`` before a
code is used), an organisation that chose it reads with it and one that did not reads as before,
``kaagaz_op_ulc_cost`` measures what it really cost (OpenRouter's reported cost of the
``ulc_scan`` ledger rows, never cached, every limit hit counted), the estimate shows it, and the
catalogue's real cost counts its pages. Every test that saves the platform defaults puts them back
(the ``platform`` fixture)."""

import pytest
from sqlalchemy import text

from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api
from tests.test_isolation_ai_settings import platform, set_org
from tests.test_migration_0023_db import as_owner
from tests.test_operator_models_db import FOLLOWS_DEFAULTS, _save, _signed_in
from tests.test_scan_cost_settings_db import reading_of, scan_model

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name
ORG_BODY = {**FOLLOWS_DEFAULTS, "ensemble": False, "ai_budget_usd": None}
NOT_IN_CATALOGUE = make_settings(ocr_model="google/not-in-the-catalogue")  # OCR_MODEL with no row
PLATFORM_BODY = {**FOLLOWS_DEFAULTS, "chat_levels": ["low"], "chat_default_level": "low", "dynamic": False}
CEILING = {
    "code": "ceiling_out_of_range",
    "message": "Choose from ₹0.20 to ₹0.40 a page, or leave it empty.",
}
NEEDS_GEMINI = {
    "code": "ulc_needs_gemini",
    "message": "Ultra low cost needs a Gemini model with a known price that can read with minimal thinking.",
}
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


def test_a_ceiling_outside_20_to_40_paise_is_refused_before_a_code_is_used(
    db: Database, platform: str
) -> None:
    api = Api(db)
    operator, client = _signed_in(api)
    customer = api.customer("ULC Ceiling")[0]
    for path, body in (
        (f"/api/operator/orgs/{customer}/ai", ORG_BODY),
        ("/api/operator/ai-defaults", PLATFORM_BODY),
    ):
        for bad in (0, 19, 41, 4000):
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


def test_it_needs_a_gemini_with_a_price_that_takes_minimal(db: Database, platform: str) -> None:
    api = Api(db, settings=NOT_IN_CATALOGUE)
    operator, client = _signed_in(api)
    customer = api.customer("ULC Gemini")[0]
    path = f"/api/operator/orgs/{customer}/ai"
    no_minimal = scan_model(db, ("low", "high"))
    minimal = scan_model(db)
    for lead, expected in ((no_minimal, 409), (minimal, 200)):
        body = {**ORG_BODY, "scan_models": [lead], "scan_default": lead, "scan_reader": "ultra_low_cost"}
        reply = _save(api, operator, client, path, body)
        assert reply.status_code == expected, reply.text
        if expected == 409:
            assert reply.json()["error"] == NEEDS_GEMINI
            assert client.get(path).json()["own"]["scan_reader"] is None  # nothing saved
    got = client.get(path).json()["own"]
    assert (got["scan_reader"], got["ulc_ceiling_paise"]) == ("ultra_low_cost", None)
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
    bad = client.post("/api/operator/scan-cost", json={**draft, "ulc_ceiling_paise": 41}, headers=CSRF)
    assert (bad.status_code, bad.json()["error"]) == (400, CEILING)


def test_the_catalogues_real_cost_counts_its_pages(db: Database, platform: str) -> None:
    lead = scan_model(db)
    customer = new_customer(db)[0]
    ledger(db, customer, _document(db, customer), lead, pages=3, cost="0.009")
    with OperatorDatabase(db.engine).operator() as conn:
        [listed] = [row for row in ops.list_models(conn, platform) if row["id"] == lead]
    assert (listed["scan_pages"], float(listed["scan_cost_usd"])) == (3, 0.009)
