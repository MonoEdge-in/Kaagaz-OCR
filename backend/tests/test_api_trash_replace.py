"""#697 (night meeting, 19 Nov 2026): a file whose exact copy is in Trash, read, may be uploaded as
a new file by who may delete forever (an Admin, or a member or reviewer an admin allowed), with no
typed password: the resumable start says ``replace_trash``, and at its complete the Trash copy
(found by the upload's own bytes) is deleted forever and the upload stored as a new file. Anyone
else is refused; a copy locked, out of Trash or that the person can't see is answered as before.
Another organisation's copy: test_isolation_trash_replace."""

from typing import Any

import httpx2 as httpx
import pytest

from kaagaz.db.engine import Database
from tests.test_api_folder_upload_files import ResumableStorage, _fingerprint
from tests.test_api_support import CSRF, Api, Signed
from tests.test_isolation_access_views import _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

DATA = b"name,amount\nsalary,1\n"
SESSIONS = "/api/documents/upload-sessions"


def upload(signed: Signed, data: bytes = DATA, **more: Any) -> httpx.Response:
    """``data`` sent the way the browser sends every upload (resumable), with ``more`` at its start."""
    start = {
        "file_name": "again.csv",
        "file_size": len(data),
        "fingerprint": _fingerprint(data),
        "user_id": signed.user_id,
        **more,
    }
    session = signed.client.post(SESSIONS, json=start, headers=CSRF).json()["id"]
    assert signed.client.put(f"{SESSIONS}/{session}/parts/1", content=data, headers=CSRF).status_code == 204
    reply: httpx.Response = signed.client.post(f"{SESSIONS}/{session}/complete", headers=CSRF)
    return reply


def in_trash(api: Api, signed: Signed) -> str:
    """DATA uploaded, read (ready) and moved to Trash: its id. A read copy is "already in Trash"."""
    reply = upload(signed)
    assert reply.status_code == 201, reply.text
    document: str = reply.json()["document"]["id"]
    _owner(api.db, "UPDATE documents SET status = 'ready', deleted_at = now() WHERE id = :d", {"d": document})
    return document


def exists(api: Api, document: str) -> bool:
    return _owner(api.db, "SELECT count(*) FROM documents WHERE id = :d", {"d": document}) == [(1,)]


def grant(admin: Signed, user_id: str) -> None:
    path = f"/api/admin/users/{user_id}/delete-forever"
    reply = admin.client.put(path, json={"allowed": True}, headers=CSRF)
    assert reply.status_code == 200, reply.text


def test_an_admin_replaces_the_copy_in_trash_with_no_password_into_the_chosen_folder(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    admin = api.new_admin()
    old = in_trash(api, admin)
    folder = api.add_folder(admin.customer_id, admin.user_id, "Invoices")
    assert upload(admin, folder_id=folder).json()["in_trash"] is True  # as before, without asking

    reply = upload(admin, folder_id=folder, replace_trash=True)

    assert reply.status_code == 201, reply.text
    body = reply.json()
    new = body["document"]["id"]
    assert (body["duplicate"], body["in_trash"], body["document"]["status"]) == (False, False, "received")
    assert body["document"]["folder_id"] == folder
    assert not exists(api, old)
    assert not api.storage.has(admin.customer_id, old)
    assert api.storage.has(admin.customer_id, new)
    assert api.queue.jobs[-1].document_id == new  # read fresh
    rows = api.audit_rows(admin.customer_id)
    assert rows[-2] == ("document.replace_from_trash", "document", old, {"versions": 1})
    assert rows[-1][:3] == ("document.upload", "document", new)
    assert rows[-1][3]["replaces"] == old


def test_a_member_an_admin_allowed_replaces_it_with_no_password(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    grant(admin, member.user_id)
    old = in_trash(api, member)

    reply = upload(member, replace_trash=True)

    assert reply.status_code == 201, reply.text
    assert not exists(api, old)
    replaced = ("document.replace_from_trash", "document", old, {"versions": 1, "by_grant": True})
    assert replaced in api.audit_rows(admin.customer_id)


@pytest.mark.parametrize("role", ["member", "reviewer"])
def test_anyone_else_is_refused_and_nothing_is_deleted(db: Database, role: str) -> None:
    api = Api(db, storage=ResumableStorage())
    admin = api.new_admin()
    person = api.sign_in(admin.customer_id, role)
    old = in_trash(api, person)

    reply = upload(person, replace_trash=True)

    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "forbidden")
    assert exists(api, old)
    assert "document.replace_from_trash" not in api.audit_actions(admin.customer_id)


def test_a_copy_out_of_trash_is_answered_as_before(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    admin = api.new_admin()
    old = in_trash(api, admin)
    _owner(db, "UPDATE documents SET deleted_at = NULL WHERE id = :d", {"d": old})

    reply = upload(admin, replace_trash=True)

    assert (reply.status_code, reply.json()["duplicate"], reply.json()["in_trash"]) == (200, True, False)
    assert exists(api, old)
    assert "document.replace_from_trash" not in api.audit_actions(admin.customer_id)


def test_a_locked_copy_the_person_may_open_is_answered_as_before(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    admin = api.new_admin()
    member = api.sign_in(admin.customer_id, "member")
    grant(admin, member.user_id)
    old = in_trash(api, member)
    _owner(
        db,
        "UPDATE documents SET locked_at = now(), locked_by = :a, status = 'locked' WHERE id = :d",
        {"a": admin.user_id, "d": old},
    )
    _owner(
        db,
        "INSERT INTO lock_grants (customer_id, document_id, user_id, given_by) VALUES (:c, :d, :u, :a)",
        {"c": admin.customer_id, "d": old, "u": member.user_id, "a": admin.user_id},
    )

    reply = upload(member, replace_trash=True)

    assert (reply.status_code, reply.json()["duplicate"], reply.json()["locked"]) == (200, True, {"id": old})
    assert exists(api, old)
    assert "document.replace_from_trash" not in api.audit_actions(admin.customer_id)


def test_a_copy_the_person_cant_see_is_answered_as_hidden_and_stays(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    org = _walled(api)
    trashed = org.files["trashed"]  # in Trash, in Salaries: private, and "member" is not on it
    same = "UPDATE documents SET sha256 = encode(sha256(:b), 'hex') WHERE id = :d"
    _owner(db, same, {"b": DATA, "d": trashed})
    member = org.people["member"]
    grant(org.people["admin"], member.user_id)

    reply = upload(member, replace_trash=True)

    assert (reply.status_code, reply.json()["document"], reply.json().get("hidden")) == (200, None, True)
    assert exists(api, trashed)
    assert api.storage.has(org.c, trashed)
    assert "document.replace_from_trash" not in api.audit_actions(org.c)


def test_an_upload_of_other_bytes_leaves_the_copy_in_trash(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    admin = api.new_admin()
    old = in_trash(api, admin)

    reply = upload(admin, b"name,amount\nsalary,2\n", replace_trash=True)

    assert (reply.status_code, reply.json()["duplicate"]) == (201, False)
    assert exists(api, old)
    assert "document.replace_from_trash" not in api.audit_actions(admin.customer_id)


def test_a_finished_session_of_the_drop_is_not_resumed_by_its_replace(db: Database) -> None:
    # The tray sends the replace in the same drop: the drop's finished session (answered "in
    # Trash") must not be offered to it, or its stored reply would come back instead.
    api = Api(db, storage=ResumableStorage())
    admin = api.new_admin()
    in_trash(api, admin)
    start = {
        "file_name": "again.csv",
        "file_size": len(DATA),
        "fingerprint": _fingerprint(DATA),
        "user_id": admin.user_id,
        "batch_id": "6f1c1d4e-3b0a-4a8e-9a39-1f3f3a8b9c11",
    }
    assert upload(admin, batch_id=start["batch_id"]).json()["in_trash"] is True

    found = admin.client.post(f"{SESSIONS}/candidates", json={**start, "replace_trash": True}, headers=CSRF)

    assert found.json() == {"candidates": []}
    plain = admin.client.post(f"{SESSIONS}/candidates", json=start, headers=CSRF)
    assert len(plain.json()["candidates"]) == 1
