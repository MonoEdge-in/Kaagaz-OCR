"""The scan model resolver and the usable models against PostgreSQL (model choice, council
11 Oct 2026, MS1, MS4, MS5; FINAL DECISIONS 1 and 10).

The order: the document's chosen model while the organisation allows it, it is eligible,
enabled, verified for the reader's prompt and served by a provider inside
OPENROUTER_PROVIDERS; else the organisation's default under the same rules; else
OCR_MODEL on the default provider (an organisation with no models reads as before). The
provider is the first of the model's providers inside the ceiling. ``usable_models``
offers the same models, the default first. Customer separation:
test_isolation_customer_models.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.db.models import resolve, usable_models
from kaagaz.settings import Settings
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_migration_0023_db import PROMPT, add_usage, allow, as_owner, catalog_model

pytestmark = pytest.mark.db

VERTEX_ONLY = make_settings()
WITH_AZURE = make_settings(openrouter_providers=frozenset({"google-vertex", "azure"}))
PLATFORM = (VERTEX_ONLY.ocr_model, "google-vertex")


def _document(db: Database, customer: str, read_model: str | None) -> str:
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
        conn.execute(
            text("UPDATE documents SET read_model = :m WHERE id = :d"), {"m": read_model, "d": document}
        )
    return document


def _resolve(db: Database, customer: str, document: str, settings: Settings = VERTEX_ONLY) -> tuple[str, str]:
    with db.customer(customer) as conn:
        return resolve(conn, customer, document, settings=settings, prompt_version=PROMPT)


def test_an_organisation_with_no_models_reads_with_ocr_model(db: Database) -> None:
    customer, _ = new_customer(db)
    chosen = catalog_model(db)
    assert (
        _resolve(db, customer, _document(db, customer, chosen)) == PLATFORM
    )  # a choice it was never allowed
    assert _resolve(db, customer, _document(db, customer, None)) == PLATFORM


def test_the_choice_else_the_default_else_ocr_model(db: Database) -> None:
    customer, _ = new_customer(db)
    default = catalog_model(db, providers=("amazon-bedrock", "google-vertex"))  # bedrock is outside
    chosen = catalog_model(db)
    not_allowed = catalog_model(db)
    allow(db, customer, [default, chosen], default)

    assert _resolve(db, customer, _document(db, customer, chosen)) == (chosen, "google-vertex")
    assert _resolve(db, customer, _document(db, customer, None)) == (default, "google-vertex")
    assert _resolve(db, customer, _document(db, customer, not_allowed)) == (default, "google-vertex")

    as_owner(db, "UPDATE model_catalog SET eligible = false WHERE id = :m", {"m": default})
    assert _resolve(db, customer, _document(db, customer, None)) == PLATFORM  # no usable default
    assert _resolve(db, customer, _document(db, customer, chosen)) == (chosen, "google-vertex")


@pytest.mark.parametrize(
    "unusable",
    [
        "UPDATE model_catalog SET eligible = false WHERE id = :m",
        "UPDATE model_catalog SET enabled_at = NULL, enabled_by = NULL WHERE id = :m",
        "UPDATE model_catalog SET verified_prompt_version = 'g1' WHERE id = :m",
        "UPDATE model_catalog SET verified_prompt_version = NULL, verified_at = NULL WHERE id = :m",
        "UPDATE model_catalog SET providers = ARRAY['azure'] WHERE id = :m",  # outside the ceiling
        "UPDATE model_catalog SET providers = '{}' WHERE id = :m",
    ],
)
def test_a_chosen_model_that_is_not_usable_now_falls_back_to_the_default(db: Database, unusable: str) -> None:
    customer, _ = new_customer(db)
    default, chosen = catalog_model(db), catalog_model(db)
    allow(db, customer, [default, chosen], default)
    document = _document(db, customer, chosen)
    as_owner(db, unusable, {"m": chosen})
    assert _resolve(db, customer, document) == (default, "google-vertex")
    with db.customer(customer) as conn:
        offered = usable_models(conn, customer, settings=VERTEX_ONLY)
    assert [model["id"] for model in offered] == [default]


def test_a_provider_inside_a_wider_ceiling_is_used_in_the_catalogues_order(db: Database) -> None:
    customer, _ = new_customer(db)
    default = catalog_model(db)
    gpt = catalog_model(db, providers=("azure", "google-vertex"))
    allow(db, customer, [default, gpt], default)
    document = _document(db, customer, gpt)
    assert _resolve(db, customer, document, WITH_AZURE) == (gpt, "azure")
    assert _resolve(db, customer, document) == (gpt, "google-vertex")  # azure not approved


def test_the_usable_models_are_the_default_first_then_by_name(db: Database) -> None:
    customer, _ = new_customer(db)
    zeta = catalog_model(db, name="Zeta", price_in=1, price_out=4, tokens=(2000, 500))
    alpha = catalog_model(db, name="Alpha", tokens=None)
    default = catalog_model(db, name="Middle")
    azure_only = catalog_model(db, name="Azure only", providers=("azure",))
    allow(db, customer, [zeta, alpha, default, azure_only], default)
    with db.customer(customer) as conn:
        offered = usable_models(conn, customer, settings=VERTEX_ONLY)
        wider = usable_models(conn, customer, settings=WITH_AZURE)
    assert [(model["name"], model["is_default_scan"], model["providers"]) for model in offered] == [
        ("Middle", True, ["google-vertex"]),
        ("Alpha", False, ["google-vertex"]),
        ("Zeta", False, ["google-vertex"]),
    ]
    assert [model["name"] for model in wider] == ["Middle", "Alpha", "Azure only", "Zeta"]
    zeta_row = offered[2]
    assert (float(zeta_row["price_in_usd_per_m"]), float(zeta_row["price_out_usd_per_m"])) == (1.0, 4.0)
    assert (zeta_row["tokens_per_page_in"], zeta_row["tokens_per_page_out"]) == (2000, 500)
    assert (offered[1]["tokens_per_page_in"], offered[1]["tokens_per_page_out"]) == (None, None)
    as_owner(db, "UPDATE model_catalog SET verified_prompt_version = 'g0' WHERE id = :m", {"m": default})
    with db.customer(customer) as conn:  # verified for another prompt version: not offered
        assert [model["name"] for model in usable_models(conn, customer, settings=VERTEX_ONLY)] == [
            "Alpha",
            "Zeta",
        ]


def test_a_page_is_estimated_from_the_last_30_days_of_its_own_scans(db: Database) -> None:
    """R3, MC3: the average tokens a non-cached scan call took in the organisation's own
    ledger of the last LEDGER_DAYS days; another kind, a cached page, an older row and
    another organisation's rows do not count; none is NULL."""
    customer, _ = new_customer(db)
    other, _ = new_customer(db)
    measured = catalog_model(db, name="Measured")
    unmeasured = catalog_model(db, name="Unmeasured")
    allow(db, customer, [measured, unmeasured], measured)
    old = datetime.now(UTC) - timedelta(days=31)
    with db.customer(customer) as conn:
        add_usage(conn, customer, model=measured, calls=2, input_tokens=6000, output_tokens=1000)
        add_usage(conn, customer, model=measured, calls=1, input_tokens=3000, output_tokens=2000)
        add_usage(conn, customer, model=measured, kind="card", input_tokens=99_000, output_tokens=99_000)
        add_usage(conn, customer, model=measured, cached=True, input_tokens=0, output_tokens=0, cost_usd=0)
        add_usage(conn, customer, model=measured, input_tokens=99_000, output_tokens=99_000, created_at=old)
    with db.customer(other) as conn:
        add_usage(conn, other, model=measured, input_tokens=99_000, output_tokens=99_000)
    with db.customer(customer) as conn:
        rows = {row["name"]: row for row in usable_models(conn, customer, settings=VERTEX_ONLY)}
    assert (float(rows["Measured"]["ledger_tokens_in"]), float(rows["Measured"]["ledger_tokens_out"])) == (
        3000.0,
        1000.0,
    )
    assert (rows["Unmeasured"]["ledger_tokens_in"], rows["Unmeasured"]["ledger_tokens_out"]) == (None, None)
