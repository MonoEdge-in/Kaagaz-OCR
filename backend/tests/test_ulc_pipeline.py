"""Ultra low cost through the pipeline (#488): the document's ceiling (``Reading.ulc``) and its
lead's catalogue prices, taken once at the job's start, reach the reader in ``DocumentPages``;
a document not under Ultra low cost gets neither, so it reads as before."""

import dataclasses
from decimal import Decimal
from unittest import mock

import pytest
from sqlalchemy import text

from kaagaz.ai import usage
from kaagaz.db import models
from kaagaz.db.engine import Database
from kaagaz.reading.ocr import UlcCeiling, UlcPrices
from tests.db_support import insert_document, new_customer
from tests.pdf_builder import make_pdf
from tests.test_migration_0023_db import catalog_model
from tests.test_model_choice_pipeline import _Reading
from tests.test_pipeline_gemini import MODEL, SCANNED, TOTAL, FakeChecker, ScriptedReader, page_read, rig
from tests.test_reader_choice_db import _job_reading

# #488 review (R3): model_catalog keeps no price a request or an image, so the worker's prices
# say none is known (per_call_usd None) and the reader refuses the lead until it does.
PRICES = UlcPrices(price_in_usd_per_m=0.3, price_out_usd_per_m=2.5, tokens_per_page_in=2500)


def _opened(reading: models.Reading, prices: UlcPrices | None) -> object:
    job_reading = _Reading(None, MODEL)
    job_reading.reading = dataclasses.replace(job_reading.reading, reading=reading, ulc_prices=prices)
    reader = ScriptedReader({1: page_read(TOTAL, read_by=MODEL)})
    rig(reader, checker=FakeChecker(), job_reading=job_reading).run(make_pdf([SCANNED]))
    (opened,) = reader.opened
    return opened


def test_488_the_pipeline_passes_the_ceiling_and_the_prices_to_the_reader() -> None:
    lead = models.Agent(MODEL, "google-vertex", "Gemini")
    opened = _opened(models.Reading("single", lead, thinking="minimal", ulc=models.ulc_ceiling(30)), PRICES)
    assert (opened.ulc, opened.ulc_prices) == (UlcCeiling(30 / 100 / 88, 30), PRICES)  # type: ignore[attr-defined]
    plain = _opened(models.Reading("single", lead, thinking="low"), None)
    assert (plain.ulc, plain.ulc_prices) == (None, None)  # type: ignore[attr-defined]


def test_488_a_ceiling_is_paise_at_88_rupees_to_the_dollar() -> None:
    assert models.ulc_ceiling(40) == UlcCeiling(usd=0.40 / 88, paise=40)
    assert not hasattr(UlcCeiling, "of")  # #488 review (R9): one way to make one, models.ulc_ceiling


@pytest.mark.db
def test_488_the_worker_takes_the_leads_catalogue_prices_once_for_a_ulc_document(db: Database) -> None:
    model = catalog_model(db, price_in=0.3, price_out=2.5, tokens=(2500, 900))
    customer, _admin = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
    lead = models.Agent(model, "google-vertex", "Gemini")
    under = models.Reading("single", lead, thinking="minimal", ulc=models.ulc_ceiling(40))
    with mock.patch.object(models, "resolve_scan", return_value=under):
        assert _job_reading(db, customer, document).ulc_prices == PRICES
    with mock.patch.object(models, "resolve_scan", return_value=dataclasses.replace(under, ulc=None)):
        assert _job_reading(db, customer, document).ulc_prices is None  # no query, no prices
    gone = dataclasses.replace(under, lead=models.Agent("google/not-in-the-catalogue", "google-vertex", "X"))
    with mock.patch.object(models, "resolve_scan", return_value=gone):
        assert _job_reading(db, customer, document).ulc_prices is None  # nothing is sent then


@pytest.mark.db
def test_488_a_limit_hit_with_no_call_is_saved_on_the_ulc_scan_row(db: Database) -> None:
    model = catalog_model(db)
    customer, _admin = new_customer(db)
    with (
        usage.usage_scope(db, customer_id=customer, user_id=None, kind="scan"),
        usage.billed_as(usage.ULC_SCAN),
    ):
        usage.record_unsent_hit(model, thinking="minimal", resolution="high")
    with db.customer(customer) as conn:
        rows = conn.execute(
            text(
                "SELECT kind, calls, input_tokens, output_tokens, cost_usd, limit_hits, pages, thinking"
                " FROM model_usage WHERE customer_id = :c"
            ),
            {"c": customer},
        ).all()
    assert [tuple(row) for row in rows] == [("ulc_scan", 1, 0, 0, Decimal(0), 1, 1, "minimal")]
