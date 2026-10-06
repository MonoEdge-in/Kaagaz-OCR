"""The operator's activity feed and health without a database (super admin PR 5): both
routes need the operator cookie, the cursor round-trips and a forged one is refused, the
filters reach the function, and a reply holds exactly the agreed keys. The SQL itself:
``test_isolation_operator_activity.py``."""

import base64
from datetime import timedelta
from typing import Any, cast

import pytest
from fastapi import FastAPI

from kaagaz.api.auth import (
    OPERATOR_COOKIE,
    SESSION_COOKIE,
    OperatorClaims,
    SessionClaims,
    sign_operator,
    sign_session,
)
from kaagaz.api.routers.operator_activity import decode_cursor, encode_cursor
from kaagaz.db import operator_activity as activity_sql
from kaagaz.db import operators as operators_sql
from kaagaz.db.operator_activity import Before
from tests.test_api_auth import _offline_client
from tests.test_api_support import CSRF, NOW, route_calls
from tests.test_operator_orgs import CID, OID, UID, _NoDatabase

AT = int(NOW.timestamp())
ACTIVITY, HEALTH = "/api/operator/activity", "/api/operator/health"
ACTIVITY_ROUTES = {("GET", ACTIVITY), ("GET", HEALTH)}
ENTRY_KEYS = {"source", "at", "action", "organisation_id", "organisation_name", "target_type", "by"}


def _row(entry_id: int, source: str = "organisation", minutes: int = 0) -> dict[str, Any]:
    return {
        "source": source,
        "entry_id": entry_id,
        "created_at": NOW - timedelta(minutes=minutes),
        "action": "document.upload",
        "customer_id": CID,
        "customer_name": "Sharma Traders",
        "target_type": "document",
        "actor": "user",
    }


def _org(customer_id: str, oldest_minutes: int | None, **counts: Any) -> dict[str, Any]:
    return {
        "customer_id": customer_id,
        "name": "Sharma Traders",
        "status": "active",
        "delete_after": None,
        "documents_by_status": counts.get("by_status", {}),
        "failed_by_code": counts.get("by_code", {}),
        "in_progress": counts.get("in_progress", 0),
        "oldest_in_progress_at": None if oldest_minutes is None else NOW - timedelta(minutes=oldest_minutes),
    }


class _Offline:
    """An app with a signed-in operator; the feed's calls are recorded in ``calls``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.client = _offline_client()
        app = cast(FastAPI, self.client.app)
        app.state.kaagaz_operator = _NoDatabase()
        self.settings = app.state.kaagaz.settings
        self.rows: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.orgs: list[dict[str, Any]] = []
        self.purges: dict[str, Any] | None = {
            "purged": 2,
            "last_purged_at": NOW,
            "swept": 1,
            "last_swept_at": None,
        }
        monkeypatch.setattr(
            operators_sql, "session_operator", lambda conn, oid: ("ops@example.test", "Ops", 2)
        )
        monkeypatch.setattr(activity_sql, "activity", self._activity)
        monkeypatch.setattr(activity_sql, "health", lambda conn, oid: self.orgs)
        monkeypatch.setattr(activity_sql, "purges", lambda conn, oid: self.purges)
        token = sign_operator(
            OperatorClaims(operator_id=OID, session_version=2, expires_at=AT + 600),
            self.settings.session_secret,
        )
        self.headers = {**CSRF, "Cookie": f"{OPERATOR_COOKIE}={token}"}

    def _activity(self, conn: Any, oid: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"operator": oid, **kwargs})
        return self.rows[: kwargs["limit"]]

    def get(self, path: str, **params: Any) -> Any:
        return self.client.get(path, params=params, headers=self.headers)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> _Offline:
    return _Offline(monkeypatch)


def test_both_routes_need_the_operator_cookie_not_a_customer_admin_one(offline: _Offline) -> None:
    app = cast(FastAPI, offline.client.app)
    assert set(route_calls(app, "/api/operator")) >= ACTIVITY_ROUTES
    admin = SessionClaims(
        user_id=UID, customer_id=CID, role="admin", scope="admin", session_version=2, expires_at=AT + 600
    )
    customer = {**CSRF, "Cookie": f"{SESSION_COOKIE}={sign_session(admin, offline.settings.session_secret)}"}
    for _, path in sorted(ACTIVITY_ROUTES):
        for headers in (CSRF, customer):
            reply = offline.client.get(path, headers=headers)
            assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in"), path
        assert offline.client.get(path, headers=offline.headers).status_code == 200, path
    assert offline.calls == [
        {"operator": OID, "before": None, "customer_id": None, "action_prefix": None, "limit": 50}
    ]


def test_a_full_page_gives_a_cursor_to_the_next_and_the_filters_reach_the_function(offline: _Offline) -> None:
    offline.rows = [_row(9, "platform"), _row(7, minutes=1), _row(5, minutes=1)]
    first = offline.get(ACTIVITY, limit=2, org=CID, action="document.")
    assert first.status_code == 200, first.text
    body = first.json()
    assert [set(item) for item in body["items"]] == [ENTRY_KEYS] * 2
    assert body["items"][0] == {
        "source": "platform",
        "at": NOW.isoformat(),
        "action": "document.upload",
        "organisation_id": CID,
        "organisation_name": "Sharma Traders",
        "target_type": "document",
        "by": "user",
    }
    assert decode_cursor(body["next"]) == Before(NOW - timedelta(minutes=1), "organisation", 7)
    offline.rows = offline.rows[2:]  # what the function gives after the cursor
    second = offline.get(ACTIVITY, limit=2, before=body["next"])
    assert second.json()["next"] is None  # fewer rows than the page: the last one
    assert offline.calls[0] == {
        "operator": OID,
        "before": None,
        "customer_id": CID,
        "action_prefix": "document.",
        "limit": 2,
    }
    assert offline.calls[1]["before"] == Before(NOW - timedelta(minutes=1), "organisation", 7)


def _forged(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()


@pytest.mark.parametrize(
    "params",
    [
        {"before": "not base64!"},
        {"before": "%%%"},
        {"before": _forged("2026-09-27T10:00:00+00:00|organisation")},  # two parts
        {"before": _forged("2026-09-27T10:00:00|organisation|7")},  # no time zone
        {"before": _forged("2026-09-27T10:00:00+00:00|details|7")},  # no such log
        {"before": _forged("2026-09-27T10:00:00+00:00|platform|0")},
        {"before": _forged("2026-09-27T10:00:00+00:00|platform|1_0")},
        {"before": _forged("2026-09-27T10:00:00+00:00|platform|-3")},
        {"before": _forged(f"2026-09-27T10:00:00+00:00|platform|{2**63}")},
        {"before": _forged("yesterday|platform|7")},
        {"before": "x" * 201},
        {"org": "not-an-id"},
        {"action": "Document"},
        {"action": "doc%"},
        {"limit": 101},
        {"limit": 0},
    ],
)
def test_a_forged_cursor_or_a_bad_filter_is_refused_before_the_database(
    offline: _Offline, params: dict[str, Any]
) -> None:
    reply = offline.get(ACTIVITY, **params)
    assert (reply.status_code, reply.json()["error"]["code"]) == (400, "bad_request"), reply.text
    assert offline.calls == []


def test_an_empty_feed_is_401_when_the_operator_was_disabled_after_the_cookie_check(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert offline.get(ACTIVITY).json() == {"items": [], "next": None}  # still enabled: just empty
    answers = iter([("ops@example.test", "Ops", 2), None])  # the cookie check, then after the feed
    monkeypatch.setattr(operators_sql, "session_operator", lambda conn, oid: next(answers))
    reply = offline.get(ACTIVITY)
    assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")


def test_the_cursor_round_trips_microseconds_and_ids_beyond_a_32_bit_number() -> None:
    before = Before(NOW.replace(microsecond=123456), "platform", 2**40)
    assert decode_cursor(encode_cursor(before)) == before


def test_health_holds_counts_only_with_the_totals_and_the_purges(offline: _Offline) -> None:
    other = "33333333-3333-4333-8333-333333333333"
    offline.orgs = [
        _org(
            CID,
            90,
            by_status={"ready": 3, "failed": 2, "received": 1},
            by_code={"corrupt_file": 1, "ocr_timeout": 1},
            in_progress=1,
        ),
        _org(other, None, by_status={"ready": 1, "rejected": 1}, by_code={"corrupt_file": 1}),
    ]
    reply = offline.get(HEALTH)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert set(body) == {"organisations", "totals", "purges"}
    first = body["organisations"][0]
    assert set(first) == {
        "id",
        "name",
        "status",
        "delete_after",
        "documents_by_status",
        "failed_by_code",
        "in_progress",
        "oldest_in_progress_at",
        "oldest_in_progress_seconds",
    }
    assert (first["in_progress"], first["oldest_in_progress_seconds"]) == (1, 90 * 60)
    assert body["organisations"][1]["oldest_in_progress_seconds"] is None
    assert body["totals"] == {
        "documents_by_status": {"ready": 4, "failed": 2, "received": 1, "rejected": 1},
        "failed_by_code": {"corrupt_file": 2, "ocr_timeout": 1},
        "in_progress": 1,
        "oldest_in_progress_seconds": 90 * 60,
    }
    assert body["purges"] == {
        "purged": 2,
        "last_purged_at": NOW.isoformat(),
        "swept": 1,
        "last_swept_at": None,
    }
    offline.purges = None  # the operator was disabled meanwhile
    assert offline.get(HEALTH).status_code == 401
