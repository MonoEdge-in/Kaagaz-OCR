"""Ultra low cost's reading against PostgreSQL (#488, production port, no migration): while
OCR_ULTRA_LOW_COST is on, ``resolve_scan`` reads every AI document single at minimal thinking
under OCR_PAGE_CEILING_PAISE, with the organisation's model when it takes minimal (else
OCR_MODEL); Ensemble is refused; the lead's prices come from ``model_catalog``; the worker's
start-up check names what OCR_MODEL lacks. Off: the reading is what it was."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.db.models import (
    AiNotAllowed,
    Reading,
    resolve_ensemble,
    resolve_scan,
    ulc_not_ready,
    ulc_prices,
)
from kaagaz.reading.ocr import INR_PER_USD, UlcCeiling, UlcPrices
from kaagaz.settings import Settings
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_migration_0023_db import allow, as_owner, catalog_model

pytestmark = pytest.mark.db

KEY = "sk-or-v1-" + "q" * 64
OFF = make_settings(ocr_engine="gemini", openrouter_api_key=KEY)
ON = make_settings(
    ocr_engine="gemini", openrouter_api_key=KEY, ocr_ultra_low_cost=True, ocr_page_ceiling_paise=40
)


def _efforts(db: Database, model: str, *efforts: str) -> None:
    as_owner(
        db,
        "UPDATE model_catalog SET efforts = CAST(:e AS text[]) WHERE id = :m",
        {"e": list(efforts), "m": model},
    )


def _document(db: Database, customer: str, read_model: str | None, mode: str = "single") -> str:
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
        conn.execute(
            text("UPDATE documents SET read_model = :m, read_mode = :mode WHERE id = :d"),
            {"m": read_model, "mode": mode, "d": document},
        )
    return document


def _scan(db: Database, customer: str, document: str, settings: Settings) -> Reading:
    with db.customer(customer) as conn:
        return resolve_scan(conn, customer, document, settings=settings)


def test_488_on_the_chosen_model_reads_single_at_minimal_under_the_ceiling(db: Database) -> None:
    customer, _ = new_customer(db)
    chosen = catalog_model(db)
    _efforts(db, chosen, "minimal", "low", "high")
    allow(db, customer, [chosen], chosen)
    for mode in ("single", "dynamic"):
        reading = _scan(db, customer, _document(db, customer, chosen, mode), ON)
        assert (reading.mode, reading.lead.model, reading.thinking) == ("single", chosen, "minimal")
        assert (reading.careful, reading.others, reading.routes) == (None, (), False)
        assert reading.notes == ("ultra_low_cost",)
        assert reading.ulc == UlcCeiling(usd=40 / 100 / INR_PER_USD, paise=40)
        assert reading.config()["ulc"] == {"paise": 40, "usd": 40 / 100 / INR_PER_USD}


def test_488_on_a_chosen_model_that_cannot_think_minimal_gives_way_to_ocr_model(db: Database) -> None:
    customer, _ = new_customer(db)
    chosen = catalog_model(db)
    _efforts(db, chosen, "low", "high")
    allow(db, customer, [chosen], chosen)
    reading = _scan(db, customer, _document(db, customer, chosen), ON)
    assert (reading.lead.model, reading.lead.provider) == (ON.ocr_model, "google-vertex")
    assert reading.notes == ("ultra_low_cost", "thinking_fallback")


def test_488_off_the_reading_is_what_it_was(db: Database) -> None:
    customer, _ = new_customer(db)
    chosen = catalog_model(db)
    _efforts(db, chosen, "minimal", "low", "high")
    allow(db, customer, [chosen], chosen)
    document = _document(db, customer, chosen)
    reading = _scan(db, customer, document, OFF)
    assert reading.ulc is None and "ulc" not in reading.config()
    assert reading.thinking == OFF.ocr_thinking_level and "ultra_low_cost" not in reading.notes


def test_488_on_ensemble_is_refused(db: Database) -> None:
    customer, _ = new_customer(db)
    document = _document(db, customer, None)
    with db.customer(customer) as conn, pytest.raises(AiNotAllowed):
        resolve_ensemble(conn, customer, document, settings=ON)


def test_488_the_leads_prices_come_from_the_catalogue(db: Database) -> None:
    customer, _ = new_customer(db)
    model = catalog_model(db, price_in=0.3, price_out=2.5, tokens=(2400, 300))
    with db.customer(customer) as conn:
        assert ulc_prices(conn, model) == UlcPrices(0.3, 2.5, 2400)
        assert ulc_prices(conn, "google/no-such-model") is None


def test_488_the_start_up_check_names_what_ocr_model_lacks(db: Database) -> None:
    model = catalog_model(db)
    on = make_settings(ocr_engine="gemini", openrouter_api_key=KEY, ocr_ultra_low_cost=True, ocr_model=model)
    with db.system() as conn:
        assert "can't take minimal thinking" in str(ulc_not_ready(conn, on))
    _efforts(db, model, "minimal", "high")
    with db.system() as conn:
        assert ulc_not_ready(conn, on) is None
    as_owner(db, "UPDATE model_catalog SET price_out_usd_per_m = 0 WHERE id = :m", {"m": model})
    with db.system() as conn:
        assert "no known price out" in str(ulc_not_ready(conn, on))
        missing = make_settings(ocr_engine="gemini", openrouter_api_key=KEY, ocr_model="google/no-such-model")
        assert "has no model_catalog row" in str(ulc_not_ready(conn, missing))
