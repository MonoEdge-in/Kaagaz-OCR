"""Customer B never sees, opens, restores or deletes customer A's folder in Trash (#542): B's
Trash lists none of A's, every route with A's trashed folder id is 404 for B and changes nothing,
and B's own connection reads no row of A's ``trashed_folders`` (row level security). The control:
A's own admin then gets through with the same ids, so B's 404s come from the customer, never
from a broken route or id."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, Api

pytestmark = pytest.mark.db

NOT_FOUND = {"error": {"code": "not_found", "message": "We couldn't find that."}}


def _state(db: Database, customer_id: str) -> tuple[set[tuple[str, ...]], ...]:
    with db.customer(customer_id) as conn:
        return tuple(
            {tuple(str(v) for v in row) for row in conn.execute(text(statement), {"c": customer_id})}
            for statement in (
                "SELECT id, parent_id FROM folders WHERE customer_id = :c",
                "SELECT id, parent_id, trash_id FROM trashed_folders WHERE customer_id = :c",
                "SELECT id, folder_id, deleted_at, trashed_folder_id FROM documents WHERE customer_id = :c",
            )
        )


def test_b_never_reaches_a_s_folder_in_trash(db: Database) -> None:
    api = Api(db)
    a = api.new_admin("Sharma Traders")
    b = api.new_admin("Mehta Textiles")
    folder = api.add_folder(a.customer_id, a.user_id, "Accounts")
    sub = api.add_folder(a.customer_id, a.user_id, "Sub", folder)
    document = api.add_document(a.customer_id, uploaded_by=a.user_id, folder_id=sub)
    assert a.client.delete(f"/api/folders/{folder}?contents=trash", headers=CSRF).status_code == 200
    before, b_before = _state(db, a.customer_id), _state(db, b.customer_id)

    assert b.client.get("/api/documents/trash/folders").json() == {"items": []}
    # Changed on purpose (#744, 22 Nov 2026): delete forever needs no fresh sign-in, so B (an admin)
    # reaches the lookup at once; its 404 still comes from the customer boundary.
    replies = [
        b.client.get(f"/api/documents/trash/folders/{sub}"),
        b.client.get(f"/api/documents?trashed=true&trashed_folder_id={sub}"),
        b.client.post(f"/api/documents/trash/folders/{folder}/restore", headers=CSRF),
        b.client.delete(f"/api/documents/trash/folders/{folder}/permanent", headers=CSRF),
        b.client.post(f"/api/documents/{document}/restore", headers=CSRF),
    ]

    assert [(reply.status_code, reply.json()) for reply in replies] == [(404, NOT_FOUND)] * 5
    assert (_state(db, a.customer_id), _state(db, b.customer_id)) == (before, b_before)
    with db.customer(b.customer_id) as conn:  # the policy alone, without the customer filter
        assert conn.execute(text("SELECT count(*) FROM trashed_folders")).scalar_one() == 0
        assert (
            conn.execute(
                text("SELECT count(*) FROM trashed_folders WHERE id = :f"), {"f": folder}
            ).scalar_one()
            == 0
        )

    # The control: A's admin opens and restores it with the same ids.
    assert [item["id"] for item in a.client.get("/api/documents/trash/folders").json()["items"]] == [folder]
    assert a.client.get(f"/api/documents/trash/folders/{sub}").status_code == 200
    assert a.client.post(f"/api/documents/trash/folders/{folder}/restore", headers=CSRF).status_code == 200
    assert _state(db, a.customer_id)[1] == set()


def test_b_cannot_write_a_trashed_folder_for_a(db: Database) -> None:
    """WITH CHECK: B's connection cannot put a row into A's trash."""
    api = Api(db)
    a = api.new_admin("Sharma Traders")
    b = api.new_admin("Mehta Textiles")
    with pytest.raises(DBAPIError, match="row-level security"), db.customer(b.customer_id) as conn:
        conn.execute(
            text(
                "INSERT INTO trashed_folders (id, customer_id, name, kind, created_by, created_at, trash_id,"
                " root, deleted_at, deleted_by) VALUES (gen_random_uuid(), :a, 'X', 'custom', :u, now(),"
                " gen_random_uuid(), true, now(), :u)"
            ),
            {"a": a.customer_id, "u": a.user_id},
        )
