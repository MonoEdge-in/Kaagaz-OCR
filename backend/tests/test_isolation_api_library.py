"""Customer B, signed in as an admin, uses customer A's exact folder, tag and
document ids on every library route and filter and gets 404, never A's data,
and A is unchanged afterwards.

B signs in on the admin screen, so the tag delete (an admin session only)
reaches the lookup instead of stopping at the session check. This runs through the whole API (session, routes,
SQL and row level security), so a route that forgot the customer filter fails.
"""

import dataclasses
import uuid

import pytest
from sqlalchemy import Connection, text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord, UnknownFolder
from tests.db_support import insert_document
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed

pytestmark = pytest.mark.db


class _World:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.api = Api(db)
        a_customer, _ = self.api.customer("Sharma Traders")
        b_customer, _ = self.api.customer("Mehta Textiles")
        self.a: Signed = self.api.admin_sign_in(a_customer)
        self.b: Signed = self.api.admin_sign_in(b_customer)
        a, api = self.a, self.api
        self.folder = api.add_folder(a.customer_id, a.user_id, "sharma folder")
        self.child = api.add_folder(a.customer_id, a.user_id, "sharma child", self.folder)
        self.tag = api.add_tag(a.customer_id, a.user_id, "sharma tag")
        with db.customer(a.customer_id) as conn:
            self.document = insert_document(
                conn, a.customer_id, display_name="sharma.pdf", folder_id=self.folder, uploaded_by=a.user_id
            )
        api.tag_document(a.customer_id, self.document, self.tag)
        b = self.b
        self.b_folder = api.add_folder(b.customer_id, b.user_id, "mehta folder")
        self.b_tag = api.add_tag(b.customer_id, b.user_id, "mehta tag")
        with db.customer(b.customer_id) as conn:
            self.b_document = insert_document(conn, b.customer_id, uploaded_by=b.user_id)

    def a_rows(self) -> dict[str, list[tuple[object, ...]]]:
        """A's folders, tags, tag links and documents, as A sees them."""
        with self.db.customer(self.a.customer_id) as conn:
            return {
                "folders": _rows(conn, "SELECT id, parent_id, name FROM folders ORDER BY id"),
                "tags": _rows(conn, "SELECT id, name FROM tags ORDER BY id"),
                "links": _rows(conn, "SELECT document_id, tag_id FROM document_tags ORDER BY tag_id"),
                "documents": _rows(conn, "SELECT id, folder_id, display_name FROM documents ORDER BY id"),
            }

    def b_tag_links(self) -> list[tuple[object, ...]]:
        with self.db.customer(self.b.customer_id) as conn:
            return _rows(conn, "SELECT document_id, tag_id FROM document_tags")


def _rows(conn: Connection, statement: str) -> list[tuple[object, ...]]:
    return [tuple(row) for row in conn.execute(text(statement))]


@pytest.fixture
def world(db: Database) -> _World:
    return _World(db)


def _assert_not_found(status: int, body: dict[str, object]) -> None:
    assert status == 404
    assert body["error"] == {"code": "not_found", "message": "We couldn't find that."}


def test_b_lists_none_of_a_folders_or_tags(world: _World) -> None:
    for path in ("/api/folders", "/api/tags"):
        reply = world.b.client.get(path)
        assert reply.status_code == 200, path
        assert "sharma" not in reply.text, path
        for a_id in (world.folder, world.child, world.tag):
            assert a_id not in reply.text, path


def test_b_cannot_rename_move_delete_or_create_inside_a_folder_of_a(world: _World) -> None:
    before = world.a_rows()
    b, folder = world.b.client, world.folder
    for reply in (
        b.patch(f"/api/folders/{folder}", json={"name": "taken over"}, headers=CSRF),
        b.patch(f"/api/folders/{world.child}", json={"parent_id": None}, headers=CSRF),
        b.patch(f"/api/folders/{world.b_folder}", json={"parent_id": folder}, headers=CSRF),
        b.post("/api/folders", json={"name": "inside a", "parent_id": folder}, headers=CSRF),
        b.delete(f"/api/folders/{world.child}", headers=CSRF),
    ):
        _assert_not_found(reply.status_code, reply.json())
        assert "sharma" not in reply.text
    assert world.a_rows() == before
    assert [item["parent_id"] for item in b.get("/api/folders").json()["items"]] == [None]


def test_b_cannot_delete_a_tag_of_a(world: _World) -> None:
    before = world.a_rows()
    reply = world.b.client.delete(f"/api/tags/{world.tag}", headers=CSRF)
    _assert_not_found(reply.status_code, reply.json())
    assert world.a_rows() == before
    # The same call works for A, so the 404 above came from the customer wall.
    assert world.a.client.delete(f"/api/tags/{world.tag}", headers=CSRF).status_code == 204


def test_b_cannot_set_tags_on_a_document_of_a_or_use_a_tag_of_a(world: _World) -> None:
    before = world.a_rows()
    b = world.b.client
    for reply in (
        b.put(f"/api/documents/{world.document}/tags", json={"tag_ids": [world.b_tag]}, headers=CSRF),
        b.put(f"/api/documents/{world.document}/tags", json={"tag_ids": []}, headers=CSRF),
        b.put(f"/api/documents/{world.b_document}/tags", json={"tag_ids": [world.tag]}, headers=CSRF),
    ):
        _assert_not_found(reply.status_code, reply.json())
        assert "sharma" not in reply.text
    assert world.a_rows() == before
    assert world.b_tag_links() == []
    a_set = world.a.client.put(f"/api/documents/{world.document}/tags", json={"tag_ids": []}, headers=CSRF)
    assert a_set.status_code == 200


def test_names_are_per_customer_so_b_may_reuse_a_names(world: _World) -> None:
    b = world.b.client
    assert b.post("/api/folders", json={"name": "sharma folder"}, headers=CSRF).status_code == 201
    assert b.post("/api/tags", json={"name": "SHARMA TAG"}, headers=CSRF).status_code == 201
    assert len(world.a_rows()["folders"]) == 2


def test_b_cannot_list_by_a_folder_or_a_tag_of_a(world: _World) -> None:
    b = world.b.client
    for params in (
        {"folder_id": world.folder},
        {"folder_id": world.folder, "include_subfolders": "true"},
        {"tag_id": [world.tag]},
        {"tag_id": [world.b_tag, world.tag]},
    ):
        reply = b.get("/api/documents", params=params)
        _assert_not_found(reply.status_code, reply.json())
        assert "sharma" not in reply.text
    # The same filters work for A.
    assert world.a.client.get("/api/documents", params={"folder_id": world.folder}).json()["total"] == 1
    assert world.a.client.get("/api/documents", params={"tag_id": [world.tag]}).json()["total"] == 1


def test_b_cannot_rename_or_move_a_document_of_a_or_file_its_own_in_a_folder_of_a(world: _World) -> None:
    before = world.a_rows()
    b, document = world.b.client, world.document
    for reply in (
        b.patch(f"/api/documents/{document}", json={"display_name": "taken.pdf"}, headers=CSRF),
        b.patch(f"/api/documents/{document}", json={"folder_id": None}, headers=CSRF),
        b.patch(f"/api/documents/{document}", json={"folder_id": world.b_folder}, headers=CSRF),
        b.patch(f"/api/documents/{world.b_document}", json={"folder_id": world.folder}, headers=CSRF),
    ):
        _assert_not_found(reply.status_code, reply.json())
        assert "sharma" not in reply.text
    assert world.a_rows() == before
    assert b.get(f"/api/documents/{world.b_document}").json()["folder_id"] is None
    moved = world.a.client.patch(f"/api/documents/{document}", json={"folder_id": world.child}, headers=CSRF)
    assert moved.status_code == 200


def test_b_cannot_upload_into_a_folder_of_a(world: _World) -> None:
    reply = world.b.client.post(
        "/api/documents",
        data={"folder_id": world.folder},
        files={"file": ("copy.pdf", PDF_BYTES, "application/pdf")},
        headers=CSRF,
    )
    _assert_not_found(reply.status_code, reply.json())
    assert world.api.storage.count() == 0
    assert world.api.queue.jobs == []
    assert world.b.client.get("/api/documents").json()["total"] == 1  # only B's own row from the setup


def test_the_repository_refuses_a_record_filed_in_a_folder_of_another_customer(world: _World) -> None:
    documents = PgDocuments(world.db)
    record = DocumentRecord(
        world.b.customer_id, str(uuid.uuid4()), "b" * 64, 10, "pdf", folder_id=world.folder
    )
    with pytest.raises(UnknownFolder):
        documents.add_if_absent(record)
    assert documents.get(world.b.customer_id, record.document_id) is None
    filed = dataclasses.replace(record, folder_id=world.b_folder)
    assert documents.add_if_absent(filed) == (filed, True)
