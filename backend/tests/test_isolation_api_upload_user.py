"""A file chosen while A was signed in is refused once B has signed in on the
same browser (critical #5): another tab, or a queued upload, would otherwise
send it with B's cookie into B's customer.

The upload form names who the tab had signed in (``user_id``). Another user
gets 409 ``session_changed``, on both upload routes, before anything is stored,
queued or audited.
"""

import httpx2 as httpx
import pytest

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, PASSWORD, PDF_BYTES, Api, Signed

pytestmark = pytest.mark.db

SESSION_CHANGED = {
    "error": {
        "code": "session_changed",
        "message": "Not uploaded. A different account has signed in on this browser since you chose this"
        " file. Reload the page to see who is signed in now.",
    }
}
V2_BYTES = b"%PDF-1.7\n" + b"2" * 200


def _send(signed: Signed, url: str, user_id: str, data: bytes = PDF_BYTES) -> httpx.Response:
    reply: httpx.Response = signed.client.post(
        url,
        data={"user_id": user_id},
        files={"file": ("sharma bill.pdf", data, "application/pdf")},
        headers=CSRF,
    )
    return reply


def test_an_upload_for_the_user_signed_in_before_is_409_and_nothing_is_kept(db: Database) -> None:
    api = Api(db)
    a = api.new_signed()
    b_customer, _ = api.customer("Mehta Textiles")
    b_user, b_email = api.add_user(b_customer, "member")
    b_document = api.add_document(b_customer, uploaded_by=b_user)
    stored = api.storage.count()

    # B signs in on A's browser: the same client, so the cookie is now B's.
    signed_in = a.client.post("/api/auth/login", json={"email": b_email, "password": PASSWORD}, headers=CSRF)
    assert signed_in.status_code == 200
    b_actions = api.audit_actions(b_customer)

    for url in ("/api/documents", f"/api/documents/{b_document}/versions"):
        reply = _send(a, url, a.user_id)
        assert (reply.status_code, reply.json()) == (409, SESSION_CHANGED), url
    assert api.storage.count() == stored
    assert api.queue.jobs == []
    assert api.audit_actions(b_customer) == b_actions  # no document.upload, no document.version
    assert a.client.get("/api/documents").json()["total"] == 1  # only B's own document


def test_the_signed_in_users_own_id_uploads_as_before(db: Database) -> None:
    api = Api(db)
    a = api.new_signed()
    reply = _send(a, "/api/documents", a.user_id)
    assert reply.status_code == 201
    version = _send(a, f"/api/documents/{reply.json()['document']['id']}/versions", a.user_id, V2_BYTES)
    assert version.status_code == 201
    assert [job.customer_id for job in api.queue.jobs] == [a.customer_id, a.customer_id]
    assert api.audit_actions(a.customer_id)[-2:] == ["document.upload", "document.version"]
