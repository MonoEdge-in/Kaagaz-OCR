"""Trash, restore and delete forever through the API (PRD 1 F5, plan section 4 WP1.6).

A family is the unit (plan 9.3): each test's document has two versions, and
every route acts on both. Moving to Trash keeps every row and file, so a
restore brings everything back; only "delete forever" removes them, only for
a document already in Trash, and only with an admin session. A family in
Trash can't be renamed, moved, tagged or given a new version.
"""

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

NOT_IN_TRASH = {
    "error": {"code": "not_in_trash", "message": "Only a document in Trash can be deleted forever."}
}
IN_TRASH = {"error": {"code": "in_trash", "message": "This document is in the Trash. Restore it first."}}
V2_BYTES = b"%PDF-1.7\n" + b"2" * 200


def _family(api: Api, signed: Signed) -> tuple[str, str]:
    """Version 1 and version 2 (current) of one family, uploaded by ``signed``, each
    with its file, a page, a value and a page picture. Returns (version 1, version 2)."""
    first = api.add_document(signed.customer_id, uploaded_by=signed.user_id, is_current=False)
    second = api.add_document(
        signed.customer_id, uploaded_by=signed.user_id, name="invoice v2.pdf", family_id=first, version_no=2
    )
    for document in (first, second):
        api.add_page(signed.customer_id, document)
        api.add_value(signed.customer_id, document)
        api.storage.page_images[(signed.customer_id, document, 1)] = b"webp"
    return first, second


def _trash_state(db: Database, customer_id: str, *ids: str) -> list[tuple[datetime | None, str | None]]:
    """(deleted_at, deleted_by) of each id, in the order given."""
    with db.customer(customer_id) as conn:
        rows = {
            str(row[0]): (row[1], None if row[2] is None else str(row[2]))
            for row in conn.execute(
                text("SELECT id, deleted_at, deleted_by FROM documents WHERE customer_id = :c"),
                {"c": customer_id},
            )
        }
    return [rows[document] for document in ids]


def _counts(db: Database, customer_id: str) -> tuple[int, int, int]:
    """How many documents, pages and values this customer has."""
    with db.customer(customer_id) as conn:
        return (
            int(conn.execute(text("SELECT count(*) FROM documents")).scalar_one()),
            int(conn.execute(text("SELECT count(*) FROM pages")).scalar_one()),
            int(conn.execute(text("SELECT count(*) FROM extracted_values")).scalar_one()),
        )


def _trash(signed: Signed, document: str) -> int:
    return signed.client.delete(f"/api/documents/{document}", headers=CSRF).status_code


def _restore(signed: Signed, document: str) -> int:
    return signed.client.post(f"/api/documents/{document}/restore", headers=CSRF).status_code


def _can_delete(signed: Signed, **params: Any) -> dict[str, bool]:
    """Each listed document's ``can_delete``, as ``signed`` sees it."""
    items = signed.client.get("/api/documents", params=params).json()["items"]
    return {item["id"]: item["can_delete"] for item in items}


# --- move to Trash ----------------------------------------------------------------------


def test_delete_moves_the_whole_family_to_trash_and_keeps_every_row_and_file(db: Database) -> None:
    api = Api(db)
    uploader = api.new_signed()
    first, second = _family(api, uploader)

    assert _trash(uploader, first) == 204

    moved = (api.clock(), uploader.user_id)
    assert _trash_state(db, uploader.customer_id, first, second) == [moved, moved]
    assert _counts(db, uploader.customer_id) == (2, 2, 2)
    assert api.storage.has(uploader.customer_id, first)
    assert api.storage.has(uploader.customer_id, second)
    assert len(api.storage.page_images) == 2
    # By id it still opens (plan 9.7): the viewer can show what is in Trash.
    assert uploader.client.get(f"/api/documents/{second}").status_code == 200
    assert api.audit_rows(uploader.customer_id)[-1] == (
        "document.trash",
        "document",
        first,
        {"status": "ready"},
    )


def test_delete_of_a_family_already_in_trash_keeps_the_first_time(db: Database) -> None:
    api = Api(db)
    uploader = api.new_signed()
    first, second = _family(api, uploader)
    assert _trash(uploader, first) == 204
    moved = (api.clock(), uploader.user_id)

    api.clock.advance(3600)
    assert _trash(uploader, second) == 204

    assert _trash_state(db, uploader.customer_id, first, second) == [moved, moved]


def test_only_the_uploader_or_an_admin_moves_to_trash_or_restores(db: Database) -> None:
    api = Api(db)
    uploader = api.new_signed()
    first, second = _family(api, uploader)
    colleague = api.sign_in(uploader.customer_id, "member")
    admin = api.sign_in(uploader.customer_id, "admin")

    refused = colleague.client.delete(f"/api/documents/{first}", headers=CSRF)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert _trash_state(db, uploader.customer_id, first) == [(None, None)]
    assert _trash(admin, first) == 204

    refused = colleague.client.post(f"/api/documents/{second}/restore", headers=CSRF)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert admin.client.post(f"/api/documents/{second}/restore", headers=CSRF).status_code == 200
    assert _trash_state(db, uploader.customer_id, first, second) == [(None, None), (None, None)]


def test_a_family_with_versions_from_two_users_goes_to_trash_only_by_an_admin(db: Database) -> None:
    api = Api(db)
    first_uploader = api.new_signed()
    second_uploader = api.sign_in(first_uploader.customer_id, "member")
    admin = api.sign_in(first_uploader.customer_id, "admin")
    first = api.add_document(first_uploader.customer_id, uploaded_by=first_uploader.user_id)
    reply = second_uploader.client.post(
        f"/api/documents/{first}/versions",
        files={"file": ("invoice v2.pdf", V2_BYTES, "application/pdf")},
        headers=CSRF,
    )
    assert reply.status_code == 201, reply.text
    second = reply.json()["document"]["id"]
    assert reply.json()["document"]["can_delete"] is False
    solo = api.add_document(first_uploader.customer_id, uploaded_by=first_uploader.user_id)

    # Trash acts on every version, so neither uploader may do it alone.
    assert _can_delete(first_uploader) == {first: False, solo: True}
    assert _can_delete(second_uploader) == {first: False, solo: False}
    assert _can_delete(admin) == {first: True, solo: True}
    for signed in (first_uploader, second_uploader):
        for document in (first, second):
            refused = signed.client.delete(f"/api/documents/{document}", headers=CSRF)
            assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert _trash_state(db, first_uploader.customer_id, first, second) == [(None, None), (None, None)]

    assert _trash(admin, second) == 204
    assert _can_delete(first_uploader, trashed="true") == {first: False}
    assert _can_delete(admin, trashed="true") == {first: True}
    assert [_restore(signed, first) for signed in (first_uploader, second_uploader)] == [403, 403]
    assert _restore(admin, first) == 200
    # A family of one version: its uploader still moves it to Trash.
    assert _trash(first_uploader, solo) == 204


def test_an_unknown_document_is_404_on_every_trash_route(db: Database) -> None:
    admin = Api(db).new_admin()
    unknown = "7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"
    replies = [
        admin.client.delete(f"/api/documents/{unknown}", headers=CSRF),
        admin.client.post(f"/api/documents/{unknown}/restore", headers=CSRF),
        admin.client.delete(f"/api/documents/{unknown}/permanent", headers=CSRF),
    ]
    assert [reply.status_code for reply in replies] == [404, 404, 404]


# --- restore -------------------------------------------------------------------------------


def test_restore_brings_the_whole_family_back_and_returns_its_summary(db: Database) -> None:
    api = Api(db)
    uploader = api.new_signed()
    first, second = _family(api, uploader)
    assert _trash(uploader, second) == 204

    reply = uploader.client.post(f"/api/documents/{first}/restore", headers=CSRF)

    assert reply.status_code == 200
    body = reply.json()
    assert (body["id"], body["display_name"], body["status"]) == (first, "invoice.pdf", "ready")
    assert _trash_state(db, uploader.customer_id, first, second) == [(None, None), (None, None)]
    assert api.audit_actions(uploader.customer_id)[-2:] == ["document.trash", "document.restore"]


def test_restore_of_a_document_not_in_trash_changes_nothing(db: Database) -> None:
    api = Api(db)
    uploader = api.new_signed()
    first, _second = _family(api, uploader)

    reply = uploader.client.post(f"/api/documents/{first}/restore", headers=CSRF)

    assert (reply.status_code, reply.json()["id"]) == (200, first)
    assert _trash_state(db, uploader.customer_id, first) == [(None, None)]


# --- delete forever ------------------------------------------------------------------------


def test_an_admin_deletes_a_family_in_trash_forever_and_everything_goes(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    first, second = _family(api, admin)
    kept = api.add_document(admin.customer_id)
    assert _trash(admin, second) == 204

    assert admin.client.delete(f"/api/documents/{first}/permanent", headers=CSRF).status_code == 204

    assert _counts(db, admin.customer_id) == (1, 0, 0)
    assert admin.client.get(f"/api/documents/{first}").status_code == 404
    assert admin.client.get(f"/api/documents/{second}").status_code == 404
    assert not api.storage.has(admin.customer_id, first)
    assert not api.storage.has(admin.customer_id, second)
    assert api.storage.has(admin.customer_id, kept)
    assert api.storage.page_images == {}
    assert api.audit_rows(admin.customer_id)[-1] == (
        "document.delete",
        "document",
        first,
        {"versions": 2},
    )


def test_a_document_not_in_trash_cannot_be_deleted_forever(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    first, second = _family(api, admin)

    reply = admin.client.delete(f"/api/documents/{second}/permanent", headers=CSRF)

    assert (reply.status_code, reply.json()) == (409, NOT_IN_TRASH)
    assert _counts(db, admin.customer_id) == (2, 2, 2)
    assert api.storage.has(admin.customer_id, first)
    assert "document.delete" not in api.audit_actions(admin.customer_id)


def test_only_an_admin_session_deletes_forever(db: Database) -> None:
    api = Api(db)
    uploader = api.new_signed()
    first, _second = _family(api, uploader)
    assert _trash(uploader, first) == 204
    admin_app_session = api.sign_in(uploader.customer_id, "admin")

    for signed in (uploader, admin_app_session):
        refused = signed.client.delete(f"/api/documents/{first}/permanent", headers=CSRF)
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "admin_session_required")
    assert _counts(db, uploader.customer_id) == (2, 2, 2)
    admin = api.admin_sign_in(uploader.customer_id)
    assert admin.client.delete(f"/api/documents/{first}/permanent", headers=CSRF).status_code == 204


def test_delete_forever_keeps_every_row_when_storage_is_down(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    first, second = _family(api, admin)
    assert _trash(admin, first) == 204
    api.storage.fail_delete = True

    reply = admin.client.delete(f"/api/documents/{first}/permanent", headers=CSRF)

    assert reply.status_code == 503
    assert _counts(db, admin.customer_id) == (2, 2, 2)
    assert "document.delete" not in api.audit_actions(admin.customer_id)
    api.storage.fail_delete = False  # storage is back: trying again works
    assert admin.client.delete(f"/api/documents/{second}/permanent", headers=CSRF).status_code == 204
    assert _counts(db, admin.customer_id) == (0, 0, 0)


# --- Trash means deleted -------------------------------------------------------------------


def test_a_family_in_trash_cannot_be_renamed_moved_tagged_or_given_a_version(db: Database) -> None:
    api = Api(db)
    uploader = api.new_signed()
    first, second = _family(api, uploader)
    folder = api.add_folder(uploader.customer_id, uploader.user_id, "Suppliers")
    paid = api.add_tag(uploader.customer_id, uploader.user_id, "Paid")
    assert _trash(uploader, first) == 204
    before = (api.audit_actions(uploader.customer_id), api.storage.count(), api.queue.jobs[:])

    client = uploader.client
    for reply in (
        client.patch(f"/api/documents/{second}", json={"display_name": "renamed.pdf"}, headers=CSRF),
        client.patch(f"/api/documents/{first}", json={"folder_id": folder}, headers=CSRF),
        client.put(f"/api/documents/{second}/tags", json={"tag_ids": [paid]}, headers=CSRF),
        client.post(
            f"/api/documents/{first}/versions",
            files={"file": ("invoice v3.pdf", V2_BYTES, "application/pdf")},
            headers=CSRF,
        ),
    ):
        assert (reply.status_code, reply.json()) == (409, IN_TRASH)

    with db.customer(uploader.customer_id) as conn:
        rows = conn.execute(text("SELECT display_name, folder_id FROM documents ORDER BY version_no")).all()
        links = int(conn.execute(text("SELECT count(*) FROM document_tags")).scalar_one())
    assert [tuple(row) for row in rows] == [("invoice.pdf", None), ("invoice v2.pdf", None)]
    assert links == 0
    assert (api.audit_actions(uploader.customer_id), api.storage.count(), api.queue.jobs) == before
    # Brought back from Trash, the same rename works.
    assert _restore(uploader, first) == 200
    renamed = client.patch(f"/api/documents/{second}", json={"display_name": "renamed.pdf"}, headers=CSRF)
    assert renamed.status_code == 200
