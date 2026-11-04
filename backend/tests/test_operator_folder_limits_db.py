"""The folder upload limits the super admin sets (0071, #484): the organisation's own, else the
platform's, else the server's default (50,000 files, 5 GB, 10,000 folders); each change with a
fresh code and one platform_audit row holding the old and new numbers only.

The platform's limits belong to no customer: every test that sets them puts them back."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from kaagaz.api import totp
from kaagaz.db.engine import Database
from kaagaz.db.folder_uploads import GIB, FolderUploadLimits, effective_limits
from tests.db_support import new_customer
from tests.test_api_support import ANY_ID, CSRF, Api, TestClient
from tests.test_operator_auth_db import _Operator, _owner

pytestmark = pytest.mark.db

PLATFORM = "/api/operator/folder-upload-limits"
DEFAULTS = {"max_files": 50_000, "max_gb": 5, "max_folders": 10_000}
NONE = {"max_files": None, "max_gb": None, "max_folders": None}


@contextmanager
def platform_reset(db: Database) -> Iterator[None]:
    try:
        yield
    finally:
        _owner(
            db,
            "UPDATE platform_ai_settings SET folder_upload_max_files = NULL, folder_upload_max_gb = NULL,"
            " folder_upload_max_folders = NULL",
            {},
        )


def _limits(db: Database, customer: str) -> FolderUploadLimits:
    with db.customer(customer) as conn:
        return effective_limits(conn, customer)


def test_effective_limits_are_the_orgs_then_the_platforms_then_the_default(db: Database) -> None:
    customer, _ = new_customer(db)
    with platform_reset(db):
        assert _limits(db, customer) == FolderUploadLimits(50_000, 5 * GIB, 10_000)
        _owner(db, "UPDATE platform_ai_settings SET folder_upload_max_files = 60000", {})
        assert _limits(db, customer) == FolderUploadLimits(60_000, 5 * GIB, 10_000)
        _owner(
            db, "INSERT INTO org_upload_limits (customer_id, max_files) VALUES (:c, 70000)", {"c": customer}
        )
        assert _limits(db, customer) == FolderUploadLimits(70_000, 5 * GIB, 10_000)  # bytes, folders follow
        other, _ = new_customer(db)
        assert _limits(db, other) == FolderUploadLimits(60_000, 5 * GIB, 10_000)  # never another's own


def _operator(api: Api) -> tuple[_Operator, TestClient]:
    operator, client = _Operator(api), api.client()
    assert operator.login(client).status_code == 200
    return operator, client


def _put(api: Api, operator: _Operator, client: TestClient, path: str, limits: dict[str, Any]) -> Any:
    api.clock.advance(totp.STEP_SECONDS)
    assert operator.login(client).status_code == 200  # its 30 minutes may be over in these tests
    api.clock.advance(totp.STEP_SECONDS)  # a fresh code each time
    return client.put(path, json={**limits, "code": api.code(operator.secret)}, headers=CSRF)


def test_setting_takes_a_fresh_code_marks_own_and_inherited_and_is_audited(db: Database) -> None:
    api = Api(db)
    operator, client = _operator(api)
    org = api.new_signed()
    path = f"/api/operator/orgs/{org.customer_id}/folder-upload-limits"
    with platform_reset(db):
        assert client.get(PLATFORM).json() == {**NONE, "defaults": DEFAULTS}
        wrong = client.put(PLATFORM, json={**NONE, "max_files": 9, "code": "000000"}, headers=CSRF)
        assert wrong.status_code == 403
        assert client.get(PLATFORM).json()["max_files"] is None  # nothing changed
        platform = {**NONE, "max_files": 60_000}
        assert _put(api, operator, client, PLATFORM, platform).json() == {**platform, "defaults": DEFAULTS}
        own = {"max_files": 70_000, "max_gb": None, "max_folders": 20_000}
        assert _put(api, operator, client, path, own).json() == {
            "own": own,
            "platform": platform,
            "defaults": DEFAULTS,
        }
        assert _put(api, operator, client, path, own).status_code == 200  # unchanged: no row
        assert _put(api, operator, client, path, NONE).json()["own"] == NONE  # follow the platform
        rows = _owner(
            db, "SELECT count(*) FROM org_upload_limits WHERE customer_id = :c", {"c": org.customer_id}
        )
        assert rows[0][0] == 0
        assert _put(api, operator, client, PLATFORM, NONE).json()["max_files"] is None
    audited = operator.sql(
        "SELECT action, subject_customer_id::text, details FROM platform_audit"
        " WHERE operator_id = :o AND action LIKE 'upload.%' ORDER BY id"
    )
    assert [tuple(row) for row in audited] == [
        ("upload.folder_limits_platform", None, {"old": NONE, "new": platform}),
        ("upload.folder_limits_org", org.customer_id, {"old": NONE, "new": own}),
        ("upload.folder_limits_org", org.customer_id, {"old": own, "new": NONE}),
        ("upload.folder_limits_platform", None, {"old": platform, "new": NONE}),
    ]


def test_a_value_over_its_ceiling_is_400_before_a_code_is_used_and_an_unknown_org_is_404(
    db: Database,
) -> None:
    api = Api(db)
    operator, client = _operator(api)
    code = api.code(operator.secret, 1)
    for over in ({"max_files": 200_001}, {"max_gb": 21}, {"max_folders": 50_001}, {"max_files": 0}):
        reply = client.put(PLATFORM, json={**NONE, **over, "code": code}, headers=CSRF)
        assert reply.status_code == 400, over
    assert (
        _put(api, operator, client, f"/api/operator/orgs/{ANY_ID}/folder-upload-limits", NONE).status_code
        == 404
    )
    assert client.get(f"/api/operator/orgs/{ANY_ID}/folder-upload-limits").status_code == 404
    with platform_reset(db):
        assert _put(api, operator, client, PLATFORM, {**NONE, "max_gb": 20}).json()["max_gb"] == 20
