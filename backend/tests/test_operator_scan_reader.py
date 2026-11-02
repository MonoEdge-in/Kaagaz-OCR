"""Who reads scanned pages, per organisation and as the platform default (#473, Tushar, 2 Nov
2026): the reader (``scan_reader``, openrouter or mistral) and the Mistral model inherit
organisation > platform > code default, ride on the ``Reading``, and the operator's API saves
and shows them. Mistral can be chosen only when the server has its key and zero data retention
confirmed (409 ``mistral_not_set_up``); the key never leaves the server; ``env.mistral_status``
says whether Mistral stopped, from the runs that waited on it. Org admins never set or see them.

Every test that saves the platform defaults puts them back (the ``platform`` fixture)."""

from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest

from kaagaz.api.routers import operator_models as router
from kaagaz.db import models
from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database
from kaagaz.reading.mistral_ocr import DEFAULT_MISTRAL_MODEL, MISTRAL_OCR_MODELS, SCAN_READERS
from kaagaz.settings import Settings, SettingsError
from tests.db_support import insert_document, new_customer
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api, wrong_code
from tests.test_isolation_ai_settings import NO_DEFAULTS, platform, set_defaults, set_org
from tests.test_migration_0023_db import as_owner, catalog_model
from tests.test_operator_models_db import FOLLOWS_DEFAULTS, _save, _signed_in
from tests.test_scan_cost_settings_db import reading_of
from tests.test_settings import shipped

__all__ = ["platform"]  # the fixture, used by name
KEY = "test-mistral-key"  # made up
READY = make_settings(mistral_api_key=KEY, mistral_zdr_approved=True)
ORG_BODY = {**FOLLOWS_DEFAULTS, "ensemble": False, "ai_budget_usd": None}
PLATFORM_BODY = {**FOLLOWS_DEFAULTS, "chat_levels": ["low"], "chat_default_level": "low", "dynamic": False}


def _reader(reading: models.Reading) -> tuple[str, str]:
    return reading.scan_reader, reading.mistral_model


# --- settings and the Reading (no database) -------------------------------------------------


def test_settings_mistral_ready_needs_the_key_and_zdr() -> None:
    assert not Settings.from_env(shipped()).mistral_ready  # .env.example: neither
    keyed = Settings.from_env(shipped(MISTRAL_API_KEY=KEY))
    assert keyed.mistral_api_key == KEY and not keyed.mistral_ready
    assert Settings.from_env(shipped(MISTRAL_API_KEY=KEY, MISTRAL_ZDR_APPROVED="true")).mistral_ready
    assert not Settings.from_env(shipped(MISTRAL_ZDR_APPROVED="true")).mistral_ready
    assert KEY not in repr(keyed)
    with pytest.raises(SettingsError, match="MISTRAL_API_KEY") as refused:
        Settings.from_env(shipped(MISTRAL_API_KEY="has a space"))
    assert "space" not in str(refused.value).split("MISTRAL_API_KEY")[0]  # the name, never the value


def test_missing_says_key_or_zdr() -> None:
    def missing(settings: Settings) -> Any:
        env = router._env(settings)
        return env["mistral_ready"], env["mistral_missing"]

    assert missing(make_settings()) == (False, "key")
    assert missing(make_settings(mistral_zdr_approved=True)) == (False, "key")
    assert missing(make_settings(mistral_api_key=KEY)) == (False, "zdr")
    assert missing(READY) == (True, None)
    env = router._env(READY)
    assert (env["scan_reader"], env["mistral_model"]) == ("openrouter", "mistral-ocr-4-1")
    assert env["mistral_models"] == [
        {"id": "mistral-ocr-4-1", "name": "Mistral OCR 4.1", "usd_per_page": 0.004, "has_blocks": True},
        {"id": "mistral-ocr-2512", "name": "Mistral OCR 3", "usd_per_page": 0.002, "has_blocks": False},
    ]


def test_config_records_reader() -> None:
    lead = models.Agent("google/x", "google-vertex", "X")
    config = models.Reading("single", lead, scan_reader="mistral", mistral_model="mistral-ocr-2512").config()
    assert (config["scan_reader"], config["mistral_model"]) == ("mistral", "mistral-ocr-2512")
    plain = models.Reading("single", lead)
    assert _reader(plain) == ("openrouter", DEFAULT_MISTRAL_MODEL)


def test_the_bodies_offer_exactly_the_readers_and_models() -> None:
    assert set(get_args(router.ScanReader)) == set(SCAN_READERS)
    assert set(get_args(router.MistralModelId)) == set(MISTRAL_OCR_MODELS)


# --- inheritance and the saves (PostgreSQL) -------------------------------------------------


@pytest.mark.db
def test_org_beats_platform_beats_default(db: Database, platform: str) -> None:
    own, inherits = new_customer(db)[0], new_customer(db)[0]
    assert _reader(reading_of(db, inherits)) == ("openrouter", DEFAULT_MISTRAL_MODEL)  # the code's
    assert (
        set_org(
            db, platform, own, ops.AiChoice(scan_reader="openrouter", mistral_model="mistral-ocr-4-1")
        ).code
        == "done"
    )
    defaults = ops.AiChoice(
        chat_levels=["low"],
        chat_default_level="low",
        dynamic=False,
        scan_reader="mistral",
        mistral_model="mistral-ocr-2512",
    )
    assert set_defaults(db, platform, defaults) == "done"
    assert _reader(reading_of(db, inherits)) == ("mistral", "mistral-ocr-2512")  # the platform's
    assert _reader(reading_of(db, own)) == ("openrouter", "mistral-ocr-4-1")  # its own
    assert set_defaults(db, platform, NO_DEFAULTS) == "done"
    assert _reader(reading_of(db, inherits)) == ("openrouter", DEFAULT_MISTRAL_MODEL)


@pytest.mark.db
def test_other_org_unchanged(db: Database, platform: str) -> None:
    mine, other = new_customer(db)[0], new_customer(db)[0]
    assert set_org(db, platform, mine, ops.AiChoice(scan_reader="mistral")).code == "done"
    assert _reader(reading_of(db, mine))[0] == "mistral"
    assert _reader(reading_of(db, other))[0] == "openrouter"
    assert (
        as_owner(db, "SELECT scan_reader FROM customer_ai_settings WHERE customer_id = :c", {"c": other})
        == []
    )


@pytest.mark.db
def test_no_ensemble_with_mistral(db: Database, platform: str) -> None:
    """Ensemble reads pages again with other Gemini models: never for a Mistral organisation."""
    settings = make_settings(ocr_engine="gemini")
    customer, _ = new_customer(db)
    first, second = catalog_model(db), catalog_model(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer)
    for reader, allowed in (("openrouter", True), ("mistral", False)):
        choice = ops.AiChoice([first, second], first, scan_reader=reader)
        assert set_org(db, platform, customer, choice, ensemble=True).code == "done"
        with db.customer(customer) as conn:
            if allowed:
                assert models.resolve_ensemble(conn, customer, document, settings=settings).mode == "ensemble"
            else:
                with pytest.raises(models.AiNotAllowed, match="ensemble_not_allowed"):
                    models.resolve_ensemble(conn, customer, document, settings=settings)


# --- the operator's API ---------------------------------------------------------------------


@pytest.mark.db
def test_mistral_refused_when_not_set_up(db: Database, platform: str) -> None:
    for settings in (
        make_settings(),
        make_settings(mistral_api_key=KEY),
        make_settings(mistral_zdr_approved=True),
    ):
        api = Api(db, settings=settings)
        operator, client = _signed_in(api)
        customer = api.customer("Mistral Not Set Up")[0]
        for path, body in (
            (f"/api/operator/orgs/{customer}/ai", ORG_BODY),
            ("/api/operator/ai-defaults", PLATFORM_BODY),
        ):
            refused = _save(api, operator, client, path, {**body, "scan_reader": "mistral"})
            assert refused.status_code == 409, refused.text
            assert refused.json()["error"] == {
                "code": "mistral_not_set_up",
                "message": "Mistral isn't set up on this server yet.",
            }
        # Gemini, and a Mistral model kept for later, still save.
        kept = {**ORG_BODY, "scan_reader": "openrouter", "mistral_model": "mistral-ocr-2512"}
        saved = _save(api, operator, client, f"/api/operator/orgs/{customer}/ai", kept)
        assert saved.status_code == 200, saved.text
        assert (saved.json()["own"]["scan_reader"], saved.json()["own"]["mistral_model"]) == (
            "openrouter",
            "mistral-ocr-2512",
        )

    api = Api(db, settings=READY)
    operator, client = _signed_in(api)
    customer = api.customer("Mistral Set Up")[0]
    body = {**ORG_BODY, "scan_reader": "mistral", "mistral_model": "mistral-ocr-4-1"}
    saved = _save(api, operator, client, f"/api/operator/orgs/{customer}/ai", body)
    assert saved.status_code == 200, saved.text
    assert (saved.json()["own"]["scan_reader"], saved.json()["own"]["mistral_model"]) == (
        "mistral",
        "mistral-ocr-4-1",
    )
    on_platform = _save(
        api, operator, client, "/api/operator/ai-defaults", {**PLATFORM_BODY, "scan_reader": "mistral"}
    )
    assert on_platform.status_code == 200, on_platform.text
    reply = on_platform.json()
    assert reply["defaults"]["scan_reader"] == "mistral"
    assert {"scan_reader", "mistral_model"} <= set(reply["orgs_inheriting"])
    assert reply["orgs_inheriting"]["scan_reader"] >= 1


@pytest.mark.db
def test_unknown_values_400(db: Database) -> None:
    """FastAPI's validation refuses an unknown reader or model; the app answers it 400 bad_request."""
    api = Api(db, settings=READY)
    operator, client = _signed_in(api)
    customer = api.customer("Unknown Reader")[0]
    for bad in (
        {"scan_reader": "gemini"},
        {"scan_reader": "MISTRAL"},
        {"mistral_model": "mistral-ocr-latest"},
        {"mistral_model": "mistral-ocr-2505"},
    ):
        for path, body in (
            (f"/api/operator/orgs/{customer}/ai", ORG_BODY),
            ("/api/operator/ai-defaults", PLATFORM_BODY),
        ):
            refused = _save(api, operator, client, path, {**body, **bad})
            assert (refused.status_code, refused.json()["error"]["code"]) == (400, "bad_request"), bad
        estimate = client.post("/api/operator/scan-cost", json=bad, headers=CSRF)
        assert estimate.status_code == 400, bad
    # The estimate and the accuracy check take both (#473 frontend): the draft's reader and model.
    draft = {"scan_reader": "mistral", "mistral_model": "mistral-ocr-2512"}
    estimate = client.post("/api/operator/scan-cost", json=draft, headers=CSRF)
    assert estimate.status_code == 200, estimate.text
    assert (estimate.json()["fixed_price"], estimate.json()["fixed_usd_per_page"]) == (False, None)


@pytest.mark.db
def test_put_needs_step_up(db: Database) -> None:
    api = Api(db, settings=READY)
    operator, client = _signed_in(api)
    customer = api.customer("Step Up Reader")[0]
    path = f"/api/operator/orgs/{customer}/ai"
    body = {**ORG_BODY, "scan_reader": "mistral", "code": wrong_code(api, operator.secret)}
    refused = client.put(path, json=body, headers=CSRF)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "step_up_failed")
    assert client.get(path).json()["own"]["scan_reader"] is None  # nothing saved


@pytest.mark.db
def test_env_never_contains_key(db: Database) -> None:
    api = Api(db, settings=READY)
    _, client = _signed_in(api)
    customer = api.customer("Key Never Shown")[0]
    for path in (f"/api/operator/orgs/{customer}/ai", "/api/operator/ai-defaults"):
        reply = client.get(path)
        assert reply.status_code == 200 and KEY not in reply.text
        env = reply.json()["env"]
        assert (env["mistral_ready"], env["mistral_missing"]) == (True, None)
        assert set(env["mistral_status"]) == {"stopped", "code", "since", "documents_waiting"}
        assert not [name for name in env if "key" in name.lower()]


@pytest.mark.db
def test_org_admin_route_refuses_reader_fields(db: Database) -> None:
    api = Api(db, settings=READY)
    customer = api.customer("Admin Never Sees")[0]
    admin = api.admin_sign_in(customer)
    for method, path, body in (
        ("put", "/api/ai-settings/organisation", {"tasks": {}, "scan_reader": "mistral"}),
        ("put", "/api/ai-settings/organisation", {"tasks": {}, "mistral_model": "mistral-ocr-4-1"}),
        ("patch", "/api/admin/settings", {"scan_reader": "mistral"}),
    ):
        reply = getattr(admin.client, method)(path, json=body, headers=CSRF)
        assert reply.status_code == 400, (path, body)
    for path in ("/api/ai-settings", "/api/admin/settings"):
        reply = admin.client.get(path)
        assert (
            reply.status_code == 200
            and "mistral" not in reply.text.lower()
            and "scan_reader" not in reply.text
        )


_RUN = (
    "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status, error_code)"
    " VALUES (:c, :d, :s, :f, :st, :e)"
)


@pytest.mark.db
def test_status_counts_waiting_mistral_documents(db: Database) -> None:
    """Every organisation's documents whose newest finished run waited on Mistral, in the last
    24 hours; a newer run that ended otherwise ends the wait, a running one does not."""
    api = Api(db, settings=READY)
    _, client = _signed_in(api)

    def status() -> dict[str, Any]:
        reply = client.get("/api/operator/ai-defaults")
        assert reply.status_code == 200
        result: dict[str, Any] = reply.json()["env"]["mistral_status"]
        return result

    before = status()["documents_waiting"]
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        waits, recovered, old, retrying = (insert_document(conn, customer) for _ in range(4))
    now = datetime.now(UTC)

    def run(document: str, minutes_ago: float, status: str, code: str | None) -> None:
        started = now - timedelta(minutes=minutes_ago)
        finished = None if status == "running" else started + timedelta(seconds=5)
        params = {"c": customer, "d": document, "s": started, "f": finished, "st": status, "e": code}
        as_owner(db, _RUN, params)

    run(waits, 30, "failed", "mistral_unavailable")
    run(waits, 1, "failed", "mistral_key_unavailable")  # the newest wait: the key
    run(recovered, 20, "failed", "mistral_unavailable")
    run(recovered, 10, "ready", None)
    run(old, 25 * 60, "failed", "mistral_unavailable")  # over a day ago
    run(retrying, 5, "failed", "mistral_unavailable")
    run(retrying, 2, "running", None)

    after = status()
    assert after["documents_waiting"] - before == 2  # waits and retrying, each once
    assert after["stopped"] is True and after["code"] == "mistral_key"
    assert datetime.fromisoformat(after["since"]) <= now - timedelta(minutes=5)
    assert customer not in str(after)  # counts and codes only
