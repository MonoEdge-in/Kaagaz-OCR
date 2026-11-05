"""The storage limit the super admin sets (0061d, #516): the platform's (null: 50 GB) and one
organisation's (null: follow the platform), 1 to 10,000 GB, each change with a fresh code and
one platform_audit row holding the old and new limit only. An organisation's reply also gives the
bytes its documents hold, never another's.

The platform's limit belongs to no customer: every test that sets it puts it back."""

from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.db_support import insert_document
from tests.test_api_storage_limit import platform_reset
from tests.test_api_support import ANY_ID, CSRF, Api
from tests.test_operator_auth_db import _owner
from tests.test_operator_folder_limits_db import _operator, _put

pytestmark = pytest.mark.db

PLATFORM = "/api/operator/storage-limit"
LIMITS = {"default_gb": 50, "most_gb": 10_000}


def _org(customer: str) -> str:
    return f"/api/operator/orgs/{customer}/storage-limit"


def _reply(storage: int | None, platform: int | None, effective: int, used: int = 0) -> dict[str, Any]:
    return {
        "storage_gb": storage,
        "platform_gb": platform,
        "effective_gb": effective,
        **LIMITS,
        "used_bytes": used,
    }


def test_setting_takes_a_fresh_code_follows_the_platform_and_is_audited(db: Database) -> None:
    api = Api(db)
    operator, client = _operator(api)
    org, other = api.new_signed(), api.new_signed()
    with db.customer(org.customer_id) as conn:
        insert_document(conn, org.customer_id, size_bytes=1234)
    with db.customer(other.customer_id) as conn:
        insert_document(conn, other.customer_id, size_bytes=99)  # never in org's reply
    path = _org(org.customer_id)
    with platform_reset(db):
        assert client.get(PLATFORM).json() == _reply(None, None, 50)
        assert client.get(path).json() == _reply(None, None, 50, 1234)
        wrong = client.put(PLATFORM, json={"storage_gb": 9, "code": "000000"}, headers=CSRF)
        assert wrong.status_code == 403
        assert client.get(PLATFORM).json()["storage_gb"] is None  # nothing changed
        assert _put(api, operator, client, PLATFORM, {"storage_gb": 60}).json() == _reply(60, 60, 60)
        assert client.get(path).json() == _reply(None, 60, 60, 1234)  # no own: the platform's
        assert _put(api, operator, client, path, {"storage_gb": 70}).json() == _reply(70, 60, 70, 1234)
        assert _put(api, operator, client, path, {"storage_gb": 70}).status_code == 200  # unchanged: no row
        assert _put(api, operator, client, path, {"storage_gb": None}).json() == _reply(None, 60, 60, 1234)
        rows = _owner(
            db, "SELECT count(*) FROM org_storage_limits WHERE customer_id = :c", {"c": org.customer_id}
        )
        assert rows[0][0] == 0
        assert _put(api, operator, client, PLATFORM, {"storage_gb": None}).json() == _reply(None, None, 50)
    audited = operator.sql(
        "SELECT action, subject_customer_id::text, details FROM platform_audit"
        " WHERE operator_id = :o AND action LIKE 'storage.%' ORDER BY id"
    )
    assert [tuple(row) for row in audited] == [
        ("storage.limit_platform", None, {"old": None, "new": 60}),
        ("storage.limit_org", org.customer_id, {"old": None, "new": 70}),
        ("storage.limit_org", org.customer_id, {"old": 70, "new": None}),
        ("storage.limit_platform", None, {"old": 60, "new": None}),
    ]


def test_a_value_outside_1_to_10000_is_400_before_the_code_is_used_and_an_unknown_org_is_404(
    db: Database,
) -> None:
    api = Api(db)
    operator, client = _operator(api)
    assert _put(api, operator, client, _org(ANY_ID), {"storage_gb": None}).status_code == 404
    assert client.get(_org(ANY_ID)).status_code == 404
    code = api.code(operator.secret, 1)
    for wrong in (0, 10_001, -1, 1.5, "3", True):
        for path in (PLATFORM, _org(api.new_signed().customer_id)):
            reply = client.put(path, json={"storage_gb": wrong, "code": code}, headers=CSRF)
            assert reply.status_code == 400, (wrong, path)
    missing = client.put(PLATFORM, json={"storage_gb": 5}, headers=CSRF)
    assert missing.status_code == 400  # no code
    with platform_reset(db):  # the same code still works: none was used up
        done = client.put(PLATFORM, json={"storage_gb": 10_000, "code": code}, headers=CSRF)
        assert (done.status_code, done.json()["storage_gb"]) == (200, 10_000), done.text


def test_the_function_refuses_a_value_outside_its_range_and_a_used_step(db: Database) -> None:
    api = Api(db)
    operator, _ = _operator(api)  # its sign-in used the current step
    for wrong in (0, 10_001):
        with pytest.raises(DBAPIError):
            operator.sql("SELECT kaagaz_op_set_storage_limit(:o, NULL, :g, :s)", g=wrong, s=api.step(5))
    used = operator.sql("SELECT kaagaz_op_set_storage_limit(:o, NULL, 5, :s)", s=api.step())
    assert used == [("step_up",)]
    assert operator.sql("SELECT storage_gb FROM platform_ai_settings") == [(None,)]
