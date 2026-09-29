"""Customer B uses customer A's document ids on the version route, the Trash view
and the version list, and gets 404 (or an empty list), never A's data; A is
unchanged afterwards (PRD 1 F5 and F6).

A has a family of two versions (in a folder, tagged) and a document in Trash.
This runs through the whole API (session, routes, SQL and row level security),
and once through the repository, so a statement that forgot the customer fails.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord
from tests.test_api_support import CSRF, Api, Signed

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
NEW_BYTES = b"%PDF-1.7\n" + b"7" * 200


class _World:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.api = api = Api(db)
        a_customer, _ = api.customer("Sharma Traders")
        b_customer, _ = api.customer("Mehta Textiles")
        self.a: Signed = api.sign_in(a_customer, "admin")
        self.b: Signed = api.sign_in(b_customer, "admin")
        c, user = self.a.customer_id, self.a.user_id
        folder, tag = api.add_folder(c, user, "sharma folder"), api.add_tag(c, user, "sharma tag")
        self.first = api.add_document(c, name="sharma.pdf", folder_id=folder)
        self.second = api.add_document(
            c, name="sharma v2.pdf", folder_id=folder, family_id=self.first, version_no=2, is_current=False
        )
        for document in (self.first, self.second):
            api.tag_document(c, document, tag)
        self.binned = api.add_document(c, name="sharma old.pdf", deleted_at=NOW, deleted_by=user)
        self.b_document = api.add_document(self.b.customer_id, name="mehta.pdf")

    def a_rows(self) -> list[tuple[object, ...]]:
        """A's documents and tag links, as A sees them."""
        with self.db.customer(self.a.customer_id) as conn:
            documents = conn.execute(
                text(
                    "SELECT id, family_id, version_no, is_current, folder_id, deleted_at, display_name"
                    " FROM documents ORDER BY id"
                )
            ).all()
            links = conn.execute(
                text("SELECT document_id, tag_id FROM document_tags ORDER BY document_id")
            ).all()
        return [tuple(row) for row in (*documents, *links)]


@pytest.fixture
def world(db: Database) -> _World:
    return _World(db)


def _assert_not_found(status: int, body: dict[str, object]) -> None:
    assert status == 404
    assert body["error"] == {"code": "not_found", "message": "We couldn't find that."}


def _version(signed: Signed, document_id: str, data: bytes = NEW_BYTES) -> tuple[int, dict[str, object]]:
    reply = signed.client.post(
        f"/api/documents/{document_id}/versions",
        files={"file": ("copy.pdf", data, "application/pdf")},
        headers=CSRF,
    )
    return reply.status_code, reply.json()


def _b_refused(world: _World, document_id: str) -> None:
    status, body = _version(world.b, document_id)
    _assert_not_found(status, body)
    assert "sharma" not in str(body)


def test_b_cannot_add_a_version_to_a_document_of_a(world: _World) -> None:
    before = world.a_rows()
    for target in (world.first, world.second, world.binned):
        _b_refused(world, target)
    # Bytes B already has are still 404 on A's id, never B's own duplicate.
    b_copy = world.b.client.post(
        "/api/documents", files={"file": ("mine.pdf", NEW_BYTES, "application/pdf")}, headers=CSRF
    )
    assert b_copy.status_code == 201
    _b_refused(world, world.first)
    assert world.a_rows() == before
    assert [job.customer_id for job in world.api.queue.jobs] == [world.b.customer_id]
    # The same call works for A, so the 404s above came from the customer wall.
    assert _version(world.a, world.first, b"%PDF-1.7\n" + b"8" * 200)[0] == 201


def test_b_sees_none_of_a_in_the_trash_view_the_list_or_by_id(world: _World) -> None:
    b = world.b.client
    assert b.get("/api/documents", params={"trashed": "true"}).json() == {"items": [], "total": 0}
    assert [item["id"] for item in b.get("/api/documents").json()["items"]] == [world.b_document]
    for path in (
        f"/api/documents/{world.first}",
        f"/api/documents/{world.binned}",
        f"/api/documents/{world.binned}/file",
    ):
        reply = b.get(path)
        _assert_not_found(reply.status_code, reply.json())
        assert "sharma" not in reply.text
    # A sees its own Trash, and both of its versions in the detail.
    a = world.a.client
    a_trash = a.get("/api/documents", params={"trashed": "true"}).json()["items"]
    assert [item["id"] for item in a_trash] == [world.binned]
    assert len(a.get(f"/api/documents/{world.first}").json()["versions"]) == 2


def test_the_repository_adds_no_version_to_a_family_of_another_customer(world: _World) -> None:
    before = world.a_rows()
    record = DocumentRecord(world.b.customer_id, str(uuid.uuid4()), "b" * 64, 10, "pdf")
    documents = PgDocuments(world.db)
    for target in (world.first, world.binned):
        assert documents.add_version_if_absent(record, of_document_id=target) is None
    assert documents.get(world.b.customer_id, record.document_id) is None
    assert world.a_rows() == before
