"""The operator's model and AI spend routes without a database (model choice, council 11
Oct 2026, MC2, MS1, MS2, SP3, SP4): every route needs the operator cookie, enabling a
model needs a fresh code, each outcome's reply in plain words, the body's checks, and a
usage payload with no user or document. The SQL itself: ``test_operator_models_db.py``."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
from fastapi import FastAPI
from sqlalchemy import Connection, Engine

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
MODEL_ROUTES = {
    ("GET", "/api/operator/models"),
    ("PUT", "/api/operator/models/{model:path}"),  # route_calls leaves a path converter as it is
    ("GET", f"/api/operator/orgs/{ANY_ID}/models"),
    ("PUT", f"/api/operator/orgs/{ANY_ID}/models"),
    ("GET", "/api/operator/usage"),
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
}


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
        self.org: dict[str, Any] | None = {
            "models": [MODEL],
            "default_scan": MODEL,
            "ai_budget_usd": Decimal("50.00"),
        }
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
            models_sql, "set_org_models", self._recorder("set_org_models", lambda: Outcome(self.outcome, CID))
        )
        monkeypatch.setattr(models_sql, "org_models", self._recorder("org_models", lambda: self.org))
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
    body = {"enabled": True, "code": "123456", "models": [], "default_scan": None, "ai_budget_usd": None}
    for method, path in sorted(MODEL_ROUTES):
        for headers in (CSRF, customer):
            reply = offline.client.request(method, path, headers=headers, json=body)
            assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), (
                method,
                path,
            )
    assert offline.calls == []


def test_the_catalogue_says_verified_for_the_current_prompt_only(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    [model] = offline.client.get("/api/operator/models", headers=offline.headers).json()["items"]
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
        "enabled_at": None,
    }
    stale = {**CATALOGUE_ROW, "verified_prompt_version": "g0", "expires_on": date(2027, 1, 31)}
    monkeypatch.setattr(models_sql, "list_models", lambda conn, oid: [stale])
    [model] = offline.client.get("/api/operator/models", headers=offline.headers).json()["items"]
    assert (model["verified"], model["expires_on"]) == (False, "2027-01-31")


def test_enabling_a_model_needs_a_fresh_code_and_names_the_current_prompt(offline: _Offline) -> None:
    path = f"/api/operator/models/{MODEL}"
    assert offline.client.put(path, json={"enabled": True}, headers=offline.headers).status_code == 400
    accepted = {totp.code_at(OWN, STEP + offset) for offset in (-1, 0, 1)}
    wrong = next(code for code in ("000000", "111111", "222222") if code not in accepted)
    refused = offline.client.put(path, json={"enabled": True, "code": wrong}, headers=offline.headers)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "step_up_failed")
    assert offline.names() == ["step_up_failed"]
    offline.calls.clear()
    done = offline.client.put(path, json={"enabled": True, "code": offline.code()}, headers=offline.headers)
    assert done.json() == {"model": {"id": MODEL, "enabled": True, "changed": True}}
    [(name, args, kwargs)] = offline.calls
    assert (name, args) == ("set_model_enabled", (OID, MODEL))
    assert kwargs == {"enabled": True, "prompt_version": READER_PROMPT_VERSION, "step": STEP}


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


def test_an_organisations_models_and_budget_round_trip(offline: _Offline) -> None:
    path = f"/api/operator/orgs/{CID}/models"
    got = offline.client.get(path, headers=offline.headers)
    assert got.json() == {"models": [MODEL], "default_scan": MODEL, "ai_budget_usd": 50.0}
    body = {"models": [MODEL], "default_scan": MODEL, "ai_budget_usd": 12.5}
    put = offline.client.put(path, json=body, headers=offline.headers)
    assert put.status_code == 200, put.text
    [(_, args, kwargs)] = [call for call in offline.calls if call[0] == "set_org_models"]
    assert args == (OID, CID)
    assert kwargs == {
        "models": [MODEL],
        "default_scan": MODEL,
        "budget": Decimal("12.5"),
        "prompt_version": READER_PROMPT_VERSION,
    }
    offline.org = None
    assert offline.client.get(path, headers=offline.headers).status_code == 404


@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [
        ("done", 200, None),
        ("missing", 404, "not_found"),
        ("deleting", 409, "conflict"),
        ("unavailable", 409, "unavailable"),
        ("not_verified", 409, "not_verified"),
        ("default_not_allowed", 400, "default_not_allowed"),
        ("signed_out", 401, "not_signed_in"),
    ],
)
def test_each_org_models_outcome_gets_its_reply(
    offline: _Offline, outcome: str, status: int, code: str | None
) -> None:
    offline.outcome = outcome
    body = {"models": [MODEL], "default_scan": MODEL, "ai_budget_usd": None}
    reply = offline.client.put(f"/api/operator/orgs/{CID}/models", json=body, headers=offline.headers)
    assert reply.status_code == status, reply.text
    if code is not None:
        assert reply.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "body",
    [
        {"models": [MODEL], "default_scan": MODEL, "ai_budget_usd": 0},
        {"models": [MODEL], "default_scan": MODEL, "ai_budget_usd": -5},
        {"models": [MODEL], "default_scan": MODEL, "ai_budget_usd": 1.005},
        {"models": [MODEL], "default_scan": MODEL, "ai_budget_usd": 10_000_000_000},
        {"models": ["gemini-3"], "default_scan": None, "ai_budget_usd": None},
        {"models": [MODEL], "default_scan": "google/gemini:free", "ai_budget_usd": None},
        {"models": [MODEL] * 65, "default_scan": MODEL, "ai_budget_usd": None},
        {"models": [MODEL], "default_scan": MODEL, "ai_budget_usd": None, "user_id": UID},
        {"models": [MODEL], "ai_budget_usd": None},
    ],
)
def test_a_bad_body_is_400_and_changes_nothing(offline: _Offline, body: dict[str, Any]) -> None:
    reply = offline.client.put(f"/api/operator/orgs/{CID}/models", json=body, headers=offline.headers)
    assert reply.status_code == 400, reply.text
    assert "set_org_models" not in offline.names()


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
