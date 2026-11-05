"""The storage limit per organisation (#516) against PostgreSQL. The limit is the organisation's
own, else the platform's, else 50 GB; the bytes used are every document row's, every version
and the Trash until it is purged. A new upload past it is refused 413 ``storage_full`` with the
same words and details at each of its two checks: the upload service (a single upload, a new
version, a resumable complete, a folder upload's file) and the folder upload plan. The
resumable start is not checked, so a duplicate sent that way costs nothing too; exactly the
bytes left go in. The service's own rules:
test_upload_storage_limit. The operator's routes: test_operator_storage_limit_db."""

import io
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.db.storage_limits import DEFAULT_STORAGE_GB, GIB, storage_room
from kaagaz.ingestion.ports import StorageRoom
from tests.db_support import insert_document, new_customer
from tests.support import TEXT_BYTES
from tests.test_api_folder_upload_files import ResumableStorage, _fingerprint, planned, send
from tests.test_api_folder_uploads import TREE, plan
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

SESSIONS = "/api/documents/upload-sessions"
_ORG = "INSERT INTO org_storage_limits (customer_id, storage_gb) VALUES (:c, :g)"


def _words(gb: int) -> str:
    return (
        f"This organisation has used its {gb} GB of storage. Delete files forever from the Trash"
        " to free space, or ask Kaagaz to raise the limit."
    )


@contextmanager
def platform_reset(db: Database) -> Iterator[None]:
    """The platform's limit belongs to no customer: every test that sets it puts it back."""
    try:
        yield
    finally:
        _owner(db, "UPDATE platform_ai_settings SET storage_gb = NULL", {})


def _room(db: Database, customer: str) -> StorageRoom:
    with db.customer(customer) as conn:
        return storage_room(conn, customer)


def fill(db: Database, customer: str, *, left: int, gb: int = 1, **columns: Any) -> str:
    """The organisation's own limit of ``gb`` GB, and one document holding all of it but ``left``
    bytes. Returns the document."""
    _owner(db, _ORG, {"c": customer, "g": gb})
    with db.customer(customer) as conn:
        return insert_document(conn, customer, size_bytes=gb * GIB - left, **columns)


def _refused(reply: Any, *, used: int, gb: int = 1) -> None:
    assert reply.status_code == 413, reply.text
    assert reply.json()["error"] == {
        "code": "storage_full",
        "message": _words(gb),
        "details": {"used_bytes": used, "limit_bytes": gb * GIB, "limit_gb": gb},
    }


def upload(signed: Signed, data: bytes, name: str = "bill.pdf") -> Any:
    files = {"file": (name, data, "application/pdf")}
    return signed.client.post("/api/documents", files=files, headers=CSRF)


# --- the limit and the bytes used -------------------------------------------------------------


def test_the_limit_is_the_orgs_then_the_platforms_then_50_gb(db: Database) -> None:
    customer, _ = new_customer(db)
    other, _ = new_customer(db)
    with platform_reset(db):
        assert _room(db, customer) == StorageRoom(DEFAULT_STORAGE_GB * GIB, 0)
        assert DEFAULT_STORAGE_GB == 50
        _owner(db, "UPDATE platform_ai_settings SET storage_gb = 60", {})
        assert _room(db, customer).limit_bytes == 60 * GIB
        _owner(db, _ORG, {"c": customer, "g": 70})
        assert _room(db, customer).limit_bytes == 70 * GIB
        assert _room(db, other).limit_bytes == 60 * GIB  # an organisation with no row follows the platform


def test_every_version_and_the_trash_count_until_purged_and_another_orgs_never(db: Database) -> None:
    customer, user = new_customer(db)
    other, _ = new_customer(db)
    with db.customer(customer) as conn:
        first = insert_document(conn, customer, size_bytes=100)
        insert_document(conn, customer, size_bytes=20, family_id=first, version_no=2, is_current=False)
        trashed = insert_document(conn, customer, size_bytes=3, deleted_at=_now(conn), deleted_by=user)
    with db.customer(other) as conn:
        insert_document(conn, other, size_bytes=5000)
    _owner(db, _ORG, {"c": other, "g": 9})
    assert _room(db, customer) == StorageRoom(DEFAULT_STORAGE_GB * GIB, 123)
    with db.customer(customer) as conn:  # purged: its row goes
        conn.execute(
            text("DELETE FROM documents WHERE customer_id = :c AND id = :d"), {"c": customer, "d": trashed}
        )
    assert _room(db, customer).used_bytes == 120
    assert _room(db, other) == StorageRoom(9 * GIB, 5000)


def _now(conn: Any) -> Any:
    return conn.execute(text("SELECT now()")).scalar_one()


# --- 1. the upload service --------------------------------------------------------------------


def test_a_single_upload_of_exactly_the_bytes_left_goes_in_and_the_next_is_refused(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    fill(db, signed.customer_id, left=len(PDF_BYTES))
    assert upload(signed, PDF_BYTES).status_code == 201
    _refused(upload(signed, TEXT_BYTES, "list.csv"), used=GIB)
    assert len(api.queue.jobs) == 1  # nothing stored or queued for the refused file
    again = upload(signed, PDF_BYTES)  # a duplicate costs nothing, even when full
    assert (again.status_code, again.json()["duplicate"]) == (200, True)


def test_one_byte_over_is_refused_and_the_trash_counts_until_it_is_deleted_forever(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    customer = admin.customer_id
    with db.customer(customer) as conn:
        held = insert_document(
            conn, customer, size_bytes=1000, deleted_at=_now(conn), deleted_by=admin.user_id
        )
    fill(db, customer, left=len(PDF_BYTES) + 1000 - 1)  # one byte short, the Trash counted
    _refused(upload(admin, PDF_BYTES), used=GIB - len(PDF_BYTES) + 1)
    api.storage.put(customer, held, io.BytesIO(b"x"))
    assert admin.client.delete(f"/api/documents/{held}/permanent", headers=CSRF).status_code == 204
    assert upload(admin, PDF_BYTES).status_code == 201


def test_a_new_version_is_refused(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = upload(signed, PDF_BYTES).json()["document"]["id"]
    fill(db, signed.customer_id, left=0)
    files = {"file": ("v2.csv", TEXT_BYTES, "text/csv")}
    reply = signed.client.post(f"/api/documents/{first}/versions", files=files, headers=CSRF)
    _refused(reply, used=GIB + len(PDF_BYTES))


def test_a_folder_uploads_file_is_refused_and_its_item_stays_waiting(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, (one,) = planned(signed, {"A/bill.pdf": PDF_BYTES})
    fill(db, signed.customer_id, left=len(PDF_BYTES) - 1)
    _refused(send(signed, one["id"], PDF_BYTES), used=GIB - len(PDF_BYTES) + 1)
    state = _owner(db, "SELECT state, failure_code FROM folder_upload_items WHERE id = :i", {"i": one["id"]})
    assert [tuple(row) for row in state] == [("waiting", None)]  # sent again once there is room


# --- 2. the resumable path: its start is not checked, its complete goes through the service -----


def _start(signed: Signed, data: bytes, **extra: Any) -> Any:
    body = {
        "file_name": "big.pdf",
        "file_size": len(data),
        "fingerprint": _fingerprint(data),
        "user_id": signed.user_id,
        **extra,
    }
    return signed.client.post(SESSIONS, json=body, headers=CSRF)


def _send_all(signed: Signed, data: bytes) -> Any:
    """Start, send the one part and complete: the complete's reply."""
    started = _start(signed, data)
    assert started.status_code == 200, started.text
    session = started.json()["id"]
    sent = signed.client.put(f"{SESSIONS}/{session}/parts/1", content=data, headers=CSRF)
    assert sent.status_code == 204
    return signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)


def _sessions(db: Database, customer: str) -> Any:
    return _owner(db, "SELECT count(*) FROM upload_sessions WHERE customer_id = :c", {"c": customer})


def test_a_full_orgs_resumable_send_of_a_file_it_already_has_is_the_duplicate(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    signed = api.new_signed()
    first = upload(signed, PDF_BYTES).json()["document"]["id"]
    fill(db, signed.customer_id, left=0)  # full; the start has no bytes, so it can't tell a duplicate
    again = _send_all(signed, PDF_BYTES)
    assert again.status_code == 200, again.text
    assert (again.json()["duplicate"], again.json()["document"]["id"]) == (True, first)


def test_a_resumable_send_past_the_limit_is_refused_at_its_complete_and_cleaned_up(db: Database) -> None:
    storage = ResumableStorage()
    api = Api(db, storage=storage)
    signed = api.new_signed()
    fill(db, signed.customer_id, left=len(PDF_BYTES) - 1)
    _refused(_send_all(signed, PDF_BYTES), used=GIB - len(PDF_BYTES) + 1)  # one byte over
    assert (storage.parts, storage.done) == ({}, {})  # its stored parts are gone
    assert _sessions(db, signed.customer_id) == [(0,)]  # and its session
    assert len(api.queue.jobs) == 0
    assert _send_all(signed, TEXT_BYTES).status_code == 201  # a file that fits still goes in


def test_a_resumable_complete_is_refused_when_the_room_went_meanwhile(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    signed = api.new_signed()
    session = _start(signed, PDF_BYTES).json()["id"]
    sent = signed.client.put(f"{SESSIONS}/{session}/parts/1", content=PDF_BYTES, headers=CSRF)
    assert sent.status_code == 204
    fill(db, signed.customer_id, left=0)  # another upload took the room
    _refused(signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF), used=GIB)
    assert len(api.queue.jobs) == 0


# --- 3. the folder upload plan ----------------------------------------------------------------


def test_a_folder_plan_past_the_limit_is_refused_before_any_folder_is_made(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    fill(db, signed.customer_id, left=3_000)
    _refused(plan(signed, TREE, total_bytes=3_001), used=GIB - 3_000)
    assert signed.client.get("/api/folders").json()["items"] == []
    assert plan(signed, TREE, total_bytes=3_000).status_code == 200  # exactly the bytes left


def test_a_re_plan_does_not_count_the_files_it_already_sent_twice(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    body, (one, _) = planned(signed, {"A/bill.pdf": PDF_BYTES, "A/list.csv": TEXT_BYTES})
    assert send(signed, one["id"], PDF_BYTES).status_code == 201
    fill(db, signed.customer_id, left=len(PDF_BYTES) + len(TEXT_BYTES))  # PDF_BYTES used since
    total = len(PDF_BYTES) + len(TEXT_BYTES)
    again = plan(signed, TREE, id=body["id"], file_count=2, total_bytes=total)  # "Continue upload"
    assert again.status_code == 200, again.text
    _refused(plan(signed, TREE, total_bytes=total), used=GIB - len(TEXT_BYTES))  # a new upload of it


# --- the organisation admin's view ------------------------------------------------------------


def test_an_admin_sees_the_storage_used_and_the_limit_and_no_other_organisations(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    other = api.new_admin()
    with db.customer(admin.customer_id) as conn:
        insert_document(conn, admin.customer_id, size_bytes=1234)
    fill(db, other.customer_id, left=5, gb=3)
    with platform_reset(db):
        assert admin.client.get("/api/admin/storage").json() == {
            "used_bytes": 1234,
            "limit_bytes": 50 * GIB,
            "limit_gb": 50,
        }
        _owner(db, "UPDATE platform_ai_settings SET storage_gb = 60", {})
        assert admin.client.get("/api/admin/storage").json()["limit_gb"] == 60
    assert other.client.get("/api/admin/storage").json() == {
        "used_bytes": 3 * GIB - 5,
        "limit_bytes": 3 * GIB,
        "limit_gb": 3,
    }


def test_a_member_may_not_read_the_storage(db: Database) -> None:
    member = Api(db).new_signed()
    reply = member.client.get("/api/admin/storage")
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "admin_session_required")
