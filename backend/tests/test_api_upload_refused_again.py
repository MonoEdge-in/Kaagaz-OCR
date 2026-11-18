"""#554 (Tushar, 11 Nov 2026): a file Kaagaz refused can be uploaded again. When the only copy
of its bytes is a refused document (``rejected``, its family's only version, in Trash or not),
the same bytes sent again are a new document, scanned afresh, and the refused copy is deleted
for good in the same transaction, its stored or quarantined file too, with one audit row
(``document.delete``, ``replaced_by``). A file still bad is refused again; a ready or failed
copy, or a refused version of a family with others, is still "already in Kaagaz". The folder
upload's "Add new files only" sends such a file, and two uploads of it at once store one.

PRD 9 F-03 (D3, #680): a copy in Trash that failed or was never read, alone in its family and seen by
the uploader, is replaced the same way; a live one, or one in Trash that was read, is not."""

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.storage_limits import storage_room
from kaagaz.db.trash import lock_family
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.jobs.worker import JobFailed
from kaagaz.scanning.gate import CUSTOMER_MESSAGES, Verdict
from tests.db_support import insert_document, new_customer, wait_until_blocked
from tests.fakes import FakeScanner, MemoryQueue, MemoryStorage
from tests.test_api_folder_uploads import item, plan, register
from tests.test_api_review import _named_database
from tests.test_api_support import CSRF, Api, Signed
from tests.test_pipeline import CSV, CSV_NAME, PIPELINE_POLICY
from tests.test_pipeline_db import with_runs
from tests.test_pipeline_store_db import new_session

pytestmark = pytest.mark.db

_SHA = hashlib.sha256(CSV).hexdigest()


def _upload(signed: Signed, data: bytes = CSV, name: str = CSV_NAME) -> httpx.Response:
    reply: httpx.Response = signed.client.post(
        "/api/documents", files={"file": (name, data, "text/csv")}, headers=CSRF
    )
    return reply


def _scan(api: Api, signed: Signed, document_id: str, verdict: Verdict) -> None:
    """The worker's job for the document, with a scanner that gives ``verdict``."""
    job = with_runs(api.db, api.storage, scanner=FakeScanner(verdict))
    if verdict is Verdict.CLEAN:
        job(Job(signed.customer_id, document_id), lambda: None)
        return
    with pytest.raises(JobFailed):  # refused for good
        job(Job(signed.customer_id, document_id), lambda: None)


def _refused(api: Api, signed: Signed) -> str:
    """CSV uploaded, and refused by its scan: its id."""
    reply = _upload(signed)
    assert reply.status_code == 201, reply.text
    document: str = reply.json()["document"]["id"]
    _scan(api, signed, document, Verdict.INFECTED)
    assert api.storage.quarantined.get((signed.customer_id, document)) == CSV
    return document


def _row(api: Api, signed: Signed, document_id: str) -> Any:
    with api.db.customer(signed.customer_id) as conn:
        return conn.execute(
            text("SELECT status, failure_code, deleted_at FROM documents WHERE id = :d"), {"d": document_id}
        ).first()


def _replacements(api: Api, signed: Signed) -> list[tuple[str, dict[str, Any]]]:
    with api.db.customer(signed.customer_id) as conn:
        rows = conn.execute(
            text(
                "SELECT target_id, details FROM audit_log WHERE action = 'document.delete'"
                " AND details ? 'replaced_by' ORDER BY id"
            )
        ).all()
    return [(str(row[0]), dict(row[1])) for row in rows]


def test_a_refused_file_uploaded_again_is_a_new_document_scanned_afresh(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    refused = _refused(api, signed)

    again = _upload(signed, name="register again.csv")

    assert again.status_code == 201, again.text
    body = again.json()
    document = body["document"]["id"]
    assert (body["duplicate"], body["in_trash"], body["document"]["status"]) == (False, False, "received")
    assert document != refused
    assert [job.document_id for job in api.queue.jobs][-1] == document
    assert _row(api, signed, refused) is None  # the refused copy is gone for good
    assert (signed.customer_id, refused) not in api.storage.quarantined  # and its file
    assert api.storage.has(signed.customer_id, document)
    assert _replacements(api, signed) == [(refused, {"replaced_by": document})]
    with db.customer(signed.customer_id) as conn:
        assert storage_room(conn, signed.customer_id).used_bytes == len(CSV)  # one copy counted
    _scan(api, signed, document, Verdict.CLEAN)  # fine now: read
    assert _row(api, signed, document).status == "ready"


def test_a_file_still_bad_is_refused_again_with_the_same_words(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    refused = _refused(api, signed)
    again = _upload(signed)
    assert again.status_code == 201, again.text  # sent to be scanned again
    document = again.json()["document"]["id"]
    assert document != refused

    _scan(api, signed, document, Verdict.INFECTED)

    assert tuple(_row(api, signed, document))[:2] == ("rejected", "virus_found")
    assert api.storage.quarantined[(signed.customer_id, document)] == CSV  # never served
    shown = signed.client.get(f"/api/documents/{document}").json()
    assert shown["status_message"] == CUSTOMER_MESSAGES["virus_found"]


def test_a_refused_file_in_trash_is_replaced_too(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    refused = _refused(api, signed)
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": refused})

    again = _upload(signed)

    assert again.status_code == 201, again.text
    assert (again.json()["duplicate"], again.json()["in_trash"]) == (False, False)
    assert _row(api, signed, refused) is None


@pytest.mark.parametrize("status", ["ready", "failed", "received"])
def test_a_copy_not_refused_is_still_already_in_kaagaz(db: Database, status: str) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = _upload(signed).json()["document"]["id"]
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE documents SET status = :s WHERE id = :d"), {"s": status, "d": first})

    again = _upload(signed)

    assert (again.status_code, again.json()["duplicate"], again.json()["document"]["id"]) == (
        200,
        True,
        first,
    )
    assert len(api.queue.jobs) == 1
    assert _replacements(api, signed) == []


# --- PRD 9 F-03 (D3): a failed or never-read copy in Trash ---------------------------------------


def _in_trash(api: Api, signed: Signed, status: str) -> str:
    """CSV uploaded, given ``status`` and moved to Trash: its id."""
    first: str = _upload(signed).json()["document"]["id"]
    with api.db.customer(signed.customer_id) as conn:
        conn.execute(
            text("UPDATE documents SET status = :s, deleted_at = now() WHERE id = :d"),
            {"s": status, "d": first},
        )
    return first


@pytest.mark.parametrize("status", ["failed", "received", "clean", "needs_reader"])
def test_a_failed_or_never_read_copy_in_trash_is_replaced_with_no_question(db: Database, status: str) -> None:
    api = Api(db)
    signed = api.new_signed()
    old = _in_trash(api, signed, status)

    again = _upload(signed)

    assert again.status_code == 201, again.text
    body = again.json()
    document = body["document"]["id"]
    assert (body["duplicate"], body["in_trash"], body["document"]["status"]) == (False, False, "received")
    assert _row(api, signed, old) is None  # gone for good
    assert not api.storage.has(signed.customer_id, old)  # its file too
    assert api.storage.has(signed.customer_id, document)
    assert _replacements(api, signed) == [(old, {"replaced_by": document})]  # Activity names it
    _scan(api, signed, document, Verdict.CLEAN)
    assert _row(api, signed, document).status == "ready"


@pytest.mark.parametrize("status", ["ready", "locked"])
def test_a_copy_in_trash_that_was_read_or_locked_is_still_already_in_trash(db: Database, status: str) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = _upload(signed).json()["document"]["id"]
    with db.customer(signed.customer_id) as conn:
        conn.execute(
            text(
                "UPDATE documents SET status = :s, deleted_at = now(),"
                " locked_at = CASE WHEN :s = 'locked' THEN now() END,"
                " locked_by = CASE WHEN :s = 'locked' THEN uploaded_by END WHERE id = :d"
            ),
            {"s": status, "d": first},
        )

    again = _upload(signed)

    assert (again.status_code, again.json()["duplicate"]) == (200, True)
    assert _row(api, signed, first) is not None
    assert _replacements(api, signed) == []
    assert len(api.queue.jobs) == 1


def test_a_failed_version_in_trash_of_a_family_with_others_is_still_already_in(db: Database) -> None:
    """Kaagaz deletes whole families only."""
    api = Api(db)
    signed = api.new_signed()
    first = _upload(signed, data=b"name,amount\nA,1\n").json()["document"]["id"]
    reply = signed.client.post(
        f"/api/documents/{first}/versions", files={"file": (CSV_NAME, CSV, "text/csv")}, headers=CSRF
    )
    assert reply.status_code == 201, reply.text
    version = reply.json()["document"]["id"]
    with db.customer(signed.customer_id) as conn:
        conn.execute(text("UPDATE documents SET status = 'failed' WHERE id = :d"), {"d": version})
        conn.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id IN (:a, :b)"), {"a": first, "b": version}
        )

    again = _upload(signed)

    assert (again.status_code, again.json()["duplicate"], again.json()["in_trash"]) == (200, True, True)
    assert _row(api, signed, version) is not None


def _refused_version(api: Api, signed: Signed) -> str:
    """A family of two: another file, then CSV as its new version, refused. The version's id."""
    first = _upload(signed, data=b"name,amount\nA,1\n").json()["document"]["id"]
    reply = signed.client.post(
        f"/api/documents/{first}/versions", files={"file": (CSV_NAME, CSV, "text/csv")}, headers=CSRF
    )
    assert reply.status_code == 201, reply.text
    version: str = reply.json()["document"]["id"]
    _scan(api, signed, version, Verdict.INFECTED)
    return version


def test_a_refused_version_of_a_family_with_others_is_still_already_in_kaagaz(db: Database) -> None:
    """Kaagaz deletes whole families only, so a refused version is never taken out of one."""
    api = Api(db)
    signed = api.new_signed()
    version = _refused_version(api, signed)

    again = _upload(signed)

    assert (again.status_code, again.json()["duplicate"], again.json()["document"]["id"]) == (
        200,
        True,
        version,
    )
    assert _row(api, signed, version).status == "rejected"


# --- folder upload: "Add new files only" (#484) -------------------------------------------------


def _register(signed: Signed) -> dict[str, Any]:
    planned = plan(signed, only_new=True).json()
    reply = register(
        signed, planned["id"], [item("A/register.csv", planned["folder_ids"][0], len(CSV), sha256=_SHA)]
    )
    assert reply.status_code == 200, reply.text
    (row,) = reply.json()["items"]
    return dict(row)


def test_a_folder_upload_sends_a_file_whose_only_copy_was_refused(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    refused = _refused(api, signed)

    row = _register(signed)

    assert (row["state"], row["document_id"]) == ("waiting", None)
    sent = signed.client.post(
        f"/api/documents?folder_upload_item_id={row['id']}",
        files={"file": (CSV_NAME, CSV, "text/csv")},
        headers=CSRF,
    )
    assert sent.status_code == 201, sent.text
    assert (sent.json()["duplicate"], sent.json()["item"]["state"]) == (False, "stored")
    assert _row(api, signed, refused) is None


def test_a_folder_upload_sends_a_file_whose_only_copy_failed_in_trash(db: Database) -> None:
    """PRD 9 F-03: the database's duplicate check (kaagaz_duplicate, 0102) agrees with the upload."""
    api = Api(db)
    signed = api.new_signed()
    _in_trash(api, signed, "failed")

    row = _register(signed)

    assert (row["state"], row["document_id"]) == ("waiting", None)


def test_a_folder_upload_finds_a_refused_version_already_in(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    version = _refused_version(api, signed)

    row = _register(signed)

    assert (row["state"], row["document_id"]) == ("duplicate", version)


# --- two uploads at once ------------------------------------------------------------------------


def test_two_uploads_of_a_refused_file_at_once_store_one(db: Database) -> None:
    """The order is forced: the test holds the refused copy's family lock until both uploads
    wait behind it. One replaces it; the other then finds the new document, a duplicate, and
    its stored file is deleted again."""
    customer, user = new_customer(db)
    storage = MemoryStorage()
    with db.customer(customer) as conn:
        refused = insert_document(conn, customer, sha256=_SHA, size_bytes=len(CSV), status="rejected")
    storage.put(customer, refused, io.BytesIO(CSV))
    storage.quarantine(customer, refused)
    sessions = [new_session(), new_session()]
    named = [_named_database(session) for session in sessions]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            with db.customer(customer) as conn:
                lock_family(conn, customer, refused)
                uploads = [
                    pool.submit(
                        UploadService(
                            PIPELINE_POLICY, PgDocuments(database, storage=storage), storage, MemoryQueue()
                        ).accept,
                        customer,
                        io.BytesIO(CSV),
                        uploaded_by=user,
                    )
                    for database in named
                ]
                for session in sessions:
                    wait_until_blocked(db, application_name=session)
            results = [upload.result(timeout=10) for upload in uploads]
    finally:
        for database in named:
            database.engine.dispose()

    assert sorted(result.duplicate for result in results) == [False, True]
    (winner,) = {result.document_id for result in results}
    with db.customer(customer) as conn:
        assert conn.execute(text("SELECT id::text FROM documents")).scalars().all() == [winner]
    assert storage.count() == 1 and storage.get(customer, winner) == CSV
    assert storage.quarantined == {}
