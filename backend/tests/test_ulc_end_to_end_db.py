"""Ultra low cost end to end (#488): an organisation the super admin sets to ``ultra_low_cost``
with a ceiling of 30 paise through the real operator function; the worker's own query
(``resolve_scan``, the lead's catalogue prices); the pipeline's ``DocumentPages``; the Gemini
reader over the real OpenRouter client against the loopback fake (never the real API): one
compact request (``{"r": string}``, effort minimal, ``max_completion_tokens`` from the page's
budget); the decoded lines saved; the ``ulc_scan`` ledger row; and ``kaagaz_op_ulc_cost`` and
the estimate's ``ulc`` block showing it. Switched back to ``openrouter``, the organisation reads
exactly as before."""

import math
from collections.abc import Callable
from typing import Any

import pytest

from kaagaz.ai.openrouter import ModelGate, OpenRouterClient
from kaagaz.ai.usage import usage_scope
from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.pipeline import JobReading
from kaagaz.reading.compact import COMPACT_SCHEMA, INSTRUCTION_COMPACT
from kaagaz.reading.gemini_ocr import INSTRUCTION, PAGE_SCHEMA, ULC_INPUT_TOKENS, GeminiEngine
from kaagaz.reading.ocr import UlcCeiling, UlcPrices, as_page_engine
from kaagaz.settings import Settings
from tests.db_support import insert_document, new_customer
from tests.openrouter_fake import KEY, OpenRouterFake, json_reply, running_fake
from tests.openrouter_fake import usage as counts
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api
from tests.test_gemini_ocr import FakeFinder, FakeRapidOcr, line, value
from tests.test_isolation_ai_settings import platform, set_org
from tests.test_migration_0023_db import as_owner
from tests.test_operator_models_db import _signed_in
from tests.test_pipeline_gemini import SCANNED, rig
from tests.test_reader_choice_db import _job_reading
from tests.test_scan_cost_settings_db import scan_model

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name
# #488 review (R3): an image's price, paid on every call (pricing.image), and no request's.
PRICES = UlcPrices(
    price_in_usd_per_m=0.30, price_out_usd_per_m=2.50, tokens_per_page_in=2500, per_call_usd=0.0002
)
PAISE = 30
# A land record's page as the model writes it compactly: a title, then a block with an unsure word.
PAGE_R = "g 0 l -\nt 40 100 70 900 Survey 42\n\np 100 100 120 900 Owner Ramesh Patil\n? Patil"
PROMPT, COMPLETION = 2400, 300
COST = (PROMPT * PRICES.price_in_usd_per_m + COMPLETION * PRICES.price_out_usd_per_m) / 1e6


def _lead(db: Database) -> str:
    """A Gemini scan model taking every level, at Flash Lite's prices (PRICES)."""
    model = scan_model(db)
    as_owner(
        db,
        "UPDATE model_catalog SET price_in_usd_per_m = :i, price_out_usd_per_m = :o, tokens_per_page_in = :t,"
        " price_request_usd = 0, price_image_usd = :image WHERE id = :m",
        {
            "m": model,
            "i": PRICES.price_in_usd_per_m,
            "o": PRICES.price_out_usd_per_m,
            "t": 2500,
            "image": PRICES.per_call_usd,
        },
    )
    return model


def _document(db: Database, customer: str) -> str:
    with db.customer(customer) as conn:
        return insert_document(conn, customer)


def _settings(fake: OpenRouterFake, lead: str) -> Settings:
    return make_settings(
        ocr_engine="gemini",
        openrouter_api_key=KEY,
        openrouter_base_url=fake.url,
        ocr_model=lead,
        ocr_timeout_seconds=5.0,
        model_timeout_seconds=5.0,
        model_rpm=10**9,
        page_image_dpi=72,
        ocr_dpi=72,
    )


def _billed(db: Database, customer: str, document: str) -> Callable[..., Any]:
    """The pipeline's ledger (``usage_scope``) for the in-memory run, on the real organisation
    and document, so its rows are the database's."""

    def scope(**work: Any) -> Any:
        return usage_scope(db, **{**work, "customer_id": customer, "document_id": document})

    return scope


def _run(db: Database, fake: OpenRouterFake, job: JobReading, customer: str, document: str) -> list[str]:
    """One scanned page read through the pipeline as ``job`` says; the saved pieces' text."""
    assert job.model is not None
    settings = _settings(fake, job.model)
    gate = ModelGate(settings, workers=1, sleep=lambda _seconds: None, random=lambda: 0.0)
    engine = GeminiEngine(
        settings,
        client=OpenRouterClient(settings, gate=gate),
        pool=FakeFinder(),
        local=lambda: as_page_engine(FakeRapidOcr()),
    )
    system = rig(
        engine,
        ocr_model=job.model,
        settings=settings,
        job_reading=lambda _c, _d: job,
        usage=_billed(db, customer, document),
    )
    ran = system.run(make_pdf([SCANNED]))
    content = system.store.content(ran.customer_id, ran.document_id)
    assert content is not None
    [page] = content.pages
    assert page.read_by == job.model  # the model read it, never the local reader
    return [piece.text for piece in content.pieces]


def _ledger(db: Database, customer: str) -> list[tuple[Any, ...]]:
    return as_owner(
        db,
        "SELECT kind, model, cached, calls, pages, thinking, cost_usd::float, limit_hits FROM model_usage"
        " WHERE customer_id = :c ORDER BY created_at, kind",
        {"c": customer},
    )


def test_an_organisation_set_to_ultra_low_cost_reads_compact_under_its_ceiling_and_back(
    db: Database, platform: str
) -> None:
    lead = _lead(db)
    customer = new_customer(db)[0]
    gemini = ops.AiChoice([lead], lead, scan_thinking="low")
    assert set_org(db, platform, customer, gemini).code == "done"
    before = _job_reading(db, customer, _document(db, customer))
    assert before.reading is not None and before.reading.ulc is None and before.ulc_prices is None

    # The super admin chooses it, at 30 paise, through the real operator function.
    ulc = ops.AiChoice(
        [lead], lead, scan_thinking="low", scan_reader="ultra_low_cost", ulc_ceiling_paise=PAISE
    )
    assert set_org(db, platform, customer, ulc).code == "done"
    document = _document(db, customer)
    job = _job_reading(db, customer, document)  # the worker's own query: resolve_scan and the prices
    assert job.reading is not None and job.model == lead
    assert (job.reading.mode, job.reading.thinking, job.reading.ulc) == (
        "single",
        "minimal",
        UlcCeiling(PAISE / 100 / 88, PAISE),
    )
    assert job.ulc_prices == PRICES

    with running_fake() as fake:
        fake.script("completions", json_reply({"r": PAGE_R}, counts=counts(PROMPT, COMPLETION, 0, COST)))
        texts = _run(db, fake, job, customer, document)
        [body] = fake.bodies()
    assert body["model"] == lead
    assert body["response_format"]["json_schema"]["schema"] == COMPACT_SCHEMA
    assert body["messages"][0]["content"][0]["text"] == INSTRUCTION_COMPACT
    assert body["reasoning"] == {"effort": "minimal", "exclude": True}
    # The catalogue's tokens a page, never below ULC_INPUT_TOKENS; the image's price paid first.
    ceiling, tokens_in = PAISE / 100 / 88, max(math.ceil(2500 * 1.15), ULC_INPUT_TOKENS)
    cap = math.floor((ceiling - (0.0002 + tokens_in * 0.30e-6)) / 2.50e-6)
    assert body["max_completion_tokens"] == cap
    assert any("Survey 42" in text for text in texts) and any("Owner Ramesh Patil" in text for text in texts)
    assert _ledger(db, customer) == [("ulc_scan", lead, False, 1, 1, "minimal", pytest.approx(COST), 0)]

    with OperatorDatabase(db.engine).operator() as conn:
        measured = ops.ulc_cost(conn, platform, customer, lead)
    assert measured is not None
    assert (measured["pages"], measured["limit_hits"]) == (1, 0)
    assert float(measured["cost_usd"]) == pytest.approx(COST)
    api = Api(db)
    _, client = _signed_in(api)
    draft = {"customer_id": customer, "scan_reader": "ultra_low_cost", "ulc_ceiling_paise": PAISE}
    estimate = client.post("/api/operator/scan-cost", json=draft, headers=CSRF)
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["ulc"] == {
        "ceiling_paise": PAISE,
        "ceiling_usd": pytest.approx(ceiling),
        "pages": 1,
        "usd_per_page": pytest.approx(COST),
        "top_usd_per_page": pytest.approx(COST),
        "limit_hits": 0,
    }

    # Back to Gemini: the reading is exactly as before, a g3 request, billed as a normal read.
    back = ops.AiChoice([lead], lead, scan_thinking="low", scan_reader="openrouter")
    assert set_org(db, platform, customer, back).code == "done"
    again = _document(db, customer)
    after = _job_reading(db, customer, again)
    assert (after.reading, after.model, after.provider, after.ulc_prices) == (
        before.reading,
        before.model,
        before.provider,
        None,
    )
    as_owner(db, "DELETE FROM model_usage WHERE customer_id = :c", {"c": customer})
    with running_fake() as fake:
        fake.script("completions", json_reply(value(line("Survey 42", (40, 100, 70, 900)))))
        _run(db, fake, after, customer, again)
        [plain] = fake.bodies()
    assert plain["response_format"]["json_schema"]["schema"] == PAGE_SCHEMA
    assert plain["messages"][0]["content"][0]["text"] == INSTRUCTION
    assert plain["reasoning"]["effort"] == "low"
    assert [row[:3] for row in _ledger(db, customer)] == [
        ("upgrade", lead, False)
    ]  # no user asked: as before
