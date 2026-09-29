"""Trash, restore and delete forever through the API (PRD 1 F5, plan section 4 WP1.6).

A family is the unit (plan 9.3): each test's document has two versions, and
every route acts on both. Moving to Trash keeps every row and file, so a
restore brings everything back; only "delete forever" removes them, and only
for a document already in Trash.
"""

from datetime import datetime

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

NOT_IN_TRASH = {
    "error": {"code": "not_in_trash", "message": "Only a document in Trash can be deleted forever."}
}


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


def test_an_unknown_document_is_404_on_every_trash_route(db: Database) -> None:
    admin = Api(db).new_signed("admin")
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
    admin = api.new_signed("admin")
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
    admin = api.new_signed("admin")
    first, second = _family(api, admin)

    reply = admin.client.delete(f"/api/documents/{second}/permanent", headers=CSRF)

    assert (reply.status_code, reply.json()) == (409, NOT_IN_TRASH)
    assert _counts(db, admin.customer_id) == (2, 2, 2)
    assert api.storage.has(admin.customer_id, first)
    assert "document.delete" not in api.audit_actions(admin.customer_id)


def test_only_an_admin_deletes_forever(db: Database) -> None:
    api = Api(db)
    uploader = api.new_signed()
    first, _second = _family(api, uploader)
    assert _trash(uploader, first) == 204

    refused = uploader.client.delete(f"/api/documents/{first}/permanent", headers=CSRF)

    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert _counts(db, uploader.customer_id) == (2, 2, 2)


def test_delete_forever_keeps_every_row_when_storage_is_down(db: Database) -> None:
    api = Api(db)
    admin = api.new_signed("admin")
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
