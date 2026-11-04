"""Folder upload routes across customers (#484): customer B, with customer A's exact ids, reads,
registers, plans into and sends nothing of A's; A's own limits never reach B; and an
organisation's admin reaches none of the operator's limit routes."""

import pytest

from kaagaz.db.engine import Database
from tests.test_api_folder_upload_files import planned, send
from tests.test_api_folder_uploads import item, plan, register
from tests.test_api_support import CSRF, PDF_BYTES, Api
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db


def test_another_customer_reaches_nothing_of_a_folder_upload(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    body, (one,) = planned(a, {"A/bill.pdf": PDF_BYTES})
    upload = body["id"]
    for path in (f"/api/folder-uploads/{upload}", f"/api/folder-uploads/{upload}/items"):
        assert b.client.get(path).status_code == 404, path
    assert b.client.get("/api/folder-uploads").json() == {"items": []}
    assert register(b, upload, [item("A/x.pdf", body["folder_ids"][0])]).status_code == 404
    assert send(b, one["id"], PDF_BYTES).status_code == 404
    into = plan(b, parent_folder_id=body["folder_ids"][0])  # A's folder as B's parent
    assert into.status_code == 404
    reused = plan(b, id=upload)  # A's upload id is B's own new upload
    assert reused.status_code == 200
    assert reused.json()["folder_ids"] != body["folder_ids"]
    assert api.storage.count() == 0
    assert a.client.get(f"/api/folder-uploads/{upload}").json()["waiting"] == 1  # A's, untouched
    assert send(a, one["id"], PDF_BYTES).status_code == 201


def test_one_organisations_own_limits_never_reach_another_and_admins_cant_set_them(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    _owner(db, "INSERT INTO org_upload_limits (customer_id, max_files) VALUES (:c, 7)", {"c": a.customer_id})
    assert a.client.get("/api/upload-limits").json()["folder_upload"]["max_files"] == 7
    assert b.client.get("/api/upload-limits").json()["folder_upload"]["max_files"] == 50_000
    admin = api.admin_sign_in(a.customer_id)
    for path in (
        "/api/operator/folder-upload-limits",
        f"/api/operator/orgs/{a.customer_id}/folder-upload-limits",
    ):
        assert admin.client.get(path).status_code == 401, path
        body = {"max_files": 9, "max_gb": None, "max_folders": None, "code": "000000"}
        assert admin.client.put(path, json=body, headers=CSRF).status_code == 401, path
