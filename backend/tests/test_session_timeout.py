"""The sign-in idle timeout (0040) without a database: the idle check and its throttled
touch, and the operator routes' body checks and shape. The rule (the organisation's, else
the platform's, else none) is one COALESCE in the session query: test_session_timeout_db."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest

from kaagaz.api.auth import idle_over, new_session, session_seconds, touch_due
from kaagaz.db import operator_models as models_sql
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database
from tests import test_auth_tokens as tokens
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, NOW
from tests.test_operator_models import CID, OID, _Offline

HOUR = timedelta(hours=1)


def test_inside_the_limit_stays_signed_in_and_past_it_does_not() -> None:
    assert not idle_over(NOW - 2 * HOUR, 2, NOW)  # exactly the limit: still in
    assert idle_over(NOW - 2 * HOUR - timedelta(seconds=1), 2, NOW)
    assert not idle_over(NOW - 30 * HOUR, None, NOW)  # nothing saved: no idle limit (today)
    assert not idle_over(None, 1, NOW)  # nothing on record: never


def test_with_hours_set_a_sign_in_lasts_24_hours_for_both_roles_else_as_before() -> None:
    settings = make_settings(session_hours=12, admin_session_minutes=60)
    assert [session_seconds(scope, settings, 1) for scope in ("app", "admin")] == [24 * 3600] * 2
    assert [session_seconds(scope, settings) for scope in ("app", "admin")] == [12 * 3600, 60 * 60]
    claims = new_session(
        user_id="u",
        customer_id="c",
        role="admin",
        scope="admin",
        session_version=1,
        settings=settings,
        now=0,
        idle_hours=5,
    )
    assert claims.expires_at == 24 * 3600


def test_the_last_use_is_written_at_most_once_a_minute() -> None:
    assert not touch_due(NOW - timedelta(seconds=59), NOW)
    assert touch_due(NOW - timedelta(seconds=60), NOW)
    assert not touch_due(None, NOW)


def test_current_user_ends_an_idle_session_and_touches_a_used_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring in current_user: 401 past the limit; the last use written only when a
    minute old, inside the same customer transaction kind as every request."""
    touched: list[tuple[str, str, datetime]] = []
    monkeypatch.setattr(Database, "customer", lambda self, customer_id: nullcontext(None))
    monkeypatch.setattr(users_sql, "touch_seen", lambda conn, c, u, *, now: touched.append((c, u, now)))
    now = datetime.fromtimestamp(tokens.NOW, UTC)
    for seen_at, hours, expected in (
        (now - timedelta(minutes=61), 1, 401),
        (now - timedelta(minutes=59), 1, 200),
        (now - timedelta(seconds=10), 1, 200),
        (now - timedelta(hours=11), None, 200),  # nothing saved: today's behaviour
    ):
        client = tokens._client(monkeypatch, tokens._user(role="member", seen_at=seen_at, idle_hours=hours))
        tokens._cookie(client, tokens._session(role="member", expires_at=tokens.NOW + 60))
        assert client.get("/any").status_code == expected, (seen_at, hours)
    # Touched for the two used sessions a minute old or more, never for the 10-second one.
    assert touched == [(tokens.CID, tokens.UID, now), (tokens.CID, tokens.UID, now)]


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> _Offline:
    offline = _Offline(monkeypatch)
    row = {"org_hours": 3, "platform_hours": 8}
    monkeypatch.setattr(models_sql, "session_timeout", offline._recorder("session_timeout", row))
    monkeypatch.setattr(
        models_sql, "set_session_timeout", offline._recorder("set_session_timeout", lambda: offline.outcome)
    )
    return offline


PATHS = ("/api/operator/session-timeout", f"/api/operator/orgs/{CID}/session-timeout")


@pytest.mark.parametrize("hours", [0, 25, 1.5, "3", True, -1])
@pytest.mark.parametrize("path", PATHS)
def test_only_whole_hours_1_to_24_are_taken(offline: _Offline, path: str, hours: object) -> None:
    reply = offline.client.put(path, json={"hours": hours, "code": offline.code()}, headers=offline.headers)
    assert reply.status_code == 400, reply.text
    assert "set_session_timeout" not in offline.names()


def test_the_platform_value_takes_a_fresh_code_and_comes_back(offline: _Offline) -> None:
    path = PATHS[0]
    assert offline.client.get(path, headers=offline.headers).json() == {"hours": 8, "default_hours": 12}
    refused = offline.client.put(
        path, json={"hours": 2, "code": offline.wrong_code()}, headers=offline.headers
    )
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "step_up_failed")
    assert "set_session_timeout" not in offline.names()
    for hours in (1, 24, None):
        done = offline.client.put(
            path, json={"hours": hours, "code": offline.code()}, headers=offline.headers
        )
        assert done.status_code == 200, done.text
    calls = [call for call in offline.calls if call[0] == "set_session_timeout"]
    assert [call[1] for call in calls] == [(OID, None, 1), (OID, None, 24), (OID, None, None)]


def test_an_organisation_value_null_follows_the_platform(offline: _Offline) -> None:
    path = PATHS[1]
    assert offline.client.get(path, headers=offline.headers).json() == {
        "hours": 3,
        "platform_hours": 8,
        "default_hours": 12,
    }
    done = offline.client.put(path, json={"hours": None, "code": offline.code()}, headers=offline.headers)
    assert done.status_code == 200, done.text
    [(_, args, _)] = [call for call in offline.calls if call[0] == "set_session_timeout"]
    assert args == (OID, CID, None)
    offline.outcome = "deleting"
    refused = offline.client.put(path, json={"hours": 4, "code": offline.code()}, headers=offline.headers)
    assert refused.status_code == 409


def test_an_unknown_organisation_is_404_and_a_customer_session_401(
    offline: _Offline, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models_sql, "session_timeout", offline._recorder("session_timeout", None))
    assert offline.client.get(PATHS[1], headers=offline.headers).status_code == 404
    for path in PATHS:
        reply = offline.client.get(path, headers=CSRF)  # no operator cookie
        assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")
