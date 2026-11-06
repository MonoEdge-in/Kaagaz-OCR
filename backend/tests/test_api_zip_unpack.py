"""A zip upload through the API (#522 PR 3): it is taken up to the organisation's folder upload
limit, and once the worker has unpacked it, its document (the list, the detail, its summary and
its drop) says what was unpacked and why a file was not, in plain words."""

import os
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.adapters.scanner import EICAR
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.messages import too_large_message
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.unpack import ZipUnpacker
from tests.test_api_support import CSRF, Api
from tests.unpack_support import EicarScanner
from tests.zip_builder import make_zip

pytestmark = pytest.mark.db
MB = 1024 * 1024
GIB = 1024 * MB
LINE = 'Unpacked: 2 files into folder "Bills", 1 skipped (1 failed the virus check).'


def test_a_zip_is_taken_unpacked_and_its_document_shows_the_result(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    data = make_zip([("2026/a.csv", b"a\n1\n"), ("2026/b.csv", b"b\n2\n"), ("eicar.txt", EICAR)])
    batch = "5f0a3c1e-4b7d-4e2a-9c1f-0d6e8b2a7c31"
    reply = signed.client.post(
        "/api/documents",
        files={"file": ("Bills.zip", data, "application/zip")},
        data={"batch_id": batch},
        headers=CSRF,
    )
    assert reply.status_code == 201
    document = reply.json()["document"]
    assert (document["detected_type"], document["unpack"]) == ("zip", None)
    assert document["status_message"] == "Waiting to start."

    record = PgDocuments(db).get(signed.customer_id, document["id"])
    assert record is not None
    job = Job(customer_id=signed.customer_id, document_id=record.document_id)
    unpacker = ZipUnpacker(db, api.storage, api.queue, EicarScanner(), api.settings)
    assert unpacker(job, record, data, lambda: None) is True
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE documents SET status = 'ready' WHERE id = :d"), {"d": record.document_id})

    detail = signed.client.get(f"/api/documents/{record.document_id}").json()
    summary = signed.client.get(f"/api/documents/{record.document_id}/summary").json()
    listed = signed.client.get("/api/documents?detected_type=zip").json()["items"]
    drop = signed.client.get(f"/api/batches/{batch}").json()["documents"]
    for shown in (detail, summary, *listed, *drop):
        assert shown["status_message"] == LINE
    unpack: dict[str, Any] = detail["unpack"]
    assert unpack["folder_name"] == "Bills" and detail["folder_id"] == unpack["folder_id"]
    assert {key: unpack[key] for key in ("files", "unpacked", "already_in", "skipped", "waiting")} == {
        "files": 3,
        "unpacked": 2,
        "already_in": 0,
        "skipped": 1,
        "waiting": 0,
    }
    assert unpack["skipped_files"] == [
        {"name": "eicar.txt", "code": "virus_found", "reason": "It failed the virus check."}
    ]
    # Every other document is as before: no unpack key.
    others = signed.client.get("/api/documents").json()["items"]
    assert [item for item in others if item["detected_type"] != "zip" and "unpack" in item] == []


def test_a_zip_may_be_as_large_as_the_folder_upload_limit_up_to_1_gb(db: Database) -> None:
    api = Api(db)  # PDFs 5 MB, sheets 1 MB; the folder upload limit is 5 GB
    signed = api.new_signed()
    limits = signed.client.get("/api/upload-limits").json()
    assert (limits["zip_bytes"], limits["folder_upload"]["max_bytes"]) == (GIB, 5 * GIB)
    noise = os.urandom(6 * MB)  # packs no smaller: a zip over the PDF limit
    big = make_zip([("noise.bin", noise)])
    assert len(big) > 5 * MB
    reply = signed.client.post(
        "/api/documents", files={"file": ("Big.zip", big, "application/zip")}, headers=CSRF
    )
    assert reply.status_code == 201
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE customers SET max_upload_mb = 4"))  # the customer's own lowers it
    refused = signed.client.post(
        "/api/documents", files={"file": ("Big2.zip", big + b"\0", "application/zip")}, headers=CSRF
    )
    assert refused.status_code == 413


def test_a_zip_over_1_gb_is_refused_at_upload_by_its_declared_size(db: Database) -> None:
    """A fake size, never a real 1 GB file: the declared size of the resumable start and of a form
    upload are each refused before a byte is read."""
    api = Api(db)
    signed = api.new_signed()
    start = {"file_name": "Huge.zip", "fingerprint": "0" * 64, "user_id": signed.user_id}
    sessions = "/api/documents/upload-sessions"
    refused = signed.client.post(sessions, json={**start, "file_size": GIB + 1}, headers=CSRF)
    assert (refused.status_code, refused.json()["error"]["code"]) == (413, "too_large")
    request = signed.client.build_request(
        "POST", "/api/documents", files={"file": ("Huge.zip", b"PK", "application/zip")}, headers=CSRF
    )
    request.headers["Content-Length"] = str(GIB + 64 * 1024 + 1)
    assert signed.client.send(request).status_code == 413
    assert too_large_message("zip", GIB + 1, GIB) == (
        "Zip files can be up to 1 GB. Unzip it on your computer and upload the folder instead."
    )
    assert api.storage.count() == 0 and api.queue.jobs == []
