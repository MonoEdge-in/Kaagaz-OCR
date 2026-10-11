"""The operator's insights routes without a database (council 11 Oct 2026, F1-F4): ``days``
is checked before the database, each function gets the agreed days and TIMEZONE, a reply
holds exactly the agreed keys with numbers for decimals and null for NULL, and no row with a
disabled operator is 401. The SQL itself: ``test_operator_insights_routes_db.py``."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
from fastapi import FastAPI

from kaagaz.api.auth import OPERATOR_COOKIE, OperatorClaims, sign_operator
from kaagaz.api.routers.operator_insights import (
    BOARD_KEYS,
    BOARD_REPLY_KEYS,
    DAILY_KEYS,
    INSIGHTS_KEYS,
    ORG_HEALTH_KEYS,
    QUALITY_KEYS,
    QUALITY_REPLY_KEYS,
)
from kaagaz.db import operator_insights as insights_sql
from kaagaz.db import operators as operators_sql
from tests.test_api_auth import _offline_client
from tests.test_api_support import CSRF, NOW
from tests.test_operator_orgs import CID, OID, _NoDatabase

AT = int(NOW.timestamp())
INSIGHTS, QUALITY, BOARD = "/api/operator/insights", "/api/operator/quality", "/api/operator/model-board"
INSIGHT_ROUTES = {("GET", INSIGHTS), ("GET", QUALITY), ("GET", BOARD)}
ZONE = "Asia/Kolkata"  # settings_factory's TIMEZONE
TODAY = date(2026, 9, 27)  # NOW (10:00 UTC) in ZONE


def _function_row(keys: frozenset[str], customer_id: str | None = CID, **values: Any) -> dict[str, Any]:
    """A row as the function gives it: ``customer_id``, every other column 3 unless given,
    and a column no key set names (which must never be sent)."""
    row: dict[str, Any] = {key: 3 for key in keys if key != "organisation_id"}
    if "organisation_id" in keys:
        row["customer_id"] = customer_id
    return {**row, **values, "email": "someone@example.test"}


class _Offline:
    """An app with a signed-in operator; the functions' calls are recorded in ``calls``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, **settings: object) -> None:
        self.client = _offline_client(**settings)
        app = cast(FastAPI, self.client.app)
        app.state.kaagaz_operator = _NoDatabase()
        self.settings = app.state.kaagaz.settings
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.rows: dict[str, list[dict[str, Any]]] = {
            "org_health": [_function_row(ORG_HEALTH_KEYS)],
            "daily": [_function_row(DAILY_KEYS, day=TODAY)],
            "quality": [_function_row(QUALITY_KEYS, None), _function_row(QUALITY_KEYS)],
            "model_board": [_function_row(BOARD_KEYS, model="google/gemini-3-flash")],
        }
        monkeypatch.setattr(
            operators_sql, "session_operator", lambda conn, oid: ("ops@example.test", "Ops", 2)
        )
        for name in self.rows:
            monkeypatch.setattr(insights_sql, name, self._recorder(name))
        token = sign_operator(
            OperatorClaims(operator_id=OID, session_version=2, expires_at=AT + 600),
            self.settings.session_secret,
        )
        self.headers = {**CSRF, "Cookie": f"{OPERATOR_COOKIE}={token}"}

    def _recorder(self, name: str) -> Any:
        def record(conn: Any, *args: Any) -> list[dict[str, Any]]:
            self.calls.append((name, args))
            return self.rows[name]

        return record

    def get(self, path: str, **params: Any) -> Any:
        return self.client.get(path, params=params, headers=self.headers)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> _Offline:
    return _Offline(monkeypatch)


@pytest.mark.parametrize("path", sorted(path for _, path in INSIGHT_ROUTES))
@pytest.mark.parametrize("days", ["0", "91", "-1", "seven", "7.5"])
def test_days_outside_1_to_90_are_refused_before_the_database(
    offline: _Offline, path: str, days: str
) -> None:
    reply = offline.get(path, days=days)
    assert (reply.status_code, reply.json()["error"]["code"]) == (400, "bad_request"), reply.text
    assert offline.calls == []


def test_insights_cover_twice_the_days_and_today_with_exactly_the_agreed_keys(offline: _Offline) -> None:
    reply = offline.get(INSIGHTS, days=7)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert set(body) == INSIGHTS_KEYS
    assert (body["from"], body["to"], body["previous_from"]) == ("2026-09-21", "2026-09-27", "2026-09-14")
    assert body["month"] == {"from": "2026-09-01", "today": "2026-09-27", "days_in_month": 30}
    assert [set(row) for row in body["organisations"]] == [ORG_HEALTH_KEYS]
    assert [set(row) for row in body["daily"]] == [DAILY_KEYS]
    assert body["daily"][0]["day"] == "2026-09-27" and body["daily"][0]["organisation_id"] == CID
    assert sorted(offline.calls) == [
        ("daily", (OID, date(2026, 9, 14), TODAY, ZONE)),  # previous_from to to: 14 days
        ("org_health", (OID, TODAY, ZONE)),
    ]


def test_the_default_period_is_30_days_and_its_window_twice_that(offline: _Offline) -> None:
    body = offline.get(INSIGHTS).json()
    start, previous = date.fromisoformat(body["from"]), date.fromisoformat(body["previous_from"])
    assert ((TODAY - start).days + 1, (start - previous).days) == (30, 30)
    daily = dict(offline.calls)["daily"]
    assert (daily[2] - daily[1]).days + 1 == 60
    assert offline.get(QUALITY).json()["from"] == offline.get(BOARD).json()["from"] == body["from"]


def test_decimals_are_numbers_null_stays_null_and_times_are_iso(offline: _Offline) -> None:
    at = datetime(2026, 9, 20, 4, 30, tzinfo=UTC)
    offline.rows["org_health"] = [
        _function_row(
            ORG_HEALTH_KEYS,
            ai_budget_usd=Decimal("25.00"),
            month_cost_usd=Decimal("1.234567"),
            not_found_30d=None,  # suppressed below K
            oldest_open_review_at=at,
            month_cost_estimated=True,
            last_active_day=date(2026, 9, 7),
        ),
        _function_row(ORG_HEALTH_KEYS, last_active_day=None),  # never active
    ]
    offline.rows["daily"] = [
        _function_row(DAILY_KEYS, day=TODAY, cost_usd=None, cost_scan_usd=Decimal("0.5"))
    ]
    body = offline.get(INSIGHTS).json()
    org = body["organisations"][0]
    assert (org["ai_budget_usd"], org["month_cost_usd"], org["not_found_30d"]) == (25.0, 1.234567, None)
    assert (org["oldest_open_review_at"], org["month_cost_estimated"]) == (at.isoformat(), True)
    assert [row["last_active_day"] for row in body["organisations"]] == ["2026-09-07", None]
    assert (body["daily"][0]["cost_usd"], body["daily"][0]["cost_scan_usd"]) == (None, 0.5)
    offline.rows["model_board"] = [
        _function_row(BOARD_KEYS, input_tokens=Decimal("1200"), seconds_per_page_p95=None, cost_usd=None)
    ]
    item = offline.get(BOARD).json()["items"][0]
    assert (item["input_tokens"], item["seconds_per_page_p95"], item["cost_usd"]) == (1200.0, None, None)
    assert type(item["input_tokens"]) is float


def test_quality_has_the_platform_row_apart_with_a_null_organisation(offline: _Offline) -> None:
    offline.rows["quality"] = [
        _function_row(QUALITY_KEYS, None, runs=12, answered=None),
        _function_row(QUALITY_KEYS, runs=12, ready_p50_seconds=4.5),
    ]
    reply = offline.get(QUALITY, days=90)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert set(body) == QUALITY_REPLY_KEYS
    assert (body["from"], body["to"]) == ("2026-06-30", "2026-09-27")
    assert set(body["platform"]) == QUALITY_KEYS
    assert (body["platform"]["organisation_id"], body["platform"]["answered"]) == (None, None)
    assert [set(row) for row in body["organisations"]] == [QUALITY_KEYS]
    assert (body["organisations"][0]["organisation_id"], body["organisations"][0]["ready_p50_seconds"]) == (
        CID,
        4.5,
    )
    assert offline.calls == [("quality", (OID, date(2026, 6, 30), TODAY, ZONE))]


def test_the_model_board_has_exactly_the_agreed_keys(offline: _Offline) -> None:
    body = offline.get(BOARD, days=1).json()
    assert set(body) == BOARD_REPLY_KEYS
    assert (body["from"], body["to"]) == ("2026-09-27", "2026-09-27")
    assert [set(item) for item in body["items"]] == [BOARD_KEYS]
    assert body["items"][0]["model"] == "google/gemini-3-flash"
    assert offline.calls == [("model_board", (OID, TODAY, TODAY, ZONE))]


def test_no_reply_sends_a_column_its_key_set_does_not_name(offline: _Offline) -> None:
    for path in (INSIGHTS, QUALITY, BOARD):
        reply = offline.get(path)
        assert reply.status_code == 200 and "someone@example.test" not in reply.text, path


def test_ai_is_off_in_keyless_mode_and_on_when_a_model_is_called(monkeypatch: pytest.MonkeyPatch) -> None:
    keyless = _Offline(monkeypatch)  # settings_factory: OCR_ENGINE none, MODEL_ENABLED false
    assert [keyless.get(path).json()["ai_enabled"] for path in (INSIGHTS, QUALITY, BOARD)] == [False] * 3
    keyed = _Offline(monkeypatch, model_enabled=True, openrouter_api_key="test-key")
    assert [keyed.get(path).json()["ai_enabled"] for path in (INSIGHTS, QUALITY, BOARD)] == [True] * 3


def test_today_and_the_zone_are_the_settings_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    # 10:00 UTC on 27 Sep is already 28 Sep 00:00 at UTC+14.
    offline = _Offline(monkeypatch, timezone="Pacific/Kiritimati")
    body = offline.get(INSIGHTS, days=1).json()
    assert (body["from"], body["to"], body["month"]["today"]) == ("2026-09-28", "2026-09-28", "2026-09-28")
    offline.get(QUALITY, days=1)
    offline.get(BOARD, days=1)
    assert {args[-1] for _, args in offline.calls} == {"Pacific/Kiritimati"}


@pytest.mark.parametrize("path", sorted(path for _, path in INSIGHT_ROUTES))
def test_no_row_is_401_when_the_operator_was_disabled_after_the_cookie_check(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    for rows in offline.rows.values():
        rows.clear()
    assert offline.get(path).status_code == 200  # still enabled: just empty
    answers = iter([("ops@example.test", "Ops", 2), None])  # the cookie check, then after the read
    monkeypatch.setattr(operators_sql, "session_operator", lambda conn, oid: next(answers))
    reply = offline.get(path)
    assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")
