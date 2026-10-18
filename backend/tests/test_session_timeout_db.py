"""The sign-in idle timeout (0040) against PostgreSQL: migration up, down (0039's schema
exactly) and up again, the 1 to 24 checks, customer separation (an organisation reads only its
own hours, two columns, and writes none), the operator routes (a fresh code, an audit row with
the old and new hours), the rule (the organisation's, else the platform's, else none), a
lowered value ending an idle session at its next request, and the operator's own session
untouched. The idle check without a database: test_session_timeout.

The platform value belongs to no customer: every test that sets it puts it back.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.api import totp
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as m0002
from tests.db_support import migrate, new_customer
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PASSWORD, Api, Signed, TestClient
from tests.test_migration_0004_db import _current, _head
from tests.test_operator_auth_db import _Operator, _owner

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
HOUR = 3600
_ORG = "INSERT INTO org_session_settings (customer_id, idle_hours) VALUES (:c, :h)"
PLATFORM = "/api/operator/session-timeout"


def test_0040_up_down_up(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "0039")
    at_0039 = m0002._schema(database)
    migrate(url, "head")
    assert _current(url) == _head()
    at_head = m0002._schema(database)
    assert "org_session_settings" in m0002._names(at_head, "relations")
    m0002._downgrade(url, "0039")
    assert m0002._schema(database) == at_0039
    migrate(url, "head")
    assert m0002._schema(database) == at_head


def test_hours_are_1_to_24_in_the_database_too(db: Database) -> None:
    customer, _ = new_customer(db)
    for hours in (0, 25):
        with pytest.raises(DBAPIError):
            _owner(db, _ORG, {"c": customer, "h": hours})
        with pytest.raises(DBAPIError):
            _owner(db, "UPDATE platform_ai_settings SET session_idle_hours = :h", {"h": hours})
    _owner(db, _ORG, {"c": customer, "h": 24})
    with pytest.raises(DBAPIError):  # one row per organisation
        _owner(db, _ORG, {"c": customer, "h": 1})


def test_an_organisation_reads_only_its_own_hours_and_writes_none(db: Database) -> None:
    mine, _ = new_customer(db)
    other, _ = new_customer(db)
    _owner(db, _ORG, {"c": other, "h": 3})
    select = "SELECT customer_id, idle_hours FROM org_session_settings"
    with db.customer(mine) as conn:
        assert conn.execute(text(select)).all() == []  # row level security: never another's row
    with db.customer(other) as conn:
        assert [row[1] for row in conn.execute(text(select))] == [3]
    with pytest.raises(DBAPIError), db.customer(other) as conn:  # never who set it
        conn.execute(text("SELECT updated_by FROM org_session_settings"))
    with pytest.raises(DBAPIError), db.customer(mine) as conn:  # only the operator's function writes
        conn.execute(text(_ORG), {"c": mine, "h": 2})
    with pytest.raises(DBAPIError), db.customer(mine) as conn:
        conn.execute(text("UPDATE platform_ai_settings SET session_idle_hours = 2"))


def _operator(api: Api) -> tuple[_Operator, TestClient]:
    operator, client = _Operator(api), api.client()
    assert operator.login(client).status_code == 200
    return operator, client


def _put(api: Api, operator: _Operator, client: TestClient, path: str, hours: int | None) -> Any:
    api.clock.advance(totp.STEP_SECONDS)
    assert operator.login(client).status_code == 200  # its 30 minutes may be over in these tests
    api.clock.advance(totp.STEP_SECONDS)  # a fresh code each time
    return client.put(path, json={"hours": hours, "code": api.code(operator.secret)}, headers=CSRF)


@contextmanager
def platform_reset(db: Database) -> Iterator[None]:
    try:
        yield
    finally:
        _owner(db, "UPDATE platform_ai_settings SET session_idle_hours = NULL", {})


def _signed_in(signed: Signed) -> int:
    return int(signed.client.get("/api/auth/me").status_code)


def test_setting_takes_a_fresh_code_and_is_audited_with_old_and_new_hours(db: Database) -> None:
    api = Api(db)
    operator, client = _operator(api)
    org = api.new_signed()
    path = f"/api/operator/orgs/{org.customer_id}/session-timeout"
    with platform_reset(db):
        assert client.get(PLATFORM).json() == {"hours": None, "default_hours": api.settings.session_hours}
        wrong = client.put(PLATFORM, json={"hours": 4, "code": "000000"}, headers=CSRF)
        assert wrong.status_code == 403
        assert client.get(PLATFORM).json()["hours"] is None  # nothing changed
        assert _put(api, operator, client, PLATFORM, 4).json()["hours"] == 4
        assert _put(api, operator, client, path, 2).json() == {
            "hours": 2,
            "platform_hours": 4,
            "default_hours": api.settings.session_hours,
        }
        assert _put(api, operator, client, path, None).json()["hours"] is None  # follow the platform
        assert _put(api, operator, client, PLATFORM, None).json()["hours"] is None
    audited = operator.sql(
        "SELECT action, subject_customer_id::text, details->>'old_hours', details->>'new_hours'"
        " FROM platform_audit WHERE operator_id = :o AND action LIKE 'session.%' ORDER BY id"
    )
    assert [tuple(row) for row in audited] == [
        ("session.timeout_platform", None, None, "4"),
        ("session.timeout_org", org.customer_id, None, "2"),
        ("session.timeout_org", org.customer_id, "2", None),
        ("session.timeout_platform", None, "4", None),
    ]


def test_an_organisation_value_affects_only_it_and_null_follows_the_platform(db: Database) -> None:
    api = Api(db, settings=make_settings(admin_session_minutes=180))  # the admin's cap is not what ends it
    operator, client = _operator(api)
    with platform_reset(db):
        assert _put(api, operator, client, PLATFORM, 5).status_code == 200
        mine, other = api.new_signed(), api.new_signed()
        admin = api.admin_sign_in(mine.customer_id)  # an organisation admin follows it too
        path = f"/api/operator/orgs/{mine.customer_id}/session-timeout"
        assert _put(api, operator, client, path, 1).status_code == 200
        api.clock.advance(HOUR + 60)  # past 1 hour since signing in
        assert (_signed_in(mine), _signed_in(admin), _signed_in(other)) == (401, 401, 200)

        assert _put(api, operator, client, path, None).status_code == 200  # follow the platform: 5
        again = api.sign_in(mine.customer_id)
        api.clock.advance(4 * HOUR)
        assert _signed_in(again) == 200
        api.clock.advance(HOUR + 60)
        assert _signed_in(again) == 401  # 5 hours idle


def test_a_used_session_slides_and_a_lowered_value_ends_an_idle_one_at_its_next_request(
    db: Database,
) -> None:
    api = Api(db)
    operator, client = _operator(api)
    member = api.new_signed()
    path = f"/api/operator/orgs/{member.customer_id}/session-timeout"
    assert _put(api, operator, client, path, 2).status_code == 200
    for _ in range(3):  # used every 100 minutes: still signed in after 5 hours
        api.clock.advance(100 * 60)
        assert _signed_in(member) == 200
    api.clock.advance(90 * 60)
    assert _put(api, operator, client, path, 1).status_code == 200  # lowered while it sits idle
    reply = member.client.get("/api/auth/me")
    assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")


def _max_age(reply: Any) -> int:
    assert reply.status_code == 200, reply.text
    found = re.search(r"kaagaz_session=[^;]+;.*?Max-Age=(\d+)", reply.headers["set-cookie"])
    assert found is not None
    return int(found[1])


def test_with_hours_set_a_sign_in_lasts_24_hours_for_members_and_admins_else_as_before(db: Database) -> None:
    api = Api(db, settings=make_settings(session_hours=12, admin_session_minutes=60))
    operator, client = _operator(api)
    customer, _ = api.customer()

    def lengths() -> tuple[int, int]:
        _, email = api.add_user(customer, "member")
        member = api.client().post(
            "/api/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF
        )
        enrolled = api.enrol_admin(customer)
        admin = api.admin_login(api.client(), enrolled.email, code=api.code(enrolled.secret))
        return _max_age(member), _max_age(admin)

    assert lengths() == (12 * HOUR, 60 * 60)  # no hours anywhere: as before
    path = f"/api/operator/orgs/{customer}/session-timeout"
    assert _put(api, operator, client, path, 2).status_code == 200
    assert lengths() == (24 * HOUR, 24 * HOUR)
    signed = api.sign_in(customer)
    api.clock.advance(2 * HOUR + 60)
    assert _signed_in(signed) == 401  # the idle timeout still ends it


def test_the_operator_session_is_untouched(db: Database) -> None:
    api = Api(db)
    operator, client = _operator(api)
    with platform_reset(db):
        assert _put(api, operator, client, PLATFORM, 1).status_code == 200
        api.clock.advance(20 * 60)  # inside the operator's own 30 minutes
        assert client.get("/api/operator/me").status_code == 200
        assert client.get(PLATFORM).json()["hours"] == 1
