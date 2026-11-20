"""Sending a folder upload's files (#484) against PostgreSQL: ``POST /api/documents`` and the
resumable start with ``folder_upload_item_id``. Each file lands in its item's folder and batch
(never another, never the top level), is never auto-filed, is checked at its registered size,
and ends its item in exactly one state however often it is sent; the organisation's and the
server's sends at once are capped with a 429 the browser backs off from."""

import hashlib
import io
import threading
import uuid
from contextlib import ExitStack
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_folder_uploads import TREE, item, plan, register
from tests.test_api_support import CSRF, PDF_BYTES, Api, ApiFakeStorage, Signed
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_DOCUMENT = "SELECT folder_id::text, batch_id::text, autofile, display_name FROM documents WHERE id = :d"
_GRANT = "UPDATE customers SET autofile_granted = true, autofile_on = true WHERE id = :c"


def planned(
    signed: Signed, files: dict[str, bytes], **changes: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A plan of A, A/B, A/B/C and these files (path: bytes), registered. Returns (plan, items)."""
    total = sum(len(data) for data in files.values())
    body = plan(signed, file_count=len(files), total_bytes=total, **changes).json()
    where = {"A": 0, "A/B": 1, "A/B/C": 2}
    rows = [
        item(path, body["folder_ids"][where[path.rsplit("/", 1)[0]]], len(data))
        for path, data in files.items()
    ]
    registered = register(signed, body["id"], rows)
    assert registered.status_code == 200, registered.text
    return body, registered.json()["items"]


def send(signed: Signed, item_id: str, data: bytes, name: str = "sent.pdf", **form: str) -> Any:
    return signed.client.post(
        f"/api/documents?folder_upload_item_id={item_id}",
        data=form,
        files={"file": (name, data, "application/pdf")},
        headers=CSRF,
    )


def _document(api: Api, customer: str, document: str) -> tuple[Any, ...]:
    with api.db.customer(customer) as conn:
        return tuple(conn.execute(text(_DOCUMENT), {"d": document}).one())


def _item(signed: Signed, upload: str, seq: int) -> dict[str, Any]:
    rows = signed.client.get(f"/api/folder-uploads/{upload}/items").json()["items"]
    return next(row for row in rows if row["seq"] == seq)


def test_a_file_lands_in_its_items_folder_and_batch_and_is_never_auto_filed(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    _owner(db, _GRANT, {"c": admin.customer_id})  # auto-filing on: a department upload would be filed
    _, (one,) = planned(admin, {"A/bill.pdf": PDF_BYTES}, target="department")
    reply = send(admin, one["id"], PDF_BYTES, name="renamed-by-the-browser.pdf")
    assert reply.status_code == 201, reply.text
    body = reply.json()
    assert (body["duplicate"], body["item"]["state"], body["item"]["document_id"]) == (
        False,
        "stored",
        body["document"]["id"],
    )
    folder, batch, autofile, name = _document(api, admin.customer_id, body["document"]["id"])
    assert (folder, autofile, name) == (one["folder_id"], None, "bill.pdf")
    upload_batch = admin.client.get(f"/api/batches/{batch}")
    assert upload_batch.status_code == 200  # the item's batch, the tray's usual one

    again = send(admin, one["id"], PDF_BYTES)  # sent again: nothing new
    assert (again.status_code, again.json()["item"]["state"], again.json()["document"]["id"]) == (
        200,
        "stored",
        body["document"]["id"],
    )
    assert len(api.queue.jobs) == 1


def test_a_send_that_stored_its_file_but_never_finished_is_stored_not_a_duplicate_of_itself(
    db: Database,
) -> None:
    api = Api(db)
    signed = api.new_signed()
    body, (one,) = planned(signed, {"A/B/bill.pdf": PDF_BYTES})
    first = send(signed, one["id"], PDF_BYTES).json()["document"]["id"]
    _owner(
        db,
        "UPDATE folder_upload_items SET state = 'waiting', document_id = NULL WHERE id = :i",
        {"i": one["id"]},
    )
    retry = send(signed, one["id"], PDF_BYTES)
    assert (retry.status_code, retry.json()["duplicate"], retry.json()["item"]["state"]) == (
        201,
        False,
        "stored",
    )
    assert retry.json()["document"]["id"] == first
    summary = signed.client.get(f"/api/folder-uploads/{body['id']}").json()
    assert (summary["stored"], summary["duplicate"], summary["waiting"]) == (1, 0, 0)


def test_two_sends_of_one_item_at_once_store_one_document(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, (one,) = planned(signed, {"A/bill.pdf": PDF_BYTES})
    replies: list[Any] = []
    threads = [
        threading.Thread(target=lambda: replies.append(send(signed, one["id"], PDF_BYTES))) for _ in "ab"
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(reply.status_code for reply in replies) in ([200, 201], [201, 201])
    assert {reply.json()["item"]["state"] for reply in replies} == {"stored"}
    assert len({reply.json()["document"]["id"] for reply in replies}) == 1
    assert len(api.queue.jobs) == 1


def test_the_same_bytes_twice_in_one_upload_are_one_stored_and_one_duplicate(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    body, (one, two) = planned(signed, {"A/bill.pdf": PDF_BYTES, "A/B/copy of bill.pdf": PDF_BYTES})
    stored = send(signed, one["id"], PDF_BYTES).json()
    duplicate = send(signed, two["id"], PDF_BYTES)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["item"] | {"document": None} == {
        **two,
        "state": "duplicate",
        "document_id": stored["document"]["id"],
        "document": None,
    }
    assert duplicate.json()["item"]["document"]["folder_id"] == one["folder_id"]  # "Already in Kaagaz, in A"
    summary = signed.client.get(f"/api/folder-uploads/{body['id']}").json()
    assert summary["stored"] + summary["duplicate"] + summary["failed"] == summary["file_count"] == 2


def test_a_send_after_its_folder_was_deleted_is_folder_gone_never_the_top_level(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, (one,) = planned(signed, {"A/B/C/bill.pdf": PDF_BYTES})
    assert signed.client.delete(f"/api/folders/{one['folder_id']}", headers=CSRF).status_code == 204
    stored = api.storage.count()
    reply = send(signed, one["id"], PDF_BYTES)
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "folder_gone")
    assert api.storage.count() == stored
    again = send(signed, one["id"], PDF_BYTES)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "item_refused")
    assert signed.client.get("/api/documents").json()["total"] == 0


def test_a_send_after_its_folder_was_renamed_and_moved_lands_in_it(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, (one,) = planned(signed, {"A/B/C/bill.pdf": PDF_BYTES})
    changes = {"name": "C renamed", "parent_id": None}
    assert (
        signed.client.patch(f"/api/folders/{one['folder_id']}", json=changes, headers=CSRF).status_code == 200
    )
    reply = send(signed, one["id"], PDF_BYTES)
    assert reply.status_code == 201, reply.text
    assert _document(api, signed.customer_id, reply.json()["document"]["id"])[0] == one["folder_id"]


def test_a_file_kaagaz_cant_take_fails_its_item_and_is_never_retried(db: Database) -> None:
    signed = Api(db).new_signed()
    program = b"MZ\x90\x00" + b"\x00" * 60
    _, (one,) = planned(signed, {"A/run.pdf": program})
    reply = send(signed, one["id"], program)
    assert (reply.status_code, reply.json()["error"]["code"]) == (400, "unsupported_type")
    again = send(signed, one["id"], program)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "item_refused")


def test_a_file_of_another_size_is_refused_and_its_item_still_waits(db: Database) -> None:
    signed = Api(db).new_signed()
    body, (one,) = planned(signed, {"A/bill.pdf": PDF_BYTES})
    reply = send(signed, one["id"], PDF_BYTES + b"changed since")
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "size_changed")
    assert _item(signed, body["id"], 0)["state"] == "waiting"
    assert send(signed, one["id"], PDF_BYTES).status_code == 201


def test_an_item_is_the_senders_own_and_names_its_folder_and_batch_itself(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, (one,) = planned(signed, {"A/bill.pdf": PDF_BYTES})
    for field in ("folder_id", "department_id", "batch_id"):
        reply = send(signed, one["id"], PDF_BYTES, **{field: str(uuid.uuid4())})
        assert reply.status_code == 400, field
    colleague = api.sign_in(signed.customer_id)
    assert send(colleague, one["id"], PDF_BYTES).status_code == 404
    assert send(signed, str(uuid.uuid4()), PDF_BYTES).status_code == 404
    assert send(signed, "not-an-id", PDF_BYTES).status_code == 400
    assert api.storage.count() == 0


def test_sends_past_the_caps_are_429_with_a_short_retry_after(db: Database) -> None:
    api = Api(
        db, settings=make_settings(folder_upload_files_at_once_per_org=1, folder_upload_files_at_once=2)
    )
    signed, other = api.new_signed(), api.new_signed()
    _, (one,) = planned(signed, {"A/bill.pdf": PDF_BYTES})
    _, (theirs,) = planned(other, {"A/bill.pdf": PDF_BYTES})
    sends = api.app.state.kaagaz.sends
    with ExitStack() as held:
        held.enter_context(sends.held(signed.customer_id, per_org=1, total=2))  # this organisation's one
        busy = send(signed, one["id"], PDF_BYTES)
        assert (busy.status_code, busy.headers["Retry-After"]) == (429, "2")
        assert busy.json()["error"]["code"] == "rate_limited"
        held.enter_context(sends.held(str(uuid.uuid4()), per_org=1, total=2))  # the server's two
        assert send(other, theirs["id"], PDF_BYTES).status_code == 429
    assert send(signed, one["id"], PDF_BYTES).status_code == 201
    assert send(other, theirs["id"], PDF_BYTES).status_code == 201


def test_the_send_slot_is_taken_only_once_the_file_is_read_and_checked(db: Database) -> None:
    api = Api(db, settings=make_settings(folder_upload_files_at_once_per_org=1))
    signed = api.new_signed()
    _, (one,) = planned(signed, {"A/bill.pdf": PDF_BYTES})
    with api.app.state.kaagaz.sends.held(signed.customer_id, per_org=1, total=8):
        reply = send(signed, one["id"], PDF_BYTES + b"changed since")
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "size_changed")


def test_a_backend_outage_inside_a_send_frees_its_slot_and_leaves_the_item_waiting(db: Database) -> None:
    api = Api(
        db, settings=make_settings(folder_upload_files_at_once_per_org=1, folder_upload_files_at_once=1)
    )
    signed = api.new_signed()
    body, (one,) = planned(signed, {"A/bill.pdf": PDF_BYTES})
    api.queue.fail_enqueue = True
    assert send(signed, one["id"], PDF_BYTES).status_code == 503
    assert _item(signed, body["id"], 0)["state"] == "waiting"
    api.queue.fail_enqueue = False
    assert send(signed, one["id"], PDF_BYTES).status_code == 201  # not 429: the slot was given back


class ResumableStorage(ApiFakeStorage):
    """The resumable calls in memory: parts by key until completed, then one file."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: dict[str, dict[int, bytes]] = {}
        self.done: dict[str, bytes] = {}

    def start_resumable(self, customer_id: str, user_id: str, session_id: str) -> tuple[str, str]:
        key = f"{customer_id}/{user_id}/{session_id}"
        self.parts[key] = {}
        return key, f"upload-{session_id}"

    def upload_resumable_part(self, key: str, upload_id: str, part_number: int, body: bytes) -> None:
        self.parts[key][part_number] = body

    def resumable_parts(self, key: str, upload_id: str) -> list[tuple[int, int, str]]:
        return [(n, len(body), f"etag-{n}") for n, body in sorted(self.parts[key].items())]

    def complete_resumable(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> None:
        self.done[key] = b"".join(self.parts[key][n] for n, _ in parts)

    def resumable_complete(self, key: str) -> bool:
        return key in self.done

    def open_resumable(self, key: str) -> Any:
        return io.BytesIO(self.done[key])

    def delete_resumable(self, key: str) -> None:
        self.done.pop(key, None)
        self.parts.pop(key, None)


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(hashlib.sha256(data).hexdigest().encode()).hexdigest()


def test_a_resumable_send_of_an_item_finishes_it_in_its_folder(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    signed = api.new_signed()
    body, (one,) = planned(signed, {"A/B/big.pdf": PDF_BYTES})
    start = {"file_name": "big.pdf", "fingerprint": _fingerprint(PDF_BYTES), "user_id": signed.user_id}
    sessions = "/api/documents/upload-sessions"
    wrong = signed.client.post(
        sessions, json={**start, "file_size": 5, "folder_upload_item_id": one["id"]}, headers=CSRF
    )
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (409, "size_changed")
    both = {
        **start,
        "file_size": len(PDF_BYTES),
        "folder_upload_item_id": one["id"],
        "folder_id": one["folder_id"],
    }
    assert signed.client.post(sessions, json=both, headers=CSRF).status_code == 400
    started = signed.client.post(
        sessions,
        json={
            **start,
            "file_size": len(PDF_BYTES),
            "folder_upload_item_id": one["id"],
            "folder_upload_id": body["id"],
        },
        headers=CSRF,
    )
    assert started.status_code == 200, started.text
    session = started.json()["id"]
    assert (
        signed.client.put(f"{sessions}/{session}/parts/1", content=PDF_BYTES, headers=CSRF).status_code == 204
    )
    done = signed.client.post(f"{sessions}/{session}/complete", headers=CSRF)
    assert done.status_code == 201, done.text
    assert done.json()["item"]["state"] == "stored"
    folder, batch, autofile, name = _document(api, signed.customer_id, done.json()["document"]["id"])
    assert (folder, batch, autofile, name) == (
        one["folder_id"],
        _batch_of(api, signed, one["id"]),
        None,
        "big.pdf",
    )


def test_a_resumable_start_and_complete_for_an_item_already_stored_reply_200_with_it(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    signed = api.new_signed()
    _, (one,) = planned(signed, {"A/big.pdf": PDF_BYTES})
    sessions = "/api/documents/upload-sessions"
    start = {
        "file_name": "big.pdf",
        "file_size": len(PDF_BYTES),
        "fingerprint": _fingerprint(PDF_BYTES),
        "user_id": signed.user_id,
        "folder_upload_item_id": one["id"],
    }
    session = signed.client.post(sessions, json=start, headers=CSRF).json()["id"]
    assert (
        signed.client.put(f"{sessions}/{session}/parts/1", content=PDF_BYTES, headers=CSRF).status_code == 204
    )
    stored = send(signed, one["id"], PDF_BYTES).json()  # another tab sent it meanwhile
    for reply in (
        signed.client.post(sessions, json=start, headers=CSRF),
        signed.client.post(f"{sessions}/{session}/complete", headers=CSRF),
    ):
        assert reply.status_code == 200, reply.text
        assert (reply.json()["item"]["state"], reply.json()["document"]["id"]) == (
            "stored",
            stored["document"]["id"],
        )
    assert len(api.queue.jobs) == 1


def _batch_of(api: Api, signed: Signed, item_id: str) -> str:
    with api.db.customer(signed.customer_id) as conn:
        found = conn.execute(
            text("SELECT batch_id::text FROM folder_upload_items WHERE id = :i"), {"i": item_id}
        )
        return str(found.scalar_one())


def test_upload_limits_say_the_folder_limits_and_whether_kaagaz_can_sort(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    limits = signed.client.get("/api/upload-limits").json()
    assert limits["folder_upload"] == {
        "max_files": 50_000,
        "max_bytes": 5 * 1024**3,
        "max_folders": 10_000,
        "max_depth": 100,
        "calls_per_minute": api.settings.folder_upload_calls_per_minute,
        "files_at_once": 4,  # the default; 8 of every organisation's
    }
    assert api.settings.folder_upload_files_at_once == 8
    assert limits["sort_available"] is False
    _owner(db, _GRANT, {"c": signed.customer_id})
    # Changed on purpose (PRD 9 F-02, D2): Kaagaz makes no department, so with none there is
    # nothing to sort into and "Let Kaagaz sort files" is not offered; once one exists it is.
    assert signed.client.get("/api/upload-limits").json()["sort_available"] is False
    other = api.new_signed()  # another organisation's department never counts
    _owner(db, _GRANT, {"c": other.customer_id})
    theirs = api.add_folder(other.customer_id, other.user_id, "Accounts")
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id = :f", {"f": theirs})
    assert signed.client.get("/api/upload-limits").json()["sort_available"] is False
    mine = api.add_folder(signed.customer_id, signed.user_id, "Accounts")
    _owner(db, "UPDATE folders SET kind = 'department' WHERE id = :f", {"f": mine})
    assert signed.client.get("/api/upload-limits").json()["sort_available"] is True
    # "Let Kaagaz sort them": loose files go in with no folder, marked for auto-filing, which
    # makes the department and the folders it needs once each is read (db/autofile.py).
    loose = signed.client.post(
        "/api/documents", files={"file": ("a.pdf", PDF_BYTES, "application/pdf")}, headers=CSRF
    )
    assert _document(api, signed.customer_id, loose.json()["document"]["id"])[:3] == (None, None, "pending")


def test_a_resumable_start_after_its_folder_was_deleted_fails_the_item(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    signed = api.new_signed()
    body, (one,) = planned(signed, {"A/B/C/big.pdf": PDF_BYTES})
    assert signed.client.delete(f"/api/folders/{one['folder_id']}", headers=CSRF).status_code == 204
    start = {"file_name": "big.pdf", "file_size": len(PDF_BYTES), "fingerprint": _fingerprint(PDF_BYTES)}
    reply = signed.client.post(
        "/api/documents/upload-sessions",
        json={**start, "user_id": signed.user_id, "folder_upload_item_id": one["id"]},
        headers=CSRF,
    )
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "folder_gone")
    assert (_item(signed, body["id"], 0)["state"], _item(signed, body["id"], 0)["failure_code"]) == (
        "failed",
        "folder_gone",
    )


def test_a_three_level_folder_end_to_end_puts_each_file_in_its_folder(db: Database) -> None:
    """Plan, register, send each file with folder_upload_item_id, read the summary: A, A/B, A/B/C
    and an empty A/Empty; a copy of a file already sent is a duplicate where the first one is,
    and a file Kaagaz can't take fails with its reason. Nothing lands anywhere else."""
    api = Api(db)
    signed = api.new_signed()
    program = b"MZ\x90\x00" + b"\x00" * 60
    files = {
        "A/top.pdf": PDF_BYTES + b"top",
        "A/B/mid.pdf": PDF_BYTES + b"mid",
        "A/B/C/deep.pdf": PDF_BYTES + b"deep",
        "A/B/C/copy of top.pdf": PDF_BYTES + b"top",
        "A/B/run.pdf": program,
    }
    folders = [*TREE, {"name": "Empty", "parent": 0}]
    planned_reply = plan(signed, folders, file_count=len(files), total_bytes=sum(map(len, files.values())))
    assert planned_reply.status_code == 200, planned_reply.text
    body = planned_reply.json()
    assert (body["created"], body["merged"]) == (4, 0)
    where = dict(zip(["A", "A/B", "A/B/C", "A/Empty"], body["folder_ids"], strict=True))
    rows = [item(path, where[path.rsplit("/", 1)[0]], len(data)) for path, data in files.items()]
    registered = register(signed, body["id"], rows)
    assert registered.status_code == 200, registered.text
    items = dict(zip(files, registered.json()["items"], strict=True))

    replies = {path: send(signed, items[path]["id"], data, name="x.pdf") for path, data in files.items()}
    assert {path: reply.status_code for path, reply in replies.items()} == {
        "A/top.pdf": 201,
        "A/B/mid.pdf": 201,
        "A/B/C/deep.pdf": 201,
        "A/B/C/copy of top.pdf": 200,
        "A/B/run.pdf": 400,
    }
    for path in ("A/top.pdf", "A/B/mid.pdf", "A/B/C/deep.pdf"):
        reply = replies[path].json()
        assert (reply["duplicate"], reply["item"]["state"]) == (False, "stored")
        folder, _, autofile, name = _document(api, signed.customer_id, reply["document"]["id"])
        assert (folder, autofile, name) == (where[path.rsplit("/", 1)[0]], None, path.rsplit("/", 1)[1])
    copy = replies["A/B/C/copy of top.pdf"].json()
    assert (copy["duplicate"], copy["item"]["state"]) == (True, "duplicate")
    assert copy["item"]["document_id"] == replies["A/top.pdf"].json()["document"]["id"]
    assert copy["item"]["document"]["folder_id"] == where["A"]  # "Already in Kaagaz, in A"
    assert replies["A/B/run.pdf"].json()["error"]["code"] == "unsupported_type"

    listed = signed.client.get(f"/api/folder-uploads/{body['id']}/items").json()["items"]
    assert [(row["folder_id"], row["state"], row["failure_code"]) for row in listed] == [
        (where["A"], "stored", None),
        (where["A/B"], "stored", None),
        (where["A/B/C"], "stored", None),
        (where["A/B/C"], "duplicate", None),
        (where["A/B"], "failed", "unsupported_type"),
    ]
    summary = signed.client.get(f"/api/folder-uploads/{body['id']}").json()
    counts = ("root_name", "file_count", "registered", "waiting", "stored", "duplicate", "failed")
    assert tuple(summary[name] for name in counts) == ("A", 5, 5, 0, 3, 1, 1)
    assert summary["reading"] + summary["ready"] + summary["problem"] == 3
    (mine,) = signed.client.get("/api/folder-uploads").json()["items"]
    assert (mine["id"], mine["reading"], mine["ready"], mine["problem"]) == (body["id"], None, None, None)

    tree = {f["id"]: f for f in signed.client.get("/api/folders").json()["items"]}
    assert (tree[where["A/Empty"]]["name"], tree[where["A/Empty"]]["parent_id"]) == ("Empty", where["A"])
    assert signed.client.get("/api/documents").json()["total"] == 3
