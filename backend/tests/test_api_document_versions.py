"""New versions, the Trash view and the newest version in the list (PRD 1 F5 and F6, WP1.6).

``POST /documents/{id}/versions`` has the checks and replies of ``POST /documents``.
A version joins the family with the next number, not current, in the family's
folder and with its tags. The list shows the newest version of each family, out
of Trash unless ``trashed=true``; by id, any version opens, in Trash or not.
"""

from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import Connection, RowMapping, text

from kaagaz.db import listing, trash
from kaagaz.db.engine import Database
from kaagaz.ingestion.messages import over_every_limit_message, too_large_message
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
MB = 1024 * 1024
V2_BYTES = b"%PDF-1.7\n" + b"2" * 200
V3_BYTES = b"%PDF-1.7\n" + b"3" * 200
UNKNOWN = "7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"


def _version(signed: Signed, document_id: str, data: bytes, name: str = "bill v2.pdf") -> httpx.Response:
    reply: httpx.Response = signed.client.post(
        f"/api/documents/{document_id}/versions",
        files={"file": (name, data, "application/pdf")},
        headers=CSRF,
    )
    return reply


def _upload(signed: Signed, data: bytes) -> httpx.Response:
    reply: httpx.Response = signed.client.post(
        "/api/documents", files={"file": ("bill.pdf", data, "application/pdf")}, headers=CSRF
    )
    return reply


def _trash(db: Database, signed: Signed, document_id: str) -> None:
    with db.customer(signed.customer_id) as conn:
        assert trash.trash_document(conn, signed.customer_id, document_id, by=signed.user_id, now=NOW)


def _listed(signed: Signed, **params: Any) -> dict[str, dict[str, Any]]:
    reply = signed.client.get("/api/documents", params=params)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["total"] == len(body["items"])
    return {item["id"]: item for item in body["items"]}


def _later(api: Api, signed: Signed, family_id: str, **columns: Any) -> str:
    """Version 2 of ``family_id``, not current, as the pipeline leaves it."""
    return api.add_document(
        signed.customer_id, family_id=family_id, version_no=2, is_current=False, **columns
    )


def _document_count(db: Database, customer_id: str) -> int:
    with db.customer(customer_id) as conn:
        return int(conn.execute(text("SELECT count(*) FROM documents")).scalar_one())


# --- the version route ------------------------------------------------------------------


def test_a_version_joins_the_family_with_the_next_number_its_folder_and_tags(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    folder = api.add_folder(signed.customer_id, signed.user_id, "Suppliers")
    paid = api.add_tag(signed.customer_id, signed.user_id, "Paid")
    first = api.add_document(signed.customer_id, folder_id=folder, uploaded_by=signed.user_id)
    api.tag_document(signed.customer_id, first, paid)

    reply = _version(signed, first, V2_BYTES, name="C:\\scans\\bill v2.pdf")

    assert reply.status_code == 201
    body = reply.json()
    assert body.keys() == {"document", "duplicate", "in_trash"}
    assert not (body["duplicate"] or body["in_trash"])
    second = body["document"]
    assert (second["family_id"], second["version_no"], second["deleted_at"]) == (first, 2, None)
    assert (second["folder_id"], second["tags"]) == (folder, [{"id": paid, "name": "Paid"}])
    assert (second["display_name"], second["status"]) == ("bill v2.pdf", "received")
    assert api.storage.has(signed.customer_id, second["id"])
    assert [job.document_id for job in api.queue.jobs] == [second["id"]]
    audited = api.audit_rows(signed.customer_id)[-1]
    assert audited == ("document.version", "document", second["id"], {"family_id": first})

    third = _version(signed, second["id"], V3_BYTES).json()["document"]  # from any version of the family
    assert (third["family_id"], third["version_no"]) == (first, 3)
    with db.customer(signed.customer_id) as conn:
        rows = conn.execute(text("SELECT version_no, is_current FROM documents ORDER BY version_no")).all()
    assert [tuple(row) for row in rows] == [(1, True), (2, False), (3, False)]


def test_the_same_bytes_as_any_document_are_the_usual_duplicate(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = _upload(signed, PDF_BYTES).json()["document"]["id"]
    other = api.add_document(signed.customer_id)

    reply = _version(signed, other, PDF_BYTES)

    assert reply.status_code == 200
    assert (reply.json()["duplicate"], reply.json()["in_trash"]) == (True, False)
    assert reply.json()["document"]["id"] == first
    assert (api.storage.count(), len(api.queue.jobs)) == (2, 1)
    assert "document.version" not in api.audit_actions(signed.customer_id)


def test_a_duplicate_of_a_document_in_trash_says_so_on_both_routes(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    binned = _upload(signed, PDF_BYTES).json()
    assert binned["in_trash"] is False  # a new file
    _trash(db, signed, binned["document"]["id"])
    live = api.add_document(signed.customer_id)

    for reply in (_upload(signed, PDF_BYTES), _version(signed, live, PDF_BYTES)):
        assert reply.status_code == 200
        body = reply.json()
        assert (body["duplicate"], body["in_trash"]) == (True, True)
        assert body["document"]["id"] == binned["document"]["id"]
        assert body["document"]["deleted_at"] == NOW.isoformat()


def test_a_version_of_a_family_in_trash_or_of_no_document_is_404_and_keeps_nothing(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = api.add_document(signed.customer_id)
    second = _later(api, signed, first)
    _trash(db, signed, second)
    live = api.add_document(signed.customer_id)

    for target in (first, second, UNKNOWN):
        reply = _version(signed, target, V2_BYTES)
        assert reply.status_code == 404, target
        assert reply.json()["error"]["code"] == "not_found"
    assert (api.storage.count(), api.queue.jobs, _document_count(db, signed.customer_id)) == (3, [], 3)
    assert _version(signed, live, V2_BYTES).status_code == 201  # the same file on a live family


def test_a_family_moved_to_trash_just_after_the_check_is_404_and_the_stored_file_goes(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = api.add_document(signed.customer_id)
    checked = listing.get_document

    def check_then_trash(conn: Connection, customer_id: str, document_id: str) -> RowMapping | None:
        row = checked(conn, customer_id, document_id)
        trash.trash_document(conn, customer_id, document_id, by=signed.user_id, now=NOW)
        return row

    monkeypatch.setattr(listing, "get_document", check_then_trash)
    reply = _version(signed, first, V2_BYTES)
    assert reply.status_code == 404
    assert (api.storage.count(), api.queue.jobs, _document_count(db, signed.customer_id)) == (1, [], 1)
    with db.customer(signed.customer_id) as conn:  # the check ran, then the family went to Trash
        assert conn.execute(text("SELECT deleted_at FROM documents")).scalar_one() == NOW


def test_the_version_route_has_the_upload_size_checks(db: Database) -> None:
    api = Api(db)  # PDFs 5 MB (the largest limit), sheets 1 MB
    signed = api.new_signed()
    first = api.add_document(signed.customer_id)
    request = signed.client.build_request(
        "POST", f"/api/documents/{first}/versions", files={"file": ("a.pdf", V2_BYTES, "application/pdf")}
    )
    request.headers.update({**CSRF, "Content-Length": str(5 * MB + 64 * 1024 + 1)})
    declared = signed.client.send(request)  # refused on its declared size, before the body is read
    assert declared.status_code == 413
    assert declared.json()["error"] == {"code": "too_large", "message": over_every_limit_message(5 * MB)}

    sheet = b"invoice,amount\n" + b"0" * MB  # over the sheet limit, counted by the upload service
    counted = _version(signed, first, sheet, name="register.csv")
    message = too_large_message("sheet", len(sheet), MB)
    assert (counted.status_code, counted.json()["error"]) == (413, {"code": "too_large", "message": message})
    no_file = signed.client.post(f"/api/documents/{first}/versions", data={"other": "x"}, headers=CSRF)
    assert no_file.status_code == 400
    assert (api.storage.count(), api.queue.jobs) == (1, [])


# --- the list and the detail ------------------------------------------------------------


def test_the_list_shows_the_newest_version_of_each_family_and_trash_only_when_asked(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    folder = api.add_folder(signed.customer_id, signed.user_id, "Suppliers")
    first = api.add_document(signed.customer_id, name="sharma.pdf", folder_id=folder)
    newest = _later(api, signed, first, name="sharma v2.pdf", folder_id=folder, status="received")
    single = api.add_document(signed.customer_id, name="mehta.pdf")
    old_bin = api.add_document(signed.customer_id, name="sharma old.pdf", folder_id=folder)
    new_bin = _later(api, signed, old_bin, name="sharma old v2.pdf", folder_id=folder)
    _trash(db, signed, old_bin)

    live = _listed(signed)
    assert set(live) == {newest, single}
    assert [live[newest][key] for key in ("version_no", "family_id", "deleted_at")] == [2, first, None]
    assert set(_listed(signed, trashed="false")) == {newest, single}
    binned = _listed(signed, trashed="true")
    assert set(binned) == {new_bin}
    assert (binned[new_bin]["family_id"], binned[new_bin]["deleted_at"]) == (old_bin, NOW.isoformat())
    # The other filters still apply, in both views.
    assert set(_listed(signed, q="SHARMA")) == {newest}
    assert set(_listed(signed, trashed="true", folder_id=folder, q="old")) == {new_bin}
    assert set(_listed(signed, trashed="true", q="mehta")) == set()
    assert set(_listed(signed, status="ready")) == {single}  # the newest version is still reading


def test_detail_lists_every_version_and_an_older_or_trashed_one_still_opens(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = api.add_document(signed.customer_id)
    second = _later(api, signed, first, status="received")
    api.add_page(signed.customer_id, first)
    api.storage.page_images[(signed.customer_id, first, 1)] = b"RIFF-webp"

    for document in (first, second):
        versions = signed.client.get(f"/api/documents/{document}").json()["versions"]
        assert [set(v) for v in versions] == [{"id", "version_no", "status", "is_current", "created_at"}] * 2
        assert [(v["id"], v["version_no"], v["status"], v["is_current"]) for v in versions] == [
            (first, 1, "ready", True),
            (second, 2, "received", False),
        ]

    _trash(db, signed, first)
    detail = signed.client.get(f"/api/documents/{first}")
    assert detail.status_code == 200
    assert (detail.json()["deleted_at"], len(detail.json()["versions"])) == (NOW.isoformat(), 2)
    assert signed.client.get(f"/api/documents/{first}/file").status_code == 200
    assert signed.client.get(f"/api/documents/{first}/pages/1/image").status_code == 200
