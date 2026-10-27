"""New versions, the Trash view and the current version in the list (PRD 1 F5 and F6, WP1.6).

``POST /documents/{id}/versions`` has the checks and replies of ``POST /documents``.
A version joins the family with the next number, not current, in the family's
folder and with its tags; a family in Trash gets none (409 ``in_trash``). The
list shows the current version of each family, out of Trash unless
``trashed=true``, with a newer version still being read or failed beside it
(``newer_version``); by id, any version opens, in Trash or not.
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
IN_TRASH = {"error": {"code": "in_trash", "message": "This document is in the Trash. Restore it first."}}


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


def test_a_version_of_a_family_in_trash_is_409_of_no_document_404_and_keeps_nothing(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = api.add_document(signed.customer_id)
    second = _later(api, signed, first)
    _trash(db, signed, second)
    live = api.add_document(signed.customer_id)

    for target in (first, second):
        reply = _version(signed, target, V2_BYTES)
        assert (reply.status_code, reply.json()) == (409, IN_TRASH), target
    unknown = _version(signed, UNKNOWN, V2_BYTES)
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "not_found")
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


def test_the_list_shows_the_current_version_of_each_family_and_trash_only_when_asked(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    folder = api.add_folder(signed.customer_id, signed.user_id, "Suppliers")
    first = api.add_document(signed.customer_id, name="sharma.pdf", folder_id=folder)
    newest = _later(api, signed, first, name="sharma v2.pdf", folder_id=folder, status="received")
    single = api.add_document(signed.customer_id, name="mehta.pdf")
    old_bin = api.add_document(signed.customer_id, name="sharma old.pdf", folder_id=folder)
    _later(api, signed, old_bin, name="sharma old v2.pdf", folder_id=folder)
    _trash(db, signed, old_bin)

    # The version search and chat use, never hidden by a newer one still reading.
    live = _listed(signed)
    assert set(live) == {first, single}
    assert [live[first][key] for key in ("version_no", "family_id", "deleted_at")] == [1, first, None]
    assert live[first]["newer_version"]["id"] == newest
    assert set(_listed(signed, trashed="false")) == {first, single}
    binned = _listed(signed, trashed="true")
    assert set(binned) == {old_bin}
    assert (binned[old_bin]["family_id"], binned[old_bin]["deleted_at"]) == (old_bin, NOW.isoformat())
    assert binned[old_bin]["newer_version"] is None  # every version is in Trash with it
    # The other filters still apply, in both views, to the current version.
    assert set(_listed(signed, q="SHARMA")) == {first}
    assert set(_listed(signed, q="v2")) == set()
    assert set(_listed(signed, trashed="true", folder_id=folder, q="old")) == {old_bin}
    assert set(_listed(signed, trashed="true", q="mehta")) == set()
    assert set(_listed(signed, status="ready")) == {first, single}
    assert set(_listed(signed, status="received")) == set()


def test_a_newer_version_still_reading_or_failed_is_shown_beside_the_current_one(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    reading = api.add_document(signed.customer_id, name="reading.pdf", attempts=1, max_attempts=5)
    reading_v2 = _later(api, signed, reading, status="received", attempts=2, max_attempts=5)
    failed = api.add_document(signed.customer_id, name="failed.pdf")
    _later(api, signed, failed, status="failed", failure_code="corrupt_file")
    failed_v3 = api.add_document(
        signed.customer_id,
        family_id=failed,
        version_no=3,
        is_current=False,
        status="failed",
        failure_code="retries_exhausted",
        attempts=5,
        max_attempts=5,
    )
    single = api.add_document(signed.customer_id, name="single.pdf")

    listed = _listed(signed)
    assert set(listed) == {reading, failed, single}
    assert listed[reading]["newer_version"] == {
        "id": reading_v2,
        "version_no": 2,
        "status": "received",
        "failure_code": None,
        "status_message": "Waiting to try again. Try 2 of 5.",  # changed on purpose: no progress after a try
    }
    assert listed[failed]["newer_version"] == {  # the newest of the newer ones
        "id": failed_v3,
        "version_no": 3,
        "status": "failed",
        "failure_code": "retries_exhausted",
        "status_message": ("The scan stopped after 5 tries. Read it again, or ask your admin to look at it."),
    }
    assert listed[single]["newer_version"] is None
    # The current version's own tries: a ready one names none.
    assert (listed[reading]["attempts"], listed[reading]["max_attempts"]) == (1, 5)
    assert listed[reading]["status_message"] == "Ready. You can ask about it."
    assert (listed[single]["attempts"], listed[single]["max_attempts"]) == (0, None)
    detail = signed.client.get(f"/api/documents/{reading}").json()
    assert detail["newer_version"] == listed[reading]["newer_version"]
    assert signed.client.get(f"/api/documents/{reading_v2}").json()["newer_version"] is None


def test_detail_lists_every_version_and_an_older_or_trashed_one_still_opens(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    first = api.add_document(signed.customer_id)
    second = _later(api, signed, first, status="received")
    api.add_page(signed.customer_id, first)
    api.storage.page_images[(signed.customer_id, first, 1)] = b"RIFF-webp"

    for document in (first, second):
        versions = signed.client.get(f"/api/documents/{document}").json()["versions"]
        # Changed on purpose (PRD 1, versions): V12's name, note, uploader and how it joined.
        keys = {"id", "version_no", "status", "is_current", "created_at", "display_name", "version_note"}
        assert [set(v) for v in versions] == [keys | {"uploaded_by", "joined"}] * 2
        assert {v["joined"] for v in versions} == {"uploaded"}
        assert [(v["id"], v["version_no"], v["status"], v["is_current"]) for v in versions] == [
            (first, 1, "ready", True),
            (second, 2, "received", False),
        ]

    _trash(db, signed, first)
    detail = signed.client.get(f"/api/documents/{first}")
    assert detail.status_code == 200
    assert (detail.json()["deleted_at"], len(detail.json()["versions"])) == (NOW.isoformat(), 2)
    # Changed on purpose (#239, 27 Oct 2026): a family in Trash opens, but its file, page images
    # and readings are 409 in_trash (File management T1, 65b9067).
    for path in (f"/api/documents/{first}/file", f"/api/documents/{first}/pages/1/image"):
        refused = signed.client.get(path)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "in_trash"), path
