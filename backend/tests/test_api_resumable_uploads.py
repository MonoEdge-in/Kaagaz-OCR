"""A plain resumable upload against PostgreSQL (production QA T1): resumed from its
saved parts after a restart, and a ``complete`` sent again after its reply was lost answers the
stored document instead of 404 (the tray showed a stored file as failed), a folder upload's item
too. A folder upload's item otherwise: test_api_folder_upload_files."""

import hashlib

import pytest

from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_folder_upload_files import ResumableStorage, _fingerprint, planned
from tests.test_api_support import CSRF, PDF_BYTES, Api
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

SESSIONS = "/api/documents/upload-sessions"
PART = 8 * 1024 * 1024  # documents._UPLOAD_PART_BYTES


def test_complete_sent_again_after_a_lost_reply_answers_the_stored_document(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    signed = api.new_signed()
    start = {
        "file_name": "big.pdf",
        "file_size": len(PDF_BYTES),
        "fingerprint": _fingerprint(PDF_BYTES),
        "user_id": signed.user_id,
    }
    session = signed.client.post(SESSIONS, json=start, headers=CSRF).json()["id"]
    assert (
        signed.client.put(f"{SESSIONS}/{session}/parts/1", content=PDF_BYTES, headers=CSRF).status_code == 204
    )
    first = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)
    assert first.status_code == 201, first.text
    queued = len(api.queue.jobs)

    again = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)  # the reply was lost
    assert again.status_code == 200, again.text
    assert again.json()["document"]["id"] == first.json()["document"]["id"]
    assert len(api.queue.jobs) == queued  # nothing queued again
    # A finished session is never offered to resume, nor resumed, nor sent to.
    candidates = signed.client.post(f"{SESSIONS}/candidates", json=start, headers=CSRF).json()
    assert candidates == {"candidates": []}
    resumed = signed.client.post(SESSIONS, json={**start, "resume_session_id": session}, headers=CSRF)
    assert resumed.status_code == 404
    sent = signed.client.put(f"{SESSIONS}/{session}/parts/1", content=PDF_BYTES, headers=CSRF)
    assert sent.status_code == 404
    # Cleaned up as an expired session is, a day on.
    expires = _owner(db, "SELECT expires_at - now() FROM upload_sessions WHERE id = :s", {"s": session})
    assert expires[0][0].days == 0


def test_a_folder_items_complete_sent_again_after_a_lost_reply_answers_its_upload(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    signed = api.new_signed()
    _, (one,) = planned(signed, {"A/big.pdf": PDF_BYTES})
    start = {
        "file_name": "big.pdf",
        "file_size": len(PDF_BYTES),
        "fingerprint": _fingerprint(PDF_BYTES),
        "user_id": signed.user_id,
        "folder_upload_item_id": one["id"],
    }
    session = signed.client.post(SESSIONS, json=start, headers=CSRF).json()["id"]
    assert (
        signed.client.put(f"{SESSIONS}/{session}/parts/1", content=PDF_BYTES, headers=CSRF).status_code == 204
    )
    first = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)
    assert first.status_code == 201, first.text
    again = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)  # the reply was lost
    assert again.status_code == 200, again.text
    assert (again.json()["document"]["id"], again.json()["item"]["state"]) == (
        first.json()["document"]["id"],
        "stored",
    )


def test_a_plain_resumable_upload_resumes_from_its_saved_parts(db: Database) -> None:
    data = PDF_BYTES + b"\n%" + b"x" * (PART + 1000)
    parts = [data[:PART], data[PART:]]
    hashes = [hashlib.sha256(part).hexdigest() for part in parts]
    fingerprint = hashlib.sha256("".join(hashes).encode()).hexdigest()
    api = Api(db, storage=ResumableStorage(), settings=make_settings(max_upload_bytes=50 * 1024 * 1024))
    signed = api.new_signed()
    start = {
        "file_name": "two.pdf",
        "file_size": len(data),
        "fingerprint": fingerprint,
        "user_id": signed.user_id,
    }
    session = signed.client.post(SESSIONS, json=start, headers=CSRF).json()["id"]
    assert (
        signed.client.put(f"{SESSIONS}/{session}/parts/1", content=parts[0], headers=CSRF).status_code == 204
    )
    # A restart: the browser finds the saved session and the part already sent.
    found = signed.client.post(f"{SESSIONS}/candidates", json=start, headers=CSRF).json()["candidates"]
    assert found == [{"id": session, "parts": {"1": hashes[0]}}]
    resumed = signed.client.post(SESSIONS, json={**start, "resume_session_id": session}, headers=CSRF)
    assert (resumed.status_code, resumed.json()["id"], resumed.json()["part_count"]) == (200, session, 2)
    assert (
        signed.client.put(f"{SESSIONS}/{session}/parts/2", content=parts[1], headers=CSRF).status_code == 204
    )
    done = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)
    assert done.status_code == 201, done.text
    assert done.json()["document"]["size_bytes"] == len(data)
