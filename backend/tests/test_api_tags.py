"""The tag routes and a document's tag set (PRD 1 F2) against a real database.

The set rules themselves are tested on the helpers (test_tags_db.py); these
tests check the reply shapes, the error codes, the name rule, the admin session
for delete, no tags on a document in Trash, the 50-tag cap at the boundary and
the audit rows.
"""

from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.db_support import insert_document
from tests.test_api_support import CSRF, NOW, Api, Signed

pytestmark = pytest.mark.db

UNKNOWN = "7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"


def _create(signed: Signed, name: str) -> httpx.Response:
    reply: httpx.Response = signed.client.post("/api/tags", json={"name": name}, headers=CSRF)
    return reply


def _set(signed: Signed, document_id: str, tag_ids: list[str]) -> httpx.Response:
    reply: httpx.Response = signed.client.put(
        f"/api/documents/{document_id}/tags", json={"tag_ids": tag_ids}, headers=CSRF
    )
    return reply


def _tag_ids_in_db(db: Database, customer_id: str, document_id: str) -> list[str]:
    with db.customer(customer_id) as conn:
        rows = conn.execute(
            text("SELECT tag_id FROM document_tags WHERE document_id = :d ORDER BY tag_id"),
            {"d": document_id},
        ).all()
    return [str(row[0]) for row in rows]


def _document(db: Database, signed: Signed, **columns: Any) -> str:
    with db.customer(signed.customer_id) as conn:
        return insert_document(conn, signed.customer_id, uploaded_by=signed.user_id, **columns)


# --- create, list, delete -------------------------------------------------------------------


def test_a_member_creates_tags_and_lists_them_by_name(db: Database) -> None:
    signed = Api(db).new_signed("member")
    paid = _create(signed, "Paid")
    assert paid.status_code == 201
    assert set(paid.json()) == {"id", "name"}
    gst = _create(signed, "  gst ").json()
    assert gst["name"] == "gst"
    listed = signed.client.get("/api/tags")
    assert listed.status_code == 200
    assert listed.json() == {"items": [gst, paid.json()]}


def test_a_tag_name_must_be_1_to_40_characters_and_unique_in_any_case(db: Database) -> None:
    signed = Api(db).new_signed()
    assert _create(signed, "a" * 40).status_code == 201
    for name in ("", "  ", "a" * 41):
        reply = _create(signed, name)
        assert reply.status_code == 400, name
        assert reply.json()["error"] == {
            "code": "bad_request",
            "message": "Enter a tag name of 1 to 40 characters.",
        }
    _create(signed, "GST")
    clash = _create(signed, "gst")
    assert clash.status_code == 409
    assert clash.json()["error"] == {"code": "conflict", "message": "That already exists."}


def test_an_admin_deletes_a_tag_and_it_comes_off_every_document(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    tag = api.add_tag(admin.customer_id, admin.user_id, "Paid")
    document = _document(db, admin)
    api.tag_document(admin.customer_id, document, tag)

    assert admin.client.delete(f"/api/tags/{tag}", headers=CSRF).status_code == 204
    assert admin.client.get("/api/tags").json() == {"items": []}
    assert _tag_ids_in_db(db, admin.customer_id, document) == []
    assert admin.client.delete(f"/api/tags/{tag}", headers=CSRF).status_code == 404


@pytest.mark.parametrize("role", ["member", "reviewer", "admin"])
def test_only_an_admin_session_may_delete_a_tag(db: Database, role: str) -> None:
    api = Api(db)
    signed = api.new_signed(role)  # an app session, an admin's included
    tag = api.add_tag(signed.customer_id, signed.user_id, "Paid")
    reply = signed.client.delete(f"/api/tags/{tag}", headers=CSRF)
    assert reply.status_code == 403
    assert reply.json()["error"]["code"] == "admin_session_required"
    assert [item["id"] for item in signed.client.get("/api/tags").json()["items"]] == [tag]


# --- a document's tag set -------------------------------------------------------------------


def test_put_replaces_the_set_and_returns_it_by_name(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    paid, gst, old = (
        api.add_tag(signed.customer_id, signed.user_id, name) for name in ("Paid", "GST", "Old")
    )
    document = _document(db, signed)
    api.tag_document(signed.customer_id, document, old)

    reply = _set(signed, document, [paid, gst, paid])
    assert reply.status_code == 200
    assert reply.json() == {"items": [{"id": gst, "name": "GST"}, {"id": paid, "name": "Paid"}]}
    assert _tag_ids_in_db(db, signed.customer_id, document) == sorted([paid, gst])

    assert _set(signed, document, []).json() == {"items": []}
    assert _tag_ids_in_db(db, signed.customer_id, document) == []


def test_the_set_is_written_to_every_version_of_the_family(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    tag = api.add_tag(signed.customer_id, signed.user_id, "Paid")
    first = _document(db, signed)
    second = _document(db, signed, family_id=first, version_no=2, is_current=False)
    assert _set(signed, second, [tag]).status_code == 200
    assert _tag_ids_in_db(db, signed.customer_id, first) == [tag]
    assert _tag_ids_in_db(db, signed.customer_id, second) == [tag]


def test_an_unknown_tag_is_404_and_nothing_is_applied(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    paid, gst = (api.add_tag(signed.customer_id, signed.user_id, name) for name in ("Paid", "GST"))
    document = _document(db, signed)
    api.tag_document(signed.customer_id, document, paid)

    reply = _set(signed, document, [gst, UNKNOWN])
    assert reply.status_code == 404
    assert reply.json()["error"]["code"] == "not_found"
    assert _tag_ids_in_db(db, signed.customer_id, document) == [paid]
    assert _set(signed, UNKNOWN, [paid]).status_code == 404
    assert _set(signed, document, [gst]).status_code == 200  # the same set without the unknown tag


def test_a_document_in_trash_cannot_be_tagged_and_keeps_its_tags(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    paid, gst = (api.add_tag(signed.customer_id, signed.user_id, name) for name in ("Paid", "GST"))
    document = _document(db, signed, deleted_at=NOW, deleted_by=signed.user_id)
    api.tag_document(signed.customer_id, document, paid)

    for tag_ids in ([gst], [], [UNKNOWN]):
        reply = _set(signed, document, tag_ids)
        assert (reply.status_code, reply.json()["error"]) == (
            409,
            {"code": "in_trash", "message": "This document is in the Trash. Restore it first."},
        )
    assert _tag_ids_in_db(db, signed.customer_id, document) == [paid]
    assert "document.tags" not in api.audit_actions(signed.customer_id)


def test_at_most_50_tags_and_only_uuids(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    tags = [api.add_tag(signed.customer_id, signed.user_id, f"Tag {n}") for n in range(51)]
    document = _document(db, signed)
    assert _set(signed, document, tags).status_code == 400
    assert _tag_ids_in_db(db, signed.customer_id, document) == []
    assert len(_set(signed, document, tags[:50]).json()["items"]) == 50
    assert _set(signed, document, ["not-a-uuid"]).status_code == 400
    assert signed.client.put(f"/api/documents/{document}/tags", json={}, headers=CSRF).status_code == 400


# --- audit and access -------------------------------------------------------------------


def test_tag_changes_are_audited_with_ids_only(db: Database) -> None:
    api = Api(db)
    admin = api.new_admin()
    document = _document(db, admin)
    tag = _create(admin, "Secret tag").json()["id"]
    _set(admin, document, [tag])
    admin.client.delete(f"/api/tags/{tag}", headers=CSRF)

    rows = [row for row in api.audit_rows(admin.customer_id) if row[0].startswith(("tag.", "document."))]
    assert rows == [
        ("tag.create", "tag", tag, {}),
        ("document.tags", "document", document, {"tag_count": 1}),
        ("tag.delete", "tag", tag, {}),
    ]
    assert "Secret" not in repr(api.audit_rows(admin.customer_id))


def test_tags_need_a_session_and_writes_need_the_csrf_header(db: Database) -> None:
    api = Api(db)
    assert api.client().get("/api/tags").status_code == 401
    signed = api.new_signed()
    reply = signed.client.post("/api/tags", json={"name": "X"})
    assert reply.status_code == 403
    assert reply.json()["error"]["code"] == "csrf"


def test_deleting_a_tag_needs_an_admin_with_two_step_sign_in_on(db: Database) -> None:
    api = Api(db)
    customer_id, _ = api.customer()
    _, email = api.add_user(customer_id, "admin")
    client = api.client()
    assert api.admin_login(client, email).status_code == 200  # password only: 2FA is off
    made = client.post("/api/tags", json={"name": "GST"}, headers=CSRF)
    assert made.status_code == 201

    reply = client.delete(f"/api/tags/{made.json()['id']}", headers=CSRF)

    assert reply.status_code == 403
    assert reply.json()["error"]["code"] == "two_step_required"
    assert [tag["name"] for tag in client.get("/api/tags").json()["items"]] == ["GST"]
