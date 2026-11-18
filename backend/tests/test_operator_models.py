"""The operator's model, AI settings and AI spend routes without a database (model choice,
council 11 Oct 2026, MC2, MS1, MS2, SP3, SP4; features 25-27, council 15 Oct 2026, C10):
every route needs the operator cookie, enabling a model and both AI saves need a fresh
code, each outcome's reply in plain words, the body's checks, and a usage payload with no
user or document. The SQL itself: ``test_operator_models_db.py``."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
from fastapi import FastAPI
from sqlalchemy import Connection, Engine

from kaagaz.answering.draft import ANSWER_PROMPT_HASH
from kaagaz.api import totp
from kaagaz.api.auth import (
    OPERATOR_COOKIE,
    SESSION_COOKIE,
    OperatorClaims,
    SessionClaims,
    sign_operator,
    sign_session,
)
from kaagaz.api.secret_box import SecretBox
from kaagaz.db import operator_models as models_sql
from kaagaz.db import operator_orgs as orgs_sql
from kaagaz.db import operators as operators_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.operator_models import AiChoice
from kaagaz.db.operator_orgs import Outcome
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from tests.test_api_auth import _offline_client
from tests.test_api_support import ANY_ID, CSRF, NOW, route_calls

OID, CID = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
UID = "44444444-4444-4444-8444-444444444444"  # a customer admin
AT = int(NOW.timestamp())
STEP = AT // totp.STEP_SECONDS
OWN = totp.new_secret()
MODEL = "google/gemini-3.1-pro-preview"
OTHER = "anthropic/claude-sonnet-5.5"
# Changed on purpose (features 25-27, 15 Oct 2026): /orgs/{id}/models became /orgs/{id}/ai,
# and the platform defaults have their own routes.
MODEL_ROUTES = {
    ("GET", "/api/operator/models"),
    ("PUT", "/api/operator/models/{model:path}"),  # route_calls leaves a path converter as it is
    ("GET", "/api/operator/ai-defaults"),
    ("PUT", "/api/operator/ai-defaults"),
    ("GET", f"/api/operator/orgs/{ANY_ID}/ai"),
    ("PUT", f"/api/operator/orgs/{ANY_ID}/ai"),
    ("GET", f"/api/operator/orgs/{ANY_ID}/model-rates"),
    ("PUT", f"/api/operator/orgs/{ANY_ID}/model-rates"),
    ("PUT", f"/api/operator/orgs/{ANY_ID}/users/{ANY_ID}/model-rate"),
    ("GET", "/api/operator/usage"),
    ("POST", "/api/operator/scan-cost"),  # #463: the approximate cost a page
    ("GET", "/api/operator/storage-limit"),  # #516
    ("PUT", "/api/operator/storage-limit"),
}
CATALOGUE_ROW = {
    "id": MODEL,
    "name": "Google: Gemini 3.1 Pro Preview",
    "price_in_usd_per_m": Decimal("2.000000"),
    "price_out_usd_per_m": Decimal("12.000000"),
    "providers": ["google-vertex"],
    "efforts": ["low", "medium", "high"],
    "expires_on": None,
    "eligible": True,
    "seen_at": NOW,
    "verified_prompt_version": READER_PROMPT_VERSION,
    "verified_at": NOW,
    "tokens_per_page_in": 1290,
    "tokens_per_page_out": 830,
    "enabled_at": None,
    "ledger_tokens_in": Decimal("2400.5"),
    "ledger_tokens_out": None,
    "chat_eligible": True,
    "chat_verified_prompt": ANSWER_PROMPT_HASH,
    "chat_verified_at": NOW,
    # 0033: 312 pages read, 300 with OpenRouter's cost; 40 questions, 38 with it.
    "scan_pages": 312,
    "scan_pages_costed": 300,
    "scan_cost_usd": Decimal("12.747000"),
    "scan_cost_unknown_calls": 12,
    "answer_questions": 40,
    "answer_questions_costed": 38,
    "answer_cost_usd": Decimal("0.190000"),
    "answer_cost_unknown_calls": 3,
    "price_request_usd": Decimal("0"),  # #488: its price a request and an image
    "price_image_usd": Decimal("0"),
    # #647: the parameters its approved endpoints take (0095).
    "parameters": ["max_tokens", "reasoning", "response_format", "structured_outputs", "temperature"],
}
# Changed on purpose (#463, 31 Oct 2026): the rows, bodies and env carry the scan cost settings.
# kaagaz_op_org_ai's row: the organisation's own scan models, the rest from the defaults.
ORG_ROW = {
    "scan_models": [MODEL, OTHER],
    "scan_default": MODEL,
    "chat_models": [],
    "chat_default": None,
    "scan_thinking": "medium",
    "chat_levels": None,
    "chat_default_level": None,
    "dynamic": None,
    "scan_resolution": "low",
    "careful_thinking": None,
    "careful_resolution": "high",
    "page_max_output_tokens": 32768,
    "scan_reader": None,  # Changed on purpose (#473)
    "mistral_model": None,
    "daily_page_limit": None,  # Changed on purpose (#478)
    "ulc_ceiling_paise": None,  # Changed on purpose (#488)
    "backup_reader": None,  # Changed on purpose (#664)
    "backup_model": None,
    "ensemble": True,
    "ai_budget_usd": Decimal("50.00"),
}
DEFAULTS_ROW = {
    "scan_models": [],
    "scan_default": None,
    "chat_models": [MODEL],
    "chat_default": MODEL,
    "scan_thinking": None,
    "chat_levels": ["low", "medium"],
    "chat_default_level": "low",
    "dynamic": False,
    "scan_resolution": None,
    "careful_thinking": "minimal",
    "careful_resolution": None,
    "page_max_output_tokens": None,
    "scan_reader": None,  # Changed on purpose (#473)
    "mistral_model": None,
    "daily_page_limit": None,  # Changed on purpose (#478)
    "ulc_ceiling_paise": None,  # Changed on purpose (#488)
    "backup_reader": None,  # Changed on purpose (#664)
    "backup_model": None,
    "orgs_inheriting_scan_models": 4,
    "orgs_inheriting_chat_models": 7,
    "orgs_inheriting_scan_thinking": 6,
    "orgs_inheriting_chat_levels": 7,
    "orgs_inheriting_dynamic": 5,
    "orgs_inheriting_scan_resolution": 3,
    "orgs_inheriting_careful_thinking": 2,
    "orgs_inheriting_careful_resolution": 1,
    "orgs_inheriting_page_max_output_tokens": 9,
    "orgs_inheriting_scan_reader": 8,  # Changed on purpose (#473)
    "orgs_inheriting_mistral_model": 8,
    "orgs_inheriting_daily_page_limit": 3,  # Changed on purpose (#478)
    "orgs_inheriting_ulc_ceiling_paise": 3,  # Changed on purpose (#488)
    "orgs_inheriting_backup_reader": 2,  # Changed on purpose (#664)
}
ORG_BODY: dict[str, Any] = {
    "scan_models": [MODEL, OTHER],
    "scan_default": MODEL,
    "chat_models": [],
    "chat_default": None,
    "scan_thinking": "medium",
    "chat_levels": None,
    "chat_default_level": None,
    "dynamic": None,
    "scan_resolution": "low",
    "careful_thinking": None,
    "careful_resolution": "high",
    "page_max_output_tokens": 32768,
    "scan_reader": None,  # Changed on purpose (#473)
    "mistral_model": None,
    "daily_page_limit": None,  # Changed on purpose (#478)
    "ulc_ceiling_paise": None,  # Changed on purpose (#488)
    "backup_reader": None,  # Changed on purpose (#664)
    "backup_model": None,
    "ensemble": True,
    "ai_budget_usd": 12.5,
}
DEFAULTS_BODY: dict[str, Any] = {
    "scan_models": [],
    "scan_default": None,
    "chat_models": [MODEL, OTHER],
    "chat_default": OTHER,
    "scan_thinking": "high",
    "chat_levels": ["high", "low"],
    "chat_default_level": "low",
    "dynamic": False,
    "scan_resolution": None,
    "careful_thinking": "minimal",
    "careful_resolution": "low",
    "page_max_output_tokens": None,
    "scan_reader": None,  # Changed on purpose (#473)
    "mistral_model": None,
    "daily_page_limit": None,  # Changed on purpose (#478)
    "ulc_ceiling_paise": None,  # Changed on purpose (#488)
    "backup_reader": None,  # Changed on purpose (#664)
    "backup_model": None,
}
USAGE_ROW = {
    "customer_id": CID,
    "customer_name": "Sharma Traders",
    "model": MODEL,
    "calls": 12,
    "cached_calls": 2,
    "input_tokens": Decimal(15000),
    "output_tokens": Decimal(4000),
    "reasoning_tokens": Decimal(900),
    "cost_usd": Decimal("0.123456"),
    "cost_estimated": False,
    "cost_part_unknown": True,
}
# What a usage row may hold (frontend OperatorUsageRow): never a user or a document.
USAGE_KEYS = {
    "organisation_id",
    "organisation_name",
    "model",
    "calls",
    "cached_calls",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_usd",
    "cost_estimated",
    "cost_part_unknown",
}
# The server's own settings (settings_factory): what an empty default list falls back to.
ENV = {
    "scan_model": MODEL,
    "chat_model": MODEL,
    "scan_thinking": "low",
    "scan_resolution": "high",
    "careful_thinking": "high",
    "careful_resolution": "high",
    "page_max_output_tokens": 65536,
    # Changed on purpose (#473): the server's reader and Mistral model, Mistral not set up here (no
    # key in settings_factory), the Mistral models, and Mistral's status from the runs.
    "scan_reader": "openrouter",
    "ulc_ceiling_paise": 40,  # #564 [V]: a saved Ultra low cost with no ceiling
    "switch_ceiling_paise": None,  # OCR_PAGE_CEILING_PAISE while OCR_ULTRA_LOW_COST is on
    "mistral_model": "mistral-ocr-4-1",
    "mistral_ready": False,
    "mistral_missing": "key",
    "mistral_models": [
        {"id": "mistral-ocr-4-1", "name": "Mistral OCR 4.1", "usd_per_page": 0.004, "has_blocks": True},
        {"id": "mistral-ocr-2512", "name": "Mistral OCR 3", "usd_per_page": 0.002, "has_blocks": False},
    ],
    "mistral_status": {"stopped": False, "code": None, "since": None, "documents_waiting": 0},
}
MISTRAL_ROW = {"code": None, "since": None, "documents_waiting": 0}  # #473: no document waits
TODAY_ROW = {"pages": 1240, "cost_usd": Decimal("0.42")}  # #478: the reading pause's counter today


class _NoDatabase(OperatorDatabase):
    def __init__(self) -> None:
        super().__init__(cast(Engine, None))

    @contextmanager
    def operator(self) -> Iterator[Connection]:
        yield cast(Connection, None)


class _Offline:
    """An app with a signed-in operator; the database calls are recorded in ``calls``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.client = _offline_client()
        app = cast(FastAPI, self.client.app)
        app.state.kaagaz_operator = _NoDatabase()
        self.settings = app.state.kaagaz.settings
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.outcome = "done"
        self.org: dict[str, Any] | None = dict(ORG_ROW)
        self.defaults: dict[str, Any] | None = dict(DEFAULTS_ROW)
        self.last_step: int | None = None
        monkeypatch.setattr(
            operators_sql, "session_operator", lambda conn, oid: ("ops@example.test", "Ops", 2)
        )
        monkeypatch.setattr(orgs_sql, "begin_step_up", self._begin)
        monkeypatch.setattr(orgs_sql, "step_up_failed", self._recorder("step_up_failed", None))
        monkeypatch.setattr(models_sql, "list_models", self._recorder("list_models", [CATALOGUE_ROW]))
        monkeypatch.setattr(models_sql, "usage", self._recorder("usage", [USAGE_ROW]))
        monkeypatch.setattr(
            models_sql, "set_model_enabled", self._recorder("set_model_enabled", lambda: self.outcome)
        )
        monkeypatch.setattr(
            models_sql, "set_org_ai", self._recorder("set_org_ai", lambda: Outcome(self.outcome, CID))
        )
        monkeypatch.setattr(models_sql, "org_ai", self._recorder("org_ai", lambda: self.org))
        monkeypatch.setattr(
            models_sql, "set_ai_defaults", self._recorder("set_ai_defaults", lambda: self.outcome)
        )
        monkeypatch.setattr(models_sql, "ai_defaults", self._recorder("ai_defaults", lambda: self.defaults))
        monkeypatch.setattr(models_sql, "mistral_status", self._recorder("mistral_status", MISTRAL_ROW))
        monkeypatch.setattr(models_sql, "reading_today", self._recorder("reading_today", TODAY_ROW))
        token = sign_operator(
            OperatorClaims(operator_id=OID, session_version=2, expires_at=AT + 600),
            self.settings.session_secret,
        )
        self.headers = {**CSRF, "Cookie": f"{OPERATOR_COOKIE}={token}"}

    def _begin(self, conn: Connection, oid: str, **_: Any) -> tuple[str, int | None, int]:
        sealed = SecretBox(self.settings.totp_encryption_key).seal(OWN, aad=OID)
        return sealed, self.last_step, 1

    def _recorder(self, name: str, result: object) -> Callable[..., Any]:
        def record(conn: Connection, *args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return result() if callable(result) else result

        return record

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    def code(self) -> str:
        return totp.code_at(OWN, STEP)

    def wrong_code(self) -> str:
        accepted = {totp.code_at(OWN, STEP + offset) for offset in (-1, 0, 1)}
        return next(code for code in ("000000", "111111", "222222") if code not in accepted)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> _Offline:
    return _Offline(monkeypatch)


def test_every_model_route_needs_the_operator_cookie_not_a_customer_one(offline: _Offline) -> None:
    app = cast(FastAPI, offline.client.app)
    calls = set(route_calls(app, "/api/operator"))
    assert calls >= MODEL_ROUTES
    admin = SessionClaims(
        user_id=UID, customer_id=CID, role="admin", scope="admin", session_version=2, expires_at=AT + 600
    )
    customer = {**CSRF, "Cookie": f"{SESSION_COOKIE}={sign_session(admin, offline.settings.session_secret)}"}
    body = {"enabled": True, **ORG_BODY, "code": "123456"}
    for method, path in sorted(MODEL_ROUTES):
        for headers in (CSRF, customer):
            reply = offline.client.request(method, path, headers=headers, json=body)
            assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), (
                method,
                path,
            )
    assert offline.calls == []


def test_the_old_org_models_routes_are_gone(offline: _Offline) -> None:
    """Changed on purpose (features 25-27, 15 Oct 2026): /orgs/{id}/ai replaced them."""
    path = f"/api/operator/orgs/{CID}/models"
    assert offline.client.get(path, headers=offline.headers).status_code in (404, 405)
    body = {"models": [MODEL], "default_scan": MODEL, "ai_budget_usd": None}
    assert offline.client.put(path, json=body, headers=offline.headers).status_code in (404, 405)
    assert offline.calls == []


def test_the_catalogue_says_verified_for_the_current_prompts_only(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    [model] = offline.client.get("/api/operator/models", headers=offline.headers).json()["items"]
    # Changed on purpose (features 25-27, 15 Oct 2026): the answer columns and the vendor check.
    assert model == {
        "id": MODEL,
        "name": "Google: Gemini 3.1 Pro Preview",
        "price_in_usd_per_m": 2.0,
        "price_out_usd_per_m": 12.0,
        "providers": ["google-vertex"],
        "efforts": ["low", "medium", "high"],
        "expires_on": None,
        "eligible": True,
        "seen_at": "2026-09-27T10:00:00+00:00",
        "verified": True,
        "verified_at": "2026-09-27T10:00:00+00:00",
        "tokens_per_page_in": 1290,
        "tokens_per_page_out": 830,
        "ledger_tokens_in": 2400.5,  # MC3: the ledger's average a page, numbers only
        "ledger_tokens_out": None,
        # 0033: the real cost OpenRouter reported, the unknown calls apart, numbers only.
        "scan_pages": 312,
        "scan_pages_costed": 300,
        "scan_cost_usd": 12.747,
        "scan_cost_unknown_calls": 12,
        "answer_questions": 40,
        "answer_questions_costed": 38,
        "answer_cost_usd": 0.19,
        "answer_cost_unknown_calls": 3,
        "enabled_at": None,
        "chat_eligible": True,
        "chat_verified": True,
        "vendor_allowed": True,
        # #488: can it lead Ultra low cost, and why not.
        "ulc_ready": False,
        "ulc_why": "It can't read with minimal thinking.",
        # Changed on purpose (#647): the parameters its approved endpoints take.
        "parameters": ["max_tokens", "reasoning", "response_format", "structured_outputs", "temperature"],
    }
    stale = {
        **CATALOGUE_ROW,
        "id": "example/model-x",
        "verified_prompt_version": "g0",
        "expires_on": date(2027, 1, 31),
        "chat_verified_prompt": "0" * 64,
        "parameters": None,
    }
    monkeypatch.setattr(models_sql, "list_models", lambda conn, oid: [stale])
    [model] = offline.client.get("/api/operator/models", headers=offline.headers).json()["items"]
    assert (model["verified"], model["chat_verified"], model["expires_on"]) == (False, False, "2027-01-31")
    assert model["parameters"] is None  # #647: not read yet
    assert model["vendor_allowed"] is False  # example is not in OPENROUTER_MODEL_VENDORS


def test_only_models_kaagaz_can_use_are_listed(offline: _Offline, monkeypatch: pytest.MonkeyPatch) -> None:
    # Tushar, 18 Oct 2026: a model failing the rules (no zero-data-retention provider...) is
    # not listed; one still enabled is, so it can be turned off.
    unusable = {**CATALOGUE_ROW, "eligible": False, "chat_eligible": False}
    rows = [
        {**unusable, "id": "anthropic/not-usable"},
        {**unusable, "id": "anthropic/still-on", "enabled_at": datetime(2026, 10, 1, tzinfo=UTC)},
        {**CATALOGUE_ROW, "id": "google/answers-only", "eligible": False},
        {**CATALOGUE_ROW, "id": "google/scans-only", "chat_eligible": False},
    ]
    monkeypatch.setattr(models_sql, "list_models", lambda conn, oid: rows)
    items = offline.client.get("/api/operator/models", headers=offline.headers).json()["items"]
    listed = [item["id"] for item in items]
    assert listed == ["anthropic/still-on", "google/answers-only", "google/scans-only"]


def test_enabling_a_model_needs_a_fresh_code_and_names_both_current_prompts(offline: _Offline) -> None:
    path = f"/api/operator/models/{MODEL}"
    assert offline.client.put(path, json={"enabled": True}, headers=offline.headers).status_code == 400
    refused = offline.client.put(
        path, json={"enabled": True, "code": offline.wrong_code()}, headers=offline.headers
    )
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "step_up_failed")
    assert offline.names() == ["step_up_failed"]
    offline.calls.clear()
    done = offline.client.put(path, json={"enabled": True, "code": offline.code()}, headers=offline.headers)
    assert done.json() == {"model": {"id": MODEL, "enabled": True, "changed": True}}
    [(name, args, kwargs)] = offline.calls
    assert (name, args) == ("set_model_enabled", (OID, MODEL))
    # Changed on purpose (features 25-27, 15 Oct 2026): a model may be enabled for answering too.
    assert kwargs == {
        "enabled": True,
        "scan_prompt": READER_PROMPT_VERSION,
        "chat_prompt": ANSWER_PROMPT_HASH,
        "step": STEP,
    }


@pytest.mark.parametrize("model", ["not-a-model", "google/gemini-3:free", "google/../key"])
def test_a_model_that_is_not_an_openrouter_id_is_404_before_any_code_is_used(
    offline: _Offline, model: str
) -> None:
    reply = offline.client.put(
        f"/api/operator/models/{model}",
        json={"enabled": True, "code": offline.code()},
        headers=offline.headers,
    )
    assert reply.status_code in (404, 405), reply.text
    assert offline.calls == []


@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [
        ("done", 200, None),
        ("unchanged", 200, None),
        ("missing", 404, "not_found"),
        ("not_eligible", 409, "not_eligible"),
        ("not_verified", 409, "not_verified"),
        ("in_use", 409, "in_use"),
        ("signed_out", 401, "not_signed_in"),
        ("step_up", 403, "step_up_failed"),
    ],
)
def test_each_enable_outcome_gets_its_reply(
    offline: _Offline, outcome: str, status: int, code: str | None
) -> None:
    offline.outcome = outcome
    reply = offline.client.put(
        f"/api/operator/models/{MODEL}",
        json={"enabled": False, "code": offline.code()},
        headers=offline.headers,
    )
    assert reply.status_code == status, reply.text
    if code is None:
        assert reply.json()["model"] == {"id": MODEL, "enabled": False, "changed": outcome == "done"}
    else:
        assert reply.json()["error"]["code"] == code
    if code in ("not_eligible", "not_verified", "in_use"):
        assert reply.json()["error"]["message"].endswith(".")  # plain words, not a code


# --- One organisation's AI settings (C10) ------------------------------------------------


def test_an_organisations_ai_settings_round_trip_with_the_defaults_it_follows(offline: _Offline) -> None:
    path = f"/api/operator/orgs/{CID}/ai"
    got = offline.client.get(path, headers=offline.headers)
    assert got.status_code == 200, got.text
    reply = got.json()
    assert reply == {
        "own": {
            "scan_models": [MODEL, OTHER],
            "scan_default": MODEL,
            "chat_models": [],  # empty: follows the platform defaults
            "chat_default": None,
            "scan_thinking": "medium",
            "scan_resolution": "low",
            "careful_thinking": None,
            "careful_resolution": "high",
            "page_max_output_tokens": 32768,
            "scan_reader": None,  # Changed on purpose (#473)
            "mistral_model": None,
            "daily_page_limit": None,  # Changed on purpose (#478)
            "ulc_ceiling_paise": None,  # Changed on purpose (#488)
            "backup_reader": None,  # Changed on purpose (#664)
            "backup_model": None,
            "chat_levels": None,
            "chat_default_level": None,
            "dynamic": None,
        },
        "defaults": {
            "scan_models": [],
            "scan_default": None,
            "chat_models": [MODEL],
            "chat_default": MODEL,
            "scan_thinking": None,
            "scan_resolution": None,
            "careful_thinking": "minimal",
            "careful_resolution": None,
            "page_max_output_tokens": None,
            "scan_reader": None,  # Changed on purpose (#473)
            "mistral_model": None,
            "daily_page_limit": None,  # Changed on purpose (#478)
            "ulc_ceiling_paise": None,  # Changed on purpose (#488)
            "backup_reader": None,  # Changed on purpose (#664)
            "backup_model": None,
            "chat_levels": ["low", "medium"],
            "chat_default_level": "low",
            "dynamic": False,
        },
        "env": ENV,
        "ensemble": True,
        "max_agents": 3,
        "ai_budget_usd": 50.0,
        "reading_today": {"pages": 1240, "cost_usd": 0.42},  # Changed on purpose (#478): the operator's
    }
    put = offline.client.put(path, json={**ORG_BODY, "code": offline.code()}, headers=offline.headers)
    assert put.status_code == 200, put.text
    assert put.json() == {**reply, "ensemble_off": False}  # the settings as saved, read again
    [(_, args, kwargs)] = [call for call in offline.calls if call[0] == "set_org_ai"]
    assert args == (
        OID,
        CID,
        AiChoice(
            scan_models=[MODEL, OTHER],
            scan_default=MODEL,
            chat_models=[],
            chat_default=None,
            scan_thinking="medium",
            chat_levels=None,
            chat_default_level=None,
            dynamic=None,
            scan_resolution="low",
            careful_thinking=None,
            careful_resolution="high",
            page_max_output_tokens=32768,
        ),
    )
    assert kwargs == {
        "ensemble": True,
        "budget": Decimal("12.5"),
        "scan_prompt": READER_PROMPT_VERSION,
        "chat_prompt": ANSWER_PROMPT_HASH,
        "step": STEP,
    }
    offline.org = None
    assert offline.client.get(path, headers=offline.headers).status_code == 404


def test_saving_an_organisations_ai_settings_needs_a_fresh_code(offline: _Offline) -> None:
    path = f"/api/operator/orgs/{CID}/ai"
    assert offline.client.put(path, json=ORG_BODY, headers=offline.headers).status_code == 400
    refused = offline.client.put(
        path, json={**ORG_BODY, "code": offline.wrong_code()}, headers=offline.headers
    )
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "step_up_failed")
    # Changed on purpose (#488): a save that follows the platform's reader reads the defaults first
    # (Ultra low cost may be theirs), before the code is checked; nothing is written.
    assert offline.names() == ["ai_defaults", "step_up_failed"]


def test_ensemble_saved_off_with_one_scanning_model_is_said_not_refused(offline: _Offline) -> None:
    # 0037: with fewer than two scanning models in effect the save is done and Ensemble off.
    offline.org = {**ORG_ROW, "scan_models": [MODEL], "dynamic": True, "ensemble": False}
    body = {**ORG_BODY, "scan_models": [MODEL], "dynamic": True, "code": offline.code()}
    put = offline.client.put(f"/api/operator/orgs/{CID}/ai", json=body, headers=offline.headers)
    assert put.status_code == 200, put.text
    assert (put.json()["ensemble"], put.json()["ensemble_off"]) == (False, True)


# Every outcome of kaagaz_op_set_org_ai (C10): a status, a code and plain words.
ORG_OUTCOMES = [
    ("done", 200, None),
    ("missing", 404, "not_found"),
    ("deleting", 409, "conflict"),
    ("unavailable", 409, "unavailable"),
    ("not_verified", 409, "not_verified"),
    ("chat_not_verified", 409, "chat_not_verified"),
    ("level_not_supported", 409, "level_not_supported"),
    ("default_not_allowed", 409, "default_not_allowed"),
    # Changed on purpose (Tushar, 16 Oct 2026: any number of models): 0037 no longer returns
    # needs_two_models_scan or needs_two_models_chat.
    ("signed_out", 401, "not_signed_in"),
    ("step_up", 403, "step_up_failed"),
]


@pytest.mark.parametrize(("outcome", "status", "code"), ORG_OUTCOMES)
def test_each_org_ai_outcome_gets_its_reply(
    offline: _Offline, outcome: str, status: int, code: str | None
) -> None:
    offline.outcome = outcome
    reply = offline.client.put(
        f"/api/operator/orgs/{CID}/ai", json={**ORG_BODY, "code": offline.code()}, headers=offline.headers
    )
    assert reply.status_code == status, reply.text
    if code is not None:
        assert reply.json()["error"]["code"] == code
        message = reply.json()["error"]["message"]
        assert message.endswith(".") and "_" not in message  # plain words, not a code


@pytest.mark.parametrize(
    "change",
    [
        {"ai_budget_usd": 0},
        {"ai_budget_usd": -5},
        {"ai_budget_usd": 1.005},
        {"ai_budget_usd": 10_000_000_000},
        {"scan_models": ["gemini-3"]},
        {"scan_default": "google/gemini:free"},
        {"chat_models": ["google/../key"]},
        {"chat_default": "nope"},
        {"scan_models": [MODEL] * 65},
        {"scan_thinking": "max"},
        {"scan_resolution": "medium"},  # #463: low or high only
        {"careful_thinking": "max"},
        {"careful_resolution": "ultra_high"},
        {"page_max_output_tokens": "4096"},  # #463: a whole number, strictly
        {"page_max_output_tokens": 4096.5},
        {"chat_levels": ["minimal"]},  # #463: minimal is for scanning only
        {"chat_default_level": "minimal"},
        {"chat_levels": []},
        {"chat_levels": ["low", "medium", "high", "low"]},
        {"chat_levels": ["quick"]},
        {"chat_default_level": "balanced"},
        {"ensemble": None},
        {"user_id": UID},
    ],
)
def test_a_bad_org_ai_body_is_400_before_a_code_is_used(offline: _Offline, change: dict[str, Any]) -> None:
    body = {**ORG_BODY, **change, "code": offline.code()}
    reply = offline.client.put(f"/api/operator/orgs/{CID}/ai", json=body, headers=offline.headers)
    assert reply.status_code == 400, reply.text
    assert offline.calls == []  # not even the step-up's count


def test_a_missing_org_ai_field_is_400(offline: _Offline) -> None:
    body = {key: value for key, value in ORG_BODY.items() if key != "dynamic"}
    reply = offline.client.put(
        f"/api/operator/orgs/{CID}/ai", json={**body, "code": offline.code()}, headers=offline.headers
    )
    assert reply.status_code == 400, reply.text


# --- The platform defaults (C10, D8) --------------------------------------------------------


def test_the_platform_defaults_say_how_many_organisations_follow_each_area(offline: _Offline) -> None:
    reply = offline.client.get("/api/operator/ai-defaults", headers=offline.headers)
    assert reply.status_code == 200, reply.text
    assert reply.json() == {
        "defaults": {
            "scan_models": [],
            "scan_default": None,
            "chat_models": [MODEL],
            "chat_default": MODEL,
            "scan_thinking": None,
            "scan_resolution": None,
            "careful_thinking": "minimal",
            "careful_resolution": None,
            "page_max_output_tokens": None,
            "scan_reader": None,  # Changed on purpose (#473)
            "mistral_model": None,
            "daily_page_limit": None,  # Changed on purpose (#478)
            "ulc_ceiling_paise": None,  # Changed on purpose (#488)
            "backup_reader": None,  # Changed on purpose (#664)
            "backup_model": None,
            "chat_levels": ["low", "medium"],
            "chat_default_level": "low",
            "dynamic": False,
        },
        "env": ENV,
        "orgs_inheriting": {
            "scan_models": 4,
            "chat_models": 7,
            "scan_thinking": 6,
            "chat_levels": 7,
            "dynamic": 5,
            "scan_resolution": 3,
            "careful_thinking": 2,
            "careful_resolution": 1,
            "page_max_output_tokens": 9,
            "scan_reader": 8,  # Changed on purpose (#473)
            "mistral_model": 8,
            "daily_page_limit": 3,  # Changed on purpose (#478)
            "ulc_ceiling_paise": 3,  # Changed on purpose (#488)
            "backup_reader": 2,  # Changed on purpose (#664)
        },
    }
    offline.defaults = None  # the operator was disabled meanwhile
    gone = offline.client.get("/api/operator/ai-defaults", headers=offline.headers)
    assert (gone.status_code, gone.json()["error"]["code"]) == (401, "not_signed_in")


def test_saving_the_platform_defaults_needs_a_fresh_code_and_sends_the_choice(offline: _Offline) -> None:
    path = "/api/operator/ai-defaults"
    assert offline.client.put(path, json=DEFAULTS_BODY, headers=offline.headers).status_code == 400
    refused = offline.client.put(
        path, json={**DEFAULTS_BODY, "code": offline.wrong_code()}, headers=offline.headers
    )
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "step_up_failed")
    assert offline.names() == ["step_up_failed"]
    offline.calls.clear()
    done = offline.client.put(path, json={**DEFAULTS_BODY, "code": offline.code()}, headers=offline.headers)
    assert done.status_code == 200, done.text
    assert set(done.json()) == {"defaults", "env", "orgs_inheriting"}
    [(_, args, kwargs)] = [call for call in offline.calls if call[0] == "set_ai_defaults"]
    assert args == (
        OID,
        AiChoice(
            scan_models=[],
            scan_default=None,
            chat_models=[MODEL, OTHER],
            chat_default=OTHER,
            scan_thinking="high",
            chat_levels=["high", "low"],
            chat_default_level="low",
            dynamic=False,
            scan_resolution=None,
            careful_thinking="minimal",
            careful_resolution="low",
        ),
    )
    assert kwargs == {"scan_prompt": READER_PROMPT_VERSION, "chat_prompt": ANSWER_PROMPT_HASH, "step": STEP}


@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [row for row in ORG_OUTCOMES if row[0] not in ("missing", "deleting")],
)
def test_each_defaults_outcome_gets_its_reply(
    offline: _Offline, outcome: str, status: int, code: str | None
) -> None:
    offline.outcome = outcome
    reply = offline.client.put(
        "/api/operator/ai-defaults", json={**DEFAULTS_BODY, "code": offline.code()}, headers=offline.headers
    )
    assert reply.status_code == status, reply.text
    if code is not None:
        assert reply.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "change",
    [
        {"dynamic": None},  # the platform always says on or off
        {"chat_levels": None},
        {"chat_default_level": None},
        {"ensemble": False},  # Ensemble is an organisation's own
        {"ai_budget_usd": 10},  # so is a budget
        {"chat_models": ["gemini"]},
    ],
)
def test_a_bad_defaults_body_is_400_before_a_code_is_used(offline: _Offline, change: dict[str, Any]) -> None:
    body = {**DEFAULTS_BODY, **change, "code": offline.code()}
    reply = offline.client.put("/api/operator/ai-defaults", json=body, headers=offline.headers)
    assert reply.status_code == 400, reply.text
    assert offline.calls == []


# --- AI spend --------------------------------------------------------------------------------


def test_usage_is_per_organisation_and_model_with_no_user_or_document(offline: _Offline) -> None:
    reply = offline.client.get("/api/operator/usage", headers=offline.headers)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["from"], body["to"]) == ("2026-09-01", "2026-09-27")  # this month so far, in TIMEZONE
    [row] = body["items"]
    assert set(row) == USAGE_KEYS
    assert row == {
        "organisation_id": CID,
        "organisation_name": "Sharma Traders",
        "model": MODEL,
        "calls": 12,
        "cached_calls": 2,
        "input_tokens": 15000,
        "output_tokens": 4000,
        "reasoning_tokens": 900,
        "cost_usd": 0.123456,
        "cost_estimated": False,
        "cost_part_unknown": True,  # R9: the sum counts the known costs only
    }
    [(_, args, _)] = offline.calls
    # Days in Asia/Kolkata, both ends included: [1 Sep 00:00 IST, 28 Sep 00:00 IST).
    assert args == (OID, datetime(2026, 8, 31, 18, 30, tzinfo=UTC), datetime(2026, 9, 27, 18, 30, tzinfo=UTC))


@pytest.mark.parametrize(
    "query",
    ["from=2026-09-30&to=2026-09-01", "from=2025-01-01&to=2026-01-02", "from=yesterday", "to=2026-13-01"],
)
def test_usage_refuses_a_bad_period(offline: _Offline, query: str) -> None:
    reply = offline.client.get(f"/api/operator/usage?{query}", headers=offline.headers)
    assert reply.status_code == 400, reply.text
    assert offline.calls == []


def test_usage_takes_a_full_year_of_days(offline: _Offline) -> None:
    reply = offline.client.get("/api/operator/usage?from=2025-01-01&to=2026-01-01", headers=offline.headers)
    assert reply.status_code == 200, reply.text  # 366 days, both ends included
    assert (reply.json()["from"], reply.json()["to"]) == ("2025-01-01", "2026-01-01")
