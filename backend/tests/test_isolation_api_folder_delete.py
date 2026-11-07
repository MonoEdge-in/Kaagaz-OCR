"""Customer B's admin uses customer A's folder id on the folder summary, on both delete modes
(#537), and as the place to move B's own folder's contents to, and gets 404, and A's folders
and documents are unchanged. The control: A's own admin then gets through with the same id, so
B's 404s come from the customer (row level security and the customer filter), never from a
broken route or id."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_support import CSRF, PDF_BYTES, Api

pytestmark = pytest.mark.db

NOT_FOUND = {"error": {"code": "not_found", "message": "We couldn't find that."}}


def _state(db: Database, customer_id: str) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
    with db.customer(customer_id) as conn:
        folders = conn.execute(
            text("SELECT id::text, coalesce(parent_id::text, '') FROM folders WHERE customer_id = :c"),
            {"c": customer_id},
        )
        documents = conn.execute(
            text(
                "SELECT id::text, coalesce(folder_id::text, ''), coalesce(deleted_at::text, '')"
                " FROM documents WHERE customer_id = :c"
            ),
            {"c": customer_id},
        )
        return {tuple(row) for row in folders}, {tuple(row) for row in documents}


def test_b_gets_404_on_the_summary_and_both_delete_modes_with_a_s_folder(db: Database) -> None:
    api = Api(db)
    a = api.new_admin("Sharma Traders")
    b = api.new_admin("Mehta Textiles")
    folder = api.add_folder(a.customer_id, a.user_id, "Accounts")
    api.add_folder(a.customer_id, a.user_id, "Sub", folder)
    api.add_document(a.customer_id, uploaded_by=a.user_id, folder_id=folder)
    own = api.add_folder(b.customer_id, b.user_id, "Own")
    api.add_document(b.customer_id, uploaded_by=b.user_id, folder_id=own)
    before, b_before = _state(db, a.customer_id), _state(db, b.customer_id)

    replies = [
        b.client.get(f"/api/folders/{folder}/summary"),
        b.client.delete(f"/api/folders/{folder}?contents=keep", headers=CSRF),
        b.client.delete(f"/api/folders/{folder}?contents=trash", headers=CSRF),
        b.client.delete(f"/api/folders/{own}?contents=keep&to={folder}", headers=CSRF),
    ]

    assert [(reply.status_code, reply.json()) for reply in replies] == [(404, NOT_FOUND)] * 4
    assert _state(db, a.customer_id) == before
    assert _state(db, b.customer_id) == b_before

    assert a.client.get(f"/api/folders/{folder}/summary").json() == {
        "files": 1,
        "folders": 1,
        "bytes": len(PDF_BYTES),
    }
    assert a.client.delete(f"/api/folders/{folder}?contents=trash", headers=CSRF).status_code == 204
