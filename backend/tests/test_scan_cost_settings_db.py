"""The scan cost settings and the approximate cost a page against PostgreSQL (#463, Tushar,
31 Oct 2026; 0064): each field inherits organisation > platform > server; minimal is a scan
level (both saves), refused where a model does not take it; both audits hold the fields; the
estimate is measured, never guessed; and a saved setting truly reaches the reader: the first
read, Careful and "Read this page again" are sent exactly what was saved.

A change reaches what is read from then on (new scans, Careful's re-reads, pages read again);
text already read is never read again for it. Customer separation: test_isolation_scan_cost.

Every test that saves the platform defaults puts them back (the ``platform`` fixture)."""

import io
import threading
import uuid
from decimal import Decimal
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import text

from kaagaz.ai.openrouter import PageReply
from kaagaz.db import models
from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.pipeline import JobReading
from kaagaz.reading.gemini_ocr import GeminiEngine
from kaagaz.reading.ocr import DocumentPages, as_page_engine
from kaagaz.settings import Settings
from tests.db_support import insert_document, new_customer
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api, TestClient
from tests.test_gemini_ocr import FakeCache, FakeFinder, FakeRapidOcr, line, page, reply, value
from tests.test_isolation_ai_settings import NO_DEFAULTS, platform, set_defaults, set_org
from tests.test_migration_0023_db import as_owner, catalog_model, new_operator
from tests.test_operator_auth_db import _owner
from tests.test_operator_models_db import FOLLOWS_DEFAULTS, _save, _signed_in
from tests.test_pipeline_gemini import SHARP, FakeChecker, rig

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name

SETTINGS = make_settings()  # OCR_THINKING_LEVEL low, OCR_MEDIA_RESOLUTION high, OCR_RETRY_* high
EVERY_LEVEL = ("minimal", "low", "medium", "high")
# Changed on purpose (#463 review, C3): each call here is one page read.
_USAGE = (
    "INSERT INTO model_usage (customer_id, kind, model, cached, calls, pages, input_tokens, output_tokens,"
    " reasoning_tokens, cost_usd, cost_estimated, thinking, resolution)"
    " VALUES (:c, :kind, :m, :cached, :calls, :calls, 10, 10, 0, :cost, :estimated, :t, :r)"
)


def scan_model(db: Database, efforts: tuple[str, ...] = EVERY_LEVEL) -> str:
    """A made-up Google scan model (``catalog_model``'s: enabled and verified), taking ``efforts``."""
    model = catalog_model(db)
    as_owner(
        db,
        "UPDATE model_catalog SET efforts = CAST(:e AS text[]) WHERE id = :m",
        {"e": list(efforts), "m": model},
    )
    return model


def spent(
    db: Database,
    customer: str,
    model: str,
    *,
    kind: str = "scan",
    calls: int = 1,
    cost: str | None = "0.01",
    thinking: str | None = "low",
    resolution: str | None = "high",
    cached: bool = False,
    estimated: bool = False,
) -> None:
    """One ledger row, as the owner (the app may not write created_at; it is now)."""
    params = {"c": customer, "kind": kind, "m": model, "cached": cached, "calls": calls, "cost": cost}
    _owner(db, _USAGE, {**params, "estimated": estimated, "t": thinking, "r": resolution})


def reading_of(db: Database, customer: str, *, mode: str = "single") -> models.Reading:
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
        conn.execute(text("UPDATE documents SET read_mode = :m WHERE id = :d"), {"m": mode, "d": document})
        return models.resolve_scan(conn, customer, document, settings=SETTINGS)


def levels(reading: models.Reading) -> tuple[str | None, str | None, str | None]:
    return reading.resolution, reading.careful_thinking, reading.careful_resolution


# --- inheritance, validation, audits -------------------------------------------------------


def test_each_field_inherits_organisation_then_platform_then_the_server(db: Database, platform: str) -> None:
    lead = scan_model(db)
    own, inherits = new_customer(db)[0], new_customer(db)[0]
    choice = ops.AiChoice([lead], lead, scan_resolution="low", careful_thinking="medium")
    assert set_org(db, platform, own, choice).code == "done"
    assert levels(reading_of(db, inherits)) == (None, None, None)  # the server's (the reader's env)
    assert levels(reading_of(db, own)) == ("low", "medium", None)

    defaults = ops.AiChoice(
        chat_levels=["low"],
        chat_default_level="low",
        dynamic=False,
        scan_resolution="high",
        careful_thinking="minimal",
        careful_resolution="low",
    )
    assert set_defaults(db, platform, defaults) == "done"
    assert levels(reading_of(db, inherits)) == ("high", "minimal", "low")  # the platform's
    assert levels(reading_of(db, own)) == ("low", "medium", "low")  # its own, field by field


def test_minimal_is_a_scan_level_and_refused_where_a_model_does_not_take_it(
    db: Database, platform: str
) -> None:
    customer, _ = new_customer(db)
    every, no_minimal = scan_model(db), scan_model(db, ("low", "high"))
    for field in ("scan_thinking", "careful_thinking"):
        minimal: dict[str, Any] = {field: "minimal"}
        assert set_org(db, platform, customer, ops.AiChoice([every], every, **minimal)).code == "done"
        refused = ops.AiChoice([no_minimal], no_minimal, **minimal)
        assert set_org(db, platform, customer, refused).code == "level_not_supported"
        platform_levels: dict[str, Any] = {
            "chat_levels": ["low"],
            "chat_default_level": "low",
            "dynamic": False,
        }
        on_platform = ops.AiChoice([every], every, **platform_levels, **minimal)
        assert set_defaults(db, platform, on_platform) == "done"
        refused = ops.AiChoice([no_minimal], no_minimal, **platform_levels, **minimal)
        assert set_defaults(db, platform, refused) == "level_not_supported"
    # An answer level is never minimal, and a picture detail is low or high only (the CHECKs too).
    bad = (
        ops.AiChoice(chat_levels=["minimal"], chat_default_level="minimal"),
        ops.AiChoice(scan_resolution="medium"),
        ops.AiChoice(careful_resolution="ultra_high"),
        ops.AiChoice(careful_thinking="max"),
    )
    for choice in bad:
        assert set_org(db, platform, customer, choice).code == "level_not_supported", choice


def test_dynamic_reads_first_at_minimal_when_the_scan_level_is_minimal(db: Database, platform: str) -> None:
    lead, low_high = scan_model(db), scan_model(db, ("low", "high"))
    customer, _ = new_customer(db)
    for scan_thinking, model, first in (
        ("minimal", lead, "minimal"),
        ("high", lead, "low"),
        (None, lead, "low"),
    ):
        choice = ops.AiChoice([model], model, scan_thinking=scan_thinking, dynamic=True)
        assert set_org(db, platform, customer, choice).code == "done"
        reading = reading_of(db, customer, mode="dynamic")
        assert (reading.mode, reading.thinking) == ("dynamic", first), scan_thinking
        assert reading_of(db, customer).thinking == (scan_thinking or SETTINGS.ocr_thinking_level)
    # A lead that does not take minimal can't be saved with it, so Dynamic stays at low with it.
    assert set_org(db, platform, customer, ops.AiChoice([low_high], low_high, dynamic=True)).code == "done"
    assert reading_of(db, customer, mode="dynamic").thinking == "low"


def test_both_audits_hold_the_scan_cost_settings(db: Database, platform: str) -> None:
    customer, _ = new_customer(db)
    lead = scan_model(db)
    choice = ops.AiChoice(
        [lead], lead, scan_resolution="low", careful_thinking="minimal", careful_resolution="high"
    )
    assert set_org(db, platform, customer, choice, budget=Decimal("3")).code == "done"
    [(details,)] = as_owner(
        db,
        "SELECT details FROM audit_log WHERE customer_id = :c AND action = 'org.ai_settings_set'",
        {"c": customer},
    )
    [(platform_details,)] = as_owner(
        db, "SELECT details FROM platform_audit WHERE subject_customer_id = :c", {"c": customer}
    )
    # Changed on purpose (#463 review, X1): the organisation's own log never holds the scan cost
    # settings (scan_thinking too); the operator's holds them, and the budget.
    hidden = {
        "scan_thinking": None,
        "scan_resolution": "low",
        "careful_thinking": "minimal",
        "careful_resolution": "high",
        "page_max_output_tokens": None,
        "scan_reader": None,  # Changed on purpose (#473, 0065): the page reader too
        "mistral_model": None,
        "daily_page_limit": None,  # Changed on purpose (#478)
    }
    assert not set(hidden) & set(details)
    assert platform_details == {**details, **hidden, "budget_usd": 3}

    inheriting = ops.AiChoice(
        chat_levels=["low"], chat_default_level="low", dynamic=False, careful_resolution="low"
    )
    assert set_defaults(db, platform, inheriting) == "done"
    [(saved,)] = as_owner(
        db,
        "SELECT details FROM platform_audit WHERE operator_id = :o AND action = 'platform.ai_defaults_set'"
        " ORDER BY id DESC LIMIT 1",
        {"o": platform},
    )
    assert (saved["before"]["careful_resolution"], saved["after"]["careful_resolution"]) == (None, "low")
    assert saved["after"]["scan_resolution"] is None and saved["after"]["careful_thinking"] is None
    # Every organisation without its own picture detail for re-reads was changed; no other field.
    assert saved["orgs_changed"]["careful_resolution"] >= 1
    assert saved["orgs_changed"]["scan_resolution"] == 0 and saved["orgs_changed"]["careful_thinking"] == 0
    with OperatorDatabase(db.engine).operator() as conn:
        row = ops.ai_defaults(conn, platform)
    assert row is not None and row["orgs_inheriting_careful_resolution"] >= 1


# --- the approximate cost a page (POST /api/operator/scan-cost) ---------------------------


def _estimate(client: TestClient, **body: Any) -> dict[str, Any]:
    reply = client.post("/api/operator/scan-cost", json=body, headers=CSRF)
    assert reply.status_code == 200, reply.text
    result: dict[str, Any] = reply.json()
    return result


def test_the_estimate_is_measured_at_the_settings_a_scan_would_really_use(
    db: Database, platform: str
) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    lead = scan_model(db)
    customer, other = api.customer("Estimate Org")[0], api.customer("Estimate Other")[0]
    assert set_org(db, platform, customer, ops.AiChoice([lead], lead, scan_thinking="medium")).code == "done"
    # Every organisation's rows measure the price; only these settings, not cached, cost reported.
    spent(db, customer, lead, calls=4, cost="0.04", thinking="medium")
    spent(db, other, lead, calls=6, cost="0.08", thinking="medium")
    spent(db, customer, lead, calls=50, cost="9", thinking="low")  # another level
    spent(db, customer, lead, calls=50, cost="9", thinking="medium", resolution="low")  # another detail
    spent(db, customer, lead, calls=50, cost="9", thinking="medium", cached=True)
    spent(db, customer, lead, calls=50, cost="9", thinking="medium", estimated=True)  # a guess: never
    spent(db, customer, lead, calls=50, cost=None, thinking="medium")  # no cost reported

    # The console sends the draft's values; a null one is what the save would inherit.
    single = _estimate(client, customer_id=customer, scan_thinking="medium")
    assert single["days"] == 30 and single["re_read"] is None  # Dynamic off
    assert single["first_read"] == {
        "model": lead,
        "thinking": "medium",
        "resolution": "high",  # OCR_MEDIA_RESOLUTION: nothing saved anywhere
        "usd_per_page": pytest.approx(0.012),
        "pages": 10,
    }
    assert single["usd_per_page"] == pytest.approx(0.012)

    # The draft's own levels; not measured at them: null, never guessed.
    draft = _estimate(client, customer_id=customer, scan_thinking="high", scan_resolution="low")
    assert (draft["first_read"]["thinking"], draft["first_read"]["resolution"]) == ("high", "low")
    assert (draft["first_read"]["usd_per_page"], draft["first_read"]["pages"], draft["usd_per_page"]) == (
        None,
        0,
        None,
    )


def test_the_estimate_with_dynamic_adds_the_share_read_again(db: Database, platform: str) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    lead = scan_model(db)
    customer = api.customer("Estimate Dynamic")[0]
    choice = ops.AiChoice([lead], lead, scan_thinking="minimal", dynamic=True, careful_thinking="high")
    assert set_org(db, platform, customer, choice).code == "done"
    spent(db, customer, lead, calls=40, cost="0.04", thinking="minimal")
    # Changed on purpose (#463 review, U2): the draft's Cost saving is sent (null: the platform's).
    draft = {"customer_id": customer, "scan_thinking": "minimal", "careful_thinking": "high", "dynamic": True}
    reply = _estimate(client, **draft)
    assert reply["first_read"]["thinking"] == "minimal"  # Dynamic's first read stays at minimal
    assert reply["re_read"] == {
        "model": lead,  # one allowed model: Careful is the lead itself
        "thinking": "high",
        "resolution": "high",  # OCR_RETRY_MEDIA_RESOLUTION
        "usd_per_page": None,  # not measured yet
        "pages": 0,
        "share": 0.0,
    }
    assert reply["usd_per_page"] == pytest.approx(0.001)  # the first read alone

    spent(db, customer, lead, kind="careful", calls=10, cost="0.05", thinking="high")
    reply = _estimate(client, **draft)
    assert reply["re_read"]["usd_per_page"] == pytest.approx(0.005)
    assert reply["re_read"]["share"] == pytest.approx(10 / 40)
    assert reply["usd_per_page"] == pytest.approx(0.001 + 0.25 * 0.005)

    off = _estimate(client, **{**draft, "careful_thinking": "low"})  # the draft's Careful level
    assert off["re_read"]["thinking"] == "low" and off["re_read"]["usd_per_page"] is None
    assert off["usd_per_page"] == pytest.approx(0.001)


def test_the_estimate_of_the_platform_defaults_and_of_an_unknown_organisation(db: Database) -> None:
    api = Api(db)
    _, client = _signed_in(api)
    reply = _estimate(client)  # the platform defaults: nothing saved, the server's
    assert reply["first_read"]["model"] == SETTINGS.ocr_model
    assert (reply["first_read"]["thinking"], reply["first_read"]["resolution"]) == ("low", "high")
    missing = client.post("/api/operator/scan-cost", json={"customer_id": str(uuid.uuid4())}, headers=CSRF)
    assert missing.status_code == 404
    for bad in (
        {"scan_thinking": "max"},
        {"scan_resolution": "medium"},
        {"careful_resolution": "x"},
        {"code": "1"},
    ):
        refused = client.post("/api/operator/scan-cost", json=bad, headers=CSRF)
        assert refused.status_code == 400, bad


# --- a saved setting truly takes effect -----------------------------------------------------


class SendsClient:
    """The model client: every page read noted as (model, thinking, picture detail); a first
    read has an unsure word, so Careful reads the page again."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str, str]] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        return 1000.0

    def read_page(
        self, *, png: bytes, media_resolution: str, thinking_level: str, model: str | None = None, **_: Any
    ) -> PageReply:
        with self._lock:
            self.calls.append((model, thinking_level, media_resolution))
        with Image.open(io.BytesIO(png)):
            pass
        unsure = ("1,180.00",) if thinking_level == "minimal" else ()
        return reply(value(line("Total: 1,180.00", (500, 100, 530, 650), unsure=unsure), quality="fair"))


def _engine(client: SendsClient, settings: Settings) -> GeminiEngine:
    local = FakeRapidOcr()
    return GeminiEngine(settings, client=client, pool=FakeFinder(), local=lambda: as_page_engine(local))


def test_a_saved_setting_reaches_the_reader_and_nothing_else(db: Database, platform: str) -> None:
    """Saved through the operator's PUT with a fresh code, then read: the first read, Careful
    and "Read this page again" are sent exactly the organisation's values; an organisation
    with nothing saved gets the platform's, else the server's; a platform change reaches an
    organisation that inherits it and not one with its own. Only reads from then on."""
    settings = make_settings(ocr_engine="gemini")
    api = Api(db, settings=settings)
    operator, client = _signed_in(api)
    lead = scan_model(db)
    own, inherits = api.customer("Saved Org")[0], api.customer("Inheriting Org")[0]
    body = {
        **FOLLOWS_DEFAULTS,
        "scan_models": [lead],
        "scan_default": lead,
        "scan_thinking": "minimal",
        "scan_resolution": "low",
        "careful_thinking": "medium",
        "careful_resolution": "low",
        "dynamic": True,
        "ensemble": False,
        "ai_budget_usd": None,
    }
    saved = _save(api, operator, client, f"/api/operator/orgs/{own}/ai", body)
    assert saved.status_code == 200, saved.text

    reading = reading_of(db, own, mode="dynamic")
    sends = SendsClient()
    system = rig(
        _engine(sends, settings),
        checker=FakeChecker("Total: 1,180.00"),
        ocr_model=lead,
        settings=settings,
        job_reading=lambda customer, document: JobReading(None, lead, "google-vertex", reading=reading),
    )
    system.run(make_pdf([SHARP]))
    assert sorted(sends.calls) == sorted(
        [(lead, "minimal", "low"), (lead, "medium", "low")]
    )  # first, Careful

    # "Read this page again": the Careful pair, at the organisation's values.
    again = SendsClient()
    pages = DocumentPages(
        customer_id=own,
        document_id=str(uuid.uuid4()),
        cache=FakeCache(),
        read_again=frozenset({1}),
        model=lead,
        provider="google-vertex",
        thinking=reading.thinking,
        resolution=reading.resolution,
        careful_thinking=reading.careful_thinking,
        careful_resolution=reading.careful_resolution,
    )
    engine = _engine(again, settings)
    with engine.document(pages):
        engine.read_page(page(), page_no=1)
    assert again.calls[0] == (lead, "medium", "low")

    # Nothing saved anywhere: the server's.
    assert levels(reading_of(db, inherits)) == (None, None, None)
    plain = SendsClient()
    engine = _engine(plain, settings)
    with engine.document(
        DocumentPages(customer_id=inherits, document_id=str(uuid.uuid4()), cache=FakeCache())
    ):
        engine.read_page(page(), page_no=1)
    assert plain.calls[0][1:] == (settings.ocr_thinking_level, settings.ocr_media_resolution)

    # A platform change reaches the organisation that inherits it, never the one with its own.
    platform_body = {
        **FOLLOWS_DEFAULTS,
        "scan_resolution": "low",
        "careful_thinking": "minimal",
        "careful_resolution": "high",
        "chat_levels": ["low"],
        "chat_default_level": "low",
        "dynamic": False,
    }
    try:
        changed = _save(api, operator, client, "/api/operator/ai-defaults", platform_body)
        assert changed.status_code == 200, changed.text
        assert levels(reading_of(db, inherits)) == ("low", "minimal", "high")
        assert levels(reading_of(db, own)) == ("low", "medium", "low")
    finally:
        assert set_defaults(db, new_operator(db), NO_DEFAULTS) == "done"


def test_a_page_read_records_its_settings_in_the_ledger_and_nothing_else_does(db: Database) -> None:
    from kaagaz.ai import usage

    customer, _ = new_customer(db)
    with usage.usage_scope(db, customer_id=customer, user_id=None, kind="scan"):
        usage.record_call("google/x-page", None, 10, 5, 0, 0.001, thinking="minimal", resolution="low")
        with usage.billed_as("careful"):
            usage.record_call("google/x-page", None, 10, 5, 0, 0.002, thinking="high", resolution="high")
    with usage.usage_scope(db, customer_id=customer, user_id=None, kind="chat"):
        usage.record_call("google/x-chat", None, 10, 5, 0, 0.001, thinking="low", resolution="high")
    rows = as_owner(
        db,
        "SELECT kind, model, thinking, resolution FROM model_usage WHERE customer_id = :c ORDER BY kind",
        {"c": customer},
    )
    assert rows == [
        ("careful", "google/x-page", "high", "high"),
        ("chat", "google/x-chat", None, None),
        ("scan", "google/x-page", "minimal", "low"),
    ]
