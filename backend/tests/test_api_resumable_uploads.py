"""A plain resumable upload against PostgreSQL (production QA T1): resumed from its
saved parts after a restart, and a ``complete`` sent again after its reply was lost answers the
stored document instead of 404 (the tray showed a stored file as failed), a folder upload's item
too. A folder upload's item otherwise: test_api_folder_upload_files."""

import hashlib

import pytest
from sqlalchemy import text

from kaagaz.db import upload_sessions as upload_sessions_sql
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from tests.settings_factory import make_settings
from tests.test_api_folder_upload_files import ResumableStorage, _fingerprint, planned
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed
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


class _PartsStayStorage(ResumableStorage):
    """The stored parts can't be deleted (storage down after the file was stored)."""

    def delete_resumable(self, key: str) -> None:
        raise BackendUnavailable("storage")


def _sent(signed: Signed, start: dict[str, object]) -> str:
    """A session for ``start`` with its one part sent: the session's id."""
    session = str(signed.client.post(SESSIONS, json=start, headers=CSRF).json()["id"])
    sent = signed.client.put(f"{SESSIONS}/{session}/parts/1", content=PDF_BYTES, headers=CSRF)
    assert sent.status_code == 204
    return session


def _start(signed: Signed, **more: object) -> dict[str, object]:
    return {
        "file_name": "big.pdf",
        "file_size": len(PDF_BYTES),
        "fingerprint": _fingerprint(PDF_BYTES),
        "user_id": signed.user_id,
        **more,
    }


def test_a_stored_file_whose_parts_cant_be_deleted_still_answers_stored_and_again(db: Database) -> None:
    # Review of #564: the reply is saved before the parts are deleted, and that delete is best
    # effort (the purge removes them with the session): no 503 for a stored file, and a complete
    # sent again never stores it a second time as "Already uploaded".
    api = Api(db, storage=_PartsStayStorage())
    signed = api.new_signed()
    session = _sent(signed, _start(signed))
    first = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)
    assert first.status_code == 201, first.text
    again = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)
    assert (again.status_code, again.json()) == (200, first.json())


def test_a_complete_while_another_is_storing_the_file_waits_its_turn(db: Database) -> None:
    # Review of #564: two completes of one session never both store it. The one that finds
    # another storing gets 429 with Retry-After (the browser waits that out); a claim left by a
    # crash is taken over once older than the browser's wait for a complete; a failure frees it.
    api = Api(db, storage=ResumableStorage())
    signed = api.new_signed()
    session = signed.client.post(SESSIONS, json=_start(signed), headers=CSRF).json()["id"]
    early = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)  # no part yet: 409
    assert early.status_code == 409, early.text
    sent = signed.client.put(f"{SESSIONS}/{session}/parts/1", content=PDF_BYTES, headers=CSRF)
    assert sent.status_code == 204
    owner = (signed.customer_id, signed.user_id, session)
    with db.customer(signed.customer_id) as conn:
        assert upload_sessions_sql.claim(conn, *owner)  # the 409 freed it; another complete holds it now
        assert not upload_sessions_sql.claim(conn, *owner)
    busy = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)
    assert (busy.status_code, busy.json()["error"]["code"]) == (429, "rate_limited")
    assert busy.headers["Retry-After"] == "2"
    with db.customer(signed.customer_id) as conn:  # the claim is a crash's now: older than 5 minutes
        conn.execute(
            text(
                "UPDATE upload_sessions SET metadata = jsonb_set(metadata, '{completing}',"
                " to_jsonb(now() - interval '301 seconds')) WHERE id = :s"
            ),
            {"s": session},
        )
    done = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)
    assert done.status_code == 201, done.text


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
