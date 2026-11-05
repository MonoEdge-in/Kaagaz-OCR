""" "Add new files only" for a folder upload (#484) against PostgreSQL: a file registered with
the browser's SHA-256 of a document of this organisation of the same size is already in
(``duplicate`` with that document, never sent, nothing stored or moved); another
organisation's or another size waits. Already-in files don't count against the limits, an
only-new plan is checked against the ceilings, the audit holds counts only, and ``previous``
finds this user's last upload of a folder whose root is still there."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db import audit
from kaagaz.db.engine import Database
from kaagaz.db.folder_uploads import GIB
from tests.test_api_folder_uploads import _error, item, plan, register
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_COUNTS = "SELECT items_registered, items_counted, bytes_registered FROM folder_uploads WHERE id = :u"
_LIMITS = (
    "INSERT INTO org_upload_limits (customer_id, max_files, max_gb, max_folders) VALUES (:c, :f, :g, :d)"
)


def _document(api: Api, signed: Signed, **columns: Any) -> tuple[str, str]:
    """A document of this organisation (``Api.add_document``): its id and its sha256."""
    document = api.add_document(signed.customer_id, data=PDF_BYTES, **columns)
    return document, hashlib.sha256(document.encode()).hexdigest()


def _counts(api: Api, signed: Signed, upload: str) -> tuple[int, ...]:
    with api.db.customer(signed.customer_id) as conn:
        return tuple(conn.execute(text(_COUNTS), {"u": upload}).one())


def _documents(api: Api, signed: Signed) -> int:
    with api.db.customer(signed.customer_id) as conn:
        return int(conn.execute(text("SELECT count(*) FROM documents")).scalar_one())


def test_a_registered_hash_of_a_document_is_already_in_never_sent(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    elsewhere = api.add_folder(signed.customer_id, signed.user_id, "Old place")
    document, sha = _document(api, signed, folder_id=elsewhere)
    planned = plan(signed, only_new=True).json()
    upload, a = planned["id"], planned["folder_ids"][0]
    stored, documents = api.storage.count(), _documents(api, signed)
    reply = register(signed, upload, [item("A/bill.pdf", a, len(PDF_BYTES), sha256=sha)])
    assert reply.status_code == 200, reply.text
    (row,) = reply.json()["items"]
    assert (row["state"], row["document_id"], row["failure_code"]) == ("duplicate", document, None)
    assert row["document"] == {
        "status": "ready",
        "failure_code": None,
        "folder_id": elsewhere,
        "in_trash": False,
    }
    assert _counts(api, signed, upload) == (1, 0, 0)  # registered, never counted
    assert (api.storage.count(), _documents(api, signed)) == (stored, documents)  # nothing stored or moved
    sent = signed.client.post(
        f"/api/documents?folder_upload_item_id={row['id']}",
        files={"file": ("bill.pdf", PDF_BYTES, "application/pdf")},
        headers=CSRF,
    )
    assert sent.status_code == 200, sent.text  # _item_reply: nothing read or stored
    body = sent.json()
    assert (body["duplicate"], body["document"]["id"], body["document"]["folder_id"]) == (
        True,
        document,
        elsewhere,
    )
    assert (api.storage.count(), _documents(api, signed)) == (stored, documents)


def test_a_hash_of_another_customers_document_waits(db: Database) -> None:
    api = Api(db)
    signed, other = api.new_signed(), api.new_signed()
    _, sha = _document(api, other)
    planned = plan(signed, only_new=True).json()
    reply = register(
        signed, planned["id"], [item("A/bill.pdf", planned["folder_ids"][0], len(PDF_BYTES), sha256=sha)]
    )
    (row,) = reply.json()["items"]
    assert (row["state"], row["document_id"], row["document"]) == ("waiting", None, None)
    assert _counts(api, signed, planned["id"]) == (1, 1, len(PDF_BYTES))


def test_a_hash_with_another_size_waits(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, sha = _document(api, signed)
    planned = plan(signed, only_new=True).json()
    reply = register(
        signed, planned["id"], [item("A/bill.pdf", planned["folder_ids"][0], len(PDF_BYTES) + 1, sha256=sha)]
    )
    assert [row["state"] for row in reply.json()["items"]] == ["waiting"]


def test_a_hash_of_a_document_in_trash_is_already_in_with_in_trash(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    document, sha = _document(api, signed, deleted_at=datetime.now(UTC))
    planned = plan(signed, only_new=True).json()
    reply = register(
        signed, planned["id"], [item("A/bill.pdf", planned["folder_ids"][0], len(PDF_BYTES), sha256=sha)]
    )
    (row,) = reply.json()["items"]
    assert (row["state"], row["document_id"], row["document"]["in_trash"]) == ("duplicate", document, True)


def test_a_hash_is_64_hex_and_a_failed_file_is_never_matched(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, sha = _document(api, signed)
    planned = plan(signed, only_new=True).json()
    a = planned["folder_ids"][0]
    for wrong in (sha.upper(), sha[:63], "x" * 64):
        assert _error(register(signed, planned["id"], [item("A/x.pdf", a, sha256=wrong)]))[:2] == (
            400,
            "bad_request",
        )
    refused = item("A/run.exe", a, len(PDF_BYTES), sha256=sha, refused="unsupported_type")
    (row,) = register(signed, planned["id"], [refused]).json()["items"]
    assert (row["state"], row["failure_code"], row["document_id"]) == ("failed", "unsupported_type", None)


def test_already_in_files_dont_count_against_the_limits(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _owner(db, _LIMITS, {"c": signed.customer_id, "f": 2, "g": None, "d": None})
    hashes = [_document(api, signed)[1] for _ in range(5)]
    planned = plan(signed, only_new=True, file_count=8, total_bytes=10**6).json()
    upload, a = planned["id"], planned["folder_ids"][0]
    page = [item(f"A/{n}.pdf", a, len(PDF_BYTES), sha256=sha) for n, sha in enumerate(hashes)]
    page += [item("A/new1.pdf", a, 100), item("A/new2.pdf", a, 200)]
    reply = register(signed, upload, page)
    assert reply.status_code == 200, reply.text
    assert [row["state"] for row in reply.json()["items"]] == ["duplicate"] * 5 + ["waiting"] * 2
    assert _counts(api, signed, upload) == (7, 2, 300)
    assert _error(register(signed, upload, [item("A/new3.pdf", a, 10)]))[:2] == (409, "too_many_files")


def test_registered_rows_stay_within_the_declared_count(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    hashes = [_document(api, signed)[1] for _ in range(3)]
    planned = plan(signed, only_new=True, file_count=2).json()
    page = [
        item(f"A/{n}.pdf", planned["folder_ids"][0], len(PDF_BYTES), sha256=sha)
        for n, sha in enumerate(hashes)
    ]
    assert _error(register(signed, planned["id"], page))[:2] == (409, "too_many_files")


def test_only_new_plan_checks_the_ceilings_not_the_org_limits(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _owner(db, _LIMITS, {"c": signed.customer_id, "f": 2, "g": 1, "d": 2})
    assert _error(plan(signed, file_count=3))[:2] == (409, "too_many_files")
    assert _error(plan(signed, total_bytes=GIB + 1, file_count=1))[:2] == (409, "too_large_upload")
    made = plan(signed, TREE_OF_TWO, only_new=True, file_count=200_000, total_bytes=20 * GIB)
    assert made.status_code == 200, made.text
    assert _error(plan(signed, only_new=True))[:2] == (409, "too_many_folders")  # 3 folders: still the org's
    for past in ({"file_count": 200_001}, {"total_bytes": 20 * GIB + 1}):
        assert _error(plan(signed, TREE_OF_TWO, only_new=True, **past))[:2] == (400, "bad_request")


TREE_OF_TWO: list[dict[str, Any]] = [{"name": "A", "parent": None}, {"name": "B", "parent": 0}]


def test_register_audits_counts_only(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    _, sha = _document(api, signed, name="secret-name.pdf")
    planned = plan(signed, only_new=True).json()
    upload, a = planned["id"], planned["folder_ids"][0]
    page = [
        item("A/private-bill.pdf", a, len(PDF_BYTES), sha256=sha),
        item("A/other.pdf", a, 100, sha256="ab" * 32),
        item("A/plain.pdf", a, 100),
    ]
    assert register(signed, upload, page).status_code == 200
    assert register(signed, upload, page).status_code == 200  # a retry of the page: nothing new, no row
    rows = api.audit_rows(signed.customer_id)
    (upload_row,) = [row for row in rows if row[0] == "folder.upload"]
    assert upload_row[3]["only_new"] is True
    (checked,) = [row for row in rows if row[0] == "folder.upload_check"]
    assert checked[1:] == (
        "folder",
        planned["root_folder_id"],
        {"folder_upload_id": upload, "checked": 2, "already_in": 1},
    )
    said = json.dumps(checked)
    for secret in (sha, "ab" * 32, "private-bill", "secret-name", "other.pdf"):
        assert secret not in said
    with api.db.customer(signed.customer_id) as conn:  # admin-only: never in a member's timeline
        shown = audit.entries(conn, signed.customer_id, audit.Filters(), limit=50)
    assert "folder.upload_check" not in {row["action"] for row in shown}
    assert "folder.upload" in {row["action"] for row in shown}


def test_previous_upload_is_this_users_latest_with_its_root_still_there(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    colleague = api.sign_in(signed.customer_id)

    def previous(who: Signed, name: str) -> Any:
        reply = who.client.get("/api/folder-uploads/previous", params={"source_name": name})
        assert reply.status_code == 200, reply.text
        return reply.json()["previous"]

    assert previous(signed, "A") is None
    first = plan(signed).json()
    second = plan(signed, merge=False).json()  # "A (2)"
    plan(colleague, merge=False)  # a colleague's later one is never this user's
    found = previous(signed, "A")
    assert {key: found[key] for key in ("id", "target", "parent_folder_id", "root_folder_id")} == {
        "id": second["id"],
        "target": "folder",
        "parent_folder_id": None,
        "root_folder_id": second["root_folder_id"],
    }
    assert isinstance(found["created_at"], str)
    for folder in reversed(second["folder_ids"]):
        assert signed.client.delete(f"/api/folders/{folder}", headers=CSRF).status_code == 204
    assert previous(signed, "A")["id"] == first["id"]  # the latest whose root is still there
    assert previous(signed, "B") is None
    assert previous(api.new_signed(), "A") is None  # another organisation's
