"""Customer B, signed in as an admin, uses customer A's exact folder, tag and
document ids on every library route and gets 404 (or an empty list, where a
list is the reply), never A's data, and A is unchanged afterwards.

B is an admin, so the admin-only tag delete reaches the lookup instead of
stopping at the role check. This runs through the whole API (session, routes,
SQL and row level security), so a route that forgot the customer filter fails.
"""

import pytest
from sqlalchemy import Connection, text

from kaagaz.db.engine import Database
from tests.db_support import insert_document
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db


class _World:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.api = Api(db)
        a_customer, _ = self.api.customer("Sharma Traders")
        b_customer, _ = self.api.customer("Mehta Textiles")
        self.a: Signed = self.api.sign_in(a_customer, "admin")
        self.b: Signed = self.api.sign_in(b_customer, "admin")
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
